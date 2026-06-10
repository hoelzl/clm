"""Tests for ``<tasks>`` spec parsing and step resolution (``clm run``)."""

import io
from pathlib import Path

import pytest

from clm.core.course_spec import CourseSpec
from clm.core.tasks import TaskStepError, resolve_step, substitute_placeholders


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
    <step>export calendar {spec} --channel jan -f ics -o release/jan.ics</step>
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
        "export calendar {spec} --channel jan -f ics -o release/jan.ics",
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
    tokens = resolve_step("export calendar {spec} --channel jan", spec_path=spec_path)
    assert tokens == ["export", "calendar", str(spec_path.resolve()), "--channel", "jan"]


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
