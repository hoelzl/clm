"""``clm run`` — execute a named task sequence declared in the course spec.

A ``<tasks>`` block in the spec declares named sequences of clm commands
(see ``clm info spec-files``). The trainer iterates with ``clm build``
(possibly ``--watch``) and, when ready, runs e.g. ``clm run pre-release
course.xml`` to execute every step — calendar/outline exports, the final
build — in order with the correct arguments.

Every step runs as a subprocess (``sys.executable -m clm …``) so it behaves
exactly as if typed at the prompt, in the same venv as the parent, with no
global state shared between steps. All steps are resolved and validated
against the Click command tree *before* the first one runs, so a typo in
step 3 fails fast instead of after a long build in step 1.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import click

from clm.core.course_spec import CourseSpec, CourseSpecError, TaskSpec
from clm.core.tasks import TaskStepError, resolve_step


def unknown_cli_command_error(tokens: list[str]) -> str | None:
    """Check that *tokens* starts with a real clm (sub)command.

    Walks the Click command tree group by group until a terminal command is
    reached; the remaining tokens are that command's arguments and are not
    validated here (only the command itself can parse them). Returns an
    error message, or None when the command resolves.
    """
    # Imported lazily: this module is itself part of the CLI, and the spec
    # validator calls this function from outside the command tree.
    from clm.cli.main import cli

    current: click.Command = cli
    path = "clm"
    i = 0
    while isinstance(current, click.Group):
        if i >= len(tokens) or tokens[i].startswith("-"):
            subcommands = ", ".join(sorted(current.commands))
            return f"'{path}' needs a subcommand (one of: {subcommands})"
        name = tokens[i]
        subcommand = current.commands.get(name)
        if subcommand is None:
            subcommands = ", ".join(sorted(current.commands))
            return f"'{path} {name}' is not a clm command (available: {subcommands})"
        current = subcommand
        path = f"{path} {name}"
        i += 1
    return None


def _resolve_task_steps(task: TaskSpec, spec_file: Path) -> list[list[str]]:
    """Resolve and validate every step of *task*; raise on the first problem."""
    resolved: list[list[str]] = []
    for i, step in enumerate(task.steps, start=1):
        try:
            tokens = resolve_step(step, spec_path=spec_file)
        except TaskStepError as e:
            raise click.ClickException(f"Task '{task.name}', step {i}: {e}") from None
        error = unknown_cli_command_error(tokens)
        if error:
            raise click.ClickException(f"Task '{task.name}', step {i}: {error}")
        resolved.append(tokens)
    return resolved


def _load_spec(spec_file: Path) -> CourseSpec:
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(str(e)) from None
    errors = spec.validate_tasks()
    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise click.ClickException(f"Invalid <tasks> block in {spec_file}:\n{details}")
    return spec


def _list_tasks(spec: CourseSpec, spec_file: Path) -> None:
    if not spec.tasks:
        click.echo(f"No <tasks> defined in {spec_file}.")
        return
    click.echo(f"Tasks in {spec_file}:")
    for task in spec.tasks:
        n = len(task.steps)
        suffix = f" — {task.description}" if task.description else ""
        click.echo(f"  {task.name}{suffix} ({n} step{'s' if n != 1 else ''})")
        for step in task.steps:
            click.echo(f"    clm {step}")


@click.command("run")
@click.argument("task_name", metavar="[TASK]", required=False)
@click.argument(
    "spec_file",
    metavar="[SPEC_FILE]",
    required=False,
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--list",
    "list_tasks",
    is_flag=True,
    help="List the spec's tasks instead of running one (same as omitting TASK).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the fully resolved commands without executing anything.",
)
def run_cmd(task_name: str | None, spec_file: Path | None, list_tasks: bool, dry_run: bool) -> None:
    """Run a named task sequence declared in the course spec.

    Tasks are sequences of clm commands declared in a <tasks> block of the
    spec file (see `clm info spec-files`). Steps run in order; the first
    failing step aborts the task and its exit code becomes clm's exit code.

    \b
    Examples:
        clm run pre-release course.xml            # run task 'pre-release'
        clm run course.xml                        # list available tasks
        clm run pre-release course.xml --dry-run  # show resolved commands
    """
    # `clm run course.xml` — a single argument naming an existing file is the
    # spec, and listing is implied.
    if spec_file is None and task_name is not None and Path(task_name).is_file():
        spec_file = Path(task_name)
        task_name = None
    if spec_file is None:
        raise click.UsageError(
            "Missing spec file. Usage: clm run TASK SPEC_FILE (or clm run SPEC_FILE to list tasks)."
        )
    if not spec_file.is_file():
        raise click.UsageError(f"Spec file not found: {spec_file}")

    spec = _load_spec(spec_file)

    if task_name is None or list_tasks:
        _list_tasks(spec, spec_file)
        return

    task = spec.task(task_name)
    if task is None:
        available = ", ".join(t.name for t in spec.tasks) or "none defined"
        raise click.ClickException(
            f"No task named {task_name!r} in {spec_file} (available: {available})."
        )

    resolved = _resolve_task_steps(task, spec_file)

    total = len(resolved)
    for i, tokens in enumerate(resolved, start=1):
        command_line = f"clm {shlex.join(tokens)}"
        click.echo(f"[{i}/{total}] {command_line}")
        if dry_run:
            continue
        returncode = subprocess.run([sys.executable, "-m", "clm", *tokens]).returncode
        if returncode != 0:
            click.echo(
                f"Task '{task.name}' aborted: step {i}/{total} failed with "
                f"exit code {returncode} ({command_line}).",
                err=True,
            )
            raise SystemExit(returncode)
