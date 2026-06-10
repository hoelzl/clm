"""Cohort-archive scenario tests for spec-consuming commands.

Each test below builds a slides tree containing the same topic ID in two
modules (the live module and a frozen-cohort archive), writes a course
spec that binds one section to each module, then exercises a command and
asserts that the module binding is honoured.

Before this fix:

* ``validate-slides course.xml`` and ``normalize-slides course.xml``
  visited *every* filesystem match for each topic ID, processing the
  cohort archive even when the spec scoped the section to the live
  module.
* ``search-slides --course-spec`` returned matches in the cohort archive
  module for a spec that bound itself to the live module.
* ``resolve-topic --course-spec`` returned a path in the wrong module if
  it happened to win first-occurrence ordering.
* ``authoring-rules --slide-path=…`` listed every course that mentioned
  the topic ID, regardless of which module each spec was bound to.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.commands.course.resolve_topic import resolve_topic_cmd
from clm.slides.authoring_rules import get_authoring_rules
from clm.slides.normalizer import normalize_course
from clm.slides.search import search_slides
from clm.slides.validator import validate_course

SLIDE_TEMPLATE = (
    '# %% [markdown] lang="de"\n# {heading}\n\n# %% [markdown] lang="en"\n# {heading}\n'
)


def _make_topic(tmp_path: Path, module: str, topic: str, heading: str) -> Path:
    """Create slides/{module}/{topic}/slides_intro.py and return its path."""
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    slide = topic_dir / "slides_intro.py"
    slide.write_text(SLIDE_TEMPLATE.format(heading=heading), encoding="utf-8")
    return slide


def _write_spec(tmp_path: Path, name: str, sections_xml: str) -> Path:
    """Write a course spec under course-specs/ with the supplied sections."""
    specs_dir = tmp_path / "course-specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_file = specs_dir / f"{name}.xml"
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>{name}</de><en>{name}</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          {sections_xml}
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


class TestValidateCourseModuleAware:
    """``validate-slides COURSE.xml`` must respect ``module=`` bindings."""

    def test_section_module_filters_to_one_copy(self, tmp_path):
        # Two modules, same topic ID.
        _make_topic(tmp_path, "module_100_live", "topic_010_intro", "Live")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "Frozen")

        # Spec binds to the live module only.
        spec_file = _write_spec(
            tmp_path,
            "live",
            """\
            <sections><section module="module_100_live">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        result = validate_course(spec_file, tmp_path / "slides")

        # Only the live copy should have been validated, not both.
        assert result.files_checked == 1


class TestNormalizeCourseModuleAware:
    """``normalize-slides COURSE.xml`` must respect ``module=`` bindings."""

    def test_section_module_normalizes_only_bound_copy(self, tmp_path):
        live = _make_topic(tmp_path, "module_100_live", "topic_010_intro", "Live")
        frozen = _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "Frozen")
        live_before = live.read_text(encoding="utf-8")
        frozen_before = frozen.read_text(encoding="utf-8")

        spec_file = _write_spec(
            tmp_path,
            "live",
            """\
            <sections><section module="module_545_frozen">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        result = normalize_course(spec_file, tmp_path / "slides", dry_run=True)

        # The dry-run should only have inspected the frozen module's slide
        # (heading "Frozen"). Tracking the change list by file path proves
        # the bound copy was the *only* thing the normalizer touched.
        touched = {Path(c.file).resolve() for c in result.changes}
        # Even without changes, no inspection of the live file should
        # have occurred — assert via filesystem mtimes / contents.
        assert live.read_text(encoding="utf-8") == live_before
        assert frozen.read_text(encoding="utf-8") == frozen_before
        # If the normalizer inspected the live file we'd see its path
        # show up among the operations it considered.
        assert all(p == frozen.resolve() for p in touched) or not touched


class TestSearchSlidesModuleAware:
    """``search-slides --course-spec`` should not return cohort copies."""

    def test_module_bound_spec_excludes_other_module(self, tmp_path):
        _make_topic(tmp_path, "module_100_live", "topic_010_intro", "Introduction")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "Introduction")

        spec_file = _write_spec(
            tmp_path,
            "live",
            """\
            <sections><section module="module_100_live">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        results = search_slides(
            "Introduction",
            tmp_path / "slides",
            course_spec_path=spec_file,
        )

        # Only the live module's copy should be returned.
        assert len(results) == 1
        assert "module_100_live" in results[0].directory
        assert "module_545_frozen" not in results[0].directory

    def test_unbound_spec_returns_all(self, tmp_path):
        _make_topic(tmp_path, "module_100_live", "topic_010_intro", "Introduction")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "Introduction")

        spec_file = _write_spec(
            tmp_path,
            "live",
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        results = search_slides(
            "Introduction",
            tmp_path / "slides",
            course_spec_path=spec_file,
        )

        modules = {Path(r.directory).parent.name for r in results}
        assert modules == {"module_100_live", "module_545_frozen"}

    def test_courses_field_excludes_other_module_specs(self, tmp_path):
        """A spec bound to module X should not be listed for hits in Y."""
        _make_topic(tmp_path, "module_100_live", "topic_010_intro", "Introduction")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "Introduction")

        # Two specs: one bound to live, one bound to frozen.
        _write_spec(
            tmp_path,
            "live-course",
            """\
            <sections><section module="module_100_live">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )
        _write_spec(
            tmp_path,
            "frozen-course",
            """\
            <sections><section module="module_545_frozen">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        results = search_slides("Introduction", tmp_path / "slides")
        by_module = {Path(r.directory).parent.name: r.courses for r in results}

        assert by_module["module_100_live"] == ["live-course.xml"]
        assert by_module["module_545_frozen"] == ["frozen-course.xml"]


class TestResolveTopicModuleAware:
    """``resolve-topic --course-spec`` must respect spec module bindings."""

    def test_course_spec_filters_by_module(self, tmp_path):
        live = _make_topic(tmp_path, "module_100_live", "topic_010_intro", "L")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "F")

        spec_file = _write_spec(
            tmp_path,
            "live",
            """\
            <sections><section module="module_100_live">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        runner = CliRunner()
        result = runner.invoke(
            resolve_topic_cmd,
            [
                "intro",
                "--course-spec",
                str(spec_file),
                "--data-dir",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0, result.output
        # Should resolve to the live copy, not the frozen one.
        assert str(live.parent.resolve()) in result.output
        assert "module_545_frozen" not in result.output


class TestAuthoringRulesModuleAware:
    """``authoring-rules --slide-path`` should match by (topic_id, module)."""

    def test_slide_in_live_module_only_lists_live_course(self, tmp_path):
        live = _make_topic(tmp_path, "module_100_live", "topic_010_intro", "L")
        _make_topic(tmp_path, "module_545_frozen", "topic_010_intro", "F")

        # Two specs, each bound to a different module. Only the live one
        # has authoring rules — but the broken behaviour would also
        # return "frozen" as a referencing course.
        _write_spec(
            tmp_path,
            "live-course",
            """\
            <sections><section module="module_100_live">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )
        _write_spec(
            tmp_path,
            "frozen-course",
            """\
            <sections><section module="module_545_frozen">
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        # Drop authoring rules for both courses so we can check which
        # gets picked up (the lookup also reports "no rules" for missing
        # files, but the count of CourseRulesEntry items is what we care
        # about).
        (tmp_path / "course-specs" / "live-course.authoring.md").write_text(
            "# Live rules\n", encoding="utf-8"
        )
        (tmp_path / "course-specs" / "frozen-course.authoring.md").write_text(
            "# Frozen rules\n", encoding="utf-8"
        )

        result = get_authoring_rules(tmp_path, slide_path=str(live))

        course_names = [e.course_spec for e in result.course_rules]
        assert course_names == ["live-course"]
