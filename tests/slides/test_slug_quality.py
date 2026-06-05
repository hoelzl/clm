"""Tests for the slug-quality classifier and scanner (gap #6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.slug_quality import (
    SlugIssue,
    classify_slug,
    render_report,
    report_to_dict,
    scan_slug_quality,
)


class TestClassify:
    @pytest.mark.parametrize(
        "slug,expected",
        [
            ("cp", [SlugIssue.VERY_SHORT]),
            ("df", [SlugIssue.VERY_SHORT]),
            ("x", [SlugIssue.VERY_SHORT]),
            ("os", [SlugIssue.VERY_SHORT]),
            ("data", [SlugIssue.GENERIC]),
            ("true", [SlugIssue.GENERIC]),
            ("value", [SlugIssue.GENERIC]),
            ("introduction", [SlugIssue.SINGLE_TOKEN]),
            ("functions", [SlugIssue.SINGLE_TOKEN]),
        ],
    )
    def test_single_token_cases(self, slug, expected):
        assert classify_slug(slug) == expected

    def test_multi_token_clean(self):
        assert classify_slug("introduction-to-functions") == []
        assert classify_slug("step-2") == []
        assert classify_slug("python-3") == []

    def test_truncation_flag(self):
        # 30 chars, at the cap.
        slug = "my-great-slide-about-functions"
        assert len(slug) == 30
        assert SlugIssue.POSSIBLY_TRUNCATED in classify_slug(slug)

    def test_short_multi_token_not_truncated(self):
        assert classify_slug("a-b-c") == []  # 5 chars, multi-token, fine

    def test_title_and_empty_never_flagged(self):
        assert classify_slug("title") == []
        assert classify_slug("") == []

    def test_preserve_marker_stripped(self):
        assert classify_slug("!data") == [SlugIssue.GENERIC]
        assert classify_slug("!introduction-to-functions") == []


def _deck(slide_ids: list[str]) -> str:
    cells = []
    for i, sid in enumerate(slide_ids):
        cells.append(
            f'# %% [markdown] lang="en" tags=["slide"] slide_id="{sid}"\n# ## Heading {i}\n'
        )
    return "\n".join(cells)


class TestScan:
    def test_collects_only_flagged(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["introduction-to-x", "df", "data"]), encoding="utf-8")
        report = scan_slug_quality([f])
        assert report.total_ids == 3
        flagged = {finding.slide_id for finding in report.findings}
        assert flagged == {"df", "data"}  # the clean multi-token id is not flagged

    def test_narrative_cells_not_double_counted(self, tmp_path):
        text = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="df"\n# ## Heading\n\n'
            '# %% [markdown] lang="en" tags=["notes"] slide_id="df"\n# speaker notes\n'
        )
        f = tmp_path / "slides_x.py"
        f.write_text(text, encoding="utf-8")
        report = scan_slug_quality([f])
        # The notes cell inherits "df" but must not be scanned/counted again.
        assert report.total_ids == 1
        assert len(report.findings) == 1

    def test_bilingual_twin_reported_once(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="df"\n# ## Titel\n\n'
            '# %% [markdown] lang="en" tags=["slide"] slide_id="df"\n# ## Title\n'
        )
        f = tmp_path / "slides_x.py"
        f.write_text(text, encoding="utf-8")
        report = scan_slug_quality([f])
        assert report.total_ids == 1
        assert len(report.findings) == 1

    def test_missing_file_skipped(self, tmp_path):
        report = scan_slug_quality([tmp_path / "gone.py"])
        assert report.files_scanned == 0
        assert report.total_ids == 0

    def test_by_severity_and_issue(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["df", "data", "introduction"]), encoding="utf-8")
        report = scan_slug_quality([f])
        assert report.by_severity == {"high": 2, "low": 1}
        assert report.by_issue[SlugIssue.VERY_SHORT] == 1
        assert report.by_issue[SlugIssue.GENERIC] == 1

    def test_at_or_above(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["df", "introduction"]), encoding="utf-8")
        report = scan_slug_quality([f])
        high = report.at_or_above("high")
        assert [f.slide_id for f in high] == ["df"]
        assert len(report.at_or_above("low")) == 2


class TestRender:
    def test_clean_message(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["introduction-to-functions"]), encoding="utf-8")
        out = render_report(scan_slug_quality([f]))
        assert "all look fine" in out

    def test_render_lists_and_summarizes(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["df", "data", "introduction"]), encoding="utf-8")
        out = render_report(scan_slug_quality([f]))
        assert 'slide_id="df"' in out
        assert "very_short" in out
        assert "3 flagged" in out

    def test_min_severity_filters_render(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["df", "introduction"]), encoding="utf-8")
        out = render_report(scan_slug_quality([f]), min_severity="high")
        assert 'slide_id="df"' in out
        assert 'slide_id="introduction"' not in out

    def test_to_dict_shape(self, tmp_path):
        f = tmp_path / "slides_x.py"
        f.write_text(_deck(["df", "introduction"]), encoding="utf-8")
        d = report_to_dict(scan_slug_quality([f]))
        assert d["total_ids"] == 2
        assert d["flagged"] == 2
        assert d["by_issue"]["very_short"] == 1
