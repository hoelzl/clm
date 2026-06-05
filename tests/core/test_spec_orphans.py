"""Tests for orphan detection and classification (gap #7)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.core.spec_orphans import (
    OrphanKind,
    classify_orphan,
    find_checkpoint_dirs,
    find_orphans,
    render_report,
    report_to_dict,
)

DECK = '# %% [markdown] lang="en" tags=["slide"]\n# ## Intro\n'


class TestClassify:
    @pytest.mark.parametrize(
        "name,kind,reason_sub",
        [
            ("slides_x_old.py", OrphanKind.SUPERSEDED, "_old"),
            ("slides_x_old2.py", OrphanKind.SUPERSEDED, "_old"),
            ("slides_x_bak.py", OrphanKind.SUPERSEDED, "bak"),
            ("slides_x_backup.py", OrphanKind.SUPERSEDED, "backup"),
            ("slides_x_orig.py", OrphanKind.SUPERSEDED, "orig"),
            ("slides_x_deprecated.py", OrphanKind.SUPERSEDED, "deprecated"),
            ("slides_x_v1.py", OrphanKind.SUPERSEDED, "version"),
            ("slides_x_2.py", OrphanKind.SUPERSEDED, "numeric"),
            ("slides_x_part1.py", OrphanKind.ALTERNATE, "part"),
            ("slides_x_part5.py", OrphanKind.ALTERNATE, "part"),
            ("slides_x_short.py", OrphanKind.ALTERNATE, "short"),
            ("slides_x_long.py", OrphanKind.ALTERNATE, "long"),
            ("slides_x.py", OrphanKind.UNKNOWN, "no recognizable"),
            ("slides_observer_advanced.py", OrphanKind.UNKNOWN, "no recognizable"),
        ],
    )
    def test_cases(self, name, kind, reason_sub):
        k, reason = classify_orphan(Path(name))
        assert k == kind
        assert reason_sub in reason

    def test_alternate_beats_numeric_for_partn(self):
        # _part2 ends in a digit but must classify as alternate, not numeric dup.
        k, _ = classify_orphan(Path("slides_topic_part2.py"))
        assert k == OrphanKind.ALTERNATE

    def test_lang_tag_stripped_before_marker(self):
        k, _ = classify_orphan(Path("slides_x_old.de.py"))
        assert k == OrphanKind.SUPERSEDED
        k2, _ = classify_orphan(Path("slides_x_part1.en.py"))
        assert k2 == OrphanKind.ALTERNATE


def _course(tmp_path: Path, referenced: list[str], orphan_decks: dict[str, list[str]]) -> Path:
    """Build a course; return the course-specs dir.

    ``referenced`` is the topic ids the single spec pulls in. ``orphan_decks``
    maps a topic-dir id -> deck filenames that no spec references.
    """
    slides = tmp_path / "slides" / "module_100_x"
    for i, tid in enumerate(referenced):
        d = slides / f"topic_{i:03d}0_{tid}"
        d.mkdir(parents=True)
        (d / f"slides_{tid}.py").write_text(DECK, encoding="utf-8")
    for tid, decks in orphan_decks.items():
        d = slides / f"topic_900_{tid}"
        d.mkdir(parents=True)
        for deck in decks:
            (d / deck).write_text(DECK, encoding="utf-8")

    specs = tmp_path / "course-specs"
    specs.mkdir(parents=True, exist_ok=True)
    topics = "".join(f"<topic>{t}</topic>" for t in referenced)
    (specs / "c.xml").write_text(
        dedent(f"""\
        <course><name><de>C</de><en>C</en></name><prog-lang>python</prog-lang>
        <description><de></de><en></en></description><certificate><de></de><en></en></certificate>
        <sections><section><name><de>S</de><en>S</en></name>
        <topics>{topics}</topics></section></sections></course>
        """),
        encoding="utf-8",
    )
    return specs


class TestFindOrphans:
    def test_referenced_decks_are_not_orphans(self, tmp_path):
        specs = _course(tmp_path, ["a"], {"b": ["slides_b_old.py"]})
        slides_dir = tmp_path / "slides"
        report = find_orphans(sorted(specs.glob("*.xml")), slides_dir)
        paths = {p.name for p in (o.path for o in report.orphans)}
        assert "slides_a.py" not in paths
        assert "slides_b_old.py" in paths
        assert report.shipping_count == 1

    def test_grouping_by_kind(self, tmp_path):
        specs = _course(
            tmp_path,
            ["a"],
            {"b": ["slides_b_old.py", "slides_b_part1.py", "slides_b_misc.py"]},
        )
        report = find_orphans(sorted(specs.glob("*.xml")), tmp_path / "slides")
        by_kind = report.by_kind
        assert len(by_kind[OrphanKind.SUPERSEDED]) == 1
        assert len(by_kind[OrphanKind.ALTERNATE]) == 1
        assert len(by_kind[OrphanKind.UNKNOWN]) == 1

    def test_checkpoints_excluded_from_decks_and_reported(self, tmp_path):
        specs = _course(tmp_path, ["a"], {})
        ck = tmp_path / "slides" / "module_100_x" / "topic_0000_a" / ".ipynb_checkpoints"
        ck.mkdir(parents=True)
        (ck / "slides_a-checkpoint.py").write_text(DECK, encoding="utf-8")
        report = find_orphans(sorted(specs.glob("*.xml")), tmp_path / "slides")
        # The checkpoint copy is not a deck and not an orphan.
        assert all(".ipynb_checkpoints" not in p.parts for p in (o.path for o in report.orphans))
        assert len(report.checkpoints) == 1

    def test_non_py_orphan_detected(self, tmp_path):
        # A .cpp orphan must be found — the report walk is extension-complete.
        specs = _course(tmp_path, ["a"], {})
        d = tmp_path / "slides" / "module_100_x" / "topic_900_c"
        d.mkdir(parents=True)
        (d / "slides_c_old.cpp").write_text("// %%\n", encoding="utf-8")
        report = find_orphans(sorted(specs.glob("*.xml")), tmp_path / "slides")
        names = {p.name for p in (o.path for o in report.orphans)}
        assert "slides_c_old.cpp" in names

    def test_find_checkpoint_dirs(self, tmp_path):
        slides = tmp_path / "slides"
        (slides / "a" / ".ipynb_checkpoints").mkdir(parents=True)
        (slides / "b" / ".ipynb_checkpoints").mkdir(parents=True)
        found = find_checkpoint_dirs(slides)
        assert len(found) == 2


class TestRender:
    def test_clean_course_message(self, tmp_path):
        specs = _course(tmp_path, ["a"], {})
        report = find_orphans(sorted(specs.glob("*.xml")), tmp_path / "slides")
        out = render_report(report, tmp_path / "slides")
        assert "No orphans" in out

    def test_render_groups_and_summarizes(self, tmp_path):
        specs = _course(tmp_path, ["a"], {"b": ["slides_b_old.py", "slides_b_part1.py"]})
        report = find_orphans(sorted(specs.glob("*.xml")), tmp_path / "slides")
        out = render_report(report, tmp_path / "slides")
        assert "superseded" in out
        assert "alternate" in out
        assert "slides_b_old.py" in out

    def test_to_dict_shape(self, tmp_path):
        specs = _course(tmp_path, ["a"], {"b": ["slides_b_old.py"]})
        report = find_orphans(sorted(specs.glob("*.xml")), tmp_path / "slides")
        d = report_to_dict(report)
        assert d["orphan_count"] == 1
        assert d["by_kind"]["superseded"] == 1
        assert d["orphans"][0]["kind"] == "superseded"
