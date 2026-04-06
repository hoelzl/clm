"""Validate slide files for format, tag, and pairing correctness."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.validator import (
    ALL_CHECKS,
    ALL_DETERMINISTIC_CHECKS,
    ValidationResult,
    validate_course,
    validate_directory,
    validate_file,
    validate_quick,
)


@click.command("validate-slides")
@click.argument(
    "path",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--checks",
    type=str,
    default=None,
    help=(
        "Comma-separated list of checks to run. "
        "Deterministic: format, pairing, tags. "
        "Review: code_quality, voiceover, completeness. "
        "Default: all deterministic checks."
    ),
)
@click.option(
    "--quick",
    is_flag=True,
    help="Fast syntax-only check (format + tags). For PostToolUse hooks.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). For course spec validation.",
)
def validate_slides_cmd(
    path: Path,
    checks: str | None,
    quick: bool,
    as_json: bool,
    data_dir: Path | None,
):
    """Validate slide files for format, tag, and pairing correctness.

    PATH can be a single .py slide file, a topic directory, or a course
    spec XML file (validates all slides in the course).

    \b
    Examples:
        clm validate-slides slides/module_100/topic_010/slides_intro.py
        clm validate-slides slides/module_100/topic_010/
        clm validate-slides slides/module_100/topic_010/ --checks format,tags
        clm validate-slides course-specs/python-basics.xml
        clm validate-slides slides/topic/slides_intro.py --quick
        clm validate-slides slides/topic/slides_intro.py --json
    """
    if quick:
        if not path.is_file():
            raise click.ClickException("--quick requires a single file path")
        result = validate_quick(path)
    else:
        check_list = _parse_checks(checks)
        result = _dispatch_validation(path, check_list, data_dir)

    if as_json:
        click.echo(json.dumps(_result_to_dict(result), indent=2))
        return

    _print_human_readable(result)

    errors = [f for f in result.findings if f.severity == "error"]
    if errors:
        raise SystemExit(1)


def _parse_checks(checks_str: str | None) -> list[str] | None:
    """Parse the --checks option into a list, or None for defaults."""
    if checks_str is None:
        # Default: deterministic checks only (CLI doesn't do LLM review)
        return list(ALL_DETERMINISTIC_CHECKS)
    parsed = [c.strip() for c in checks_str.split(",") if c.strip()]
    invalid = set(parsed) - ALL_CHECKS
    if invalid:
        raise click.ClickException(
            f"Unknown check(s): {', '.join(sorted(invalid))}. "
            f"Valid: {', '.join(sorted(ALL_CHECKS))}"
        )
    return parsed


def _dispatch_validation(
    path: Path,
    check_list: list[str] | None,
    data_dir: Path | None,
) -> ValidationResult:
    """Dispatch to the right validate_* function based on path type."""
    if path.is_file() and path.suffix in (".xml",):
        # Course spec file
        slides_dir = _resolve_slides_dir(data_dir, path)
        return validate_course(path, slides_dir, checks=check_list)
    elif path.is_dir():
        return validate_directory(path, checks=check_list)
    elif path.is_file():
        return validate_file(path, checks=check_list)
    else:
        raise click.ClickException(f"Path is not a file or directory: {path}")


def _resolve_slides_dir(data_dir: Path | None, spec_file: Path) -> Path:
    """Determine the slides/ directory."""
    if data_dir:
        return data_dir / "slides"
    return spec_file.parent.parent / "slides"


def _print_human_readable(result: ValidationResult) -> None:
    """Print validation results in human-readable format."""
    if not result.findings:
        click.echo(f"OK — {result.summary}.")
        return

    for f in result.findings:
        icon = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(f.severity, "???")
        location = f"{f.file}:{f.line}" if f.file and f.line else f.file or ""
        click.echo(f"[{icon}] {location}: {f.message}")
        if f.suggestion:
            click.echo(f"       {f.suggestion}")

    click.echo()
    click.echo(result.summary + ".")


def _result_to_dict(result: ValidationResult) -> dict:
    """Convert a ValidationResult to a JSON-serializable dict."""
    d: dict = {
        "files_checked": result.files_checked,
        "summary": result.summary,
        "findings": [
            {
                k: v
                for k, v in {
                    "severity": f.severity,
                    "category": f.category,
                    "file": f.file,
                    "line": f.line,
                    "message": f.message,
                    "suggestion": f.suggestion or None,
                }.items()
                if v is not None
            }
            for f in result.findings
        ],
    }
    if result.review_material is not None:
        rm = result.review_material
        review: dict = {}
        if rm.code_quality is not None:
            review["code_quality"] = rm.code_quality
        if rm.voiceover_gaps is not None:
            review["voiceover_gaps"] = rm.voiceover_gaps
        if rm.completeness is not None:
            review["completeness"] = rm.completeness
        if review:
            d["review_material"] = review
    return d
