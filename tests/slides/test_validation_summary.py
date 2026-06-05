"""Tests for :mod:`clm.slides.validation_summary`."""

from __future__ import annotations

from clm.slides.validation_summary import (
    classify_kind,
    render_summary,
    summarize_findings,
)
from clm.slides.validator import Finding


def _f(severity: str, category: str, file: str, message: str, line: int = 1) -> Finding:
    return Finding(severity=severity, category=category, file=file, line=line, message=message)


class TestClassifyKind:
    def test_missing_slide_id(self):
        assert classify_kind("slide/subslide cell missing slide_id") == "missing-slide_id"

    def test_slug(self):
        assert (
            classify_kind("slide_id 'Foo' is not a valid kebab-case ASCII slug") == "slide_id-slug"
        )

    def test_malformed_marker(self):
        assert classify_kind("Cell header does not start with '# %%'") == "malformed-marker"

    def test_start_completed(self):
        assert (
            classify_kind("'completed' tag without a preceding 'start' cell") == "start-completed"
        )

    def test_unknown_falls_back_to_other(self):
        assert classify_kind("something entirely novel") == "other"


class TestSummarizeFindings:
    def test_counts_by_severity_and_category(self):
        findings = [
            _f("error", "format", "a.py", "Cell header does not start with '# %%'"),
            _f("error", "tags", "a.py", "Unrecognized tag 'x'"),
            _f("warning", "pairing", "b.py", "slide_id mismatch across pair"),
        ]
        s = summarize_findings(findings)

        assert s.total == 3
        assert s.by_severity["error"] == 2
        assert s.by_severity["warning"] == 1
        assert s.by_category_severity["format"]["error"] == 1
        assert s.by_category_severity["pairing"]["warning"] == 1

    def test_per_file_sorted_worst_first(self):
        findings = [
            _f("warning", "tags", "low.py", "x"),
            _f("error", "format", "high.py", "Cell header does not start with '# %%'"),
            _f("error", "tags", "high.py", "Unrecognized tag 'y'"),
        ]
        s = summarize_findings(findings)

        assert s.by_file[0].file == "high.py"
        assert s.by_file[0].errors == 2
        assert s.by_file[1].file == "low.py"

    def test_to_dict_roundtrips_structure(self):
        s = summarize_findings([_f("error", "format", "a.py", "missing slide_id")])
        d = s.to_dict()
        assert d["total"] == 1
        assert d["by_kind"]["missing-slide_id"] == 1
        assert d["by_file"][0]["file"] == "a.py"

    def test_empty(self):
        s = summarize_findings([])
        assert s.total == 0
        assert s.by_file == []


class TestRenderSummary:
    def test_empty_is_single_line(self):
        lines = render_summary(summarize_findings([]))
        assert len(lines) == 1
        assert "0 finding" in lines[0]

    def test_truncates_file_list(self):
        findings = [_f("error", "format", f"deck_{i}.py", "missing slide_id") for i in range(25)]
        lines = render_summary(summarize_findings(findings), top_files=10)
        text = "\n".join(lines)
        assert "and 15 more deck(s)" in text
