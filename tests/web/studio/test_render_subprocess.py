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
