"""Tests for ``<tasks>`` spec parsing and step resolution (``clm run``)."""

import io
from pathlib import Path

import pytest

from clm.core.course_spec import CourseSpec
from clm.core.tasks import (
    TaskStepError,
    resolve_step,
    step_argument_usage,
    substitute_placeholders,
)


def _spec(tasks_block: str) -> CourseSpec:
    xml = f"""
    <course>
      <name><de>T</de><en>T</en></name>
      <prog-lang>python</prog-lang>
      <sections>
        <section>
          <name><de>S</de><en>S</en></name>
          <topics><topic>intro</topic></topics>
        </section>
      </sections>
      {tasks_block}
    </course>
    """
    return CourseSpec.from_file(io.StringIO(xml))


PRE_RELEASE = """
<tasks>
  <task name="pre-release" description="Exports, then build">
    <step>calendar generate {spec} --channel jan -f ics -o release/jan.ics</step>
    <step>build {spec}</step>
  </task>
  <task name="check">
    <step>validate {spec}</step>
  </task>
</tasks>
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_absent_tasks_is_empty_list():
    assert _spec("").tasks == []


def test_parses_tasks_in_order_with_steps_and_description():
    tasks = _spec(PRE_RELEASE).tasks
    assert [t.name for t in tasks] == ["pre-release", "check"]

    pre = tasks[0]
    assert pre.description == "Exports, then build"
    assert pre.steps == (
        "calendar generate {spec} --channel jan -f ics -o release/jan.ics",
        "build {spec}",
    )
    assert tasks[1].description == ""
    assert tasks[1].steps == ("validate {spec}",)


def test_task_lookup_by_name():
    spec = _spec(PRE_RELEASE)
    task = spec.task("check")
    assert task is not None
    assert task.steps == ("validate {spec}",)
    assert spec.task("missing") is None


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def test_valid_tasks_produce_no_errors():
    assert _spec(PRE_RELEASE).validate_tasks() == []


def test_duplicate_task_name_is_an_error():
    block = """
    <tasks>
      <task name="a"><step>build {spec}</step></task>
      <task name="a"><step>validate {spec}</step></task>
    </tasks>
    """
    errors = _spec(block).validate_tasks()
    assert any("Duplicate task name: 'a'" in e for e in errors)


def test_missing_task_name_is_an_error():
    errors = _spec("<tasks><task><step>build {spec}</step></task></tasks>").validate_tasks()
    assert any("needs a name attribute" in e for e in errors)


def test_task_without_steps_is_an_error():
    errors = _spec('<tasks><task name="empty"/></tasks>').validate_tasks()
    assert any("no <step> elements" in e for e in errors)


def test_empty_step_is_an_error():
    errors = _spec('<tasks><task name="a"><step>  </step></task></tasks>').validate_tasks()
    assert any("step 1 is empty" in e for e in errors)


def test_step_invoking_clm_run_is_an_error():
    block = """
    <tasks>
      <task name="a"><step>run other {spec}</step></task>
    </tasks>
    """
    errors = _spec(block).validate_tasks()
    assert any("cannot invoke other tasks" in e for e in errors)


def test_spec_validate_includes_task_errors():
    block = '<tasks><task name="empty"/></tasks>'
    assert any("no <step> elements" in e for e in _spec(block).validate())


# ---------------------------------------------------------------------------
# Step resolution
# ---------------------------------------------------------------------------


def test_resolve_step_tokenizes_and_substitutes_spec(tmp_path: Path):
    spec_path = tmp_path / "course.xml"
    tokens = resolve_step("calendar generate {spec} --channel jan", spec_path=spec_path)
    assert tokens == ["calendar", "generate", str(spec_path.resolve()), "--channel", "jan"]


def test_spec_path_with_spaces_and_backslashes_stays_one_token(tmp_path: Path):
    # Substitution happens after tokenization, so a substituted path is never
    # re-parsed by shlex — spaces and (Windows) backslashes survive intact.
    spec_path = tmp_path / "my courses" / "course.xml"
    tokens = resolve_step("build {spec}", spec_path=spec_path)
    assert tokens == ["build", str(spec_path.resolve())]


def test_placeholder_embedded_in_a_larger_token(tmp_path: Path):
    spec_path = tmp_path / "course.xml"
    tokens = resolve_step("build --spec={spec}", spec_path=spec_path)
    assert tokens == ["build", f"--spec={spec_path.resolve()}"]


def test_quoted_argument_with_spaces(tmp_path: Path):
    tokens = resolve_step('export outline {spec} -o "out dir/"', spec_path=tmp_path / "c.xml")
    assert tokens[-1] == "out dir/"


def test_unknown_placeholder_is_an_error(tmp_path: Path):
    with pytest.raises(TaskStepError, match=r"unknown placeholder \{specc\}"):
        resolve_step("build {specc}", spec_path=tmp_path / "c.xml")


def test_empty_step_raises(tmp_path: Path):
    with pytest.raises(TaskStepError, match="empty"):
        resolve_step("   ", spec_path=tmp_path / "c.xml")


def test_unbalanced_quote_raises(tmp_path: Path):
    with pytest.raises(TaskStepError, match="cannot parse step"):
        resolve_step('build "unclosed', spec_path=tmp_path / "c.xml")


def test_escaped_braces_become_literal_braces():
    assert substitute_placeholders("a{{b}}c", {"spec": "X"}) == "a{b}c"
    assert substitute_placeholders("{spec}{{spec}}", {"spec": "X"}) == "X{spec}"


# ---------------------------------------------------------------------------
# Argument placeholders — {args} / {1}, {2}, … (issue #342)
# ---------------------------------------------------------------------------


def test_bare_args_token_expands_to_one_token_per_argument(tmp_path: Path):
    spec_path = tmp_path / "c.xml"
    tokens = resolve_step(
        "release week {spec} {args} --channel materials/2026-04-de",
        spec_path=spec_path,
        args=["name:Week 09", "--push"],
    )
    assert tokens == [
        "release",
        "week",
        str(spec_path.resolve()),
        "name:Week 09",
        "--push",
        "--channel",
        "materials/2026-04-de",
    ]


def test_args_values_are_never_requoted_or_reparsed(tmp_path: Path):
    # An argument containing spaces or quote characters stays one argv token.
    tokens = resolve_step(
        "release week {spec} {args}",
        spec_path=tmp_path / "c.xml",
        args=['name:"Week 09" extra'],
    )
    assert tokens[-1] == 'name:"Week 09" extra'


def test_positional_placeholders_place_individual_arguments(tmp_path: Path):
    tokens = resolve_step(
        "release week {spec} {2} --channel {1}",
        spec_path=tmp_path / "c.xml",
        args=["materials/2026-04-de", "name:Week 09"],
    )
    assert tokens[3:] == ["name:Week 09", "--channel", "materials/2026-04-de"]


def test_positional_placeholder_embedded_in_larger_token(tmp_path: Path):
    tokens = resolve_step(
        "build {spec} --channel=materials/{1}",
        spec_path=tmp_path / "c.xml",
        args=["2026-04"],
    )
    assert tokens[-1] == "--channel=materials/2026-04"


def test_embedded_args_placeholder_is_an_error(tmp_path: Path):
    with pytest.raises(TaskStepError, match=r"\{args\} must be a standalone token"):
        resolve_step("build prefix-{args}", spec_path=tmp_path / "c.xml", args=["x"])


def test_out_of_range_positional_is_an_error(tmp_path: Path):
    with pytest.raises(TaskStepError, match=r"references argument \{2\} but only 1"):
        resolve_step("build {2}", spec_path=tmp_path / "c.xml", args=["only-one"])


def test_validation_mode_accepts_argument_placeholders(tmp_path: Path):
    # args=None (the spec validator's call shape) must accept {args}/{n}
    # references — the actual values only exist at `clm run` time.
    tokens = resolve_step(
        "release week {spec} {args} --channel {1}", spec_path=tmp_path / "c.xml"
    )
    assert tokens[0] == "release"
    assert "<args>" in tokens
    assert "<1>" in tokens


def test_zero_positional_is_unknown(tmp_path: Path):
    with pytest.raises(TaskStepError, match=r"unknown placeholder \{0\}"):
        resolve_step("build {0}", spec_path=tmp_path / "c.xml", args=["x"])


def test_step_argument_usage_reports_args_and_max_positional():
    assert step_argument_usage("build {spec}") == (False, 0)
    assert step_argument_usage("release week {spec} {args}") == (True, 0)
    assert step_argument_usage("release week {1} --channel {3}") == (False, 3)
    assert step_argument_usage("release week {args} {2}") == (True, 2)
    # Escaped braces are literal, not references; unparseable steps report
    # no usage (resolution surfaces the parse error).
    assert step_argument_usage("build {{args}}") == (False, 0)
    assert step_argument_usage('build "unclosed') == (False, 0)
