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
from attrs import define

from clm.infrastructure.llm.cache import (
    SyncAlignmentCache,
    SyncCorrespondenceCache,
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
from clm.slides.pairing import (
    derive_split_pair_from_stem,
    derive_split_twin,
    find_split_slide_files_recursive,
    iter_split_pairs,
    order_split_pair,
    split_lang_tag,
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
from clm.slides.sync_recover import (
    DEFAULT_RECOVERY_MODEL,
    OpenRouterAlignmentRecoverer,
    OpenRouterCorrespondenceVerifier,
)
from clm.slides.sync_translate import DEFAULT_TRANSLATION_MODEL, OpenRouterSlideTranslator

if TYPE_CHECKING:
    from collections.abc import Callable

    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier
    from clm.slides.sync_translate import SlideTranslator

CACHE_DB_NAME = "clm-llm.sqlite"


def _resolve_sync_pair(de_path: Path, en_path: Path) -> tuple[Path, Path]:
    """Validate the two positional paths are the DE/EN halves of one split deck,
    auto-correcting a swapped order, and return them as ``(de, en)``.

    Guards the #162 footgun: a swapped, same-file, same-language, or cross-deck
    pair would otherwise sync silently — producing a divergent or no-op result
    on an error-free pass (the cross-deck-orphan fail-safe only runs on clean
    passes, so it does not catch this). Raises :class:`click.UsageError` on an
    invalid pair. The check is deliberately prefix-agnostic — ``sync`` reconciles
    whatever two halves it is given, independent of the build's topic-routing
    prefix; existence on disk is already enforced by ``click.Path(exists=True)``.
    """
    if de_path == en_path:
        raise click.UsageError(
            f"DE_PATH and EN_PATH are the same file ({de_path}); pass the two "
            "halves of a split deck — <deck>.de.py and <deck>.en.py."
        )
    de_tag, en_tag = split_lang_tag(de_path), split_lang_tag(en_path)
    if de_tag is None or en_tag is None:
        bad = de_path if de_tag is None else en_path
        raise click.UsageError(
            f"{bad} is not a split-format slide half. `clm slides sync` expects "
            "two paths named <deck>.de.py and <deck>.en.py "
            "(run `clm slides split <deck>.py` to produce them)."
        )
    if de_tag == en_tag:
        raise click.UsageError(
            f"both paths are the same language (.{de_tag}); pass one .de half and one .en half."
        )
    ordered = order_split_pair(de_path, en_path)
    if ordered is None:
        raise click.UsageError(
            f"{de_path.name} and {en_path.name} belong to different decks; "
            "pass the two halves of ONE deck (same name before the .de/.en tag)."
        )
    if ordered != (de_path, en_path):
        click.echo(
            f"note: arguments look swapped — treating {ordered[0].name} as the "
            f"DE half and {ordered[1].name} as the EN half.",
            err=True,
        )
    return ordered


def _resolve_single_path(de_path: Path, en_path: Path | None) -> tuple[Path, Path]:
    """Single-path contract: when EN_PATH is omitted, derive the second half from
    DE_PATH so the author can run ``clm slides sync <deck>.de.py``.

    DE_PATH may be **one half** (``<deck>.de.py`` / ``<deck>.en.py``) — the twin
    is derived from disk — or a **bilingual deck stem** (``<deck>.py``, no
    ``.de``/``.en`` tag) whose two halves both exist. Derivation is prefix-agnostic
    (so ``apis.de.py`` works) and the resolved pair is still funnelled through
    :func:`_resolve_sync_pair` for the #162 pairing guard. Raises
    :class:`click.UsageError` when the twin / halves are not found on disk — a
    missing twin is almost always a typo or an un-split deck, so we error clearly
    rather than invent a full translated half.
    """
    if en_path is not None:
        return de_path, en_path
    tag = split_lang_tag(de_path)
    if tag is not None:
        twin = derive_split_twin(de_path)
        if twin is None:
            if de_path.name.startswith("voiceover_"):
                raise click.UsageError(
                    f"{de_path.name} is a voiceover companion, not a deck half; "
                    f"`clm slides sync` reconciles slide decks (<deck>.de.py / "
                    f"<deck>.en.py), not their voiceover companions."
                )
            other = "EN" if tag == "de" else "DE"
            raise click.UsageError(
                f"no {other} twin found next to {de_path.name}; expected its sibling "
                f"split half on disk. Pass both halves explicitly, or run "
                f"`clm slides split` to produce the pair."
            )
        # Return already (de, en)-ordered so the pairing guard's swap note does not
        # fire on a single derived path — the author supplied one path; nothing was
        # "swapped". (derive_split_twin gives the OTHER half, so order by our tag.)
        return (de_path, twin) if tag == "de" else (twin, de_path)
    # No language tag → treat DE_PATH as a bilingual deck stem and derive both halves.
    pair = derive_split_pair_from_stem(de_path)
    if pair is None:
        ext = de_path.suffix
        stem = de_path.name[: -len(ext)] if ext else de_path.name
        raise click.UsageError(
            f"{de_path.name} is neither a split half (<deck>.de.py / <deck>.en.py) "
            f"nor a deck stem with both halves present (expected {stem}.de{ext} and "
            f"{stem}.en{ext} on disk). Pass the two halves explicitly."
        )
    return pair


@click.command("sync")
@click.argument(
    "de_path",
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
@click.argument(
    "en_path",
    required=False,
    default=None,
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
    "--verify-cold-pairs/--no-verify-cold-pairs",
    "verify_cold_pairs",
    default=None,
    help=(
        "Bootstrap a never-id'd split pair by minting shared slide_ids — and reconcile "
        "a committed pair whose halves gave one slide divergent ids (#228) — but only "
        "after a cheap LLM (Haiku, via OpenRouter) confirms the two halves actually "
        "correspond (#216). Default: on when $OPENROUTER_API_KEY (or $OPENAI_API_KEY) "
        "is set. With no provider, or --no-verify-cold-pairs, such a pair is refused "
        "(sync one direction at a time, or run `clm slides assign-ids`)."
    ),
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
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    default=False,
    help=(
        "Batch (DIR) only: confirm a writing run over a whole directory without "
        "the interactive prompt. A directory apply writes to every pair under the "
        "tree, so it is gated; --dry-run / --explain batches run freely. Ignored "
        "for a single pair."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def slides_sync_cmd(
    de_path: Path,
    en_path: Path | None,
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
    verify_cold_pairs: bool | None,
    cache_dir: Path | None,
    no_cache: bool,
    no_env_file: bool,
    yes: bool,
    as_json: bool,
) -> None:
    """Bring a split DE/EN deck pair into sync after editing one side.

    DE_PATH and EN_PATH are the two halves of a split-format deck
    (``<deck>.de.py`` and ``<deck>.en.py``). EN_PATH is **optional**: pass just
    one half (or the bilingual deck stem ``<deck>.py``) and the other half is
    derived from disk — ``clm slides sync slides_x.de.py`` syncs the pair.

    \b
    DE_PATH may also be a **directory** — batch mode: every ``.de``/``.en`` deck
    pair under the tree is synced in one pass (prefix-agnostic, so un-prefixed
    decks count too). A half with no twin under the tree is skipped with a
    warning. The run continues past a failing pair and the exit code is the
    worst over all pairs (0 clean < 1 review < 2 error). A *writing* directory
    run needs --yes (or an interactive confirm); --dry-run / --explain run
    freely. --interactive is single-pair only.

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

    # Cold-start minting (#216 §12): a verifier is on by default when a provider is
    # configured; --no-verify-cold-pairs forces it off (cold pairs then refuse).
    # ``provider_available`` is a plan-time fact (identical in dry-run and apply), so
    # the two agree on whether a cold pair is a `pending` mint candidate or a `refuse`.
    verify_enabled = verify_cold_pairs is not False
    provider_available = verify_enabled and has_openrouter_api_key()

    # Batch mode: a directory triggers a sweep over every split pair under the
    # tree (one funnel — no separate `sync-all` subcommand). Branch here, before
    # the single-path / pairing-guard resolution (which both assume a file).
    if de_path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{de_path} is a directory (batch mode), which takes a single "
                "directory argument; do not pass a second path."
            )
        if interactive:
            raise click.UsageError(
                "--interactive cannot be combined with a directory (it walks one "
                "pair's proposals). Sync a single deck/half interactively, or use "
                "--dry-run / --explain over the directory."
            )
        batch_mode = "explain" if explain else ("dry-run" if dry_run else "apply")
        _run_batch(
            de_path,
            mode=batch_mode,
            as_json=as_json,
            yes=yes,
            no_cache=no_cache,
            no_env_file=no_env_file,
            cache_dir=cache_dir,
            provider_available=provider_available,
            make_judge=lambda: _resolve_judge(provider, llm_model, ollama_url, llm_timeout),
            make_translator=lambda: OpenRouterSlideTranslator(model=translation_model),
            make_recoverer=lambda: _resolve_recoverer(llm_recover, recovery_model),
            make_verifier=lambda: _resolve_verifier(verify_enabled),
        )
        return  # _run_batch always sys.exit()s; this is just for the type-checker.

    # Single-path contract: when EN_PATH is omitted, derive the twin (or both
    # halves from a deck stem) from disk before anything else.
    de_path, en_path = _resolve_single_path(de_path, en_path)

    # Pairing guard (#162 Tier-2): reject a same-file / same-language / cross-deck
    # pair and auto-correct a swapped (en, de) order before anything reads or
    # writes. Runs for every mode (incl. --dry-run/--explain) so the footgun is
    # caught even on read-only passes.
    de_path, en_path = _resolve_sync_pair(de_path, en_path)

    # Canonicalize to absolute, resolved paths. The watermark is keyed by the
    # (de_path, en_path) *strings*, so the single-path surface must key by the
    # same form the directory-batch surface does (its enumerator resolves every
    # file) — otherwise the SAME pair acquires two watermark keys across surfaces
    # (a relative single-path run vs. a resolved batch run) and the second silently
    # misses the first's watermark and re-baselines off git HEAD.
    de_path, en_path = de_path.resolve(), en_path.resolve()

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
    correspondence_cache: SyncCorrespondenceCache | None = None
    if not no_cache:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
        if llm_recover and not dry_run:
            alignment_cache = SyncAlignmentCache(cache_root / CACHE_DB_NAME)
        if provider_available and not dry_run:
            correspondence_cache = SyncCorrespondenceCache(cache_root / CACHE_DB_NAME)

    plan: SyncPlan
    apply_result: ApplyResult | None = None
    walk: PlanWalkResult | None = None
    explain_text: str | None = None
    try:
        plan = build_sync_plan(
            de_path, en_path, watermark_cache=watermark_cache, provider_available=provider_available
        )

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
            verifier = _resolve_verifier(verify_enabled)
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
                verifier=verifier,
                correspondence_cache=correspondence_cache,
            )
            apply_result = walk.apply_result
        else:
            mode = "apply"
            judge = _resolve_judge(provider, llm_model, ollama_url, llm_timeout)
            translator = OpenRouterSlideTranslator(model=translation_model)
            recoverer = _resolve_recoverer(llm_recover, recovery_model)
            verifier = _resolve_verifier(verify_enabled)
            apply_result = apply_plan(
                plan,
                judge=judge,
                translator=translator,
                watermark_cache=watermark_cache,
                recoverer=recoverer,
                alignment_cache=alignment_cache,
                verifier=verifier,
                correspondence_cache=correspondence_cache,
            )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()
        if alignment_cache is not None:
            alignment_cache.close()
        if correspondence_cache is not None:
            correspondence_cache.close()

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


# ---------------------------------------------------------------------------
# Batch mode (`clm slides sync DIR`)
#
# A directory sweeps every split deck pair under the tree in one pass, sharing a
# single watermark cache + judge/translator/recoverer. Continue-on-error: a pair
# that raises is recorded as errored (exit 2) and the sweep proceeds; the process
# exit is the worst per-pair code. A writing sweep is gated behind --yes (or an
# interactive confirm); --dry-run / --explain run freely.
# ---------------------------------------------------------------------------


@define
class _PairResult:
    """The outcome of syncing one deck pair inside a directory batch.

    ``plan``/``apply_result`` are ``None`` only when the pair raised before
    producing them (``error`` set) — every clean/review pair carries a plan.
    """

    de_path: Path
    en_path: Path
    plan: SyncPlan | None
    apply_result: ApplyResult | None
    explain_text: str | None
    exit_code: int
    error: str | None


def _run_batch(
    root: Path,
    *,
    mode: str,
    as_json: bool,
    yes: bool,
    no_cache: bool,
    no_env_file: bool,
    cache_dir: Path | None,
    provider_available: bool,
    make_judge: Callable[[], SyncJudge | None],
    make_translator: Callable[[], SlideTranslator],
    make_recoverer: Callable[[], AlignmentRecoverer | None],
    make_verifier: Callable[[], CorrespondenceVerifier | None],
) -> None:
    """Sync every split deck pair under ``root`` in one pass, then ``sys.exit`` the
    worst per-pair exit code (0 clean < 1 review < 2 error)."""
    pairs, solos = iter_split_pairs(find_split_slide_files_recursive(root))
    for solo in solos:
        tag = split_lang_tag(solo)
        other = "EN" if tag == "de" else "DE"
        click.echo(
            f"warning: skipping {solo.name} — no {other} twin found under {root}.",
            err=True,
        )
    if not pairs:
        if as_json:
            click.echo(
                json.dumps({"mode": mode, "root": str(root), "exit_code": 0, "pairs": []}, indent=2)
            )
        else:
            click.echo(f"no split-format deck pairs found under {root}.")
        sys.exit(0)

    writing = mode == "apply"
    if writing and not yes:
        # A directory apply writes to every pair under the tree — gate it. With
        # --json there is no usable prompt, so require the explicit flag.
        if as_json:
            raise click.UsageError(
                f"a writing batch over {len(pairs)} pair(s) needs --yes (cannot prompt "
                "with --json); add --yes, or preview with --dry-run."
            )
        click.confirm(
            f"About to sync {len(pairs)} deck pair(s) under {root} — this writes to "
            "the working tree. Continue?",
            abort=True,
        )

    # One env load + one cache + one judge/translator/recoverer for the whole sweep.
    # Discover .env from the root AND from each deck's own directory (like the
    # single-pair path), so a project .env at the root and a nested .env above a
    # deck buried below it are both found — load_env_files de-dups by file, and
    # root-first keeps a top-level project .env authoritative.
    if writing and not no_env_file:
        from clm.cli.env_loading import load_env_files

        deck_dirs = [d for de_p, en_p in pairs for d in (de_p.parent, en_p.parent)]
        load_env_files(root, *deck_dirs)

    cache_root: Path | None = None
    watermark_cache: SyncWatermarkCache | None = None
    alignment_cache: SyncAlignmentCache | None = None
    correspondence_cache: SyncCorrespondenceCache | None = None
    if not no_cache:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)

    judge: SyncJudge | None = None
    translator: SlideTranslator | None = None
    recoverer: AlignmentRecoverer | None = None
    verifier: CorrespondenceVerifier | None = None
    if writing:
        judge = make_judge()
        translator = make_translator()
        recoverer = make_recoverer()
        verifier = make_verifier()
        if recoverer is not None and cache_root is not None:
            alignment_cache = SyncAlignmentCache(cache_root / CACHE_DB_NAME)
        if verifier is not None and cache_root is not None:
            correspondence_cache = SyncCorrespondenceCache(cache_root / CACHE_DB_NAME)

    results: list[_PairResult] = []
    try:
        for de_path, en_path in pairs:
            results.append(
                _sync_one_pair(
                    de_path,
                    en_path,
                    mode=mode,
                    watermark_cache=watermark_cache,
                    alignment_cache=alignment_cache,
                    judge=judge,
                    translator=translator,
                    recoverer=recoverer,
                    provider_available=provider_available,
                    verifier=verifier,
                    correspondence_cache=correspondence_cache,
                )
            )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()
        if alignment_cache is not None:
            alignment_cache.close()
        if correspondence_cache is not None:
            correspondence_cache.close()

    exit_code = max((r.exit_code for r in results), default=0)
    _emit_batch(root, mode, results, exit_code, as_json=as_json)
    sys.exit(exit_code)


def _sync_one_pair(
    de_path: Path,
    en_path: Path,
    *,
    mode: str,
    watermark_cache: SyncWatermarkCache | None,
    alignment_cache: SyncAlignmentCache | None,
    judge: SyncJudge | None,
    translator: SlideTranslator | None,
    recoverer: AlignmentRecoverer | None,
    provider_available: bool,
    verifier: CorrespondenceVerifier | None,
    correspondence_cache: SyncCorrespondenceCache | None,
) -> _PairResult:
    """Sync one pair for the batch, catching any failure so the sweep continues."""
    try:
        plan = build_sync_plan(
            de_path, en_path, watermark_cache=watermark_cache, provider_available=provider_available
        )
        apply_result: ApplyResult | None = None
        explain_text: str | None = None
        if mode == "explain":
            explain_text = render_explain(
                de_path, en_path, plan=plan, watermark_cache=watermark_cache
            )
        elif mode == "apply":
            apply_result = apply_plan(
                plan,
                judge=judge,
                translator=translator,
                watermark_cache=watermark_cache,
                recoverer=recoverer,
                alignment_cache=alignment_cache,
                verifier=verifier,
                correspondence_cache=correspondence_cache,
            )
        # else dry-run: classify only, write nothing.
        exit_code = (
            _plan_exit_code(plan) if apply_result is None else _apply_exit_code(plan, apply_result)
        )
        return _PairResult(de_path, en_path, plan, apply_result, explain_text, exit_code, None)
    except Exception as exc:  # continue-on-error: one bad pair must not abort the sweep
        return _PairResult(de_path, en_path, None, None, None, 2, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Batch output (human one-liners + rollup; JSON envelope)
# ---------------------------------------------------------------------------

_BATCH_STATUS = {0: "OK    ", 1: "REVIEW", 2: "ERROR "}


def _deck_label(de_path: Path, root: Path) -> str:
    """The DE half's path relative to the batch root (bare name if not under it)."""
    try:
        return str(de_path.resolve().relative_to(root.resolve()))
    except ValueError:
        return de_path.name


def _counts_str(plan: SyncPlan) -> str:
    kinds = (
        "add",
        "edit",
        "retag",
        "move",
        "remove",
        "conflict",
        "rename",
        "refuse",
        "mint",
        "adopt",
        "reconcile",
    )
    parts = [f"{plan.count(k)} {k}" for k in kinds if plan.count(k)]
    return ", ".join(parts) if parts else "0"


def _batch_pair_detail(r: _PairResult) -> str:
    if r.apply_result is None:  # dry-run / explain — describe the plan
        plan = r.plan
        assert plan is not None  # a non-error pair always carries a plan
        if plan.has_errors:
            return f"{_counts_str(plan)} (classifier error)"
        if plan.proposals:
            return f"would change: {_counts_str(plan)}"
        return "nothing to do"
    res = r.apply_result
    parts: list[str] = []
    if res.applied:
        parts.append(f"applied {res.applied}")
    if res.deferred:
        parts.append(f"{res.deferred} deferred")
    if res.errors:
        parts.append(f"{len(res.errors)} error(s)")
    return ", ".join(parts) if parts else "in sync"


def _batch_pair_line(r: _PairResult, root: Path) -> str:
    label = _deck_label(r.de_path, root)
    if r.error is not None:
        return f"{_BATCH_STATUS[2]} {label}: {r.error}"
    return f"{_BATCH_STATUS[r.exit_code]} {label}: {_batch_pair_detail(r)}"


def _batch_rollup(results: list[_PairResult]) -> str:
    clean = sum(1 for r in results if r.exit_code == 0)
    review = sum(1 for r in results if r.exit_code == 1)
    errored = sum(1 for r in results if r.exit_code == 2)
    return f"{len(results)} pair(s): {clean} clean, {review} review, {errored} errored."


def _emit_batch(
    root: Path,
    mode: str,
    results: list[_PairResult],
    exit_code: int,
    *,
    as_json: bool,
) -> None:
    if as_json:
        click.echo(json.dumps(_batch_to_dict(root, mode, results, exit_code), indent=2))
        return
    if mode == "explain":
        for r in results:
            click.echo("")
            click.echo(f"=== {_deck_label(r.de_path, root)} ===")
            click.echo(f"  error: {r.error}" if r.error is not None else r.explain_text)
        click.echo("")
        click.echo(_batch_rollup(results))
        return
    # dry-run / apply: a scannable one-liner per pair, then the rollup.
    for r in results:
        click.echo(_batch_pair_line(r, root))
    # Surface each pair's apply-time errors in full (the one-liner only counts them).
    for r in results:
        if r.apply_result is not None and r.apply_result.errors:
            label = _deck_label(r.de_path, root)
            for err in r.apply_result.errors:
                click.echo(f"  {label}: error: {err}")
    click.echo("")
    click.echo(_batch_rollup(results))
    if mode == "apply" and any(
        r.apply_result is not None and r.apply_result.applied for r in results
    ):
        click.echo("Review the propagated changes with `git diff` before committing.")


def _batch_to_dict(root: Path, mode: str, results: list[_PairResult], exit_code: int) -> dict:
    return {
        "mode": mode,
        "root": str(root),
        "exit_code": exit_code,
        "pairs": [_batch_pair_dict(r, mode) for r in results],
    }


def _batch_pair_dict(r: _PairResult, mode: str) -> dict:
    if r.error is not None:
        return {
            "de_path": str(r.de_path),
            "en_path": str(r.en_path),
            "mode": mode,
            "exit_code": r.exit_code,
            "error": r.error,
        }
    assert r.plan is not None
    # Each non-errored pair reuses the single-pair object shape verbatim, so a
    # consumer can treat ``pairs[i]`` exactly like one ``clm slides sync --json``.
    return _to_dict(r.plan, r.apply_result, None, mode, r.exit_code)


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


def _resolve_verifier(verify_enabled: bool) -> CorrespondenceVerifier | None:
    """The cold-start correspondence verifier (#216 §12), or ``None``.

    ``None`` (—-no-verify-cold-pairs, or no OpenRouter key) makes a cold both-id-less
    pair **refuse** instead of mint — and the plan already showed it as `refuse`,
    because ``provider_available`` folds in the same two checks, so dry-run and apply
    agree. On by default when a key is configured.
    """
    if not verify_enabled or not has_openrouter_api_key():
        return None
    return OpenRouterCorrespondenceVerifier()


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
        f"applied: {result.applied_edit} edit, {result.applied_retag} retag, "
        f"{result.applied_remove} remove, "
        f"{result.applied_move} move, {result.applied_add} add, "
        f"{result.applied_rename} rename, {result.applied_mint} mint, "
        f"{result.applied_adopt} adopt, {result.applied_reconcile} reconcile; "
        f"{result.in_sync} already in sync; "
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
            for kind in (
                "add",
                "edit",
                "retag",
                "move",
                "remove",
                "conflict",
                "rename",
                "refuse",
                "mint",
                "adopt",
                "reconcile",
            )
        },
        "proposals": [
            {
                "kind": p.kind,
                "role": p.role,
                "direction": p.direction,
                "slide_id": p.slide_id,
                "reason": p.reason,
                "translation_pending": p.translation_pending,
                "disposition": p.disposition,
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
            "retag": result.applied_retag,
            "remove": result.applied_remove,
            "move": result.applied_move,
            "add": result.applied_add,
            "rename": result.applied_rename,
            "mint": result.applied_mint,
            "adopt": result.applied_adopt,
            "reconcile": result.applied_reconcile,
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
