"""Unified ``clm validate <path>`` command (Phase 0 of slide-format-redesign).

Inspects the given path and dispatches to either spec validation
(``.xml`` files) or slide validation (``.py`` files or directories).
Override with ``--kind=slides|spec`` for ambiguous cases (e.g. an empty
directory, or an ``.xml`` file you nonetheless want to feed to the
slide validator).

The two underlying commands (``validate-slides``, ``validate-spec``)
remain available as deprecated aliases — this command consolidates
them so 95% of users no longer have to remember which to use.
"""

from __future__ import annotations

from pathlib import Path

import click

from clm.cli.commands.validate_slides import validate_slides_cmd
from clm.cli.commands.validate_spec import validate_spec_cmd


def _infer_kind(path: Path) -> str | None:
    """Return ``"spec"``, ``"slides"``, or ``None`` if ambiguous."""
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".xml":
            return "spec"
        if suffix == ".py":
            return "slides"
        return None
    if path.is_dir():
        # Directories are slide directories. An "empty directory of spec
        # files" isn't a real shape — directories aren't passed to the
        # spec validator. If you want to validate a spec, pass the .xml.
        return "slides"
    return None


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
        "Default: all deterministic checks. Not valid with --kind=spec."
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
) -> None:
    """Validate a course spec file or slide files.

    \b
    Argument dispatch:
        clm validate course.xml             # → spec validation
        clm validate slides/                # → slide validation
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
        if checks or quick:
            raise click.UsageError(
                "--checks and --quick are slides-only; not valid with --kind=spec."
            )
        if not path.is_file() or path.suffix.lower() != ".xml":
            # Spec validator expects a single XML file; refuse anything
            # else explicitly so the error doesn't come from deeper code.
            raise click.UsageError(f"--kind=spec requires an .xml file, got {path}.")
        ctx.invoke(
            validate_spec_cmd,
            spec_file=path,
            data_dir=data_dir,
            as_json=as_json,
            include_disabled=include_disabled,
        )
    else:  # slides
        if include_disabled:
            raise click.UsageError("--include-disabled is spec-only; not valid with --kind=slides.")
        ctx.invoke(
            validate_slides_cmd,
            path=path,
            checks=checks,
            quick=quick,
            as_json=as_json,
            data_dir=data_dir,
        )
