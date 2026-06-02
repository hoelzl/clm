"""Materialize ``<include>`` declarations on disk for local development.

The build pipeline already splices includes virtually via the
``source_origin`` field on :class:`clm.core.course_file.CourseFile`. That
covers ``clm build`` execution. But notebook authors running a deck
directly (VS Code, ``jupyter lab``) need the included package to sit
physically next to the slide file so Python's import system finds it.

``clm sync-includes`` resolves every ``<include>`` declared in a course
spec and materializes the source under ``<topic-dir>/<as>`` as a copy
(default), symlink, or set of hardlinks. Each topic that received at
least one materialization gets a JSON ledger at ``<topic-dir>/.clm-include``
listing exactly what was created — ``--remove`` reads the ledger to
delete only paths the command put there, so user files in the topic dir
are never touched.

``--print-gitignore`` emits suggested ``.gitignore`` patterns to stdout so
the author can paste them once into a course-root ``.gitignore``. The
command never writes ``.gitignore`` files itself; see
``docs/claude/design/sync-includes-gitignore-redesign-archive.md`` for
the rationale.

See ``docs/claude/design/shared-source-includes-and-output-dedup.md`` for
the locked design of the core feature.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import click

from clm.core.course_spec import CourseSpec, CourseSpecError, IncludeSpec
from clm.core.include_ledger import (
    LEDGER_NAME,
)
from clm.core.include_ledger import (
    Ledger as _Ledger,
)
from clm.core.include_ledger import (
    LedgerEntry as _LedgerEntry,
)
from clm.core.topic_resolver import build_topic_map, matches_for_binding
from clm.infrastructure.utils.path_utils import (
    is_ignored_dir_for_course,
    is_ignored_file_for_course,
)

logger = logging.getLogger(__name__)

SUPPORTED_MODES = ("copy", "symlink", "hardlink")


@dataclass
class _SyncSummary:
    """Tallies of what happened during a sync run."""

    materialized: int = 0
    refreshed: int = 0
    removed: int = 0
    skipped: int = 0
    missing_required: int = 0
    shadowed: int = 0
    fallbacks: int = 0
    unresolved_topics: int = 0


@click.command("sync-includes")
@click.argument(
    "spec_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help=("Course data directory (contains slides/). Default: inferred from spec file location."),
)
@click.option(
    "--mode",
    type=click.Choice(SUPPORTED_MODES),
    default="copy",
    show_default=True,
    help=(
        "How to materialize each include. 'copy' is the most portable. "
        "'symlink' is faster and avoids drift but requires admin or "
        "Developer Mode on Windows. 'hardlink' is per-file and "
        "filesystem-local."
    ),
)
@click.option(
    "--remove",
    is_flag=True,
    help=(
        "Delete previously-synced materializations. Only paths recorded "
        "in each topic's .clm-include ledger are removed."
    ),
)
@click.option(
    "--print-gitignore",
    "print_gitignore",
    is_flag=True,
    help=(
        "Print suggested .gitignore patterns for every materialized "
        "include (and the .clm-include ledger) to stdout, then exit. The "
        "command never writes .gitignore files itself — paste the output "
        "into your course-root .gitignore once. Idempotent; safe to "
        "redirect with `>> .gitignore`."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would happen without modifying the filesystem.",
)
def sync_includes_cmd(
    spec_file: Path,
    data_dir: Path | None,
    mode: str,
    remove: bool,
    print_gitignore: bool,
    dry_run: bool,
) -> None:
    """Materialize <include> declarations from a course spec on disk.

    Local notebook execution (VS Code, jupyter lab) needs included
    packages to sit physically beside the notebook. This command resolves
    every ``<include>`` in the spec and creates the materialization under
    ``<topic-dir>/<as>``.

    A small ``.clm-include`` ledger is written into each affected topic
    directory recording exactly which paths were materialized — only
    those paths are touched by ``--remove``.

    \b
    Examples:
        clm sync-includes course-specs/ml-azav.xml
        clm sync-includes course-specs/ml-azav.xml --mode=symlink
        clm sync-includes course-specs/ml-azav.xml --remove
        clm sync-includes course-specs/ml-azav.xml --print-gitignore >> .gitignore
    """
    if print_gitignore and remove:
        raise click.UsageError("--print-gitignore and --remove are mutually exclusive.")

    course_root = _resolve_course_root(data_dir, spec_file)
    slides_dir = course_root / "slides"

    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(str(e)) from None

    if print_gitignore:
        as_paths = _collect_as_paths_from_spec(spec)
        _emit_gitignore_patterns(as_paths)
        return

    topic_map = build_topic_map(slides_dir)
    summary = _SyncSummary()

    for binding in spec.iter_topic_bindings():
        matches = matches_for_binding(topic_map, binding.topic_id, binding.effective_module)
        if len(matches) != 1:
            includes = binding.section.includes_for(binding.topic_spec)
            if includes:
                summary.unresolved_topics += 1
                _warn(
                    f"Topic '{binding.topic_id}' did not resolve to a "
                    f"single directory; skipping its includes. Run "
                    f"`clm validate` to diagnose."
                )
            continue

        topic_match = matches[0]
        topic_dir = _topic_materialization_root(topic_match.path, topic_match.path_type)
        includes = binding.section.includes_for(binding.topic_spec)
        if not includes:
            continue

        ledger_path = topic_dir / LEDGER_NAME
        ledger = _Ledger.load(ledger_path)

        if remove:
            _remove_materializations(
                topic_dir=topic_dir,
                ledger=ledger,
                ledger_path=ledger_path,
                summary=summary,
                dry_run=dry_run,
            )
            continue

        for inc in includes:
            _materialize_include(
                inc=inc,
                course_root=course_root,
                topic_dir=topic_dir,
                topic_id=binding.topic_id,
                mode=mode,
                ledger=ledger,
                summary=summary,
                dry_run=dry_run,
            )

        if ledger.entries and not dry_run:
            _write_ledger(ledger_path, ledger)

    _print_summary(summary, dry_run=dry_run, remove=remove)
    if summary.missing_required > 0:
        raise SystemExit(1)


def _resolve_course_root(data_dir: Path | None, spec_file: Path) -> Path:
    """Resolve the course root containing ``slides/`` and include sources."""
    if data_dir is not None:
        return data_dir.resolve()
    # Course specs are conventionally located at <course-root>/course-specs/.
    return spec_file.resolve().parent.parent


def _topic_materialization_root(topic_path: Path, path_type: str) -> Path:
    """Where ``<topic>/<as>`` lives on disk for materialization.

    ``DirectoryTopic`` resolves to the topic directory itself; the
    materialization sits inside it. ``FileTopic`` resolves to a single
    file, so includes land alongside it (mirroring how a FileTopic's
    sibling files are discovered today).
    """
    if path_type == "directory":
        return topic_path
    return topic_path.parent


def _materialize_include(
    *,
    inc: IncludeSpec,
    course_root: Path,
    topic_dir: Path,
    topic_id: str,
    mode: str,
    ledger: _Ledger,
    summary: _SyncSummary,
    dry_run: bool,
) -> None:
    source_path = (course_root / inc.source).resolve()
    target_path = topic_dir / inc.as_path

    if not source_path.exists():
        if inc.optional:
            summary.skipped += 1
            logger.debug(
                "Topic '%s': optional include '%s' source missing — skipping.",
                topic_id,
                inc.source,
            )
            return
        summary.missing_required += 1
        _warn(
            f"Topic '{topic_id}': include source '{inc.source}' does not "
            f"exist (required). Skipping."
        )
        return

    existing_entry = next((e for e in ledger.entries if e.as_path == inc.as_path), None)

    if target_path.exists() and existing_entry is None:
        # Untracked path at the target — leave it alone. The shadow
        # warning surfaces during validate / build; we mirror the
        # "real file wins" rule here.
        summary.shadowed += 1
        _warn(
            f"Topic '{topic_id}': target '{inc.as_path}' already exists "
            f"and was not created by sync-includes; leaving it untouched. "
            f"Remove it manually if you want the include materialized."
        )
        return

    is_refresh = existing_entry is not None

    if dry_run:
        action = "would refresh" if is_refresh else "would create"
        click.echo(f"  {action} {topic_dir.name}/{inc.as_path}  <- {inc.source} ({mode})")
        if is_refresh:
            summary.refreshed += 1
        else:
            summary.materialized += 1
        return

    # Clear any prior materialization at this path so mode changes work.
    if target_path.exists() or target_path.is_symlink():
        _delete_path(target_path)

    effective_mode = _do_materialize(
        source_path=source_path,
        target_path=target_path,
        mode=mode,
    )

    if effective_mode != mode:
        summary.fallbacks += 1

    ledger.upsert(_LedgerEntry(as_path=inc.as_path, source=inc.source, mode=effective_mode))

    if is_refresh:
        summary.refreshed += 1
    else:
        summary.materialized += 1


def _do_materialize(*, source_path: Path, target_path: Path, mode: str) -> str:
    """Create the requested materialization, returning the effective mode.

    On Windows, ``--mode=symlink`` may fail without admin or Developer
    Mode. We catch ``OSError`` from :func:`os.symlink` and fall back to
    ``copy`` so authors are never blocked.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "symlink":
        try:
            os.symlink(source_path, target_path, target_is_directory=source_path.is_dir())
            return "symlink"
        except OSError as e:
            _warn(
                f"symlink creation failed for '{target_path}': {e}. "
                f"Falling back to copy. (On Windows, enable Developer "
                f"Mode or run as administrator for symlink support.)"
            )
            return _do_materialize(source_path=source_path, target_path=target_path, mode="copy")

    if mode == "hardlink":
        if source_path.is_dir():
            return _hardlink_tree(source_path, target_path)
        try:
            os.link(source_path, target_path)
            return "hardlink"
        except OSError as e:
            _warn(f"hardlink creation failed for '{target_path}': {e}. Falling back to copy.")
            return _do_materialize(source_path=source_path, target_path=target_path, mode="copy")

    # mode == "copy"
    if source_path.is_dir():
        _copy_tree(source_path, target_path)
    else:
        shutil.copy2(source_path, target_path)
    return "copy"


def _copy_tree(source: Path, target: Path) -> None:
    """Copy a directory tree, skipping CLM-ignored cruft."""
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        # Skip pycache / .venv / .git etc. by inspecting the rel path.
        if any(
            part in {".git", ".venv", "__pycache__", "node_modules"}
            or is_ignored_dir_for_course(Path(part))
            for part in rel.parts
        ):
            continue
        dest = target / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if is_ignored_file_for_course(item):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)


def _hardlink_tree(source: Path, target: Path) -> str:
    """Create a directory mirrored by per-file hardlinks.

    Returns the effective mode: ``hardlink`` on success, ``copy`` if we
    had to fall back partway (e.g., the filesystem refuses cross-device
    hardlinks).
    """
    target.mkdir(parents=True, exist_ok=True)
    fell_back = False
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        if any(
            part in {".git", ".venv", "__pycache__", "node_modules"}
            or is_ignored_dir_for_course(Path(part))
            for part in rel.parts
        ):
            continue
        dest = target / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if is_ignored_file_for_course(item):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(item, dest)
        except OSError:
            shutil.copy2(item, dest)
            fell_back = True
    return "copy" if fell_back else "hardlink"


def _delete_path(path: Path) -> None:
    """Remove a file, symlink, or directory tree at *path*."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def _remove_materializations(
    *,
    topic_dir: Path,
    ledger: _Ledger,
    ledger_path: Path,
    summary: _SyncSummary,
    dry_run: bool,
) -> None:
    """Delete every path recorded in *ledger*, then drop the ledger itself."""
    if not ledger.entries:
        return
    for entry in ledger.entries:
        target = topic_dir / entry.as_path
        if dry_run:
            click.echo(f"  would remove {topic_dir.name}/{entry.as_path}")
            summary.removed += 1
            continue
        if target.exists() or target.is_symlink():
            _delete_path(target)
        summary.removed += 1
    if not dry_run and ledger_path.exists():
        ledger_path.unlink()


def _write_ledger(ledger_path: Path, ledger: _Ledger) -> None:
    """Write *ledger* to disk as JSON with a stable shape."""
    payload = ledger.to_dict()
    payload["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _collect_as_paths_from_spec(spec: CourseSpec) -> list[str]:
    """Return every effective ``as`` path declared in *spec*, deduplicated.

    Walks the full topic-binding set so section-level defaults and
    per-topic overrides are both surfaced. The list is intentionally
    spec-driven (not ledger-driven) so ``--print-gitignore`` works on a
    fresh checkout before any materialization has happened.
    """
    seen: set[str] = set()
    for binding in spec.iter_topic_bindings():
        for inc in binding.section.includes_for(binding.topic_spec):
            seen.add(inc.as_path)
    return sorted(seen)


def _compute_gitignore_patterns(as_paths: Iterable[str]) -> list[str]:
    """Pure helper: render the suggested gitignore line set.

    The universal ledger pattern (``**/.clm-include``) is always emitted,
    even when no ``as`` paths exist, so a fresh checkout can bootstrap
    with `clm sync-includes spec.xml --print-gitignore`. Each ``as`` path
    is anchored under ``slides/**/`` to avoid accidentally matching the
    canonical source under ``examples/`` (or anywhere else).
    """
    patterns: list[str] = [f"**/{LEDGER_NAME}"]
    for as_path in sorted({a for a in as_paths if a}):
        # Trailing slash so the pattern matches a directory; for file
        # includes the ledger filter is what matters and the directory
        # pattern is harmless (no such directory exists).
        patterns.append(f"slides/**/{as_path}/")
    return patterns


def _emit_gitignore_patterns(as_paths: Iterable[str]) -> None:
    """Write the suggested gitignore block to stdout."""
    click.echo("# Added by `clm sync-includes --print-gitignore`")
    click.echo("# Materialized include targets and per-topic ledgers.")
    for pattern in _compute_gitignore_patterns(as_paths):
        click.echo(pattern)


def _print_summary(summary: _SyncSummary, *, dry_run: bool, remove: bool) -> None:
    prefix = "(dry-run) " if dry_run else ""
    if remove:
        click.echo(f"{prefix}Removed {summary.removed} materialization(s).")
        if summary.unresolved_topics:
            click.echo(
                f"{prefix}Skipped {summary.unresolved_topics} topic(s) that did not resolve."
            )
        return

    parts: list[str] = []
    if summary.materialized:
        parts.append(f"{summary.materialized} created")
    if summary.refreshed:
        parts.append(f"{summary.refreshed} refreshed")
    if summary.skipped:
        parts.append(f"{summary.skipped} optional skipped")
    if summary.shadowed:
        parts.append(f"{summary.shadowed} shadowed")
    if summary.fallbacks:
        parts.append(f"{summary.fallbacks} mode fallback(s)")
    if summary.missing_required:
        parts.append(f"{summary.missing_required} missing (required)")
    if summary.unresolved_topics:
        parts.append(f"{summary.unresolved_topics} unresolved topic(s)")
    if not parts:
        click.echo(f"{prefix}No includes declared in this spec.")
        return
    click.echo(f"{prefix}{'; '.join(parts)}.")
    if summary.materialized + summary.refreshed > 0:
        click.echo(
            "Tip: run `clm sync-includes <spec> --print-gitignore` for suggested .gitignore rules."
        )


def _warn(msg: str) -> None:
    """Emit a user-visible warning line."""
    click.echo(f"WARN  {msg}", err=True)
