"""Subprocess entry for the Studio tier-2 preview render (issue #698).

The parent (:func:`clm.web.studio.render.render_j2_cell_in_subprocess`)
spawns ``python -I -m clm.web.studio.render_child``, writes one JSON request
to stdin (``{"deck_path": …, "body": …, "lang": …, "budget": …}``), and
reads one JSON response from stdout (``{"ok": …, "error": …, "text": …}``).
The render itself is the ordinary in-process
:func:`~clm.web.studio.render.render_j2_cell` with all its value-size caps —
the subprocess adds what those caps cannot: a wall-clock bound, so CPU burn
like nested ``range()`` loops ends with the process instead of stalling the
server.

**The child is self-limiting** (#698 review HIGH-1): the parent's kill is
the fast path, but no parent-side code path may be load-bearing for the
bound — a cancelled request, a crashed server, or a kill that only reaches
a venv launcher trampoline (uv's ``python.exe`` re-launches the real
interpreter as a *child*; ``TerminateProcess`` does not kill trees) must
not leave an unbounded burner behind. So the child arms its own limits
before rendering: a daemon watchdog timer that hard-exits at the budget
plus grace (works everywhere, immune to what the template does), and on
POSIX ``RLIMIT_CPU``/``RLIMIT_AS`` belts.

Protocol notes:

- Exactly one request per process — no reuse, so a wedged render never
  poisons a later one and the kill path stays trivial.
- Pipes are **explicit UTF-8 binary** (#698 review MINOR-2): text-mode
  stdio uses the locale encoding, which round-trips today only because
  ``json.dumps`` defaults to ``ensure_ascii=True`` — an undeclared,
  load-bearing invariant this removes.
- Any crash, malformed output, nonzero exit, or watchdog exit degrades the
  preview to tier-1 in the parent; nothing here needs to be defensive
  about its own failure mode.
- stdout carries ONLY the JSON response (logging is forced to stderr,
  which the parent drains and logs on failure).
"""

from __future__ import annotations

import json
import os
import sys
import threading

#: Address-space cap for the child (POSIX only). Generous: the in-process
#: value caps keep legitimate previews far below this.
_ADDRESS_SPACE_LIMIT_BYTES = 1_024 * 1_024 * 1_024  # 1 GiB

#: Fallback wall-clock budget when the request carries none, and the grace
#: added on top of the parent's budget — the parent's kill is the fast
#: path; the watchdog only has to catch the parent *failing* to kill.
_DEFAULT_BUDGET_SECONDS = 30.0
_WATCHDOG_GRACE_SECONDS = 5.0

#: Watchdog exit code, distinguishable from render crashes in the parent.
WATCHDOG_EXIT_CODE = 3


def _apply_limits(budget: float) -> None:
    """Arm the self-limits: watchdog everywhere, rlimits on POSIX."""
    timer = threading.Timer(budget + _WATCHDOG_GRACE_SECONDS, lambda: os._exit(WATCHDOG_EXIT_CODE))
    timer.daemon = True
    timer.start()
    try:
        import resource  # POSIX-only module

        # The paired unused-ignore keeps POSIX mypy green too — Windows
        # mypy cannot see these attrs (the transcribe.py pattern).
        resource.setrlimit(  # type: ignore[attr-defined,unused-ignore]
            resource.RLIMIT_AS,  # type: ignore[attr-defined,unused-ignore]
            (_ADDRESS_SPACE_LIMIT_BYTES, _ADDRESS_SPACE_LIMIT_BYTES),
        )
        cpu_seconds = max(1, int(budget + _WATCHDOG_GRACE_SECONDS))
        resource.setrlimit(  # type: ignore[attr-defined,unused-ignore]
            resource.RLIMIT_CPU,  # type: ignore[attr-defined,unused-ignore]
            (cpu_seconds, cpu_seconds),
        )
    except Exception:  # noqa: BLE001 - belts only; the watchdog is the bound
        pass


def main() -> int:
    import logging
    from pathlib import Path

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    try:
        budget = float(request.get("budget") or _DEFAULT_BUDGET_SECONDS)
    except (TypeError, ValueError):
        budget = _DEFAULT_BUDGET_SECONDS
    _apply_limits(budget)

    from clm.web.studio.render import render_j2_cell

    ok, error, text = render_j2_cell(
        Path(request["deck_path"]), request["body"], request.get("lang")
    )
    sys.stdout.buffer.write(json.dumps({"ok": ok, "error": error, "text": text}).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
