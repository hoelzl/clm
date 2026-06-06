"""Normalize slide files by applying mechanical fixes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.cli.commands.shared import has_deck_scope, resolve_scoped_files
from clm.slides.normalizer import (
    ALL_OPERATIONS,
    NormalizationResult,
    normalize_course,
    normalize_directory,
    normalize_file,
    normalize_files,
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
    "--canonicalize-start-completed",
    is_flag=True,
    help=(
        "Force start/completed cohesion pairs into the canonical DE/EN "
        "interleave, even when DE/EN code differs (e.g. localized "
        "identifiers). Run before `clm slides split` so the round-trip "
        "unify(split(deck)) == deck holds byte-for-byte. Only affects the "
        "interleaving operation."
    ),
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
    help="Course data directory (contains slides/). For course spec normalization "
    "and --shipping-only scope resolution.",
)
@click.option(
    "--only",
    type=click.Choice(["bilingual", "split"]),
    default=None,
    help="Scope a directory run to only bilingual decks (no .de/.en tag) or only "
    "split halves (e.g. leave .de/.en pairs for `clm slides sync`).",
)
@click.option(
    "--exclude",
    multiple=True,
    metavar="GLOB",
    help="Skip decks matching GLOB (matched against the full path and each path "
    "component, so `--exclude _archive` skips an _archive/ dir). Repeatable.",
)
@click.option(
    "--shipping-only",
    is_flag=True,
    help="Scope a directory run to decks reachable from course specs (the shipping set).",
)
@click.option(
    "--specs-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="For --shipping-only: directory of *.xml specs. Default: <course-root>/course-specs/.",
)
def normalize_slides_cmd(
    path: Path,
    operations: str | None,
    dry_run: bool,
    canonicalize_start_completed: bool,
    as_json: bool,
    data_dir: Path | None,
    only: str | None,
    exclude: tuple[str, ...],
    shipping_only: bool,
    specs_dir: Path | None,
):
    """Normalize slide files by applying mechanical fixes.

    PATH can be a single .py slide file, a topic directory, or a course
    spec XML file (normalizes all slides in the course).

    \b
    Operations:
        preamble_code   Wrap code that precedes the first cell into its own cell
        tag_migration   Rename alt->completed after start cells
        workshop_tags   Add workshop tag to workshop heading cells
        interleaving    Normalize DE/EN cell ordering
        slide_ids       Auto-generate slide_id metadata for cells
        cell_spacing    Normalize blank-line separation between cells
        all             All of the above (default)

    \b
    Examples:
        clm slides normalize slides/module_100/topic_010/ --dry-run
        clm slides normalize slides/module_100/topic_010/
        clm slides normalize slides/ --operations tag_migration
        clm slides normalize course-specs/python-basics.xml
    """
    op_list = _parse_operations(operations)

    if has_deck_scope(only, exclude, shipping_only):
        if not path.is_dir():
            raise click.UsageError(
                "--only / --exclude / --shipping-only apply to a directory, not a "
                "single file or spec."
            )
        files = resolve_scoped_files(
            path,
            only=only,
            exclude=exclude,
            shipping_only=shipping_only,
            specs_dir=specs_dir,
            data_dir=data_dir,
        )
        result = normalize_files(
            files,
            operations=op_list,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )
    else:
        result = _dispatch(path, op_list, dry_run, data_dir, canonicalize_start_completed)

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
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    if path.is_file() and path.suffix == ".xml":
        slides_dir = _resolve_slides_dir(data_dir, path)
        return normalize_course(
            path,
            slides_dir,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )
    elif path.is_dir():
        return normalize_directory(
            path,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )
    elif path.is_file():
        return normalize_file(
            path,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )
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
