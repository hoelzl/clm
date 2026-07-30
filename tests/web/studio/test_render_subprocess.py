"""Tests for the subprocess preview render (issue #698).

The in-process value caps bound memory; the subprocess bounds CPU via a
wall-clock kill and removes the thread-occupancy vector (the route awaits
the child on the event loop). One child per request; every failure mode
degrades to tier-1.
"""

import asyncio
import time
from pathlib import Path

import pytest

from clm.web.studio.render import (
    render_j2_cell_html_in_subprocess,
    render_j2_cell_in_subprocess,
)

DECK = "slides_x.de.py"

#: 10^10 iterations producing two characters — the issue's CPU-bomb shape,
#: measured at roughly two hours in-process.
CPU_BOMB = "{% for a in range(100000) %}{% for b in range(100000) %}{% endfor %}{% endfor %}ok"


@pytest.mark.slow
class TestSubprocessRender:
    def test_normal_body_renders(self, tmp_path: Path):
        ok, error, text = asyncio.run(
            render_j2_cell_in_subprocess(tmp_path / DECK, "{{ 1 + 2 }}", "de")
        )
        assert ok, error
        assert text == "3"

    def test_cpu_bomb_is_killed_within_the_budget(self, tmp_path: Path):
        """The #698 regression: in-process this runs for hours; the
        subprocess is killed at the wall-clock budget and the preview
        degrades to tier-1 with the body unchanged."""
        started = time.monotonic()
        ok, error, text = asyncio.run(
            render_j2_cell_in_subprocess(tmp_path / DECK, CPU_BOMB, "de", timeout=3.0)
        )
        elapsed = time.monotonic() - started
        assert ok is False
        assert error is not None and "timed out" in error
        assert text == CPU_BOMB  # tier-1 fallback gets the original body
        assert elapsed < 30, f"kill took {elapsed:.1f}s"

    def test_render_errors_degrade_like_in_process(self, tmp_path: Path):
        ok, error, text = asyncio.run(
            render_j2_cell_in_subprocess(tmp_path / DECK, "{% include 'missing.j2' %}", "de")
        )
        assert ok is False
        assert error
        assert text == "{% include 'missing.j2' %}"

    def test_value_caps_hold_inside_the_child(self, tmp_path: Path):
        ok, error, _text = asyncio.run(
            render_j2_cell_in_subprocess(tmp_path / DECK, '{{ "A" * 200000000 }}', "de")
        )
        assert ok is False
        assert error

    def test_html_variant_sanitizes_the_child_output(self, tmp_path: Path):
        ok, error, html = asyncio.run(
            render_j2_cell_html_in_subprocess(
                tmp_path / DECK, "# {{ 1 + 2 }} <script>alert(1)</script>", "de"
            )
        )
        assert ok, error
        assert html is not None
        assert "3" in html
        assert "<script>" not in html


def _live_render_children():
    """Pids of live render_child processes (whole tree, launcher included)."""
    import psutil

    found = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
        except Exception:  # noqa: BLE001 - processes vanish mid-iteration
            continue
        if any("clm.web.studio.render_child" in part for part in cmdline):
            found.append(proc)
    return found


def _assert_no_live_children(deadline: float = 10.0) -> None:
    """The whole child tree must be gone — on Windows the venv launcher is a
    trampoline whose grandchild does the rendering (#698 review MEDIUM-1)."""
    import time as time_module

    end = time_module.monotonic() + deadline
    while time_module.monotonic() < end:
        if not _live_render_children():
            return
        time_module.sleep(0.2)
    raise AssertionError(f"live render children: {_live_render_children()}")


@pytest.mark.slow
@pytest.mark.serial
class TestChildLifetime:
    """#698 review HIGH-1: no code path may leave a live child behind —
    the kill/reap is in a finally, and the child self-limits."""

    def test_timeout_kills_the_whole_child_tree(self, tmp_path: Path):
        ok, _error, _text = asyncio.run(
            render_j2_cell_in_subprocess(tmp_path / DECK, CPU_BOMB, "de", timeout=3.0)
        )
        assert ok is False
        _assert_no_live_children()

    def test_cancellation_kills_the_child(self, tmp_path: Path):
        """Cancelling the awaiting task (timeout middleware, teardown) must
        not orphan a burning child — the finally + the child watchdog."""

        async def scenario():
            task = asyncio.create_task(
                render_j2_cell_in_subprocess(tmp_path / DECK, CPU_BOMB, "de", timeout=60.0)
            )
            await asyncio.sleep(2.0)  # let the child spawn and start burning
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            # Give the finally's bounded reap a moment.
            await asyncio.sleep(0.5)

        asyncio.run(scenario())
        _assert_no_live_children()

    def test_child_watchdog_self_limits_without_a_parent_kill(self, tmp_path: Path):
        """The parent-death shape (#698 review HIGH-1 case 2): even with no
        parent-side kill at all, the child's own watchdog exits it. Spawn
        the child directly and never kill it."""
        import json
        import subprocess
        import sys
        import time as time_module

        request = json.dumps(
            {
                "deck_path": str(tmp_path / DECK),
                "body": CPU_BOMB,
                "lang": "de",
                "budget": 2.0,  # watchdog fires at budget + 5s grace
            }
        ).encode("utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-I", "-m", "clm.web.studio.render_child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.stdin.write(request)
            proc.stdin.close()
            deadline = time_module.monotonic() + 30
            while proc.poll() is None and time_module.monotonic() < deadline:
                time_module.sleep(0.5)
            assert proc.poll() is not None, "watchdog did not fire"
            from clm.web.studio.render_child import WATCHDOG_EXIT_CODE

            # Windows: the watchdog timer exits with its marker code. POSIX:
            # RLIMIT_CPU's hard limit can beat the timer and kill with
            # SIGKILL (-9) or SIGXCPU (-24) — that is the self-limit
            # working too, via the other belt.
            assert proc.returncode in (WATCHDOG_EXIT_CODE, -9, -24), proc.returncode
        finally:
            if proc.poll() is None:
                proc.kill()


@pytest.mark.slow
class TestParentBranches:
    """The parent's degradation branches, driven by a stubbed child."""

    def _run_with_child(self, monkeypatch, tmp_path: Path, child_args: tuple):
        from clm.web.studio import render as render_module

        monkeypatch.setattr(render_module, "_CHILD_ARGS", child_args)
        return asyncio.run(
            render_j2_cell_in_subprocess(tmp_path / DECK, "{{ 1 }}", "de", timeout=5.0)
        )

    def test_nonzero_exit_degrades(self, monkeypatch, tmp_path: Path):
        ok, error, text = self._run_with_child(
            monkeypatch, tmp_path, ("-c", "import sys; sys.exit(7)")
        )
        assert ok is False and "exit 7" in error and text == "{{ 1 }}"

    def test_malformed_stdout_degrades(self, monkeypatch, tmp_path: Path):
        ok, error, text = self._run_with_child(monkeypatch, tmp_path, ("-c", "print('not json')"))
        assert ok is False and error and text == "{{ 1 }}"

    def test_oversized_child_output_is_refused(self, monkeypatch, tmp_path: Path):
        child = (
            "-c",
            "import json,sys;"
            "sys.stdout.write(json.dumps({'ok': True, 'error': None, 'text': 'A'*2000000}))",
        )
        ok, error, text = self._run_with_child(monkeypatch, tmp_path, child)
        assert ok is False and "too large" in error and text == "{{ 1 }}"
