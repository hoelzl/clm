"""File/orchestration layer for ``clm slides translate`` (deck bootstrap).

Phase 2 of Issue #232. Wraps the pure Phase 1 engine
(:func:`clm.slides.translate_deck.translate_deck_text`) with the side-effecting
work it deliberately avoids — disk resolution, the *idempotency dispatch*, id
minting and the watermark seal — so that running ``translate`` over a
single-language deck produces a valid split pair *and* every subsequent run
degrades to a plain incremental ``sync`` instead of re-translating (or doubling)
the deck.

The central safety property (design decision D2) is **idempotency by
delegation**:

* **twin absent** (or empty, or ``--force``) → run the bootstrap engine, write
  the new half, mint EN-authority shared ``slide_id``\\ s across the freshly
  written pair (:func:`~clm.slides.assign_ids.assign_ids_in_split_pair`), and
  record the structural watermark so the next ``sync`` is a clean no-op;
* **twin present** → do **not** bootstrap; delegate straight to
  :func:`~clm.slides.sync_plan.build_sync_plan` +
  :func:`~clm.slides.sync_apply.apply_plan`, exactly as ``clm slides sync``
  wires them. Re-running therefore converges to incremental sync by
  construction — it never re-translates the whole deck and never doubles it.

This module is pure orchestration over injected dependencies (the translator,
edit judge, recoverer, verifier and caches) — it neither builds an LLM client
nor loads ``.env``; the CLI (Phase 4) owns provider/key wiring and the cache
lifecycle. That keeps the whole dispatch offline-testable through the
``SlideTranslator`` / ``SyncJudge`` protocols.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.assign_ids import AssignOptions, AssignResult, assign_ids_in_split_pair
from clm.slides.pairing import split_lang_tag
from clm.slides.sync_apply import ApplyResult, _record_watermark, apply_plan
from clm.slides.sync_plan import SyncPlan, build_sync_plan
from clm.slides.translate_deck import TranslateDeckResult, translate_deck_text

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import (
        SyncAlignmentCache,
        SyncCorrespondenceCache,
        SyncWatermarkCache,
    )
    from clm.infrastructure.llm.ollama_client import SyncJudge
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier
    from clm.slides.sync_translate import SlideTranslator

logger = logging.getLogger(__name__)

__all__ = [
    "BootstrapPaths",
    "BootstrapResult",
    "CompanionResult",
    "TranslateBootstrapError",
    "bootstrap_deck",
    "derive_bootstrap_paths",
]

_SUPPORTED_LANGS = ("de", "en")

BootstrapAction = Literal["bootstrapped", "synced"]


class TranslateBootstrapError(Exception):
    """Raised when a deck cannot be resolved for bootstrapping.

    Covers a source that is not a single split half — a bilingual deck stem
    (run ``clm slides split`` first), a voiceover companion (translated with its
    deck, not on its own), or an unsupported / contradictory ``--to`` target.
    Translation failures inside the engine surface as
    :class:`~clm.slides.translate_deck.TranslateDeckError`.
    """


@dataclass(frozen=True)
class BootstrapPaths:
    """The resolved direction and file paths for one bootstrap.

    ``source_path`` / ``twin_path`` are the half the author wrote and the half
    we will produce; ``de_path`` / ``en_path`` are the same two files ordered by
    language (the order :func:`assign_ids_in_split_pair`, the watermark and the
    sync engine all expect). All three are absolute, resolved paths so the
    watermark key matches a later ``clm slides sync`` over the same deck.
    ``twin_exists`` is true only when the twin file is present **and non-empty**
    — an empty twin is treated as absent so a stray ``touch`` does not route a
    fresh deck through sync.
    """

    source_path: Path
    twin_path: Path
    de_path: Path
    en_path: Path
    source_lang: str
    target_lang: str
    twin_exists: bool


CompanionAction = Literal["translated", "skipped"]


@dataclass(frozen=True)
class CompanionResult:
    """How the source half's voiceover companion was handled (design decision D5).

    ``action`` is ``"translated"`` when a new ``voiceover_<name>.<tgt>.py`` was
    synthesized in lockstep with the deck, or ``"skipped"`` when one already
    existed (left untouched, never doubled). ``translation`` carries the engine
    result for a translated companion (``None`` for a skip). ``source`` /
    ``target`` are the companion halves.
    """

    source: Path
    target: Path
    action: CompanionAction
    translation: TranslateDeckResult | None = None


@dataclass
class BootstrapResult:
    """Outcome of one :func:`bootstrap_deck` call.

    ``action`` is ``"bootstrapped"`` when a new half was synthesized or
    ``"synced"`` when an existing twin routed the call to the incremental sync
    engine. The per-path detail (``deck`` / ``assign`` for a bootstrap;
    ``plan`` / ``apply_result`` for a sync) is whichever path ran. ``companion``
    records the voiceover companion translated alongside a bootstrap (``None``
    when the source has no companion, or on the sync path).
    """

    action: BootstrapAction
    source_path: Path
    twin_path: Path
    de_path: Path
    en_path: Path
    source_lang: str
    target_lang: str
    deck: TranslateDeckResult | None = None
    assign: AssignResult | None = None
    plan: SyncPlan | None = None
    apply_result: ApplyResult | None = None
    watermark_recorded: bool = False
    companion: CompanionResult | None = None

    @property
    def ids_assigned(self) -> int:
        """How many ``slide_id``\\ s the EN-authority mint stamped (0 when the
        source was already fully id'd, or when delegating to sync)."""
        return len(self.assign.assignments) if self.assign is not None else 0


def _twin_path(source_path: Path, target_lang: str) -> Path:
    """The sibling split half's path for ``target_lang`` — existence-agnostic.

    Unlike :func:`~clm.slides.pairing.derive_split_twin` (which returns the twin
    only if it is already on disk), this derives the path the twin *would* have
    so the bootstrap can create it. The ``.de`` / ``.en`` tag sits immediately
    before the final extension (``split_lang_tag`` guarantees the
    ``<stem>.<lang>.<ext>`` shape), so swapping the second-to-last dotted segment
    is prefix- and extension-agnostic (``apis.de.cpp`` → ``apis.en.cpp``).
    """
    parts = source_path.name.split(".")
    parts[-2] = target_lang
    return source_path.with_name(".".join(parts))


def derive_bootstrap_paths(source_path: Path, target_lang: str | None = None) -> BootstrapPaths:
    """Resolve direction + twin path for ``source_path`` (writes nothing).

    Exposed so the CLI can decide *before* building any LLM client whether a run
    will bootstrap or delegate to sync. ``target_lang`` overrides the inferred
    direction (``--to``); when ``None`` it is the opposite of the source half's
    ``.de`` / ``.en`` tag. Raises :class:`TranslateBootstrapError` for a source
    that is not a single split half or a contradictory target.
    """
    if source_path.name.startswith("voiceover_"):
        raise TranslateBootstrapError(
            f"{source_path.name} is a voiceover companion, not a deck half; "
            "its narration is translated together with the deck it belongs to "
            "(run `clm slides translate` on the deck itself)."
        )
    source_lang = split_lang_tag(source_path)
    if source_lang is None:
        raise TranslateBootstrapError(
            f"{source_path.name} carries no .de/.en language tag, so there is no "
            "single source half to translate from. If it is a bilingual deck, run "
            "`clm slides split` first, then translate one of the halves."
        )
    if target_lang is None:
        target_lang = "en" if source_lang == "de" else "de"
    if target_lang not in _SUPPORTED_LANGS:
        raise TranslateBootstrapError(
            f"unsupported target language {target_lang!r}; supported languages are "
            f"{_SUPPORTED_LANGS}."
        )
    if target_lang == source_lang:
        raise TranslateBootstrapError(
            f"source half is already .{source_lang} and --to {target_lang} asks for "
            "the same language; pass the other language (or omit --to to infer it)."
        )

    # Resolve so the watermark key (keyed by the (de_path, en_path) strings) is
    # the same form `clm slides sync` records — otherwise a later sync would miss
    # this run's watermark and re-baseline off git HEAD. resolve() is happy with a
    # not-yet-existent twin (strict=False).
    source_path = source_path.resolve()
    twin_path = _twin_path(source_path, target_lang).resolve()
    de_path, en_path = (source_path, twin_path) if source_lang == "de" else (twin_path, source_path)
    twin_exists = twin_path.exists() and twin_path.stat().st_size > 0
    return BootstrapPaths(
        source_path=source_path,
        twin_path=twin_path,
        de_path=de_path,
        en_path=en_path,
        source_lang=source_lang,
        target_lang=target_lang,
        twin_exists=twin_exists,
    )


def bootstrap_deck(
    source_path: Path,
    *,
    target_lang: str | None = None,
    translator: SlideTranslator,
    judge: SyncJudge | None = None,
    watermark_cache: SyncWatermarkCache | None = None,
    recoverer: AlignmentRecoverer | None = None,
    alignment_cache: SyncAlignmentCache | None = None,
    verifier: CorrespondenceVerifier | None = None,
    correspondence_cache: SyncCorrespondenceCache | None = None,
    provider_available: bool = False,
    force: bool = False,
) -> BootstrapResult:
    """Bootstrap the other-language half of ``source_path`` — or sync if it exists.

    The dispatch (design decision D2): when the twin is **absent** (or empty, or
    ``force``) synthesize it with the Phase 1 engine, mint EN-authority shared
    ids over the new pair, and record the watermark; when the twin is **present**
    delegate to the incremental sync engine. The caller owns the cache lifecycle
    (open / close) exactly as ``clm slides sync`` does — this function never
    closes a cache it was handed.

    ``translator`` drives both the whole-deck bootstrap and sync's new-slide
    path; ``judge`` (plus the optional recoverer / verifier / caches) is only
    consulted on the sync-delegation path. Raises
    :class:`TranslateBootstrapError` for an unbootstrappable source and
    :class:`~clm.slides.translate_deck.TranslateDeckError` if the engine cannot
    produce a valid half.
    """
    paths = derive_bootstrap_paths(source_path, target_lang)

    if paths.twin_exists and not force:
        # Twin already on disk → this is no longer a cold start. Converge to plain
        # incremental sync rather than re-translate (and never double the deck).
        return _delegate_to_sync(
            paths,
            judge=judge,
            translator=translator,
            watermark_cache=watermark_cache,
            recoverer=recoverer,
            alignment_cache=alignment_cache,
            verifier=verifier,
            correspondence_cache=correspondence_cache,
            provider_available=provider_available,
        )

    return _bootstrap_new_twin(
        paths, translator=translator, watermark_cache=watermark_cache, force=force
    )


def _bootstrap_new_twin(
    paths: BootstrapPaths,
    *,
    translator: SlideTranslator,
    watermark_cache: SyncWatermarkCache | None,
    force: bool,
) -> BootstrapResult:
    """Synthesize, write and seal the missing-language half (and its companion).

    Order matters: translate the deck **and** the voiceover companion *up front*
    (the engine self-checks each pair's split/unify round-trip, and nothing is on
    disk yet, so a translation failure leaves no half-written deck) → write the
    deck twin → write the companion twin → mint EN-authority shared ids across
    **both** deck halves (also fills the source half if it was id-less, so the
    pair is never born id-less) → record the watermark from the final, id'd files
    so the next ``sync`` is a clean no-op.
    """
    source_text = paths.source_path.read_text(encoding="utf-8")
    comment_token = comment_token_for_path(paths.source_path)
    deck = translate_deck_text(
        source_text,
        source_lang=paths.source_lang,
        target_lang=paths.target_lang,
        translator=translator,
        comment_token=comment_token,
    )
    # Translate the companion in lockstep (D5) BEFORE any write, so a companion
    # failure aborts the whole bootstrap instead of leaving a deck with no
    # narration twin. Returns None when the source half has no companion.
    companion = _translate_companion(paths, translator=translator, force=force)

    # newline="\n": never let the platform inject CRLF (the split-pair tooling and
    # the round-trip invariant assume LF), matching assign_ids' own write.
    paths.twin_path.write_text(deck.target_text, encoding="utf-8", newline="\n")
    if companion is not None and companion.translation is not None:
        companion.target.write_text(
            companion.translation.target_text, encoding="utf-8", newline="\n"
        )

    # EN-authority shared-id parity (de_id == en_id) over the freshly written pair.
    # accept_content_derived=True so a heading-less slide still mints from its
    # content slug instead of refusing (a bootstrap should leave no id-less cell).
    # force=False so any id the author already wrote is preserved, not re-slugged.
    # The engine's round-trip guard already proved the pair is unifiable, so this
    # never returns None in practice; treat a defensive None as "ids unchanged".
    assign = assign_ids_in_split_pair(
        paths.de_path, paths.en_path, AssignOptions(accept_content_derived=True)
    )
    if assign is None:
        # Unreachable given the engine's round-trip guard (it proved the exact
        # pair now on disk is unifiable). Surface it if it ever fires in the field
        # rather than silently shipping an id-less pair.
        logger.warning(
            "id minting skipped for %s / %s: pair was not unifiable despite the "
            "engine round-trip guard — the bootstrapped pair may be id-less",
            paths.de_path,
            paths.en_path,
        )

    watermark_recorded = False
    if watermark_cache is not None:
        # Record both decks' post-mint state as the sync baseline — load-bearing
        # for D2 (without it the next `sync` re-diffs off git HEAD and may
        # re-propose). Reads the files back from disk, so it sees the id'd halves.
        _record_watermark(watermark_cache, paths.de_path, paths.en_path)
        watermark_recorded = True

    return BootstrapResult(
        action="bootstrapped",
        source_path=paths.source_path,
        twin_path=paths.twin_path,
        de_path=paths.de_path,
        en_path=paths.en_path,
        source_lang=paths.source_lang,
        target_lang=paths.target_lang,
        deck=deck,
        assign=assign,
        watermark_recorded=watermark_recorded,
        companion=companion,
    )


def _translate_companion(
    paths: BootstrapPaths,
    *,
    translator: SlideTranslator,
    force: bool,
) -> CompanionResult | None:
    """Plan the voiceover companion's translation in lockstep with the deck (D5).

    Returns ``None`` when the source half has no voiceover companion. Otherwise
    the target companion is ``voiceover_<name>.<tgt>.py`` placed in the **same
    directory** the source companion lives in (so a foldered ``voiceover/`` topic
    stays foldered) — mirroring ``split``'s ``_plan_companion_split``. An existing
    non-empty target is left untouched (``"skipped"``) unless ``force``, so a
    re-run never doubles the narration. Translation happens here (raising before
    any write on failure); the **caller** performs the write.

    A companion is just ``lang``-tagged narrative cells with no header macro, so
    :func:`translate_deck_text` handles it directly — translating each localized
    cell while ``build_twin_cell`` preserves ``for_slide`` / ``vo_anchor`` /
    ``slide_id`` / ``tags`` verbatim (only ``lang`` and the body change), which is
    exactly the companion ``for_slide`` parity the validator checks.
    """
    # Deferred import: a deck with no companion never pulls in the voiceover layer.
    from clm.slides.voiceover_tools import companion_name, resolve_companion

    source_companion = resolve_companion(paths.source_path)
    if source_companion is None:
        return None
    # Keep the target in the source companion's directory (sibling or voiceover/
    # subdir), named for the deck twin — voiceover_<name>.<tgt>.py.
    target = source_companion.parent / companion_name(paths.twin_path)
    if target.exists() and target.stat().st_size > 0 and not force:
        return CompanionResult(source=source_companion, target=target, action="skipped")
    translation = translate_deck_text(
        source_companion.read_text(encoding="utf-8"),
        source_lang=paths.source_lang,
        target_lang=paths.target_lang,
        translator=translator,
        comment_token=comment_token_for_path(paths.source_path),
    )
    return CompanionResult(
        source=source_companion, target=target, action="translated", translation=translation
    )


def _delegate_to_sync(
    paths: BootstrapPaths,
    *,
    judge: SyncJudge | None,
    translator: SlideTranslator,
    watermark_cache: SyncWatermarkCache | None,
    recoverer: AlignmentRecoverer | None,
    alignment_cache: SyncAlignmentCache | None,
    verifier: CorrespondenceVerifier | None,
    correspondence_cache: SyncCorrespondenceCache | None,
    provider_available: bool,
) -> BootstrapResult:
    """Route a present-twin run through the incremental sync engine.

    Mirrors the apply branch of ``slides_sync_cmd`` so a second ``translate`` (or
    a ``translate`` of a deck whose twin already exists) behaves identically to
    ``clm slides sync`` — incremental, watermark-driven, never re-translating the
    whole deck.
    """
    plan = build_sync_plan(
        paths.de_path,
        paths.en_path,
        watermark_cache=watermark_cache,
        provider_available=provider_available,
    )
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
    return BootstrapResult(
        action="synced",
        source_path=paths.source_path,
        twin_path=paths.twin_path,
        de_path=paths.de_path,
        en_path=paths.en_path,
        source_lang=paths.source_lang,
        target_lang=paths.target_lang,
        plan=plan,
        apply_result=apply_result,
        watermark_recorded=apply_result.watermark_recorded,
    )
