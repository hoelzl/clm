"""Tests for the assign-ids refusal worklist (gap #5)."""

from __future__ import annotations

from pathlib import Path

from clm.slides.assign_ids import AssignOptions, Refusal, assign_ids_in_file
from clm.slides.refusal_report import (
    build_refusal_worklist,
    render_worklist,
    worklist_to_dict,
)

# A deck with: a headed slide, a hard-refusal slide (img, no alt), and a
# soft-refusal slide (bullet content but no heading).
DECK = (
    '# %% [markdown] lang="en" tags=["slide"]\n'
    "# ## Introduction\n"
    "#\n"
    "# Welcome.\n"
    "\n"
    '# %% [markdown] lang="en" tags=["slide"]\n'
    '# <img src="img/diagram.png">\n'
    "\n"
    '# %% [markdown] lang="en" tags=["slide"]\n'
    "# - first bullet here\n"
    "# - second bullet\n"
)


def _write(tmp_path: Path, text: str = DECK) -> Path:
    f = tmp_path / "slides_x.py"
    f.write_text(text, encoding="utf-8")
    return f


class TestBuildWorklist:
    def test_hard_and_soft_collected(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        wl = build_refusal_worklist(result.refusals)
        assert len(wl.hard) == 1
        assert len(wl.soft) == 1
        # Hard sorts before soft.
        assert wl.entries[0].severity == "hard"
        assert wl.entries[1].severity == "soft"

    def test_no_context_means_no_file_read(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        # Delete the file: build without context must not need it.
        f.unlink()
        wl = build_refusal_worklist(result.refusals, with_context=False)
        assert all(e.context is None for e in wl.entries)

    def test_context_recovers_marker_body_anchors(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        wl = build_refusal_worklist(result.refusals, with_context=True)
        hard = wl.hard[0]
        assert hard.context is not None
        assert hard.context.marker.startswith("# %% [markdown]")
        assert "img/diagram.png" in hard.context.body
        assert hard.context.preceding_heading == "Introduction"
        assert hard.context.cell_type == "markdown"
        assert hard.context.lang == "en"

    def test_soft_refusal_keeps_proposal(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        wl = build_refusal_worklist(result.refusals, with_context=True)
        soft = wl.soft[0]
        assert soft.proposed_slug  # bullet-derived slug
        assert soft.context is not None
        assert "first bullet" in soft.context.body

    def test_preceding_slide_id_after_write(self, tmp_path):
        # When ids are actually written, the hard refusal's preceding anchor
        # picks up the real slide_id minted on the headed slide.
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(accept_content_derived=True))
        wl = build_refusal_worklist(result.refusals, with_context=True)
        hard = wl.hard[0]
        assert hard.context is not None
        assert hard.context.preceding_slide_id == "introduction"

    def test_missing_file_degrades_gracefully(self, tmp_path):
        refusals = [Refusal(file=str(tmp_path / "gone.py"), line=3, severity="hard", reason="x")]
        wl = build_refusal_worklist(refusals, with_context=True)
        assert wl.entries[0].context is None

    def test_start_of_deck_has_no_anchors(self, tmp_path):
        text = '# %% [markdown] lang="en" tags=["slide"]\n# <img src="x.png">\n'
        f = _write(tmp_path, text)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        wl = build_refusal_worklist(result.refusals, with_context=True)
        ctx = wl.hard[0].context
        assert ctx is not None
        assert ctx.preceding_slide_id is None
        assert ctx.preceding_heading is None


class TestRender:
    def test_empty_worklist_message(self):
        out = render_worklist(build_refusal_worklist([]))
        assert "No refusals" in out

    def test_render_includes_body_and_anchor(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        out = render_worklist(build_refusal_worklist(result.refusals, with_context=True))
        assert "[hard]" in out
        assert 'heading "Introduction"' in out
        assert "img/diagram.png" in out
        assert "1 hard refusal(s)" in out

    def test_render_without_context_is_compact(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        out = render_worklist(build_refusal_worklist(result.refusals))
        assert "[hard]" in out
        # No body lines piped in without context.
        assert "img/diagram.png" not in out


class TestToDict:
    def test_dict_shape(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        d = worklist_to_dict(build_refusal_worklist(result.refusals, with_context=True))
        assert d["hard_refusals"] == 1
        assert d["soft_refusals"] == 1
        assert len(d["refusals"]) == 2
        hard = next(r for r in d["refusals"] if r["severity"] == "hard")
        assert hard["context"]["preceding_heading"] == "Introduction"

    def test_dict_no_context_is_null(self, tmp_path):
        f = _write(tmp_path)
        result = assign_ids_in_file(f, AssignOptions(report_only=True))
        d = worklist_to_dict(build_refusal_worklist(result.refusals))
        assert all(r["context"] is None for r in d["refusals"])
