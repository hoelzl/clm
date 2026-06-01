"""``clm slides sync`` — single-language authoring sync for split decks.

Issue #166. After an author edits **one** half of a split-format deck pair
(``<deck>.de.py`` / ``<deck>.en.py``, the layout produced by
``clm slides split``), this command brings the *other* half into sync in a
single pass: edits are propagated, brand-new slides are translated and inserted,
removed slides are dropped, reorders are mirrored, and a shared ``slide_id`` is
minted onto both decks as it goes.

Direction is decided **per cell** by diffing each deck against a structural
**watermark** — the last-synced state, recorded only on a successful apply, so it
is immune to the author's git-commit cadence. There is no global
``--source-lang``: different cells can flow in different directions in the same
pass, and a cell edited on *both* sides since the last sync is isolated as a
*conflict* rather than guessed. When no watermark exists yet, the baseline falls
back to each deck's git ``HEAD`` (and finally to the id-less-as-new heuristic).

Modes:

- **default** — write the agreed changes to the working tree in one pass. The
  watermark advances; nothing is committed. Review with ``git diff`` (the
  design's primary review surface).
- ``--dry-run`` — classify only; print the plan and write nothing.
- ``--interactive`` — walk each proposal and choose
  ``[a]pply`` / ``[s]kip`` / ``[q]uit`` (``[d]e-wins`` / ``[e]n-wins`` for a
  conflict) before a single atomic apply.

Edits are reconciled by a :class:`SyncJudge` whose backend is selectable with
``--provider`` (or ``$CLM_SYNC_PROVIDER``): ``openrouter`` (the default — Claude
Sonnet via OpenRouter, fast) or ``local`` (the offline Ollama model, slower).
Brand-new slides are always translated by an OpenRouter :class:`SlideTranslator`
(Claude Sonnet). When the judge backend is unavailable (Ollama unreachable, or
no OpenRouter key), edits are recorded as errors; when no translator key is
configured, adds defer.

Exit codes:

- ``0`` — clean: every change applied (or nothing to do), no errors
- ``1`` — something is left for review (a skipped proposal / unresolved conflict)
- ``2`` — a structural error (classifier issue, missing target cell, LLM down)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from clm.infrastructure.llm.cache import (
    SyncAlignmentCache,
    SyncWatermarkCache,
    resolve_cache_dir,
)
from clm.infrastructure.llm.ollama_client import (
    DEFAULT_SYNC_MODEL,
    OllamaSyncJudge,
    is_available,
)
from clm.infrastructure.llm.openrouter_client import (
    DEFAULT_SYNC_JUDGE_MODEL,
    OpenRouterSyncJudge,
    has_openrouter_api_key,
)
from clm.slides.sync_apply import ApplyResult, apply_plan
from clm.slides.sync_plan import (
    PlanIssue,
    SyncPlan,
    build_sync_plan,
    render_explain,
    render_plan,
)
from clm.slides.sync_plan_walker import PlanWalkResult, WalkerOptions, run_plan_walker
from clm.slides.sync_recover import DEFAULT_RECOVERY_MODEL, OpenRouterAlignmentRecoverer
from clm.slides.sync_translate import DEFAULT_TRANSLATION_MODEL, OpenRouterSlideTranslator

if TYPE_CHECKING:
    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_recover import AlignmentRecoverer

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
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Classify only: print the plan and write nothing. The default "
        "(without this flag) writes the agreed changes to the working tree."
    ),
)
@click.option(
    "--interactive",
    is_flag=True,
    default=False,
    help=(
        "Walk each proposal and choose [a]pply / [s]kip / [q]uit "
        "([d]e-wins / [e]n-wins for a conflict) before a single atomic apply. "
        "Mutually exclusive with --dry-run and --json."
    ),
)
@click.option(
    "--explain",
    is_flag=True,
    default=False,
    help=(
        "Diagnostic: print the content-anchor diff — each cell's anchor "
        "(id:/construct:/hash:) and whether it is unchanged/edited/new/removed vs "
        "the watermark, the neutral-cell propagation direction, and any drifted "
        "slide_ids (id-migration candidates) — then the plan, and write nothing. A "
        "read-only superset of --dry-run for understanding why a cell did or did not "
        "sync (Issue #190). Mutually exclusive with --interactive and --json."
    ),
)
@click.option(
    "--provider",
    type=click.Choice(["openrouter", "local"]),
    default=lambda: os.environ.get("CLM_SYNC_PROVIDER") or "openrouter",
    show_default="openrouter (or $CLM_SYNC_PROVIDER)",
    help=(
        "Backend for the edit-reconciliation judge: 'openrouter' (Claude Sonnet "
        "via OpenRouter — fast, needs $OPENROUTER_API_KEY or $OPENAI_API_KEY) or "
        "'local' (the Ollama daemon — offline, slower). Overridable with "
        "$CLM_SYNC_PROVIDER."
    ),
)
@click.option(
    "--llm-model",
    default=None,
    help=(
        "Model for the edit-reconciliation judge. Default depends on --provider: "
        f"'{DEFAULT_SYNC_JUDGE_MODEL}' for openrouter, '{DEFAULT_SYNC_MODEL}' for local."
    ),
)
@click.option(
    "--ollama-url",
    default=None,
    help=(
        "Base URL of the Ollama daemon (only used with --provider local). "
        "Defaults to $OLLAMA_URL or http://localhost:11434."
    ),
)
@click.option(
    "--llm-timeout",
    type=float,
    default=None,
    show_default="120 (openrouter) / 300 (local)",
    help=(
        "Per-call timeout (seconds) for the edit judge. Defaults are "
        "provider-aware: 120s for openrouter (fast hosted model) and 300s for "
        "local (a large local reasoning model can spend minutes 'thinking')."
    ),
)
@click.option(
    "--translation-model",
    default=DEFAULT_TRANSLATION_MODEL,
    show_default=True,
    help=(
        "OpenRouter model used to translate brand-new slides for the add path. "
        "Needs $OPENROUTER_API_KEY (or $OPENAI_API_KEY); adds defer when absent."
    ),
)
@click.option(
    "--llm-recover",
    is_flag=True,
    default=False,
    help=(
        "Opt into the bounded-LLM recovery tier (Issue #190 §10): when the "
        "deterministic id-migration is stuck on an ambiguous drifted slide_id "
        "(a function renamed while a cell was split, an unresolvable tie), ask "
        "Claude (Opus, via OpenRouter) for a validated, body-free id↔cell "
        "alignment. Default off — without it such a region is left untouched and "
        "re-surfaces next run. Needs $OPENROUTER_API_KEY (or $OPENAI_API_KEY)."
    ),
)
@click.option(
    "--recovery-model",
    default=DEFAULT_RECOVERY_MODEL,
    show_default=True,
    help="OpenRouter model for --llm-recover alignment (a strong reasoning model).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory holding the structural watermark (default: --cache-dir > "
        "$CLM_CACHE_DIR > tool.clm.cache_dir in pyproject.toml > <cwd>/.clm-cache/)."
    ),
)
@click.option(
    "--no-cache",
    is_flag=True,
    help=(
        "Do not read or write the watermark. Every run then re-derives its "
        "baseline from git HEAD and no synced state is persisted."
    ),
)
@click.option(
    "--no-env-file",
    is_flag=True,
    default=False,
    help=(
        "Do not auto-load a .env file. By default sync walks up from each deck's "
        "directory and loads the first .env found (without overriding already-set "
        "variables), so $OPENROUTER_API_KEY / $OPENAI_API_KEY kept in the project "
        ".env are available to the judge and translator."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def slides_sync_cmd(
    de_path: Path,
    en_path: Path,
    dry_run: bool,
    interactive: bool,
    explain: bool,
    provider: str,
    llm_model: str | None,
    ollama_url: str | None,
    llm_timeout: float | None,
    translation_model: str,
    llm_recover: bool,
    recovery_model: str,
    cache_dir: Path | None,
    no_cache: bool,
    no_env_file: bool,
    as_json: bool,
) -> None:
    """Bring a split DE/EN deck pair into sync after editing one side.

    DE_PATH and EN_PATH are the two halves of a split-format deck
    (``<deck>.de.py`` and ``<deck>.en.py``).

    \b
    Behavior:
      * Diffs both decks against the structural watermark (last synced
        state) to classify per-cell add / edit / move / remove / conflict
        changes — direction is decided per cell, not globally.
      * Default: writes the agreed changes to the working tree in one pass
        (edits reconciled by the selected judge — Claude Sonnet via OpenRouter
        by default, or local Ollama with --provider local — new slides
        translated + inserted, a shared slide_id minted onto both decks) and
        advances the watermark. Nothing is committed — review with ``git diff``.
      * --dry-run: prints the plan and writes nothing.
      * --explain: prints the content-anchor diff (per-cell anchor + drift,
        the neutral propagation direction, drifted slide_ids) then the plan,
        and writes nothing — a read-only diagnostic.
      * --interactive: prompts per proposal before a single atomic apply.
      * A cell edited on both sides since the last sync is isolated as a
        conflict (left untouched, listed in the summary) rather than guessed.
    """
    if interactive and as_json:
        raise click.UsageError("--interactive and --json are mutually exclusive")
    if interactive and dry_run:
        raise click.UsageError(
            "--interactive and --dry-run are mutually exclusive "
            "(--dry-run writes nothing; --interactive applies after prompting)"
        )
    if explain and interactive:
        raise click.UsageError(
            "--explain and --interactive are mutually exclusive (--explain writes nothing)"
        )
    if explain and as_json:
        raise click.UsageError(
            "--explain and --json are mutually exclusive "
            "(--explain is a human-readable diagnostic; use --dry-run --json for the structured plan)"
        )

    # Load the project .env before resolving the judge/translator, so keys kept
    # only in .env (the usual course-repo layout) are found. Without this, every
    # add defers and every edit errors as "LLM unavailable" even though the keys
    # exist on disk (the reported sync bug). Skipped for --dry-run / --explain (no
    # LLM) and when --no-env-file is given.
    if not no_env_file and not dry_run and not explain:
        from clm.cli.env_loading import load_env_files

        load_env_files(de_path.parent, en_path.parent)

    watermark_cache: SyncWatermarkCache | None = None
    alignment_cache: SyncAlignmentCache | None = None
    if not no_cache:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
        if llm_recover and not dry_run:
            alignment_cache = SyncAlignmentCache(cache_root / CACHE_DB_NAME)

    plan: SyncPlan
    apply_result: ApplyResult | None = None
    walk: PlanWalkResult | None = None
    explain_text: str | None = None
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=watermark_cache)

        if explain:
            mode = "explain"
            # Render while the watermark cache is still open (the finally closes it);
            # --explain writes nothing and uses no LLM, like --dry-run.
            explain_text = render_explain(
                de_path, en_path, plan=plan, watermark_cache=watermark_cache
            )
        elif dry_run:
            mode = "dry-run"
        elif interactive:
            mode = "interactive"
            judge = _resolve_judge(provider, llm_model, ollama_url, llm_timeout)
            translator = OpenRouterSlideTranslator(model=translation_model)
            recoverer = _resolve_recoverer(llm_recover, recovery_model)
            for issue in plan.issues:
                click.echo(_issue_line(issue))
            walk = run_plan_walker(
                plan,
                judge=judge,
                translator=translator,
                watermark_cache=watermark_cache,
                options=WalkerOptions(),
                recoverer=recoverer,
                alignment_cache=alignment_cache,
            )
            apply_result = walk.apply_result
        else:
            mode = "apply"
            judge = _resolve_judge(provider, llm_model, ollama_url, llm_timeout)
            translator = OpenRouterSlideTranslator(model=translation_model)
            recoverer = _resolve_recoverer(llm_recover, recovery_model)
            apply_result = apply_plan(
                plan,
                judge=judge,
                translator=translator,
                watermark_cache=watermark_cache,
                recoverer=recoverer,
                alignment_cache=alignment_cache,
            )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()
        if alignment_cache is not None:
            alignment_cache.close()

    exit_code = (
        _plan_exit_code(plan) if apply_result is None else _apply_exit_code(plan, apply_result)
    )

    if mode == "explain":
        click.echo(explain_text)
    elif as_json:
        click.echo(json.dumps(_to_dict(plan, apply_result, walk, mode, exit_code), indent=2))
    else:
        _print_human(plan, apply_result, walk, mode=mode)

    sys.exit(exit_code)


# Provider-aware default per-call timeout. A large local reasoning model
# (qwen3:30b) can legitimately spend minutes on a substantial cell, so the
# 120s default that the hosted model is fine with starved the local judge and
# dropped most edits (the reported sync bug); give local a wider budget.
_DEFAULT_TIMEOUT_OPENROUTER = 120.0
_DEFAULT_TIMEOUT_LOCAL = 300.0


def _resolve_timeout(value: float | None, default: float) -> float:
    """The effective per-call timeout: ``value`` if positive, else ``default``.

    A non-positive timeout is meaningless and is rejected by ``urllib`` (a
    negative one raises an uncaught ``ValueError`` on the local path), so a
    ``<= 0`` value falls back to the provider default rather than crashing.
    """
    return value if value is not None and value > 0 else default


def _resolve_judge(
    provider: str,
    llm_model: str | None,
    ollama_url: str | None,
    llm_timeout: float | None,
) -> SyncJudge | None:
    """Construct the edit judge for ``provider``, or ``None`` (with a warning).

    A ``None`` judge records each edit proposal as an LLM-unavailable error, so
    the run still completes and surfaces exactly what could not be reconciled.
    The ``--llm-model`` and ``--llm-timeout`` defaults are resolved per provider
    here so a bare run picks the right model and timeout for the chosen backend.
    """
    if provider == "local":
        ollama_judge = OllamaSyncJudge(
            model=llm_model or DEFAULT_SYNC_MODEL,
            base_url=ollama_url,
            timeout=_resolve_timeout(llm_timeout, _DEFAULT_TIMEOUT_LOCAL),
        )
        if is_available(ollama_judge):
            return ollama_judge
        click.echo(
            f"warning: Ollama is not reachable at {ollama_judge.base_url}; "
            "every edit will be recorded as an LLM-unavailable error. "
            "Set --provider openrouter (the default) to use a hosted model.",
            err=True,
        )
        return None

    # provider == "openrouter"
    if not has_openrouter_api_key():
        click.echo(
            "warning: OPENROUTER_API_KEY (or OPENAI_API_KEY) is not set; "
            "every edit will be recorded as an LLM-unavailable error. "
            "Set a key, or use --provider local for the offline Ollama judge.",
            err=True,
        )
        return None
    return OpenRouterSyncJudge(
        model=llm_model or DEFAULT_SYNC_JUDGE_MODEL,
        timeout=_resolve_timeout(llm_timeout, _DEFAULT_TIMEOUT_OPENROUTER),
    )


def _resolve_recoverer(llm_recover: bool, recovery_model: str) -> AlignmentRecoverer | None:
    """The bounded-LLM alignment recoverer for ``--llm-recover``, or ``None``.

    ``None`` (the default, or a missing key) leaves an ambiguous drifted-id region
    untouched to re-surface next run — recovery is strictly opt-in and degrades
    gracefully when no OpenRouter key is configured (warning, not error).
    """
    if not llm_recover:
        return None
    if not has_openrouter_api_key():
        click.echo(
            "warning: --llm-recover needs OPENROUTER_API_KEY (or OPENAI_API_KEY); "
            "ambiguous id-migration regions will be left for review instead.",
            err=True,
        )
        return None
    return OpenRouterAlignmentRecoverer(model=recovery_model)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def _plan_exit_code(plan: SyncPlan) -> int:
    """Dry-run exit: 2 on a classifier error, 1 if anything would change, else 0."""
    if plan.has_errors:
        return 2
    if plan.proposals:
        return 1
    return 0


def _apply_exit_code(plan: SyncPlan, result: ApplyResult) -> int:
    """Apply exit: 2 on any error, 1 if anything was deferred, else 0.

    Mirrors :attr:`PlanWalkResult.exit_code` so batch and interactive runs share
    one definition of clean / needs-review / error.
    """
    if plan.has_errors or result.has_errors:
        return 2
    if result.deferred > 0:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Human output
# ---------------------------------------------------------------------------


def _issue_line(issue: PlanIssue) -> str:
    sid = f" {issue.slide_id}" if issue.slide_id else ""
    return f"issue-{issue.severity}{sid}: {issue.reason}"


def _outcome_line(result: ApplyResult) -> str:
    return (
        f"applied: {result.applied_edit} edit, {result.applied_remove} remove, "
        f"{result.applied_move} move, {result.applied_add} add, "
        f"{result.applied_rename} rename; {result.in_sync} already in sync; "
        f"{result.deferred} deferred; {len(result.errors)} error(s); "
        f"watermark {'advanced' if result.watermark_recorded else 'held'}."
    )


def _print_human(
    plan: SyncPlan,
    apply_result: ApplyResult | None,
    walk: PlanWalkResult | None,
    *,
    mode: str,
) -> None:
    if mode == "dry-run":
        click.echo(render_plan(plan))
        return

    assert apply_result is not None  # apply / interactive always produce a result
    if mode == "interactive":
        # The walker echoed each proposal block + decision during the walk; its
        # two-line summary reports decisions and outcomes without conflating them.
        click.echo("")
        for line in walk.summary() if walk is not None else []:
            click.echo(line)
    else:  # batch apply — show the full classified plan, then what was written
        click.echo(render_plan(plan))
        click.echo("")
        click.echo(_outcome_line(apply_result))

    for err in apply_result.errors:
        click.echo(f"  error: {err}")
    if apply_result.applied > 0:
        click.echo("Review the propagated changes with `git diff` before committing.")


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def _to_dict(
    plan: SyncPlan,
    apply_result: ApplyResult | None,
    walk: PlanWalkResult | None,
    mode: str,
    exit_code: int,
) -> dict:
    return {
        "de_path": str(plan.de_path),
        "en_path": str(plan.en_path),
        "mode": mode,
        "exit_code": exit_code,
        "plan": _plan_dict(plan),
        "apply": _apply_dict(apply_result) if apply_result is not None else None,
        "walker": _walker_dict(walk) if walk is not None else None,
    }


def _plan_dict(plan: SyncPlan) -> dict:
    return {
        "baseline_source": plan.baseline_source,
        "in_sync": plan.in_sync_count,
        "counts": {
            kind: plan.count(kind)
            for kind in ("add", "edit", "move", "remove", "conflict", "rename")
        },
        "proposals": [
            {
                "kind": p.kind,
                "role": p.role,
                "direction": p.direction,
                "slide_id": p.slide_id,
                "reason": p.reason,
                "translation_pending": p.translation_pending,
            }
            for p in plan.proposals
        ],
        "issues": [
            {"severity": i.severity, "slide_id": i.slide_id, "reason": i.reason}
            for i in plan.issues
        ],
    }


def _apply_dict(result: ApplyResult) -> dict:
    return {
        "applied": {
            "edit": result.applied_edit,
            "remove": result.applied_remove,
            "move": result.applied_move,
            "add": result.applied_add,
            "rename": result.applied_rename,
            "total": result.applied,
        },
        "in_sync": result.in_sync,
        "deferred": result.deferred,
        "watermark_recorded": result.watermark_recorded,
        "errors": list(result.errors),
    }


def _walker_dict(walk: PlanWalkResult) -> dict:
    return {
        "accepted": walk.accepted,
        "conflicts_resolved": walk.conflicts_resolved,
        "skipped": walk.skipped,
        "auto_applied": walk.auto_applied,
        "unvisited": walk.unvisited,
    }
