"""Normalize slide files by applying mechanical fixes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.cli.commands.shared import has_deck_scope, resolve_scoped_files
from clm.slides.normalizer import (
    ALL_OPERATIONS,
    Change,
    NormalizationResult,
    ReviewItem,
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
    "--stamp-ids",
    "stamp_ids",
    is_flag=True,
    help=(
        "One-time sync-v3 normalization (#520): stamp slide_ids onto id-less "
        "localized cells and give every voiceover/notes narrative its own "
        "unique content-slug id (re-pointing inherited-owner ids). "
        "EN-authority and pair-atomic (split decks are stamped through the "
        "unified pair; unpairable cells are refused, never half-stamped). "
        "Shared language-neutral cells are never stamped. Runs INSTEAD of "
        "the regular operations; combine with --dry-run to preview."
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
    "--confirm-pairs",
    "confirm_pairs",
    type=str,
    default=None,
    metavar="FILE",
    help="Apply an agent-confirmed interleave (#236). FILE (or '-' for stdin) is a JSON "
    'array of {"de_line": N, "en_line": M} pairs taken from a `--json` '
    "`similarity_failure` worklist; each bypasses the similarity gate and is reordered "
    "into adjacency (a plain re-run is then clean). Single slide FILE only.",
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
    stamp_ids: bool,
    dry_run: bool,
    canonicalize_start_completed: bool,
    as_json: bool,
    confirm_pairs: str | None,
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
        preamble_code     Wrap code that precedes the first cell into its own cell
        placeholder_start Untag scaffolding-less start cells whose solution
                          follows as markdown (retags markdown completed->alt)
        tag_migration     Rename alt->completed after start cells
        workshop_tags     Add workshop tag to workshop heading cells
        interleaving      Normalize DE/EN cell ordering
        slide_ids         Auto-generate slide_id metadata for cells
        cell_spacing      Normalize blank-line separation between cells
        all               All of the above (default)

    \b
    Examples:
        clm slides normalize slides/module_100/topic_010/ --dry-run
        clm slides normalize slides/module_100/topic_010/
        clm slides normalize slides/ --operations tag_migration
        clm slides normalize course-specs/python-basics.xml
        clm slides normalize slides/ --stamp-ids --dry-run

    --stamp-ids is the one-time sync-v3 (#520) id normalization: every
    localized cell and every narrative gets a slide_id (narratives their
    OWN unique id, not the owner slide's). It replaces the regular
    operations for that run.
    """
    if stamp_ids:
        if operations is not None:
            raise click.UsageError(
                "--stamp-ids replaces the regular operations; drop --operations."
            )
        if confirm_pairs is not None:
            raise click.UsageError(
                "--confirm-pairs belongs to the interleaving operation, not --stamp-ids."
            )
        if canonicalize_start_completed:
            raise click.UsageError(
                "--canonicalize-start-completed belongs to the interleaving "
                "operation, not --stamp-ids."
            )
        result = _run_stamp_ids(
            path,
            dry_run=dry_run,
            only=only,
            exclude=exclude,
            shipping_only=shipping_only,
            specs_dir=specs_dir,
            data_dir=data_dir,
        )
        if as_json:
            click.echo(json.dumps(_result_to_dict(result), indent=2))
        else:
            _print_human_readable(result, dry_run)
        if result.review_items and not result.changes:
            sys.exit(2)
        elif result.review_items:
            sys.exit(1)
        return

    op_list = _parse_operations(operations)

    confirmed_pairings = _parse_confirm_pairs(confirm_pairs) if confirm_pairs else None
    if confirmed_pairings is not None and not (path.is_file() and path.suffix == ".py"):
        raise click.UsageError(
            "--confirm-pairs operates on a single slide .py file (its line numbers are "
            "per-file), not a directory or spec."
        )

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
        result = _dispatch(
            path, op_list, dry_run, data_dir, canonicalize_start_completed, confirmed_pairings
        )

    if as_json:
        click.echo(json.dumps(_result_to_dict(result), indent=2))
    else:
        _print_human_readable(result, dry_run)

    # Exit codes: 0=clean/applied, 1=partial (review items), 2=blocked
    if result.review_items and not result.changes:
        sys.exit(2)
    elif result.review_items:
        sys.exit(1)


def _run_stamp_ids(
    path: Path,
    *,
    dry_run: bool,
    only: str | None,
    exclude: tuple[str, ...],
    shipping_only: bool,
    specs_dir: Path | None,
    data_dir: Path | None,
) -> NormalizationResult:
    """The ``--stamp-ids`` pass: localized + narrative id stamping (#520 Phase 0).

    Runs the assign-ids engine in stamp mode over the resolved file set —
    ``assign_ids_in_files`` pairs split halves and stamps each pair through
    the unified deck, which is what keeps DE/EN ids identical (#162). A
    single split-half argument is expanded to its on-disk twin for the same
    reason. Results are folded into the normalize report shape.

    Discovery is prefix-AGNOSTIC for split decks: the sync surface supports
    ``apis.de.py`` as well as ``slides_x.de.py``, and this one-time sync-v3
    migration must reach every deck sync manages — so a directory walk
    unions the routing-prefixed slide files with every prefix-less split
    half (voiceover companions are excluded by both walks).
    """
    from clm.core.topic_resolver import find_slide_files_recursive
    from clm.slides.assign_ids import AssignOptions, assign_ids_in_files
    from clm.slides.pairing import derive_split_twin, find_split_slide_files_recursive

    if path.is_file() and path.suffix == ".xml":
        raise click.UsageError(
            "--stamp-ids runs on a slide file or directory, not a course spec; "
            "pass the slides/ directory instead."
        )

    options = AssignOptions(
        stamp_ids=True,
        accept_content_derived=True,
        accept_code_derived=True,
        report_only=dry_run,
    )
    if has_deck_scope(only, exclude, shipping_only):
        files = resolve_scoped_files(
            path,
            only=only,
            exclude=exclude,
            shipping_only=shipping_only,
            specs_dir=specs_dir,
            data_dir=data_dir,
        )
    elif path.is_dir():
        files = sorted(
            set(find_slide_files_recursive(path)) | set(find_split_slide_files_recursive(path))
        )
    else:
        files = [path]
        twin = derive_split_twin(path)
        if twin is not None:
            files.append(twin)

    assign_result = assign_ids_in_files(files, options)

    result = NormalizationResult(files_modified=assign_result.files_modified)
    for a in assign_result.assignments:
        result.changes.append(
            Change(
                file=a.file,
                operation="stamp_ids",
                line=a.line,
                description=f'slide_id="{a.slide_id}" ({a.source})',
            )
        )
    for r in assign_result.refusals:
        details = {
            k: v
            for k, v in {
                "line": r.line,
                "proposed_slug": r.proposed_slug,
                "proposed_title": r.proposed_title,
            }.items()
            if v is not None
        }
        result.review_items.append(
            ReviewItem(
                file=r.file,
                issue=f"stamp_id_{r.severity}_refusal",
                suggestion=r.reason,
                details=details,
            )
        )
    return result


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
    confirmed_pairings: set[tuple[int, int]] | None = None,
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
            confirmed_pairings=confirmed_pairings,
        )
    else:
        raise click.ClickException(f"Path is not a file or directory: {path}")


def _parse_confirm_pairs(spec: str) -> set[tuple[int, int]]:
    """Read + parse ``--confirm-pairs`` (a FILE or ``-`` for stdin) to ``{(de_line, en_line)}``."""
    raw = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"--confirm-pairs is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise click.UsageError(
            '--confirm-pairs must be a JSON array of {"de_line": N, "en_line": M} objects.'
        )
    pairs: set[tuple[int, int]] = set()
    for item in data:
        try:
            pairs.add((int(item["de_line"]), int(item["en_line"])))
        except (TypeError, KeyError, ValueError) as exc:
            raise click.UsageError(
                f"--confirm-pairs entries need integer 'de_line' and 'en_line': {exc}"
            ) from exc
    return pairs


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
