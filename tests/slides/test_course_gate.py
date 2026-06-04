"""Tests for :mod:`clm.slides.course_gate`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.course_gate import run_course_gate, scope_decks

# A balanced DE/EN pair with headings but no slide_id → content-derived mint
# (mechanical, no author work).
HEADING_PAIR = (
    '# %% [markdown] lang="de" tags=["slide"]\n# ## Einfuehrung\n\n'
    '# %% [markdown] lang="en" tags=["slide"]\n# ## Introduction\n'
)

# A single non-extractable DE slide → hard refusal (needs author).
NON_EXTRACTABLE = '# %% [markdown] lang="de" tags=["slide"]\n#\n'

# alt-after-start → a tag_migration mechanical change.
ALT_AFTER_START = (
    '# %% [markdown] lang="de" tags=["slide", "start"] slide_id="s"\n# ## A\n\n'
    '# %% [markdown] lang="de" tags=["alt"] slide_id="s"\n# ## B\n'
)


def _deck(
    tmp_path: Path,
    name: str,
    content: str,
    *,
    module: str = "module_100_x",
    topic: str = "topic_010_y",
) -> Path:
    d = tmp_path / "slides" / module / topic
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


class TestRunCourseGate:
    def test_mechanical_only_no_author(self, tmp_path):
        _deck(tmp_path, "slides_intro.py", HEADING_PAIR)
        report = run_course_gate(tmp_path / "slides", tmp_path / "slides")

        assert report.deck_count == 1
        assert report.changes_by_operation.get("slide_ids", 0) == 2
        assert report.needs_author == []
        assert report.residual is None  # dry-run
        assert report.is_clean is True

    def test_hard_refusal_needs_author(self, tmp_path):
        _deck(tmp_path, "slides_bad.py", NON_EXTRACTABLE)
        report = run_course_gate(tmp_path / "slides", tmp_path / "slides")

        issues = {ri.issue for ri in report.needs_author}
        assert "slide_id_hard_refusal" in issues
        assert report.is_clean is False

    def test_tag_migration_change(self, tmp_path):
        _deck(tmp_path, "slides_tags.py", ALT_AFTER_START)
        report = run_course_gate(tmp_path / "slides", tmp_path / "slides")

        assert report.changes_by_operation.get("tag_migration", 0) == 1

    def test_dry_run_writes_nothing(self, tmp_path):
        p = _deck(tmp_path, "slides_intro.py", HEADING_PAIR)
        before = p.read_text(encoding="utf-8")

        run_course_gate(tmp_path / "slides", tmp_path / "slides", apply=False)

        assert p.read_text(encoding="utf-8") == before

    def test_apply_writes_and_clears(self, tmp_path):
        p = _deck(tmp_path, "slides_intro.py", HEADING_PAIR)
        report = run_course_gate(tmp_path / "slides", tmp_path / "slides", apply=True)

        assert 'slide_id="' in p.read_text(encoding="utf-8")
        assert report.residual is not None
        assert report.residual.by_severity.get("error", 0) == 0
        assert report.is_clean is True

    def test_invalid_operation_raises(self, tmp_path):
        _deck(tmp_path, "slides_intro.py", HEADING_PAIR)
        with pytest.raises(ValueError, match="Unknown operation"):
            run_course_gate(tmp_path / "slides", tmp_path / "slides", operations=["bogus"])

    def test_scope_decks_directory(self, tmp_path):
        _deck(tmp_path, "slides_a.py", HEADING_PAIR)
        _deck(tmp_path, "slides_b.py", HEADING_PAIR, topic="topic_020_z")
        decks = scope_decks(tmp_path / "slides", tmp_path / "slides")
        assert len(decks) == 2
