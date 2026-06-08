"""Validator tests for the ``<subsection>`` day-of-week checks (issue #261)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from clm.slides.spec_validator import validate_spec


def _write_spec(tmp_path: Path, topics_xml: str) -> Path:
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>Test</de><en>Test</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          <sections><section>
            <name><de>Woche 1</de><en>Week 1</en></name>
            <topics>
{topics_xml}
            </topics>
          </section></sections>
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


def _make_topic(tmp_path: Path, module: str, topic: str, *, with_slide: bool = True) -> Path:
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    if with_slide:
        (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hi\n")
    return topic_dir


def _types(result) -> list[str]:
    return [f.type for f in result.findings]


class TestCleanSubsections:
    def test_well_formed_subsections_have_no_subsection_findings(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_topic(tmp_path, "module_100", "topic_020_more")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon"><topic>intro</topic></subsection>
            <subsection weekday="tue"><topic>more</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        subsection_types = {
            "duplicate_weekday",
            "weekday_out_of_order",
            "empty_day",
            "unscheduled_topics",
        }
        assert not (set(_types(result)) & subsection_types)


class TestDuplicateWeekday:
    def test_duplicate_weekday_warns_once(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        _make_topic(tmp_path, "module_100", "topic_020_b")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon"><topic>a</topic></subsection>
            <subsection weekday="mon"><topic>b</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        dups = [f for f in result.findings if f.type == "duplicate_weekday"]
        assert len(dups) == 1
        assert dups[0].severity == "warning"
        assert "mon" in dups[0].message


class TestWeekdayOrdering:
    def test_out_of_order_weekday_warns(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        _make_topic(tmp_path, "module_100", "topic_020_b")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="tue"><topic>a</topic></subsection>
            <subsection weekday="mon"><topic>b</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        order = [f for f in result.findings if f.type == "weekday_out_of_order"]
        assert len(order) == 1
        assert order[0].severity == "warning"

    def test_out_of_order_repeated_weekday_reported_once(self, tmp_path):
        """A weekday that is both out of order AND repeated emits a single
        out-of-order finding, not one per occurrence."""
        _make_topic(tmp_path, "module_100", "topic_010_a")
        _make_topic(tmp_path, "module_100", "topic_020_b")
        _make_topic(tmp_path, "module_100", "topic_030_c")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="tue"><topic>a</topic></subsection>
            <subsection weekday="mon"><topic>b</topic></subsection>
            <subsection weekday="mon"><topic>c</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        order = [f for f in result.findings if f.type == "weekday_out_of_order"]
        assert len(order) == 1

    def test_in_order_weekdays_do_not_warn(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        _make_topic(tmp_path, "module_100", "topic_020_b")
        _make_topic(tmp_path, "module_100", "topic_030_c")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon"><topic>a</topic></subsection>
            <subsection weekday="wed"><topic>b</topic></subsection>
            <subsection weekday="fri"><topic>c</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert "weekday_out_of_order" not in _types(result)


class TestEmptyDay:
    def test_subsection_without_topics_warns(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon"><topic>a</topic></subsection>
            <subsection weekday="tue"><name><de>Leer</de><en>Empty</en></name></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        empty = [f for f in result.findings if f.type == "empty_day"]
        assert len(empty) == 1
        assert empty[0].severity == "warning"

    def test_subsection_resolving_to_zero_decks_warns(self, tmp_path):
        # Topic dir exists (resolves) but has no slide files.
        _make_topic(tmp_path, "module_100", "topic_010_empty", with_slide=False)
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="mon"><topic>empty</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides")
        empty = [f for f in result.findings if f.type == "empty_day"]
        assert len(empty) == 1
        assert "zero slide decks" in empty[0].message

    def test_unresolved_topic_does_not_double_report_as_empty_day(self, tmp_path):
        # No topic dir at all → unresolved_topic error, but NOT empty_day
        # (the error already explains the emptiness).
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="mon"><topic>missing</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert "unresolved_topic" in _types(result)
        assert "empty_day" not in _types(result)


class TestUnscheduledTopics:
    def test_bare_topic_mixed_with_subsection_is_info(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_bare")
        _make_topic(tmp_path, "module_100", "topic_020_mon")
        spec = _write_spec(
            tmp_path,
            """\
            <topic>bare</topic>
            <subsection weekday="mon"><topic>mon</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        info = [f for f in result.findings if f.type == "unscheduled_topics"]
        assert len(info) == 1
        assert info[0].severity == "info"
        assert "bare" in info[0].message

    def test_bare_topic_sharing_id_with_subsection_topic_still_reported(self, tmp_path):
        """Identity-keyed detection: a genuinely bare topic whose id also
        appears inside a subsection is still flagged as unscheduled."""
        _make_topic(tmp_path, "module_100", "topic_010_shared")
        _make_topic(tmp_path, "module_100", "topic_020_mon")
        spec = _write_spec(
            tmp_path,
            """\
            <topic>shared</topic>
            <subsection weekday="mon">
                <topic>shared</topic>
                <topic>mon</topic>
            </subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        info = [f for f in result.findings if f.type == "unscheduled_topics"]
        assert len(info) == 1
        assert "shared" in info[0].message

    def test_no_unscheduled_when_all_topics_in_subsections(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_mon")
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="mon"><topic>mon</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert "unscheduled_topics" not in _types(result)

    def test_no_subsection_checks_when_no_subsections(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(tmp_path, "<topic>a</topic>")
        result = validate_spec(spec, tmp_path / "slides")
        assert "unscheduled_topics" not in _types(result)


class TestDisabledSubsectionNotChecked:
    def test_disabled_subsection_excluded_from_checks_by_default(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        # A disabled subsection with a duplicate weekday should be invisible
        # to the duplicate/order checks by default (it is dropped at parse).
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon"><topic>a</topic></subsection>
            <subsection weekday="mon" enabled="false"><topic>a</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert "duplicate_weekday" not in _types(result)


class TestMultipleWeekdays:
    def test_multi_weekday_subsection_counts_each_day(self, tmp_path):
        """A ``weekday="tue,mon"`` subsection is flattened, so the out-of-order
        pair (tue before mon) is still flagged."""
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="tue,mon"><topic>a</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert "weekday_out_of_order" in _types(result)

    def test_duplicate_across_multi_weekday_subsections(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        _make_topic(tmp_path, "module_100", "topic_020_b")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon,tue"><topic>a</topic></subsection>
            <subsection weekday="tue,wed"><topic>b</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides")
        dups = [f for f in result.findings if f.type == "duplicate_weekday"]
        assert len(dups) == 1
        assert "tue" in dups[0].message


class TestWorkdayCoverage:
    def test_missing_workday_off_by_default(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="mon"><topic>a</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert "missing_workday" not in _types(result)

    def test_missing_workday_warns_when_enabled(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="mon"><topic>a</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides", check_workdays=True)
        missing = [f for f in result.findings if f.type == "missing_workday"]
        assert len(missing) == 1
        assert missing[0].severity == "warning"
        for day in ("tue", "wed", "thu", "fri"):
            assert day in missing[0].message

    def test_full_week_passes(self, tmp_path):
        for letter in "abcde":
            _make_topic(tmp_path, "module_100", f"topic_0{ord(letter)}_{letter}")
        spec = _write_spec(
            tmp_path,
            """\
            <subsection weekday="mon"><topic>a</topic></subsection>
            <subsection weekday="tue"><topic>b</topic></subsection>
            <subsection weekday="wed"><topic>c</topic></subsection>
            <subsection weekday="thu"><topic>d</topic></subsection>
            <subsection weekday="fri"><topic>e</topic></subsection>""",
        )
        result = validate_spec(spec, tmp_path / "slides", check_workdays=True)
        assert "missing_workday" not in _types(result)

    def test_one_multi_weekday_subsection_can_cover_the_week(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(
            tmp_path,
            '<subsection weekday="mon,tue,wed,thu,fri"><topic>a</topic></subsection>',
        )
        result = validate_spec(spec, tmp_path / "slides", check_workdays=True)
        assert "missing_workday" not in _types(result)

    def test_thematic_only_section_exempt(self, tmp_path):
        """A section whose subsections carry no weekday (thematic grouping)
        is not held to workday coverage."""
        _make_topic(tmp_path, "module_100", "topic_010_a")
        spec = _write_spec(
            tmp_path,
            "<subsection><name><de>Thema</de><en>Theme</en></name><topic>a</topic></subsection>",
        )
        result = validate_spec(spec, tmp_path / "slides", check_workdays=True)
        assert "missing_workday" not in _types(result)
