"""Subprocess entry for the Studio tier-2 preview render (issue #698).

The parent (:func:`clm.web.studio.render.render_j2_cell_in_subprocess`)
spawns ``python -m clm.web.studio.render_child``, writes one JSON request to
stdin (``{"deck_path": …, "body": …, "lang": …}``), and reads one JSON
response from stdout (``{"ok": …, "error": …, "text": …}``). The render
itself is the ordinary in-process :func:`~clm.web.studio.render.render_j2_cell`
with all its value-size caps — the subprocess adds what those caps cannot:
a wall-clock bound enforced by the parent killing this process, and (POSIX)
an address-space rlimit, so CPU burn like nested ``range()`` loops ends with
the process instead of stalling the server.

Protocol notes:

- Exactly one request per process — no reuse, so a wedged render never
  poisons a later one and the kill path stays trivial.
- Any crash, malformed output, or nonzero exit degrades the preview to
  tier-1 in the parent; nothing here needs to be defensive about its own
  failure mode.
- stdout carries ONLY the JSON response (logging is forced to stderr), so
  the parent can parse unconditionally.
"""

from __future__ import annotations

import json
import sys

#: Address-space cap for the child (POSIX only — Windows has no rlimits; the
#: wall-clock kill is the bound there). Generous: the in-process value caps
#: keep legitimate previews far below this.
_ADDRESS_SPACE_LIMIT_BYTES = 1_024 * 1_024 * 1_024  # 1 GiB


def _apply_posix_rlimit() -> None:
    try:
        import resource  # POSIX-only module

        # The paired unused-ignore keeps POSIX mypy green too — Windows
        # mypy cannot see these attrs (the transcribe.py pattern).
        resource.setrlimit(  # type: ignore[attr-defined,unused-ignore]
            resource.RLIMIT_AS,  # type: ignore[attr-defined,unused-ignore]
            (_ADDRESS_SPACE_LIMIT_BYTES, _ADDRESS_SPACE_LIMIT_BYTES),
        )
    except Exception:  # noqa: BLE001 - belt only; the wall clock is the bound
        pass


def main() -> int:
    import logging
    from pathlib import Path

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    _apply_posix_rlimit()

    request = json.loads(sys.stdin.read())

    from clm.web.studio.render import render_j2_cell

    ok, error, text = render_j2_cell(
        Path(request["deck_path"]), request["body"], request.get("lang")
    )
    sys.stdout.write(json.dumps({"ok": ok, "error": error, "text": text}))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
