"""Normalize slide files by applying mechanical fixes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides.normalizer import (
    ALL_OPERATIONS,
    NormalizationResult,
    normalize_course,
    normalize_directory,
    normalize_file,
)


@click.command("normalize-slides")
@click.argument(
    "path",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--operations",
    type=str,
    default=None,
    help=(
        "Comma-separated list of operations: "
        + ", ".join(sorted(ALL_OPERATIONS))
        + ", all. Default: all."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without modifying files.",
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
    help="Course data directory (contains slides/). For course spec normalization.",
)
def normalize_slides_cmd(
    path: Path,
    operations: str | None,
    dry_run: bool,
    as_json: bool,
    data_dir: Path | None,
):
    """Normalize slide files by applying mechanical fixes.

    PATH can be a single .py slide file, a topic directory, or a course
    spec XML file (normalizes all slides in the course).

    \b
    Operations:
        tag_migration   Rename alt->completed after start cells
        workshop_tags   Add workshop tag to workshop heading cells
        interleaving    Normalize DE/EN cell ordering
        slide_ids       Auto-generate slide_id metadata for cells
        all             All of the above (default)

    \b
    Examples:
        clm normalize-slides slides/module_100/topic_010/ --dry-run
        clm normalize-slides slides/module_100/topic_010/
        clm normalize-slides slides/ --operations tag_migration
        clm normalize-slides course-specs/python-basics.xml
    """
    op_list = _parse_operations(operations)
    result = _dispatch(path, op_list, dry_run, data_dir)

    if as_json:
        click.echo(json.dumps(_result_to_dict(result), indent=2))
    else:
        _print_human_readable(result, dry_run)

    # Exit codes: 0=clean/applied, 1=partial (review items), 2=blocked
    if result.review_items and not result.changes:
        sys.exit(2)
    elif result.review_items:
        sys.exit(1)


def _parse_operations(ops_str: str | None) -> list[str] | None:
    """Parse --operations option. Returns None for 'all'."""
    if ops_str is None:
        return None
    parsed = [o.strip() for o in ops_str.split(",") if o.strip()]
    if "all" in parsed:
        return None
    invalid = set(parsed) - ALL_OPERATIONS
    if invalid:
        raise click.ClickException(
            f"Unknown operation(s): {', '.join(sorted(invalid))}. "
            f"Valid: {', '.join(sorted(ALL_OPERATIONS))}, all"
        )
    return parsed


def _dispatch(
    path: Path,
    operations: list[str] | None,
    dry_run: bool,
    data_dir: Path | None,
) -> NormalizationResult:
    if path.is_file() and path.suffix == ".xml":
        slides_dir = _resolve_slides_dir(data_dir, path)
        return normalize_course(path, slides_dir, operations=operations, dry_run=dry_run)
    elif path.is_dir():
        return normalize_directory(path, operations=operations, dry_run=dry_run)
    elif path.is_file():
        return normalize_file(path, operations=operations, dry_run=dry_run)
    else:
        raise click.ClickException(f"Path is not a file or directory: {path}")


def _resolve_slides_dir(data_dir: Path | None, spec_file: Path) -> Path:
    if data_dir:
        return data_dir / "slides"
    return spec_file.parent.parent / "slides"


def _print_human_readable(result: NormalizationResult, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""

    if not result.changes and not result.review_items:
        click.echo(f"{prefix}No changes needed.")
        return

    for c in result.changes:
        location = f"{c.file}:{c.line}"
        click.echo(f"{prefix}[{c.operation}] {location}: {c.description}")

    for r in result.review_items:
        click.echo(f"[REVIEW] {r.file}: {r.issue} — {r.suggestion}")

    click.echo()
    click.echo(prefix + result.summary + ".")


def _result_to_dict(result: NormalizationResult) -> dict:
    d: dict = {
        "files_modified": result.files_modified,
        "status": result.status,
        "summary": result.summary,
        "changes": [
            {
                "file": c.file,
                "operation": c.operation,
                "line": c.line,
                "description": c.description,
            }
            for c in result.changes
        ],
    }
    if result.review_items:
        d["review_items"] = [
            {
                k: v
                for k, v in {
                    "file": r.file,
                    "issue": r.issue,
                    "suggestion": r.suggestion or None,
                    **r.details,
                }.items()
                if v is not None
            }
            for r in result.review_items
        ]
    return d
