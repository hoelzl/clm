"""Tests for the DE/EN coverage core (gap #8)."""

from __future__ import annotations

from pathlib import Path

from clm.slides.lang_coverage import (
    CoverageStatus,
    classify_counts,
    count_languages,
    render_report,
    report_to_dict,
    scan_coverage,
)


def _bi(de: int, en: int) -> str:
    cells = []
    for i in range(de):
        cells.append(f'# %% [markdown] lang="de" tags=["slide"]\n# ## De {i}\n')
    for i in range(en):
        cells.append(f'# %% [markdown] lang="en" tags=["slide"]\n# ## En {i}\n')
    return "\n".join(cells)


class TestCounting:
    def test_count_languages(self):
        assert count_languages(_bi(2, 3)) == (2, 3)

    def test_narrative_not_counted(self):
        text = (
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Slide\n\n'
            '# %% [markdown] lang="en" tags=["notes"]\n# notes\n\n'
            '# %% [markdown] lang="de" tags=["notes"]\n# notizen\n'
        )
        # One EN slide; the two notes cells are excluded.
        assert count_languages(text) == (0, 1)

    def test_classify(self):
        assert classify_counts(2, 2) == CoverageStatus.BALANCED
        assert classify_counts(0, 0) == CoverageStatus.BALANCED
        assert classify_counts(3, 0) == CoverageStatus.DE_ONLY
        assert classify_counts(0, 3) == CoverageStatus.EN_ONLY
        assert classify_counts(3, 2) == CoverageStatus.IMBALANCED


class TestScan:
    def test_bilingual_balanced_and_imbalanced(self, tmp_path):
        (tmp_path / "slides_bal.py").write_text(_bi(2, 2), encoding="utf-8")
        (tmp_path / "slides_imb.py").write_text(_bi(2, 1), encoding="utf-8")
        report = scan_coverage(sorted(tmp_path.glob("*.py")))
        by = {e.label.split("slides_")[1]: e for e in report.entries}
        assert by["bal.py"].status == CoverageStatus.BALANCED
        assert by["imb.py"].status == CoverageStatus.IMBALANCED
        assert by["imb.py"].delta == 1
        assert by["imb.py"].kind == "bilingual"

    def test_split_pair_scored_together(self, tmp_path):
        (tmp_path / "slides_p.de.py").write_text(_bi(1, 0), encoding="utf-8")
        (tmp_path / "slides_p.en.py").write_text(_bi(0, 2), encoding="utf-8")
        report = scan_coverage(sorted(tmp_path.glob("*.py")))
        assert len(report.entries) == 1
        e = report.entries[0]
        assert e.kind == "split-pair"
        assert (e.de_cells, e.en_cells) == (1, 2)
        assert e.status == CoverageStatus.IMBALANCED

    def test_lone_split_half_is_one_language(self, tmp_path):
        (tmp_path / "slides_lone.de.py").write_text(_bi(2, 0), encoding="utf-8")
        report = scan_coverage(sorted(tmp_path.glob("*.py")))
        e = report.entries[0]
        assert e.kind == "split-half"
        assert (e.de_cells, e.en_cells) == (2, 0)
        assert e.status == CoverageStatus.DE_ONLY

    def test_balanced_split_pair(self, tmp_path):
        (tmp_path / "slides_p.de.py").write_text(_bi(2, 0), encoding="utf-8")
        (tmp_path / "slides_p.en.py").write_text(_bi(0, 2), encoding="utf-8")
        report = scan_coverage(sorted(tmp_path.glob("*.py")))
        assert report.entries[0].status == CoverageStatus.BALANCED
        assert not report.incomplete

    def test_incomplete_excludes_balanced(self, tmp_path):
        (tmp_path / "slides_bal.py").write_text(_bi(2, 2), encoding="utf-8")
        (tmp_path / "slides_de.de.py").write_text(_bi(1, 0), encoding="utf-8")
        report = scan_coverage(sorted(tmp_path.glob("*.py")))
        assert [e.kind for e in report.incomplete] == ["split-half"]


class TestRender:
    def test_all_balanced_message(self, tmp_path):
        (tmp_path / "slides_a.py").write_text(_bi(1, 1), encoding="utf-8")
        out = render_report(scan_coverage(sorted(tmp_path.glob("*.py"))))
        assert "balanced" in out

    def test_render_groups(self, tmp_path):
        (tmp_path / "slides_de.de.py").write_text(_bi(1, 0), encoding="utf-8")
        (tmp_path / "slides_imb.py").write_text(_bi(2, 1), encoding="utf-8")
        out = render_report(scan_coverage(sorted(tmp_path.glob("*.py"))), base=tmp_path)
        assert "needs EN translation" in out
        assert "imbalanced" in out
        assert "Δ1" in out

    def test_to_dict(self, tmp_path):
        (tmp_path / "slides_de.de.py").write_text(_bi(1, 0), encoding="utf-8")
        d = report_to_dict(scan_coverage(sorted(tmp_path.glob("*.py"))))
        assert d["total"] == 1
        assert d["by_status"]["de_only"] == 1
        assert d["decks"][0]["status"] == "de_only"
