"""``clm slides sync`` — single-language authoring sync for split decks.

Issue #166. After an author edits **one** half of a split-format deck pair
(``<deck>.de.<ext>`` / ``<deck>.en.<ext>``, the layout produced by
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
from clm.slides.glossary import GLOSSARY_STEM, resolve_guidance_by_lang
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
from clm.slides.sync_verify import VerifyResult, verify_pair

if TYPE_CHECKING:
    from collections.abc import Callable

    from clm.infrastructure.llm.ollama_client import SyncJudge, SyncProposal
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier
    from clm.slides.sync_translate import SlideTranslator

CACHE_DB_NAME = "clm-llm.sqlite"


def _resolve_prog_lang(path: Path) -> str:
    """The deck's programming language for the translator prompt.

    Resolves from a file's extension, or (batch mode) from the first deck found
    under a directory. Falls back to ``"python"`` when unresolvable — the prompt
    descriptors then degrade gracefully.
    """
    from clm.core.topic_resolver import find_slide_files_recursive
    from clm.infrastructure.utils.path_utils import path_to_prog_lang

    try:
        target = path
        if path.is_dir():
            decks = find_slide_files_recursive(path)
            if not decks:
                return "python"
            target = decks[0]
        return path_to_prog_lang(target)
    except (KeyError, ValueError):
        return "python"


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
            "halves of a split deck — <deck>.de.<ext> and <deck>.en.<ext>."
        )
    de_tag, en_tag = split_lang_tag(de_path), split_lang_tag(en_path)
    if de_tag is None or en_tag is None:
        bad = de_path if de_tag is None else en_path
        raise click.UsageError(
            f"{bad} is not a split-format slide half. `clm slides sync` expects "
            "two paths named <deck>.de.<ext> and <deck>.en.<ext> "
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
    DE_PATH so the author can run ``clm slides sync <deck>.de.<ext>``.

    DE_PATH may be **one half** (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``) — the twin
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
                    f"`clm slides sync` reconciles slide decks (<deck>.de.<ext> / "
                    f"<deck>.en.<ext>), not their voiceover companions."
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
            f"{de_path.name} is neither a split half (<deck>.de.<ext> / <deck>.en.<ext>) "
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
    baseline_ref: str | None,
    no_env_file: bool,
    yes: bool,
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
    if verify and (dry_run or explain or interactive or rebaseline or baseline_ref is not None):
        raise click.UsageError(
            "--verify is a standalone read-only structural check; it is mutually "
            "exclusive with --dry-run / --explain / --interactive / --rebaseline / "
            "--baseline (it uses no watermark, no baseline, and no LLM). Combine it "
            "only with --json."
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
        )
        # Read the commit the watermark was recorded at (Fix D) while the cache is
        # still open, so the stale-watermark hint can name the exact --baseline ref.
        if watermark_cache is not None:
            recorded_commit = watermark_cache.get_synced_commit(str(de_path), str(en_path))

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
        # Per-LLM-call progress ticks (stderr) so a long writing sweep shows what it
        # is working on; suppressed under --json (stdout must stay pure JSON).
        judge, translator = _wrap_progress(judge, translator, enabled=not as_json)
        recoverer = make_recoverer()
        verifier = make_verifier()
        if recoverer is not None and cache_root is not None:
            alignment_cache = SyncAlignmentCache(cache_root / CACHE_DB_NAME)
        if verifier is not None and cache_root is not None:
            correspondence_cache = SyncCorrespondenceCache(cache_root / CACHE_DB_NAME)

    results: list[_PairResult] = []
    try:
        for i, (de_path, en_path) in enumerate(pairs, 1):
            # Progress header (stderr) so a multi-pair sweep shows which pair is in
            # flight — the per-pair result lines are printed together at the end.
            if not as_json:
                click.echo(f"[{i}/{len(pairs)}] {_deck_label(de_path, root)} …", err=True)
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
    # Surface each pair's cold-start deferral detail (#231) and apply-time
    # errors in full (the one-liner only counts them).
    for r in results:
        if r.apply_result is not None and r.apply_result.cold_deferrals:
            label = _deck_label(r.de_path, root)
            click.echo(f"  {label}:")
            for line in _cold_deferral_lines(r.apply_result, indent="    "):
                click.echo(line)
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


def _progress_snippet(text: str, limit: int = 44) -> str:
    """A short, scannable handle for a cell being reconciled / translated."""
    for raw in text.split("\n"):
        line = raw.strip()
        for pre in ("# ", "// "):
            if line.startswith(pre):
                line = line[len(pre) :].strip()
                break
        if line and line not in ("#", "//"):
            return line[:limit] + ("…" if len(line) > limit else "")
    return "a cell"


class _ProgressJudge:
    """A :class:`SyncJudge` wrapper that emits a stderr tick before each LLM call.

    The judge / translator calls are the slow part of a sync (each is a hosted-LLM
    round-trip), so a tick per call shows the command is alive and what it is
    working on. Pure pass-through otherwise; ``None`` judges are never wrapped.
    """

    def __init__(self, inner: SyncJudge, echo: Callable[[str], None]) -> None:
        self.inner = inner
        self.echo = echo
        self.prompt_version = inner.prompt_version  # protocol member (writable)

    def propose(
        self, source_text: str, target_text: str, *, source_lang: str, target_lang: str
    ) -> SyncProposal:
        self.echo(f"  · reconciling {_progress_snippet(source_text)} …")
        return self.inner.propose(
            source_text, target_text, source_lang=source_lang, target_lang=target_lang
        )


class _ProgressTranslator:
    """A :class:`SlideTranslator` wrapper that emits a stderr tick before each call."""

    def __init__(self, inner: SlideTranslator, echo: Callable[[str], None]) -> None:
        self.inner = inner
        self.echo = echo
        self.prompt_version = inner.prompt_version  # protocol member (writable)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.echo(f"  · translating {_progress_snippet(source_body)} …")
        return self.inner.translate(
            source_body=source_body,
            source_lang=source_lang,
            target_lang=target_lang,
            role=role,
        )


def _wrap_progress(
    judge: SyncJudge | None, translator: SlideTranslator | None, *, enabled: bool
) -> tuple[SyncJudge | None, SlideTranslator | None]:
    """Wrap the judge/translator so each LLM call prints a progress tick to stderr.

    No-op when ``enabled`` is False (e.g. ``--json``, where stderr ticks would not
    matter and stdout must stay pure JSON) or when the backend is unavailable
    (``None``). Wrapping is transparent — the wrappers satisfy the same protocols.
    """
    if not enabled:
        return judge, translator

    def echo(msg: str) -> None:
        click.echo(msg, err=True)

    return (
        _ProgressJudge(judge, echo) if judge is not None else None,
        _ProgressTranslator(translator, echo) if translator is not None else None,
    )


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
# Rebaseline (reset a stale watermark) — Issue #364
# ---------------------------------------------------------------------------


def _githead_plan(de_path: Path, en_path: Path, *, provider_available: bool) -> SyncPlan:
    """Classify the pair against the git-HEAD baseline (ignoring any watermark).

    Passing ``watermark_cache=None`` forces ``build_sync_plan`` down its git-HEAD
    fallback, so the result reflects only what changed since each half's commit — the
    same baseline a cold ``sync`` (or ``--no-cache``) uses.
    """
    return build_sync_plan(
        de_path, en_path, watermark_cache=None, provider_available=provider_available
    )


def _is_stale_but_consistent(
    de_path: Path, en_path: Path, plan: SyncPlan, *, provider_available: bool
) -> bool:
    """True when the watermark run was non-trivial but git HEAD shows the pair clean.

    That combination — a watermark baseline that errors/conflicts, while the halves
    are consistent against git HEAD — is the signature of a stale watermark (the
    baseline fell behind a both-halves edit committed without a sync). Only the
    watermark baseline is interrogated (``baseline_source == "watermark"``); a run
    that already fell back to git HEAD has no watermark to be stale.
    """
    if plan.baseline_source != "watermark" or plan.is_noop:
        return False
    return _githead_plan(de_path, en_path, provider_available=provider_available).is_noop


def _rebaseline_hint_text(de_path: Path, recorded_commit: str | None = None) -> str:
    # When we know the commit the watermark was recorded at, name it as a precise
    # --baseline target (diff against the last synced point) alongside --rebaseline.
    baseline_ref = recorded_commit[:12] if recorded_commit else "<last-synced-commit>"
    return (
        "note: this deck's halves are consistent against git HEAD, but its recorded "
        "watermark is stale (the usual cause: both halves edited + committed without an "
        "intervening sync). Reset it with "
        f"`clm slides sync --rebaseline {de_path.name}` (refuses if HEAD shows real "
        "changes), diff against the last sync with "
        f"`clm slides sync --baseline {baseline_ref} {de_path.name}`, "
        "or `clm slides watermark clear`."
    )


def _cold_baseline_hint_text(de_path: Path) -> str:
    return (
        "note: no watermark recorded for this deck, so the baseline was git HEAD and "
        "nothing changed against it. If you committed single-language edits before "
        "syncing, they already match HEAD and read as consistent — diff against the "
        f"pre-edit commit with `clm slides sync --baseline HEAD~1 {de_path.name}`."
    )


def _run_rebaseline(
    de_path: Path,
    en_path: Path,
    *,
    cache_dir: Path | None,
    provider_available: bool,
    as_json: bool,
) -> None:
    """Reset a stale watermark, or refuse when git HEAD shows real changes.

    Safe by construction: the watermark is re-recorded only when the git-HEAD plan is
    a no-op (the halves are mutually consistent at their committed state), so nothing
    that still needs syncing is masked. A non-no-op git-HEAD plan is refused with the
    pending changes named — the divergence must be resolved (a normal ``sync``) first.
    Always ``sys.exit``s.
    """
    cache_root = resolve_cache_dir(cli_override=cache_dir)
    watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
    try:
        githead = _githead_plan(de_path, en_path, provider_available=provider_available)
        if not githead.is_noop:
            _emit_rebaseline_refused(de_path, githead, as_json=as_json)
            sys.exit(2)
        had = watermark_cache.has_pair(str(de_path), str(en_path))
        removed = watermark_cache.clear_pair(str(de_path), str(en_path)) if had else 0
        # Re-record the watermark from the current (consistent) state. A no-op plan has
        # no proposals, so no judge/translator is needed; apply still writes the fresh
        # whole-deck watermark.
        result = apply_plan(githead, judge=None, watermark_cache=watermark_cache)
    finally:
        watermark_cache.close()
    _emit_rebaseline_done(de_path, removed, result, as_json=as_json)
    sys.exit(0)


def _emit_rebaseline_refused(de_path: Path, githead: SyncPlan, *, as_json: bool) -> None:
    reason = (
        "git HEAD carries classifier errors"
        if githead.has_errors
        else f"git HEAD shows pending changes ({_counts_str(githead)})"
    )
    if as_json:
        click.echo(
            json.dumps(
                {
                    "de_path": str(de_path),
                    "mode": "rebaseline",
                    "exit_code": 2,
                    "rebaselined": False,
                    "reason": reason,
                },
                indent=2,
            )
        )
        return
    click.echo(
        f"refusing to --rebaseline {de_path.name}: {reason}. The halves are not "
        "consistent at their committed state, so re-baselining could mask an un-synced "
        "edit — resolve it with a normal `clm slides sync` first."
    )


def _emit_rebaseline_done(
    de_path: Path, removed: int, result: ApplyResult, *, as_json: bool
) -> None:
    if as_json:
        click.echo(
            json.dumps(
                {
                    "de_path": str(de_path),
                    "mode": "rebaseline",
                    "exit_code": 0,
                    "rebaselined": True,
                    "rows_cleared": removed,
                    "watermark_recorded": result.watermark_recorded,
                },
                indent=2,
            )
        )
        return
    if removed:
        click.echo(f"cleared {removed} stale watermark row(s) for {de_path.name}.")
    else:
        click.echo(f"{de_path.name} had no recorded watermark.")
    click.echo(
        "re-baselined off git HEAD (halves consistent); "
        f"watermark {'recorded' if result.watermark_recorded else 'not recorded'}."
    )


# ---------------------------------------------------------------------------
# Verify mode (`clm slides sync --verify`)
#
# A read-only structural check: confirm a pair is a valid split (reuses unify)
# and warn on id'd cells dropped vs git HEAD. No watermark, no LLM, no write.
# Exit 0 = all pairs valid (warnings allowed), 2 = any structural corruption.
# ---------------------------------------------------------------------------


def _run_verify(de_path: Path, en_path: Path | None, *, as_json: bool) -> None:
    """Structurally verify a pair or a directory tree, then ``sys.exit``.

    Single pair: resolve the twin / pairing exactly as the sync modes do, then
    verify. Directory: sweep every split pair under the tree (a half with no twin
    is skipped with a warning). Exit is the worst per-pair code (0 valid < 2
    corrupt); warnings never fail the gate. Always ``sys.exit``s.
    """
    root: Path | None
    if de_path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{de_path} is a directory (batch verify), which takes a single "
                "directory argument; do not pass a second path."
            )
        pairs, solos = iter_split_pairs(find_split_slide_files_recursive(de_path))
        for solo in solos:
            tag = split_lang_tag(solo)
            other = "EN" if tag == "de" else "DE"
            click.echo(
                f"warning: skipping {solo.name} — no {other} twin found under {de_path}.",
                err=True,
            )
        results = [verify_pair(de_p.resolve(), en_p.resolve()) for de_p, en_p in pairs]
        root = de_path
    else:
        de_resolved, en_resolved = _resolve_single_path(de_path, en_path)
        de_resolved, en_resolved = _resolve_sync_pair(de_resolved, en_resolved)
        de_resolved, en_resolved = de_resolved.resolve(), en_resolved.resolve()
        results = [verify_pair(de_resolved, en_resolved)]
        root = None

    exit_code = 2 if any(not r.ok for r in results) else 0
    if as_json:
        click.echo(json.dumps(_verify_to_dict(results, root, exit_code), indent=2))
    else:
        _print_verify_human(results, root)
    sys.exit(exit_code)


def _verify_to_dict(
    results: list[VerifyResult], root: Path | None, exit_code: int
) -> dict[str, object]:
    payload: dict[str, object] = {"mode": "verify", "exit_code": exit_code}
    if root is not None:
        payload["root"] = str(root)
    payload["pairs"] = [
        {
            "de_path": str(r.de_path),
            "en_path": str(r.en_path),
            "ok": r.ok,
            "git_baseline": r.git_baseline,
            "violations": [
                {
                    "severity": v.severity,
                    "kind": v.kind,
                    "message": v.message,
                    "slide_id": v.slide_id,
                }
                for v in r.violations
            ],
        }
        for r in results
    ]
    return payload


def _print_verify_human(results: list[VerifyResult], root: Path | None) -> None:
    if not results:
        click.echo(
            f"no split-format deck pairs found under {root}."
            if root is not None
            else "nothing to verify."
        )
        return
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        bits = []
        if r.errors:
            bits.append(f"{len(r.errors)} error{'s' if len(r.errors) != 1 else ''}")
        if r.warnings:
            bits.append(f"{len(r.warnings)} warning{'s' if len(r.warnings) != 1 else ''}")
        if not r.git_baseline:
            bits.append("no-drop check skipped (untracked)")
        summary = f" ({', '.join(bits)})" if bits else " (structurally valid)"
        click.echo(f"{mark} {r.de_path.name}{summary}")
        for v in r.violations:
            click.echo(f"    {v.severity} [{v.kind}]: {v.message}")
    if len(results) > 1:
        valid = sum(1 for r in results if r.ok)
        total_warn = sum(len(r.warnings) for r in results)
        tail = f", {total_warn} warning(s)" if total_warn else ""
        click.echo(
            f"\nverified {len(results)} pair(s): {valid} valid, "
            f"{len(results) - valid} with errors{tail}."
        )


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
    counts = (
        f"{result.applied_edit} edit, {result.applied_retag} retag, "
        f"{result.applied_remove} remove, "
        f"{result.applied_move} move, {result.applied_add} add, "
        f"{result.applied_rename} rename, {result.applied_mint} mint, "
        f"{result.applied_adopt} adopt, {result.applied_reconcile} reconcile, "
        f"{result.applied_structural} structural"
    )
    watermark = "advanced" if result.watermark_recorded else "held"
    if not result.flushed:
        # The atomic temp-swap never fired (an apply-time or classifier error
        # rolled the whole pass back), so NOTHING reached disk. Reporting the
        # in-memory counters as "applied" would contradict the file, masking the
        # rollback as success — so label them as the writes that did NOT happen.
        return (
            f"rolled back — nothing written (would have applied: {counts}); "
            f"{result.in_sync} already in sync; "
            f"{result.deferred} deferred; {len(result.errors)} error(s); "
            f"watermark {watermark}."
        )
    return (
        f"applied: {counts}; "
        f"{result.in_sync} already in sync; "
        f"{result.deferred} deferred; {len(result.errors)} error(s); "
        f"watermark {watermark}."
    )


def _cold_deferral_lines(result: ApplyResult, *, indent: str = "  ") -> list[str]:
    """Actionable detail for each cold-start mint/adopt deferral (#231).

    Turns the opaque ``N deferred`` count into a lead the author can act
    on — on a verifier "no" this names the rejected pair indices and both
    headings (crossed DE/EN content and alignment-shifting missing/merged
    cells are the usual causes).
    """
    lines: list[str] = []
    for d in result.cold_deferrals:
        if d.reason == "rejected-pairs":
            lines.append(
                f"{indent}deferred {d.kind}: {len(d.rejected_pairs)} pair(s) "
                "judged non-corresponding:"
            )
            for rp in d.rejected_pairs:
                lines.append(
                    f'{indent}  pair {rp.index}: DE "{rp.de_heading}" / EN "{rp.en_heading}"'
                )
            lines.append(
                f"{indent}  hint: crossed DE/EN content or a missing/merged cell "
                "usually explains this — `clm slides validate <deck>` can pinpoint it."
            )
        elif d.reason == "no-verifier":
            lines.append(
                f"{indent}deferred {d.kind}: no correspondence verifier "
                "(verification disabled or no API key) — nothing was written."
            )
        elif d.reason == "safe-abort":
            lines.append(
                f"{indent}deferred {d.kind}: correspondence verification failed "
                "(transport/parse) — safe-abort, nothing was written; re-run to retry."
            )
        elif d.reason == "plan-errors":
            lines.append(
                f"{indent}deferred {d.kind}: the plan carries classifier errors "
                "(see issues above) — fix those first."
            )
        elif d.reason == "race":
            lines.append(
                f"{indent}deferred {d.kind}: the files changed between planning and "
                "apply — re-run sync."
            )
    return lines


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

    for line in _cold_deferral_lines(apply_result):
        click.echo(line)
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
    *,
    rebaseline_hint: bool = False,
    cold_baseline_hint: bool = False,
) -> dict:
    return {
        "de_path": str(plan.de_path),
        "en_path": str(plan.en_path),
        "mode": mode,
        "exit_code": exit_code,
        "plan": _plan_dict(plan),
        "apply": _apply_dict(apply_result) if apply_result is not None else None,
        "walker": _walker_dict(walk) if walk is not None else None,
        "rebaseline_hint": rebaseline_hint,
        "cold_baseline_hint": cold_baseline_hint,
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
            "structural": result.applied_structural,
            "total": result.applied,
        },
        "in_sync": result.in_sync,
        "deferred": result.deferred,
        "cold_deferrals": [
            {
                "kind": d.kind,
                "reason": d.reason,
                "rejected_pairs": [
                    {
                        "index": rp.index,
                        "de_heading": rp.de_heading,
                        "en_heading": rp.en_heading,
                    }
                    for rp in d.rejected_pairs
                ],
            }
            for d in result.cold_deferrals
        ],
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
