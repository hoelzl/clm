"""``clm slides sync autopilot`` — the legacy all-in-one command (epic #440).

This is the **agent-less human's escape hatch**: the one verb that drives the four
embedded model clients (the edit judge, the new-slide translator, the cold-pair
correspondence verifier, and the ``--llm-recover`` id recoverer). Decision (B) of the
agent-toolkit redesign is *relocate, not delete* — so the full pre-redesign
``clm slides sync`` behaviour (classify → tier-1 apply → judge tier-2 edits →
translate tier-2 adds → cold-pair verify → recover) lives here, isolated behind the
single ``autopilot`` verb, and **nothing else in the agent path imports a model
client**. The verb group registers this command *lazily* (`sync.py`'s
``lazy_subcommands``), so a plain ``import ...slides.sync`` — the agent path — never
loads this module or its OpenRouter/Ollama imports.

Everything model-free is shared from the sibling ``sync`` module (path resolution,
exit codes, output rendering, the batch sweep, ``--verify`` / ``--rebaseline``); only
the model-client construction (``_resolve_judge`` / ``_resolve_recoverer`` /
``_resolve_verifier`` / the translator) and this command live here.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from clm.cli.commands.slides.sync import (
    _SINCE_OPTION,
    CACHE_DB_NAME,
    _apply_exit_code,
    _apply_since,
    _auto_heal_stale_watermark,
    _cold_baseline_hint_text,
    _is_stale_but_consistent,
    _issue_line,
    _load_ledger_if,
    _maybe_record_ledger,
    _parse_baseline_from,
    _plan_exit_code,
    _print_human,
    _rebaseline_hint_text,
    _resolve_prog_lang,
    _resolve_single_path,
    _resolve_sync_pair,
    _run_batch,
    _run_rebaseline,
    _run_verify,
    _to_dict,
    _wrap_progress,
)
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
from clm.slides.glossary import GLOSSARY_STEM, resolve_guidance_by_lang
from clm.slides.sync_apply import ApplyResult, apply_plan
from clm.slides.sync_plan import SyncPlan, build_sync_plan, render_explain
from clm.slides.sync_plan_walker import PlanWalkResult, WalkerOptions, run_plan_walker
from clm.slides.sync_recover import (
    DEFAULT_RECOVERY_MODEL,
    OpenRouterAlignmentRecoverer,
    OpenRouterCorrespondenceVerifier,
)
from clm.slides.sync_translate import DEFAULT_TRANSLATION_MODEL, OpenRouterSlideTranslator

if TYPE_CHECKING:
    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier


def _resolve_sync_guidance(
    source_dir: Path,
    glossary_de: Path | None,
    glossary_en: Path | None,
    *,
    as_json: bool,
) -> dict[str, str]:
    """Resolve the per-target-language translation conventions for the add path.

    ``sync`` is bidirectional — a brand-new EN slide is translated to DE (using the
    DE conventions) and a brand-new DE slide to EN (using the EN conventions) in the
    same pass — so a glossary is resolved **per target language**: an explicit
    ``--glossary-de`` / ``--glossary-en``, else an auto-discovered
    ``clm-glossary.<lang>.md`` walking up from ``source_dir`` (a deck's directory,
    or the batch root). Returns the ``{target_lang: conventions_text}`` map the
    :class:`OpenRouterSlideTranslator` selects from; echoes which file supplied each
    (unless ``--json``). A language with no glossary is simply absent — that
    direction translates with no conventions, exactly as before this option.
    """
    guidance, used = resolve_guidance_by_lang(
        source_dir, explicit={"de": glossary_de, "en": glossary_en}
    )
    if not as_json:
        for lang in sorted(used):
            click.echo(f"Using glossary ({lang}): {used[lang]}", err=True)
    return guidance


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
    "--verify",
    is_flag=True,
    default=False,
    help=(
        "Structural safety check (no LLM, no watermark, writes nothing): confirm the "
        "DE/EN halves are a structurally valid split pair — byte-identical shared "
        "cells, matching slide_ids, header parity, clean alignment (it reuses unify) "
        "— and warn on any id'd cell dropped vs git HEAD. Answers 'did an edit corrupt "
        "the deck?', NOT 'is it in sync?' (use --dry-run) or 'is the translation "
        "good?' (a semantic call). Exit 0 = valid (warnings allowed), 2 = corrupt. "
        "Works on a single pair or a directory; pairs with --json."
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
    "--glossary-de",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Translation conventions (Markdown: a style note + term glossary) for "
        "German targets — a brand-new EN slide translated to DE on the add path. "
        f"Default: auto-discover '{GLOSSARY_STEM}.de.md' walking up from the deck."
    ),
)
@click.option(
    "--glossary-en",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Translation conventions (Markdown: a style note + term glossary) for "
        "English targets — a brand-new DE slide translated to EN on the add path. "
        f"Default: auto-discover '{GLOSSARY_STEM}.en.md' walking up from the deck."
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
    "--rebaseline",
    is_flag=True,
    default=False,
    help=(
        "Reset a STALE watermark. When the recorded watermark errors/conflicts but "
        "the pair's halves are already consistent against git HEAD (the usual cause: "
        "both halves edited + committed without an intervening sync), clear the "
        "watermark and re-record it from the current state — the safe fix for the "
        "'id-less localized cells edited on both decks' error. REFUSES if git HEAD "
        "shows real changes/divergence, so it cannot silently mask an un-synced edit. "
        "Writes the watermark; single-pair only; mutually exclusive with "
        "--dry-run / --explain / --no-cache."
    ),
)
@click.option(
    "--auto-heal/--no-auto-heal",
    "auto_heal",
    default=True,
    help=(
        "Auto-re-baseline a stale-but-consistent watermark on a WRITING run instead of "
        "erroring (#364): the same safe heal as --rebaseline (only when git HEAD shows "
        "the halves consistent), applied automatically before reconciling. On by "
        "default; ignored for --dry-run / --explain / --no-cache / --baseline / "
        "--baseline-from / --rebaseline."
    ),
)
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help=(
        "Diff against an explicit git ref (e.g. HEAD~1, a commit SHA, origin/master) "
        "instead of the watermark or HEAD. Use after you committed single-language "
        "edits before syncing: --baseline HEAD~1 diffs against the pre-edit commit so "
        "the edits are detected (the watermark/HEAD baseline would see the committed "
        "edits as already consistent). The watermark still advances on apply (unless "
        "--no-cache). Single-pair only; mutually exclusive with --rebaseline."
    ),
)
@click.option(
    "--baseline-from",
    "baseline_from_spec",
    default=None,
    metavar="PATH[@REF]",
    help=(
        "Diff a RENAMED deck against where its content used to live (epic #440). PATH "
        "is the deck's pre-rename DE or EN half (the old folder/stem); its sibling old "
        "half is derived by swapping the .de/.en tag. REF defaults to HEAD. Use when you "
        "renamed a topic folder / deck stem and the old location no longer exists on "
        "disk, so neither the watermark nor the HEAD baseline can find the pre-edit "
        "content. Single-pair only; mutually exclusive with --baseline / --rebaseline."
    ),
)
@_SINCE_OPTION
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
@click.option(
    "--ledger",
    is_flag=True,
    help=(
        "Use the per-slide consistency ledger (#448): **read** it to skip slides "
        "byte-stable since a recorded confirmation (no re-litigation) before syncing, "
        "**and** — on a fully clean pass (nothing deferred, the watermark fully "
        "advanced) — **record** the now-in-sync localized slides back to "
        "<topic>/.clm/sync-ledger.json (confirmed_by=autopilot, gated on structural "
        "verify). A pass with residue records nothing. Works over a directory (each "
        "pair uses its own topic ledger)."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def slides_sync_cmd(
    de_path: Path,
    en_path: Path | None,
    dry_run: bool,
    interactive: bool,
    explain: bool,
    verify: bool,
    provider: str,
    llm_model: str | None,
    ollama_url: str | None,
    llm_timeout: float | None,
    translation_model: str,
    glossary_de: Path | None,
    glossary_en: Path | None,
    llm_recover: bool,
    recovery_model: str,
    verify_cold_pairs: bool | None,
    cache_dir: Path | None,
    no_cache: bool,
    rebaseline: bool,
    auto_heal: bool,
    baseline_ref: str | None,
    baseline_from_spec: str | None,
    since_spec: str | None,
    no_env_file: bool,
    yes: bool,
    ledger: bool,
    as_json: bool,
) -> None:
    """Bring a split DE/EN deck pair into sync after editing one side.

    DE_PATH and EN_PATH are the two halves of a split-format deck
    (``<deck>.de.<ext>`` and ``<deck>.en.<ext>``). EN_PATH is **optional**: pass just
    one half (or the bilingual deck stem ``<deck>.py``) and the other half is
    derived from disk — ``clm slides sync slides_x.de.<ext>`` syncs the pair.

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

    ``--since DATE|REF`` resolves a timeframe to the baseline ref (sugar over
    --baseline) — the one-shot "reconcile everything I changed since Monday".
    """
    # --since (#446): reject its own conflicts BEFORE resolving so we never echo a
    # resolution we then reject; then fold it into baseline_ref so every existing
    # --baseline guard/behavior applies unchanged.
    if since_spec is not None and (rebaseline or verify):
        raise click.UsageError(
            "--since is mutually exclusive with --rebaseline / --verify (it pins a "
            "historical baseline; --rebaseline resets the watermark and --verify is a "
            "standalone structural check)."
        )
    baseline_ref = _apply_since(since_spec, baseline_ref, baseline_from_spec, de_path)
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
    if verify and (
        dry_run
        or explain
        or interactive
        or rebaseline
        or baseline_ref is not None
        or baseline_from_spec is not None
    ):
        raise click.UsageError(
            "--verify is a standalone read-only structural check; it is mutually "
            "exclusive with --dry-run / --explain / --interactive / --rebaseline / "
            "--baseline / --baseline-from (it uses no watermark, no baseline, and no "
            "LLM). Combine it only with --json."
        )
    if rebaseline and (dry_run or explain):
        raise click.UsageError(
            "--rebaseline writes the watermark; it is mutually exclusive with "
            "--dry-run / --explain (which write nothing)."
        )
    if rebaseline and no_cache:
        raise click.UsageError(
            "--rebaseline manages the watermark, so it cannot be combined with --no-cache."
        )
    if baseline_ref is not None and rebaseline:
        raise click.UsageError(
            "--baseline and --rebaseline are mutually exclusive: --rebaseline resets the "
            "watermark from git HEAD, while --baseline pins the diff to a specific ref."
        )
    if baseline_from_spec is not None and (rebaseline or baseline_ref is not None):
        raise click.UsageError(
            "--baseline-from is mutually exclusive with --baseline / --rebaseline: it "
            "pins the baseline to the deck's pre-rename location, while --baseline pins a "
            "ref and --rebaseline resets the watermark."
        )

    # --verify: a standalone, read-only structural check (no watermark, no LLM, no
    # env load). Handle it before any cache/provider/judge machinery — it shares
    # only the path-resolution surface with the sync modes. Always sys.exit()s.
    if verify:
        _run_verify(de_path, en_path, as_json=as_json)
        return  # _run_verify always sys.exit()s; this is just for the type-checker.

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
        if rebaseline:
            raise click.UsageError(
                "--rebaseline operates on a single deck pair; run it per deck "
                "(e.g. `clm slides sync --rebaseline <deck>.de.py`), not over a directory."
            )
        if baseline_ref is not None:
            raise click.UsageError(
                "--baseline operates on a single deck pair; run it per deck "
                "(e.g. `clm slides sync --baseline HEAD~1 <deck>.de.py`), not over a directory."
            )
        if baseline_from_spec is not None:
            raise click.UsageError(
                "--baseline-from operates on a single deck pair; run it per deck "
                "(e.g. `clm slides sync --baseline-from <old>.de.py <new>.de.py`), not over "
                "a directory."
            )
        batch_mode = "explain" if explain else ("dry-run" if dry_run else "apply")
        # One glossary resolution for the whole sweep (the translator is shared across
        # pairs), discovered from the batch root. Only an apply sweep translates, so a
        # dry-run / explain sweep neither reads a glossary nor echoes a "Using glossary"
        # line. A glossary buried below the root, beside a single deck, is not found by
        # this root-level walk — pass it explicitly, or sync that deck on its own.
        batch_guidance = (
            _resolve_sync_guidance(de_path, glossary_de, glossary_en, as_json=as_json)
            if batch_mode == "apply"
            else {}
        )
        _run_batch(
            de_path,
            mode=batch_mode,
            as_json=as_json,
            yes=yes,
            no_cache=no_cache,
            no_env_file=no_env_file,
            cache_dir=cache_dir,
            provider_available=provider_available,
            ledger=ledger,
            auto_heal=auto_heal,
            make_judge=lambda: _resolve_judge(provider, llm_model, ollama_url, llm_timeout),
            make_translator=lambda: OpenRouterSlideTranslator(
                model=translation_model,
                prog_lang=_resolve_prog_lang(de_path),
                guidance_by_lang=batch_guidance,
            ),
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

    # --baseline-from: parse the deck's pre-rename half spec into the (old_de, old_en,
    # ref) the engine reads its baseline from (epic #440). Done after the pairing guard
    # so a UsageError surfaces the same way a bad pair does.
    baseline_from = _parse_baseline_from(baseline_from_spec) if baseline_from_spec else None

    # --rebaseline: reset a stale watermark when the halves are already consistent
    # against git HEAD. Self-contained (own cache lifetime); always sys.exit()s. No
    # LLM is needed (a consistent pair has no proposals to reconcile), so this runs
    # before the env load / judge resolution below.
    if rebaseline:
        _run_rebaseline(
            de_path,
            en_path,
            cache_dir=cache_dir,
            provider_available=provider_available,
            as_json=as_json,
        )
        return  # _run_rebaseline always sys.exit()s; this is just for the type-checker.

    # Load the project .env before resolving the judge/translator, so keys kept
    # only in .env (the usual course-repo layout) are found. Without this, every
    # add defers and every edit errors as "LLM unavailable" even though the keys
    # exist on disk (the reported sync bug). Skipped for --dry-run / --explain (no
    # LLM) and when --no-env-file is given.
    if not no_env_file and not dry_run and not explain:
        from clm.cli.env_loading import load_env_files

        load_env_files(de_path.parent, en_path.parent)

    # Resolve per-target-language translation conventions (style + glossary) for the
    # bidirectional add path. Only the writing modes build a translator, so dry-run /
    # explain resolve nothing (and echo no "Using glossary" line).
    guidance_by_lang: dict[str, str] = {}
    if not dry_run and not explain:
        guidance_by_lang = _resolve_sync_guidance(
            de_path.parent, glossary_de, glossary_en, as_json=as_json
        )

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
    recorded_commit: str | None = None
    try:
        plan = build_sync_plan(
            de_path,
            en_path,
            watermark_cache=watermark_cache,
            provider_available=provider_available,
            baseline_ref=baseline_ref,
            baseline_from=baseline_from,
            # Auto-recover a rename on the pure-git read path (epic #440). The engine
            # gates this to the no-watermark / no-explicit-baseline case, so a normal
            # watermarked apply is unaffected.
            detect_rename=True,
            ledger=_load_ledger_if(ledger, de_path),
        )
        if ledger and plan.ledger_skipped:
            click.echo(
                f"ledger: skipped {plan.ledger_skipped} slide(s) trusted in-sync "
                "(byte-stable since their last recorded confirmation).",
                err=True,
            )
        # Read the commit the watermark was recorded at (Fix D) while the cache is
        # still open, so the stale-watermark hint can name the exact --baseline ref.
        if watermark_cache is not None:
            recorded_commit = watermark_cache.get_synced_commit(str(de_path), str(en_path))

        # Auto-heal a stale-but-consistent watermark on a WRITING run (#364): re-baseline
        # and re-plan so the reconcile proceeds cleanly instead of erroring on a false
        # stale-baseline conflict. Read-only modes (dry-run/explain) keep the hint
        # below; --rebaseline / --baseline / --no-cache opt out, as does --no-auto-heal.
        if (
            auto_heal
            and not dry_run
            and not explain
            and not no_cache
            and not rebaseline
            and baseline_ref is None
            and baseline_from is None
            and _auto_heal_stale_watermark(
                de_path,
                en_path,
                plan,
                watermark_cache=watermark_cache,
                provider_available=provider_available,
            )
        ):
            click.echo(
                f"note: re-baselined a stale watermark for {de_path.name} automatically "
                "(halves consistent against git HEAD). Pass --no-auto-heal to opt out.",
                err=True,
            )
            plan = build_sync_plan(
                de_path,
                en_path,
                watermark_cache=watermark_cache,
                provider_available=provider_available,
                baseline_ref=None,
                baseline_from=None,
                detect_rename=True,
                ledger=_load_ledger_if(ledger, de_path),
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
            translator = OpenRouterSlideTranslator(
                model=translation_model,
                prog_lang=_resolve_prog_lang(de_path),
                guidance_by_lang=guidance_by_lang,
            )
            run_judge, run_translator = _wrap_progress(judge, translator, enabled=True)
            recoverer = _resolve_recoverer(llm_recover, recovery_model)
            verifier = _resolve_verifier(verify_enabled)
            for issue in plan.issues:
                click.echo(_issue_line(issue))
            walk = run_plan_walker(
                plan,
                judge=run_judge,
                translator=run_translator,
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
            translator = OpenRouterSlideTranslator(
                model=translation_model,
                prog_lang=_resolve_prog_lang(de_path),
                guidance_by_lang=guidance_by_lang,
            )
            run_judge, run_translator = _wrap_progress(judge, translator, enabled=not as_json)
            recoverer = _resolve_recoverer(llm_recover, recovery_model)
            verifier = _resolve_verifier(verify_enabled)
            apply_result = apply_plan(
                plan,
                judge=run_judge,
                translator=run_translator,
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

    # #448 P2: after a fully-clean autopilot apply / interactive pass, record the
    # now-in-sync slides to the ledger (confirmed_by=autopilot) — mirrors `apply
    # --ledger` (#464) for the model-bearing path. `_maybe_record_ledger` re-gates on
    # structural verify and reads the post-apply working tree (no open cache needed);
    # dry-run / explain (apply_result None) record nothing.
    ledger_recorded: int | None = None
    if apply_result is not None:
        ledger_recorded = _maybe_record_ledger(
            ledger, plan, apply_result, de_path, en_path, confirmed_by="autopilot"
        )

    exit_code = (
        _plan_exit_code(plan) if apply_result is None else _apply_exit_code(plan, apply_result)
    )

    # Stale-watermark hint: the run did not come out clean against the recorded
    # watermark, yet the halves are consistent against git HEAD — the signature of a
    # baseline that fell behind (both halves edited + committed without a sync). Point
    # the user at the one-command fix rather than leaving them to diagnose it.
    rebaseline_hint = not no_cache and _is_stale_but_consistent(
        de_path, en_path, plan, provider_available=provider_available
    )
    # Cold-baseline hint (Fix D): no watermark for this pair, so the baseline was
    # the implicit git HEAD — and nothing changed. If the user committed
    # single-language edits before syncing, those edits already match HEAD and look
    # consistent. Point at --baseline so they can diff against the pre-edit commit.
    # Suppressed when the user explicitly chose a baseline (they already know how).
    cold_baseline_hint = (
        baseline_ref is None and plan.baseline_source == "git-head" and plan.is_noop
    )

    if mode == "explain":
        click.echo(explain_text)
    elif as_json:
        click.echo(
            json.dumps(
                _to_dict(
                    plan,
                    apply_result,
                    walk,
                    mode,
                    exit_code,
                    rebaseline_hint=rebaseline_hint,
                    cold_baseline_hint=cold_baseline_hint,
                    ledger_recorded=ledger_recorded,
                ),
                indent=2,
            )
        )
    else:
        _print_human(plan, apply_result, walk, mode=mode)

    if rebaseline_hint and not as_json:
        click.echo(_rebaseline_hint_text(de_path, recorded_commit), err=True)

    if cold_baseline_hint and not as_json:
        click.echo(_cold_baseline_hint_text(de_path), err=True)

    sys.exit(exit_code)
