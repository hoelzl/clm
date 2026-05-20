"""``clm slides sync`` — Phase 7 of the slide-format-redesign.

Cross-language sync helper for split-format decks (``<deck>.de.py`` /
``<deck>.en.py``). After an author edits one side, this command walks
the pair by ``slide_id``, asks the local LLM to propose updates to the
other side, and prints a unified diff per cell.

For v1 only ``--dry-run`` is supported (the default and only mode):
the command is read-only and writes nothing. Interactive
apply/skip/edit and ``--apply --trivial`` modes are planned follow-ups.

Exit codes:

- ``0`` — no proposed updates and no errors
- ``1`` — at least one proposed update (author should review)
- ``2`` — at least one structural error (mismatch / LLM unavailable)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.infrastructure.llm.cache import SyncCache, SyncSnapshotCache, resolve_cache_dir
from clm.infrastructure.llm.ollama_client import (
    DEFAULT_SYNC_MODEL,
    OllamaSyncJudge,
    is_available,
)
from clm.slides.sync import SyncOptions, SyncResult, sync_split_pair
from clm.slides.sync_walker import WalkerOptions, run_interactive_walker

CACHE_DB_NAME = "clm-llm.sqlite"


@click.command("sync")
@click.argument(
    "de_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "en_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--source-lang",
    type=click.Choice(["de", "en"]),
    required=True,
    help=(
        "Language that was edited; updates are proposed for the other "
        "side. Required in v1 (no auto-detection yet)."
    ),
)
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    help=(
        "Show proposed diffs without modifying any file (default). "
        "``--interactive`` automatically disables dry-run."
    ),
)
@click.option(
    "--interactive",
    is_flag=True,
    default=False,
    help=(
        "Walk proposed updates one by one and prompt "
        "[a]pply / [s]kip / [e]dit / [q]uit per proposal. On accept "
        "and on edit the target file is written in place and the new "
        "(de_hash, en_hash) is recorded in the sync_snapshots table."
    ),
)
@click.option(
    "--llm-model",
    default=DEFAULT_SYNC_MODEL,
    show_default=True,
    help="Ollama model used for the sync judge.",
)
@click.option(
    "--ollama-url",
    default=None,
    help="Base URL of the Ollama daemon. Defaults to $OLLAMA_URL or http://localhost:11434.",
)
@click.option(
    "--llm-timeout",
    type=float,
    default=120.0,
    show_default=True,
    help="Per-call timeout (seconds) for the sync judge.",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory for the LLM cache (default: --cache-dir > $CLM_CACHE_DIR > "
        "tool.clm.cache_dir in pyproject.toml > <cwd>/.clm-cache/)."
    ),
)
@click.option(
    "--no-cache",
    is_flag=True,
    help=(
        "Skip cache reads and writes. Useful when iterating on the "
        "prompt or model — every run fires fresh LLM calls."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def slides_sync_cmd(
    de_path: Path,
    en_path: Path,
    source_lang: str,
    dry_run: bool,
    interactive: bool,
    llm_model: str,
    ollama_url: str | None,
    llm_timeout: float,
    cache_dir: Path | None,
    no_cache: bool,
    as_json: bool,
) -> None:
    """Propose cross-language sync edits for a split DE/EN deck pair.

    DE_PATH and EN_PATH must be the two halves of a split-format deck
    (``<deck>.de.py`` and ``<deck>.en.py``).

    \b
    Behavior:
      * Walks the pair by slide_id (assign-ids must have run first).
      * For each paired cell, asks the local LLM to propose any needed
        update to the target side.
      * Default mode (dry-run): emits a unified diff per proposed
        update; no files are modified.
      * --interactive: walks proposals one by one with
        [a]pply / [s]kip / [e]dit / [q]uit, writes accepted/edited
        proposals to the target file, and records the post-write
        (de_hash, en_hash) in the sync_snapshots table.
      * Memoizes LLM calls via the SyncCache; unchanged pairs cache-hit.
      * Reports structural mismatches (cells present on one side only,
        or unequal counts within a slide_id) as warnings/errors.
    """
    if interactive and as_json:
        raise click.UsageError("--interactive and --json are mutually exclusive")

    # Direction-of-edit is required so the LLM knows which side is the
    # source of truth. Auto-detection via git history is on the v2 list.
    judge = OllamaSyncJudge(
        model=llm_model,
        base_url=ollama_url,
        timeout=llm_timeout,
    )
    if not is_available(judge):
        click.echo(
            f"warning: Ollama is not reachable at {judge.base_url}; "
            "every pair will be recorded as an LLM-unavailable error.",
            err=True,
        )
        judge_for_options = None
    else:
        judge_for_options = judge

    cache: SyncCache | None = None
    snapshot_cache: SyncSnapshotCache | None = None
    if not no_cache:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        cache = SyncCache(cache_root / CACHE_DB_NAME)
        snapshot_cache = SyncSnapshotCache(cache_root / CACHE_DB_NAME)

    options = SyncOptions(
        source_lang=source_lang,
        judge=judge_for_options,
        cache=cache,
    )

    try:
        result = sync_split_pair(de_path, en_path, options)
        if interactive:
            run_interactive_walker(
                result,
                WalkerOptions(snapshot_cache=snapshot_cache),
            )
    finally:
        if cache is not None:
            cache.close()
        if snapshot_cache is not None:
            snapshot_cache.close()

    effective_dry_run = dry_run and not interactive
    if as_json:
        click.echo(json.dumps(_to_dict(result), indent=2))
    else:
        _print_human(result, dry_run=effective_dry_run, interactive=interactive)

    sys.exit(_exit_code(result))


def _print_human(result: SyncResult, *, dry_run: bool, interactive: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""

    for issue in result.issues:
        click.echo(
            f"issue-{issue.severity} {issue.slide_id} "
            f"(de={issue.de_count}, en={issue.en_count}): {issue.reason}"
        )

    # In interactive mode the walker has already printed per-proposal
    # output (diff + prompt + action). The end-of-run summary still
    # surfaces in_sync / error outcomes and the headline counters so
    # the trainer sees everything in one place.
    for outcome in result.outcomes:
        if outcome.verdict == "in_sync":
            cached_tag = " (cached)" if outcome.cached else ""
            click.echo(
                f"{prefix}in-sync {outcome.slide_id}/{outcome.role} "
                f"de:{outcome.de_line} en:{outcome.en_line}{cached_tag}"
                + (f" — {outcome.reason}" if outcome.reason else "")
            )
        elif outcome.verdict == "update" and not interactive:
            cached_tag = " (cached)" if outcome.cached else ""
            click.echo(
                f"{prefix}propose {outcome.slide_id}/{outcome.role} "
                f"({outcome.direction}) de:{outcome.de_line} "
                f"en:{outcome.en_line}{cached_tag}"
                + (f" — {outcome.reason}" if outcome.reason else "")
            )
            if outcome.diff:
                click.echo(outcome.diff)
                click.echo()
        elif outcome.verdict == "error":
            click.echo(
                f"error {outcome.slide_id}/{outcome.role} "
                f"de:{outcome.de_line} en:{outcome.en_line}: {outcome.error}"
            )

    click.echo()
    click.echo(
        f"{prefix}{result.pairs_visited} pair(s) visited, "
        f"{result.pairs_in_sync} in sync, "
        f"{result.pairs_proposed} proposed update(s), "
        f"{result.pairs_error} error(s), "
        f"{result.cache_hits} cache hit(s), "
        f"{len(result.issues)} structural issue(s)."
    )
    if interactive:
        click.echo(
            f"walker: {result.pairs_accepted} accepted, "
            f"{result.pairs_edited} edited, "
            f"{result.pairs_skipped} skipped, "
            f"{result.pairs_quit} unvisited (quit)."
        )


def _to_dict(result: SyncResult) -> dict:
    return {
        "de_path": str(result.de_path),
        "en_path": str(result.en_path),
        "pairs_visited": result.pairs_visited,
        "pairs_in_sync": result.pairs_in_sync,
        "pairs_proposed": result.pairs_proposed,
        "pairs_error": result.pairs_error,
        "cache_hits": result.cache_hits,
        "pairs_accepted": result.pairs_accepted,
        "pairs_skipped": result.pairs_skipped,
        "pairs_edited": result.pairs_edited,
        "pairs_quit": result.pairs_quit,
        "outcomes": [
            {
                "slide_id": o.slide_id,
                "role": o.role,
                "de_line": o.de_line,
                "en_line": o.en_line,
                "direction": o.direction,
                "verdict": o.verdict,
                "reason": o.reason,
                "cached": o.cached,
                "diff": o.diff,
                "error": o.error,
                "proposed_text": (o.proposal.proposed_text if o.proposal is not None else ""),
            }
            for o in result.outcomes
        ],
        "issues": [
            {
                "slide_id": i.slide_id,
                "severity": i.severity,
                "reason": i.reason,
                "de_count": i.de_count,
                "en_count": i.en_count,
            }
            for i in result.issues
        ],
    }


def _exit_code(result: SyncResult) -> int:
    if result.has_errors or any(i.severity == "error" for i in result.issues):
        return 2
    if result.has_proposals:
        return 1
    return 0
