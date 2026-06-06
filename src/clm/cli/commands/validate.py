"""Unified ``clm validate <path>`` command (Phase 0 of slide-format-redesign).

Inspects the given path and dispatches to either spec validation
(``.xml`` files) or slide validation (``.py`` files or directories).
Override with ``--kind=slides|spec`` for ambiguous cases (e.g. an empty
directory, or an ``.xml`` file you nonetheless want to feed to the
slide validator).

Beyond the basic dispatch, three flags close the "structure-OK ≠ decks-clean"
gap (course-conversion tooling gap #2):

- ``--deep`` (spec): after structure validation, run the full slide validator
  on every deck the spec pulls in (its shipping set) and report both.
- ``--summary``: roll findings up into a category/kind histogram with per-deck
  counts instead of a flat list of thousands of lines.
- ``--shipping-only`` (directory): restrict a directory walk to the decks
  reachable from course specs, so archived / unreferenced decks don't drown the
  signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.cli.commands.validate_slides import (
    _parse_checks,
    _print_human_readable,
    _raise_on_findings,
    validate_slides_cmd,
)
from clm.cli.commands.validate_slides import (
    _result_to_dict as _slides_result_to_dict,
)
from clm.cli.commands.validate_spec import (
    _result_to_dict as _spec_result_to_dict,
)
from clm.cli.commands.validate_spec import (
    validate_spec_cmd,
)


def _infer_kind(path: Path) -> str | None:
    """Return ``"spec"``, ``"slides"``, or ``None`` if ambiguous."""
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".xml":
            return "spec"
        # Any slide-source extension (#- and //-family), not just .py.
        if suffix in (".py", ".cs", ".cpp", ".cxx", ".cc", ".java", ".ts", ".rs"):
            return "slides"
        return None
    if path.is_dir():
        # Directories are slide directories. An "empty directory of spec
        # files" isn't a real shape — directories aren't passed to the
        # spec validator. If you want to validate a spec, pass the .xml.
        return "slides"
    return None


def _slides_dir_for_spec(data_dir: Path | None, spec_file: Path) -> Path:
    if data_dir:
        return data_dir / "slides"
    return spec_file.parent.parent / "slides"


@click.command("validate")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--kind",
    type=click.Choice(["slides", "spec"], case_sensitive=False),
    default=None,
    help=("Force a specific validator. Default inference: .xml → spec, .py / directory → slides."),
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=("Course data directory (contains slides/). Passed through to both validators."),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
@click.option(
    "--checks",
    type=str,
    default=None,
    help=(
        "Slides-only: comma-separated list of checks. "
        "Deterministic: format, pairing, tags. "
        "Review: code_quality, voiceover, completeness. "
        "Default: all deterministic checks. Not valid with --kind=spec unless --deep."
    ),
)
@click.option(
    "--quick",
    is_flag=True,
    help=(
        "Slides-only: fast syntax-only check (format + tags). "
        "For PostToolUse hooks. Not valid with --kind=spec."
    ),
)
@click.option(
    "--include-disabled",
    is_flag=True,
    help=('Spec-only: validate sections marked enabled="false". Not valid with --kind=slides.'),
)
@click.option(
    "--fail-on",
    type=click.Choice(["error", "warning"], case_sensitive=False),
    default=None,
    help=(
        "Slides-only: exit non-zero when findings reach this severity. "
        "'warning' makes the cross-file slide_id / voiceover for_slide parity "
        "warnings fail a pre-commit gate. Not valid with --kind=spec unless --deep."
    ),
)
@click.option(
    "--deep",
    is_flag=True,
    help=(
        "Spec-only: after structure validation, run the full slide validator on "
        "every deck the spec pulls in (its shipping set). 'Spec validates OK' does "
        "not mean the decks are clean — this checks both."
    ),
)
@click.option(
    "--summary",
    is_flag=True,
    help=(
        "Roll findings up into a category/kind histogram with per-deck counts "
        "instead of a flat list. Works with --deep and with slides/directory "
        "validation. On a bare spec it implies --deep."
    ),
)
@click.option(
    "--shipping-only",
    is_flag=True,
    help=(
        "Slides directory only: restrict the walk to decks reachable from course "
        "specs (the shipping set), skipping archived / unreferenced decks."
    ),
)
@click.option(
    "--specs-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="For --shipping-only: directory of *.xml specs. Default: <course-root>/course-specs/.",
)
@click.pass_context
def validate_cmd(
    ctx: click.Context,
    path: Path,
    kind: str | None,
    data_dir: Path | None,
    as_json: bool,
    checks: str | None,
    quick: bool,
    include_disabled: bool,
    fail_on: str | None,
    deep: bool,
    summary: bool,
    shipping_only: bool,
    specs_dir: Path | None,
) -> None:
    """Validate a course spec file or slide files.

    \b
    Argument dispatch:
        clm validate course.xml             # → spec validation (structure)
        clm validate course.xml --deep      # → structure + every referenced deck
        clm validate course.xml --summary   # → deep, rolled up into a histogram
        clm validate slides/                # → slide validation
        clm validate slides/ --shipping-only --summary   # only decks that ship
        clm validate slides/x.py            # → slide validation
        clm validate something --kind=spec  # → forced spec validation
    """
    resolved_kind = (kind or "").lower() or _infer_kind(path)
    if resolved_kind is None:
        raise click.UsageError(
            f"Cannot infer validator kind from {path}. "
            "Pass --kind=slides or --kind=spec explicitly."
        )

    if resolved_kind == "spec":
        _validate_spec_path(
            ctx,
            path,
            data_dir=data_dir,
            as_json=as_json,
            checks=checks,
            quick=quick,
            include_disabled=include_disabled,
            fail_on=fail_on,
            deep=deep,
            summary=summary,
            shipping_only=shipping_only,
        )
    else:  # slides
        _validate_slides_path(
            ctx,
            path,
            data_dir=data_dir,
            as_json=as_json,
            checks=checks,
            quick=quick,
            include_disabled=include_disabled,
            fail_on=fail_on,
            deep=deep,
            summary=summary,
            shipping_only=shipping_only,
            specs_dir=specs_dir,
        )


def _validate_spec_path(
    ctx: click.Context,
    path: Path,
    *,
    data_dir: Path | None,
    as_json: bool,
    checks: str | None,
    quick: bool,
    include_disabled: bool,
    fail_on: str | None,
    deep: bool,
    summary: bool,
    shipping_only: bool,
) -> None:
    if quick:
        raise click.UsageError("--quick is slides-only; not valid with a spec.")
    if shipping_only:
        raise click.UsageError(
            "--shipping-only applies to a slides directory; a spec already scopes "
            "to its shipping set (use --deep)."
        )
    if not path.is_file() or path.suffix.lower() != ".xml":
        raise click.UsageError(f"--kind=spec requires an .xml file, got {path}.")

    # --summary on a spec is about the decks, so it implies a deep run.
    deep = deep or summary
    if (checks or fail_on) and not deep:
        raise click.UsageError(
            "--checks and --fail-on are slides-only; with a spec they require --deep."
        )

    if not deep:
        # Unchanged structure-only behavior.
        ctx.invoke(
            validate_spec_cmd,
            spec_file=path,
            data_dir=data_dir,
            as_json=as_json,
            include_disabled=include_disabled,
        )
        return

    _run_deep_spec(
        path,
        data_dir=data_dir,
        as_json=as_json,
        checks=checks,
        include_disabled=include_disabled,
        fail_on=fail_on,
        summary=summary,
    )


def _run_deep_spec(
    spec_file: Path,
    *,
    data_dir: Path | None,
    as_json: bool,
    checks: str | None,
    include_disabled: bool,
    fail_on: str | None,
    summary: bool,
) -> None:
    """Validate a spec's structure AND the content of every deck it pulls in."""
    from clm.core.course_spec import CourseSpecError
    from clm.slides.spec_validator import validate_spec
    from clm.slides.validator import validate_course

    slides_dir = _slides_dir_for_spec(data_dir, spec_file)
    check_list = _parse_checks(checks)

    try:
        spec_result = validate_spec(spec_file, slides_dir, include_disabled=include_disabled)
    except CourseSpecError as e:
        raise click.ClickException(str(e)) from None

    slides_result = validate_course(spec_file, slides_dir, checks=check_list)

    if as_json:
        payload: dict = {
            "kind": "deep",
            "spec": _spec_result_to_dict(spec_result),
            "slides": _slides_result_to_dict(slides_result),
        }
        if summary:
            from clm.slides.validation_summary import summarize_findings

            payload["summary"] = summarize_findings(slides_result.findings).to_dict()
        click.echo(json.dumps(payload, indent=2))
    else:
        _print_spec_findings(spec_result)
        click.echo()
        if summary:
            _print_summary(slides_result)
        else:
            _print_human_readable(slides_result)

    spec_has_errors = any(f.severity == "error" for f in spec_result.findings)
    if spec_has_errors:
        raise SystemExit(1)
    _raise_on_findings(slides_result.findings, fail_on, as_json)


def _validate_slides_path(
    ctx: click.Context,
    path: Path,
    *,
    data_dir: Path | None,
    as_json: bool,
    checks: str | None,
    quick: bool,
    include_disabled: bool,
    fail_on: str | None,
    deep: bool,
    summary: bool,
    shipping_only: bool,
    specs_dir: Path | None,
) -> None:
    if include_disabled:
        raise click.UsageError("--include-disabled is spec-only; not valid with --kind=slides.")
    if deep:
        raise click.UsageError("--deep applies to a spec, not a slides path.")

    if not (summary or shipping_only):
        # Unchanged slide-validation behavior.
        ctx.invoke(
            validate_slides_cmd,
            path=path,
            checks=checks,
            quick=quick,
            as_json=as_json,
            data_dir=data_dir,
            fail_on=fail_on,
        )
        return

    if quick:
        raise click.UsageError("--quick is not compatible with --summary / --shipping-only.")

    result = _run_slides(path, checks, shipping_only, specs_dir)

    if as_json:
        payload = _slides_result_to_dict(result)
        if summary:
            from clm.slides.validation_summary import summarize_findings

            payload["summary"] = summarize_findings(result.findings).to_dict()
        click.echo(json.dumps(payload, indent=2))
    elif summary:
        _print_summary(result)
    else:
        _print_human_readable(result)

    _raise_on_findings(result.findings, fail_on, as_json)


def _run_slides(
    path: Path,
    checks: str | None,
    shipping_only: bool,
    specs_dir: Path | None,
):
    """Run slide validation, optionally scoped to the shipping set."""
    from clm.slides.validator import (
        validate_directory,
        validate_file,
        validate_files,
    )

    check_list = _parse_checks(checks)

    if not shipping_only:
        if path.is_dir():
            return validate_directory(path, checks=check_list)
        return validate_file(path, checks=check_list)

    if not path.is_dir():
        raise click.UsageError("--shipping-only requires a directory path.")

    course_root = _course_root_for_slides_path(path)
    resolved_specs_dir = specs_dir or (course_root / "course-specs")
    if not resolved_specs_dir.is_dir():
        raise click.ClickException(
            f"Specs directory not found: {resolved_specs_dir}. Pass --specs-dir explicitly."
        )
    spec_files = sorted(resolved_specs_dir.glob("*.xml"))
    if not spec_files:
        raise click.ClickException(f"No *.xml specs found in {resolved_specs_dir}.")

    from clm.core.spec_decks import shipping_set

    slides_dir = course_root / "slides"
    ship = shipping_set(spec_files, slides_dir)
    # The shipping set is already the resolved deck list (extension-aware via
    # find_slide_files), so filter it to the decks under `path` directly rather
    # than walking — validate_directory's deep walk is .py-only and would miss
    # .cs / .cpp decks.
    base = path.resolve()
    kept = sorted(d for d in ship if d == base or base in d.parents)
    return validate_files(kept, checks=check_list)


def _course_root_for_slides_path(path: Path) -> Path:
    """Infer the course root from a slides path (``…/slides/…`` or the root)."""
    resolved = path.resolve()
    if resolved.name == "slides":
        return resolved.parent
    for parent in resolved.parents:
        if parent.name == "slides":
            return parent.parent
    raise click.ClickException(
        "Could not infer the course root from the path (no 'slides/' ancestor). "
        "Pass --specs-dir explicitly."
    )


def _print_spec_findings(spec_result) -> None:
    """Print spec-structure findings (mirrors the validate-spec human output)."""
    if not spec_result.findings:
        click.echo(f"Spec structure: OK — {spec_result.topics_total} topics, no issues.")
        return
    click.echo("Spec structure:")
    for f in spec_result.findings:
        icon = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(f.severity, "???")
        click.echo(f"  [{icon}] {f.message}")
        if f.suggestion:
            click.echo(f"         {f.suggestion}")
    errors = sum(1 for f in spec_result.findings if f.severity == "error")
    warnings = sum(1 for f in spec_result.findings if f.severity == "warning")
    click.echo(
        f"  {spec_result.topics_total} topics checked: {errors} error(s), {warnings} warning(s)."
    )


def _print_summary(result) -> None:
    """Print the rolled-up deck-content summary."""
    from clm.slides.validation_summary import render_summary, summarize_findings

    click.echo(f"Deck content ({result.files_checked} deck(s) checked):")
    for line in render_summary(summarize_findings(result.findings)):
        click.echo(line)
