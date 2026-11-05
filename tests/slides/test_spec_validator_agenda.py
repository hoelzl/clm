"""Validator tests for issue #1163: unknown spec elements and the agenda rules."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from clm.slides.spec_validator import validate_spec


def _write(tmp_path: Path, body: str) -> Path:
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(dedent(body), encoding="utf-8")
    (tmp_path / "slides").mkdir(exist_ok=True)
    return spec_file


def _course(sections: str, *, course_extra: str = "") -> str:
    return f"""\
    <course>
      <name><de>Test</de><en>Test</en></name>
      <prog-lang>python</prog-lang>
      <description><de></de><en></en></description>
      <certificate><de></de><en></en></certificate>
      {course_extra}
      <sections>{sections}</sections>
    </course>
    """


def _make_topic(tmp_path: Path, topic: str) -> None:
    topic_dir = tmp_path / "slides" / "module_100" / f"topic_010_{topic}"
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hi\n", encoding="utf-8")


def _of(result, type_: str):
    return [f for f in result.findings if f.type == type_]


# ---------------------------------------------------------------------------
# Unknown elements (always on)
# ---------------------------------------------------------------------------
class TestUnknownElements:
    def test_typo_subsection_is_flagged_with_its_path(self, tmp_path):
        """Regression test for #1163: <subsecton> silently dropped its topics."""
        _make_topic(tmp_path, "a")
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <topic>a</topic>
                  <subsecton weekday="mon"><topic>lost</topic></subsecton>
                </topics></section>"""
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "unknown_element")
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "warning"
        assert "<subsecton>" in f.message
        assert "course/sections/section/topics/subsecton" in f.message
        assert f.details["path"] == "course/sections/section/topics/subsecton"
        assert f.section == "W1"

    def test_nested_children_of_an_unknown_element_are_not_reported_again(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <wrapper><topic>x</topic><other/></wrapper>
                </topics></section>"""
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "unknown_element")
        assert len(findings) == 1
        assert "<wrapper>" in findings[0].message

    def test_misplaced_known_element_is_flagged(self, tmp_path):
        """<topic> directly under <section> (outside <topics>) is ignored by the parser."""
        _make_topic(tmp_path, "a")
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topic>a</topic>
                  <topics><topic>a</topic></topics></section>"""
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "unknown_element")
        assert [f.details["path"] for f in findings] == ["course/sections/section/topic"]

    def test_index_disambiguates_repeated_siblings(self, tmp_path):
        _make_topic(tmp_path, "a")
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics><topic>a</topic></topics></section>
                <section><name>W2</name><topics><topic>a</topic><typo/></topics></section>"""
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "unknown_element")
        assert [f.details["path"] for f in findings] == ["course/sections/section[2]/topics/typo"]
        assert findings[0].section == "W2"

    def test_known_elements_everywhere_produce_no_finding(self, tmp_path):
        _make_topic(tmp_path, "a")
        spec = _write(
            tmp_path,
            _course(
                """<section id="s1" module="module_100">
                  <name><de>W1</de><en>W1</en></name>
                  <agenda><subtitle><de>s</de></subtitle><outcome>o</outcome></agenda>
                  <topics>
                    <subsection weekday="mon">
                      <name><de>Mo</de><en>Mon</en></name>
                      <block units="8" form="theory">
                        <name><de>B</de></name><contents><de>c</de><en>c</en></contents>
                        <topic>a</topic>
                      </block>
                      <activity kind="x"><de>A</de><en>A</en></activity>
                    </subsection>
                  </topics>
                </section>""",
                course_extra="""
                  <project-slug>t</project-slug>
                  <author>Me</author>
                  <organization><de>O</de><en>O</en></organization>
                  <sidecar-layout>subdir</sidecar-layout>
                  <kernel-python>.venv</kernel-python>
                  <agenda-targets units-per-day="8"/>
                  <github><repository-base>https://x</repository-base>
                    <include-speaker>true</include-speaker></github>
                  <image-options><format>svg</format><inline>false</inline></image-options>
                  <dir-groups><dir-group><name>Code</name><path>code</path>
                    <subdirs><subdir>a</subdir></subdirs></dir-group></dir-groups>
                  <output-targets><output-target name="t"><path>out</path>
                    <kinds><kind>completed</kind></kinds><formats><format>html</format></formats>
                    <languages><language>de</language></languages>
                  </output-target></output-targets>
                  <tasks><task name="x"><step>info commands</step></task></tasks>
                """,
            ),
        )
        assert _of(validate_spec(spec, tmp_path / "slides"), "unknown_element") == []


# ---------------------------------------------------------------------------
# Agenda rules (only when the spec uses <block>)
# ---------------------------------------------------------------------------
_TARGETS = '<agenda-targets units-per-day="8" days-per-week="2" units-total="32"/>'


class TestAgendaRules:
    def test_no_blocks_means_no_agenda_findings(self, tmp_path):
        _make_topic(tmp_path, "a")
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon"><topic>a</topic></subsection>
                </topics></section>""",
                course_extra=_TARGETS,
            ),
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert not [f for f in result.findings if f.type.startswith("agenda_")]

    def test_matching_targets_are_clean(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon"><block units="4"/><block units="4"/></subsection>
                  <subsection weekday="tue"><block units="8"/></subsection>
                </topics></section>
                <section><name>W2</name><topics>
                  <subsection weekday="mon,tue"><block units="16"/></subsection>
                </topics></section>""",
                course_extra=_TARGETS,
            ),
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert not [f for f in result.findings if f.type.startswith("agenda_")]
        # A day carrying only deck-less blocks is planned, not empty.
        assert _of(result, "empty_day") == []

    def test_day_week_and_total_mismatches_are_warnings(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon"><block units="6"/></subsection>
                  <subsection weekday="tue"><block units="8"/></subsection>
                </topics></section>""",
                course_extra=_TARGETS,
            ),
        )
        result = validate_spec(spec, tmp_path / "slides")
        day = _of(result, "agenda_day_units")
        week = _of(result, "agenda_week_units")
        total = _of(result, "agenda_total_units")
        assert [f.severity for f in day + week + total] == ["warning"] * 3
        assert "6" in day[0].message and "8" in day[0].message
        assert "14" in week[0].message and "16" in week[0].message
        assert "14" in total[0].message and "32" in total[0].message

    def test_multi_day_subsection_target_scales_with_span(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon,tue"><block units="8"/></subsection>
                </topics></section>""",
                course_extra='<agenda-targets units-per-day="8"/>',
            ),
        )
        day = _of(validate_spec(spec, tmp_path / "slides"), "agenda_day_units")
        assert len(day) == 1
        assert "16" in day[0].message

    @pytest.mark.parametrize(
        ("targets", "expected"),
        [
            ("", set()),
            ('<agenda-targets units-per-day="8"/>', {"agenda_day_units"}),
            ('<agenda-targets days-per-week="5"/>', set()),
            (
                '<agenda-targets units-per-day="8" days-per-week="5"/>',
                {"agenda_day_units", "agenda_week_units"},
            ),
            ('<agenda-targets units-total="480"/>', {"agenda_total_units"}),
        ],
    )
    def test_each_target_checked_only_when_set(self, tmp_path, targets, expected):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon"><block units="3"/></subsection>
                </topics></section>""",
                course_extra=targets,
            ),
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert {f.type for f in result.findings if f.type.startswith("agenda_")} == expected

    def test_block_without_units(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon">
                    <block units="8"><name>Ok</name></block>
                    <block form="practice"><name><de>Übung</de><en>Exercise</en></name></block>
                  </subsection>
                </topics></section>"""
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "agenda_block_without_units")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "Exercise" in findings[0].message

    def test_bare_topic_in_subsection_with_blocks(self, tmp_path):
        _make_topic(tmp_path, "a")
        _make_topic(tmp_path, "b")
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon">
                    <topic>a</topic>
                    <block units="8"><topic>b</topic></block>
                  </subsection>
                  <subsection weekday="tue"><topic>a</topic></subsection>
                </topics></section>"""
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "agenda_unblocked_topic")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert findings[0].topic_id == "a"

    def test_week_of_topic_less_blocks_is_not_an_empty_section(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon"><block units="8" status="planned"/></subsection>
                </topics></section>"""
            ),
        )
        result = validate_spec(spec, tmp_path / "slides")
        assert _of(result, "empty_section") == []
        assert _of(result, "empty_day") == []

    def test_unknown_block_attribute_is_flagged(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon">
                    <block units="4" enabled="false"/>
                    <block ue="4"/>
                  </subsection>
                </topics></section>""",
                course_extra='<agenda-targets units-per-day="8" per-week="40"/>',
            ),
        )
        findings = _of(validate_spec(spec, tmp_path / "slides"), "unknown_attribute")
        assert sorted(f.details["attribute"] for f in findings) == ["enabled", "per-week", "ue"]
        assert all(f.severity == "warning" for f in findings)
        ue = next(f for f in findings if f.details["attribute"] == "ue")
        assert '"units"' in ue.message
        assert ue.details["path"] == "course/sections/section/topics/subsection/block[2]"

    def test_optional_content_does_not_count_towards_targets(self, tmp_path):
        spec = _write(
            tmp_path,
            _course(
                """<section><name>W1</name><topics>
                  <subsection weekday="mon"><block units="8"/></subsection>
                  <subsection weekday="tue" optional="true"><block units="8"/></subsection>
                </topics></section>""",
                course_extra='<agenda-targets units-total="8"/>',
            ),
        )
        assert _of(validate_spec(spec, tmp_path / "slides"), "agenda_total_units") == []
