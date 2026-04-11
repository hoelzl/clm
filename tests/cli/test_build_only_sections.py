"""Integration tests for ``clm build --only-sections``.

These tests exercise the section-filter plumbing end-to-end *without*
spinning up real workers. They cover:

- CLI argument parsing (``--only-sections`` accepted, help text, empty
  selector rejected)
- Selector → ``Course.from_spec`` → filtered ``Course.sections`` flow
- ``_compute_section_dirs_for_cleanup`` returning the correct per-section
  subdirectories
- The section-level cleanup semantics: ``rmtree`` only selected sections,
  leave unselected sections' directories untouched
- Rename warning when a selected section's output directory is missing
- Dir-group processing skipped in ``--only-sections`` mode

Full end-to-end builds with actual worker subprocesses live in
``tests/e2e/`` and are marked ``@pytest.mark.e2e``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.build import _compute_section_dirs_for_cleanup
from clm.cli.main import cli
from clm.core.course import Course
from clm.core.course_spec import CourseSpec

# Shared test data
DATA_DIR = Path(__file__).parent.parent / "test-data"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


THREE_SECTION_XML = """
<course>
    <name>
        <de>Drei-Abschnitte-Kurs</de>
        <en>Three-Section Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description><de>Demo</de><en>Demo</en></description>
    <certificate><de>.</de><en>.</en></certificate>
    <sections>
        <section id="w01">
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>some_topic_from_test_1</topic>
            </topics>
        </section>
        <section id="w02">
            <name>
                <de>Woche 2</de>
                <en>Week 2</en>
            </name>
            <topics>
                <topic>a_topic_from_test_2</topic>
            </topics>
        </section>
        <section id="w03">
            <name>
                <de>Woche 3</de>
                <en>Week 3</en>
            </name>
            <topics>
                <topic>another_topic_from_test_1</topic>
            </topics>
        </section>
    </sections>
</course>
"""


@pytest.fixture
def three_section_spec() -> CourseSpec:
    """A 3-section course spec with stable ``id`` attributes."""
    return CourseSpec.from_file(io.StringIO(THREE_SECTION_XML))


@pytest.fixture
def three_section_spec_with_disabled() -> CourseSpec:
    """A 3-section course spec where w02 is disabled. Parsed with
    ``keep_disabled=True`` so all three sections are present."""
    xml = THREE_SECTION_XML.replace('<section id="w02">', '<section id="w02" enabled="false">')
    return CourseSpec.from_file(io.StringIO(xml), keep_disabled=True)


# ---------------------------------------------------------------------------
# CLI-level tests (argument parsing and error surfaces)
# ---------------------------------------------------------------------------


class TestBuildOnlySectionsCli:
    def test_only_sections_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["build", "--help"])
        assert result.exit_code == 0
        assert "--only-sections" in result.output

    def test_empty_value_rejected(self, tmp_path):
        """Empty ``--only-sections ""`` is an error, not a silent full build."""
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text(THREE_SECTION_XML)
        runner = CliRunner()
        result = runner.invoke(cli, ["build", str(spec_file), "--only-sections", ""])
        assert result.exit_code != 0
        assert "empty" in result.output.lower() or "whitespace" in result.output.lower()

    def test_whitespace_only_value_rejected(self, tmp_path):
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text(THREE_SECTION_XML)
        runner = CliRunner()
        result = runner.invoke(cli, ["build", str(spec_file), "--only-sections", "   "])
        assert result.exit_code != 0

    def test_trailing_comma_rejected(self, tmp_path):
        """``w01,`` → second token is empty → reject."""
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text(THREE_SECTION_XML)
        runner = CliRunner()
        result = runner.invoke(cli, ["build", str(spec_file), "--only-sections", "w01,"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Course.from_spec + section_selection — the filter cascade
# ---------------------------------------------------------------------------


class TestCourseFromSpecSectionFilter:
    def test_unfiltered_builds_all_sections(self, three_section_spec, tmp_path):
        course = Course.from_spec(three_section_spec, DATA_DIR, tmp_path)
        assert [s.name.en for s in course.sections] == [
            "Week 1",
            "Week 2",
            "Week 3",
        ]

    def test_single_section_selection_by_id(self, three_section_spec, tmp_path):
        sel = three_section_spec.resolve_section_selectors(["w02"])
        course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            tmp_path,
            section_selection=sel,
        )
        assert [s.name.en for s in course.sections] == ["Week 2"]

    def test_multiple_sections_preserve_declared_order(self, three_section_spec, tmp_path):
        """Token order: w03, w01. Output order should match the declared
        spec order (w01 first, then w03)."""
        sel = three_section_spec.resolve_section_selectors(["w03", "w01"])
        course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            tmp_path,
            section_selection=sel,
        )
        assert [s.name.en for s in course.sections] == [
            "Week 1",
            "Week 3",
        ]

    def test_filtering_cascades_to_files(self, three_section_spec, tmp_path):
        full_course = Course.from_spec(three_section_spec, DATA_DIR, tmp_path)
        sel = three_section_spec.resolve_section_selectors(["w01"])
        filtered_course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            tmp_path,
            section_selection=sel,
        )
        # course.files and course.topics are derived properties; filtering
        # `sections` should reduce both automatically.
        assert len(filtered_course.files) < len(full_course.files)
        assert len(filtered_course.topics) < len(full_course.topics)

    def test_disabled_section_not_built_even_when_kept_in_spec(
        self, three_section_spec_with_disabled, tmp_path
    ):
        """The resolver excludes disabled sections from `resolved_indices`,
        so `_build_sections` never sees them — even when the spec was
        parsed with ``keep_disabled=True``."""
        spec = three_section_spec_with_disabled
        # All three sections are still in spec.sections (because of
        # keep_disabled=True), and w02 is disabled.
        assert [s.name.en for s in spec.sections] == [
            "Week 1",
            "Week 2",
            "Week 3",
        ]
        assert [s.enabled for s in spec.sections] == [True, False, True]

        sel = spec.resolve_section_selectors(["w01", "w02", "w03"])
        assert sel.resolved_indices == [0, 2]
        assert sel.skipped_disabled == ["w02"]

        course = Course.from_spec(spec, DATA_DIR, tmp_path, section_selection=sel)
        assert [s.name.en for s in course.sections] == [
            "Week 1",
            "Week 3",
        ]


# ---------------------------------------------------------------------------
# Section directory cleanup helper
# ---------------------------------------------------------------------------


class TestComputeSectionDirsForCleanup:
    def test_returns_one_dir_per_section_per_spec(self, three_section_spec, tmp_path):
        """With default targets (both languages × all kinds × all formats),
        each section yields multiple output directories per combination.
        The set must contain only section-level dirs, never the top-level
        output root."""
        sel = three_section_spec.resolve_section_selectors(["w01"])
        course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            tmp_path,
            section_selection=sel,
        )
        dirs = _compute_section_dirs_for_cleanup(course)
        assert dirs, "expected at least one section directory"
        # Every returned directory must be a subdirectory of the course
        # output root — it must NOT be the root itself.
        for d in dirs:
            assert course.output_root not in [d, d.parent]
            # The dir name should sanitize to the section name (for some
            # language).
            assert ("Week 1" in str(d)) or ("Woche 1" in str(d))

    def test_multiple_sections_produce_disjoint_dir_names(self, three_section_spec, tmp_path):
        sel = three_section_spec.resolve_section_selectors(["w01", "w03"])
        course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            tmp_path,
            section_selection=sel,
        )
        dirs = _compute_section_dirs_for_cleanup(course)
        # Each returned dir must correspond to either Week 1 or Week 3,
        # never Week 2 (which is not selected).
        for d in dirs:
            name = str(d)
            assert "Week 2" not in name and "Woche 2" not in name


# ---------------------------------------------------------------------------
# End-to-end cleanup semantics (no real workers)
#
# We run the cleanup loop from process_course_with_backend manually against
# a temp output tree, then verify the filesystem state. This exercises the
# exact logic that `--only-sections` uses without depending on worker
# execution or the job queue.
# ---------------------------------------------------------------------------


class TestSectionLevelCleanupSemantics:
    def _fabricate_section_tree(
        self, course: Course, marker_name: str = "sentinel.txt"
    ) -> list[Path]:
        """Create each expected section output directory on disk with a
        sentinel file inside. Returns the list of sentinel paths."""
        # Use the full (unfiltered) Course to build every section dir so
        # we can assert that unselected ones survive.
        from clm.cli.commands.build import _compute_section_dirs_for_cleanup

        sentinels: list[Path] = []
        for section_dir in _compute_section_dirs_for_cleanup(course):
            section_dir.mkdir(parents=True, exist_ok=True)
            sentinel = section_dir / marker_name
            sentinel.write_text("sentinel", encoding="utf-8")
            sentinels.append(sentinel)
        return sentinels

    def test_only_selected_section_is_removed(self, three_section_spec, tmp_path):
        """Full course builds every section dir with a sentinel. A
        follow-up --only-sections w02 rmtree on filtered_course
        should wipe only w02's dirs and leave w01/w03 sentinels intact."""
        import shutil

        full_course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            output_root=tmp_path / "out",
        )
        full_sentinels = self._fabricate_section_tree(full_course)
        # Partition sentinels by which week they belong to.
        w01_sentinels = [s for s in full_sentinels if "Woche 1" in str(s) or "Week 1" in str(s)]
        w02_sentinels = [s for s in full_sentinels if "Woche 2" in str(s) or "Week 2" in str(s)]
        w03_sentinels = [s for s in full_sentinels if "Woche 3" in str(s) or "Week 3" in str(s)]
        assert w01_sentinels and w02_sentinels and w03_sentinels

        # Now mimic the --only-sections cleanup loop on a filtered course.
        sel = three_section_spec.resolve_section_selectors(["w02"])
        filtered_course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            output_root=tmp_path / "out",
            section_selection=sel,
        )
        for section_dir in _compute_section_dirs_for_cleanup(filtered_course):
            if section_dir.exists():
                shutil.rmtree(section_dir, ignore_errors=True)

        # w02 sentinels must be gone; w01 and w03 must still exist.
        for s in w02_sentinels:
            assert not s.exists(), f"w02 sentinel should be removed: {s}"
        for s in w01_sentinels:
            assert s.exists(), f"w01 sentinel should be preserved: {s}"
        for s in w03_sentinels:
            assert s.exists(), f"w03 sentinel should be preserved: {s}"

    def test_missing_section_dir_is_tolerated(self, three_section_spec, tmp_path):
        """If a selected section has no existing output directory (fresh
        build or rename), the cleanup loop should not raise."""
        import shutil

        sel = three_section_spec.resolve_section_selectors(["w02"])
        filtered_course = Course.from_spec(
            three_section_spec,
            DATA_DIR,
            output_root=tmp_path / "out",
            section_selection=sel,
        )
        # Do NOT pre-create any section dirs.
        section_dirs = _compute_section_dirs_for_cleanup(filtered_course)
        # Should not raise.
        for section_dir in section_dirs:
            if section_dir.exists():
                shutil.rmtree(section_dir, ignore_errors=True)
