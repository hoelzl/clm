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
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from attrs import define

from clm.cli._lazy_group import LazyGroup

# The agent-path module imports NO model client AND no key-presence check (epic #440
# decision B + Issue #438): the read surface classifies a cold pair as always
# agent-driveable — the agent is the verifier (`accept` runs the validator) — so it
# gates cold-pair candidacy on nothing local. The four OpenRouter/Ollama clients, the
# legacy all-in-one command, and the surviving `has_openrouter_api_key` gate all live in
# the sibling ``sync_autopilot`` module, registered lazily so a plain import of this
# module never loads them.
from clm.infrastructure.llm.cache import (
    SyncAlignmentCache,
    SyncCorrespondenceCache,
    SyncWatermarkCache,
    resolve_cache_dir,
)
from clm.slides.glossary import resolve_guidance_by_lang
from clm.slides.pairing import (
    derive_split_pair_from_stem,
    derive_split_twin,
    find_split_slide_files_recursive,
    iter_split_pairs,
    order_split_pair,
    split_lang_tag,
)
from clm.slides.sync_accept import (
    AcceptRejected,
    AcceptResult,
    AcceptUnavailable,
    accept_answer,
)
from clm.slides.sync_apply import ApplyResult, apply_plan
from clm.slides.sync_plan import (
    PlanIssue,
    SyncPlan,
    build_sync_plan,
    render_explain,
    render_plan,
)
from clm.slides.sync_report import build_report
from clm.slides.sync_task import (
    _FRAMEABLE_KINDS,
    SyncTask,
    TaskUnavailable,
    build_task,
    build_tasks,
)
from clm.slides.sync_verify import VerifyResult, verify_pair

if TYPE_CHECKING:
    from collections.abc import Callable

    from clm.infrastructure.llm.ollama_client import SyncJudge, SyncProposal
    from clm.slides.sync_plan_walker import PlanWalkResult
    from clm.slides.sync_recover import AlignmentRecoverer, CorrespondenceVerifier
    from clm.slides.sync_report import ReconciliationItem
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


def _swap_split_lang(name: str, to: str) -> str | None:
    """Swap a split half's language tag in its filename (``x.de.py`` ↔ ``x.en.py``).

    Lexical only — the sibling old half may no longer exist on disk. ``None`` when
    ``name`` carries no ``.de``/``.en`` tag.
    """
    parts = name.split(".")
    if len(parts) >= 3 and parts[-2] in ("de", "en"):
        parts[-2] = to
        return ".".join(parts)
    return None


def _parse_baseline_from(spec: str) -> tuple[Path, Path, str]:
    """Parse ``--baseline-from PATH[@REF]`` into ``(old_de, old_en, ref)`` (epic #440).

    PATH is the deck's pre-rename DE **or** EN half (the old folder/stem); the sibling
    old half is derived by swapping the ``.de``/``.en`` tag lexically — the old
    directory may be gone, so no disk access. ``@REF`` is optional and defaults to
    ``HEAD``. Raises :class:`click.UsageError` when PATH carries no ``.de``/``.en`` tag.
    """
    raw, sep, ref = spec.rpartition("@")
    old_str, ref = (raw, ref or "HEAD") if sep else (spec, "HEAD")
    old = Path(old_str)
    tag = split_lang_tag(old)
    if tag is None:
        raise click.UsageError(
            f"--baseline-from {old_str}: expected the deck's pre-rename .de/.en half "
            "(e.g. old_folder/slides_x.de.py), optionally @REF."
        )
    sibling_name = _swap_split_lang(old.name, "en" if tag == "de" else "de")
    assert sibling_name is not None  # split_lang_tag guarantees a swappable tag
    sibling = old.with_name(sibling_name)
    return (old, sibling, ref) if tag == "de" else (sibling, old, ref)


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
    baseline_ref: str | None = None,
    make_judge: Callable[[], SyncJudge | None],
    make_translator: Callable[[], SlideTranslator | None],
    make_recoverer: Callable[[], AlignmentRecoverer | None],
    make_verifier: Callable[[], CorrespondenceVerifier | None],
) -> None:
    """Sync every split deck pair under ``root`` in one pass, then ``sys.exit`` the
    worst per-pair exit code (0 clean < 1 review < 2 error).

    ``baseline_ref`` (an explicit git ref, e.g. ``HEAD~10``) is applied to EVERY pair —
    each is diffed against its own content at that ref. This is the "reconcile a week of
    committed single-language edits" sweep: a plain git-HEAD batch reads such edits as
    already-consistent (they match HEAD), so pin a baseline from before the editing.

    A read-only sweep (``mode`` ``dry-run`` / ``explain``) never constructs a model, so
    the ``make_*`` factories are only called on a writing (``apply``) sweep — the
    read-only ``report`` path passes ``lambda: None`` for all four."""
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
                    baseline_ref=baseline_ref,
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
    baseline_ref: str | None = None,
) -> _PairResult:
    """Sync one pair for the batch, catching any failure so the sweep continues."""
    try:
        plan = build_sync_plan(
            de_path,
            en_path,
            watermark_cache=watermark_cache,
            provider_available=provider_available,
            baseline_ref=baseline_ref,
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
    # Itemize each pair's tier-2/3 residue so an agent can map deck → item-ids for
    # `task`/`accept` (the rollup only counts them; single-pair apply does this too).
    if mode == "apply":
        residue_pairs = [(r, _apply_residue(r.plan)) for r in results if r.plan is not None]
        residue_pairs = [(r, res) for r, res in residue_pairs if res]
        if residue_pairs:
            click.echo("")
            click.echo("residue (per deck) — run `report` / `task` / `accept` per deck:")
            for r, res in residue_pairs:
                ids = ", ".join(f"{it.item}[{it.kind}]" for it in res)
                click.echo(f"  {_deck_label(r.de_path, root)}: {ids}")
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
        # The blessed agent contract (PR #422 step ii): the engine's work projected
        # into the three tiers an agent acts on differently — mechanical (trust),
        # assisted (a scoped model task), ambiguity (your judgement). The flat
        # ``plan`` block below is kept for existing consumers. Cell-text excerpts are
        # resolved only on a dry-run: they index the working-tree files, which match
        # the plan's positions only before an apply mutates them.
        "report": build_report(plan, with_excerpts=mode == "dry-run").model_dump(mode="json"),
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
        # Issue #448 P1: content proposals the consistency-ledger overlay suppressed
        # (slides byte-stable since a recorded confirmation). Surfaced so a `--ledger
        # --json` consumer can tell "0 real changes" from "N changes trusted away" —
        # without it a suppressed run reads as falsely consistent. 0 when no --ledger.
        "ledger_skipped": plan.ledger_skipped,
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


# ---------------------------------------------------------------------------
# Agent-toolkit verb surface (Issue #366 / epic #440)
#
# `clm slides sync` is a *group* of single-purpose verbs an agent drives. Bare
# `clm slides sync DECK` defaults to the read-only `report`. The model-free agent
# verbs — ``report`` / ``verify`` / ``task`` / ``accept`` / ``apply`` — each have their
# own implementation here (``_run_report`` / ``_run_verify`` / the verb bodies) and
# never call a model; only ``autopilot`` (the legacy all-in-one, lazily registered)
# constructs the embedded clients. Redesign decisions reflected:
#   * read-by-default — bare ``sync DECK`` returns the report, never writes.
#   * watermark demoted — ``report``/``verify`` baseline off git HEAD by default;
#     ``report --use-watermark`` (or ``--baseline REF``) opts back in.
#   * model-free engine path — ``apply`` applies only tier-1 and defers (not errors)
#     the model-requiring residue (decision B; ``apply_plan(deterministic_only=True)``);
#     ``task`` frames the rest and ``accept`` validates the agent's answer, both model-free.
#   * model-import split — the agent verbs (this module) import NO model-client class;
#     the four clients + the legacy all-in-one command live in ``sync_autopilot`` and are
#     registered LAZILY, so importing this module (the CLI startup path) never loads them.
# Phases 1-4 have landed (verb surface + watermark demotion + ``task``/``accept`` for all
# six kinds + the ``clm info`` cutover); the remaining cross-repo skill / PythonCourses
# migration is gated on this PR merging (docs/claude/design/sync-agent-toolkit-redesign.md).
# ---------------------------------------------------------------------------

#: The autopilot verb is loaded lazily (only when invoked or in a ``--help`` listing), so
#: a plain ``import`` of this agent-path module never imports ``sync_autopilot`` or its
#: model clients. This is what makes "no model client on the agent path" structural.
_AUTOPILOT_SPEC = "clm.cli.commands.slides.sync_autopilot:slides_sync_cmd"


class _DefaultVerbGroup(LazyGroup):
    """A ``sync`` group whose bare ``clm slides sync DECK`` runs ``report``.

    Click groups have no native default subcommand. When the first token is not a
    known verb (and not a help flag), prepend ``report`` so a bare deck path is
    treated as ``report DECK`` — the read-only default the redesign mandates. The
    known-verb check folds in the lazily-registered names (so ``sync autopilot DECK``
    is not mistaken for a deck path).
    """

    _DEFAULT_VERB = "report"

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        known = set(self.commands) | set(self._lazy_subcommands)
        if args and args[0] not in known and args[0] not in ("--help", "-h"):
            args = [self._DEFAULT_VERB, *args]
        return super().parse_args(ctx, args)


@click.group("sync", cls=_DefaultVerbGroup, lazy_subcommands={"autopilot": _AUTOPILOT_SPEC})
def slides_sync_group() -> None:
    """Agent toolkit for syncing split DE/EN deck pairs.

    \b
    Bare `clm slides sync DECK` == `clm slides sync report DECK` (read-only).
    Verbs:
      report     what is necessary? tiered report (read-only, no model, no key)
      verify     structural integrity check (no model, no watermark)
      task       emit a framed model task for a tier-2/3 item (read-only, no model)
      accept     validate a model answer + write it to both halves (writes, no model)
      apply      apply the reconciliation (writes; see `apply --help` re: models)
      autopilot  legacy all-in-one WITH embedded models (agent-less human)
      baseline   inspect/maintain the watermark accelerator
    """


#: Shared deck arguments for the read/apply verbs (the same surface the legacy
#: command exposes), so every verb takes ``DECK [EN_PATH]`` consistently.
_DECK_ARG = click.argument(
    "de_path",
    metavar="DECK",
    type=click.Path(exists=True, dir_okay=True, path_type=Path),
)
_EN_ARG = click.argument(
    "en_path",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)


def _load_ledger_if(enabled: bool, de_path: Path):  # -> SyncLedger | None
    """Load the per-slide consistency ledger for ``de_path``'s topic, or ``None`` (#448).

    Imported lazily so the read surface pays nothing when ``--ledger`` is off. An
    absent file is an empty ledger (every slide cold), so enabling the flag on a deck
    that was never blessed is a safe no-op.
    """
    if not enabled:
        return None
    from clm.slides.sync_ledger import ledger_path_for, load

    return load(ledger_path_for(de_path))


def _run_report(
    de_path: Path,
    en_path: Path | None,
    *,
    as_json: bool,
    explain: bool,
    baseline_ref: str | None,
    baseline_from_spec: str | None,
    use_watermark: bool,
    cache_dir: Path | None,
    ledger: bool = False,
) -> None:
    """Build the plan and render the read-only report / anchor-diff — never a model.

    The promoted, model-free ``--dry-run``/``--explain`` path of the legacy command,
    lifted out so ``report`` no longer routes through the model-driven ``autopilot``
    body (epic #440 decision B): it constructs NO model client. Default baseline is git
    HEAD; ``--use-watermark`` / ``--baseline`` opt the accelerator back in. Handles a
    single pair or a directory sweep (the read-only batch passes no model factories).
    Always ``sys.exit``s.
    """
    use_wm = use_watermark or baseline_ref is not None
    # Issue #438: the read surface classifies cold pairs as the agent always being the
    # verifier — `accept` runs `validate_correspondence`, so a genuinely-new/changed
    # id-less pair surfaces as a mint/adopt *task candidate* (driveable) rather than a
    # dead-end `refuse` keyed on a local env var. (A clean *committed* id-less deck is a
    # no-op regardless, short-circuited in `build_sync_plan`.) Only `autopilot` — which
    # really constructs the embedded client — gates this on `has_openrouter_api_key()`.
    provider_available = True
    mode = "explain" if explain else "dry-run"

    if de_path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{de_path} is a directory (batch report), which takes a single directory "
                "argument; do not pass a second path."
            )
        if baseline_from_spec is not None:
            raise click.UsageError(
                "--baseline-from pins ONE deck's pre-rename half, so it is single-pair "
                "only; run it per deck. (--baseline REF works over a directory — it diffs "
                "every pair against that ref, e.g. to reconcile a week of committed edits.)"
            )
        if ledger:
            raise click.UsageError(
                "--ledger is single-pair in this release (P1); run `report --ledger` per "
                "deck. A batch ledger overlay is a planned follow-up."
            )
        _run_batch(
            de_path,
            mode=mode,
            as_json=as_json,
            yes=True,  # a read-only sweep has no write to gate
            no_cache=not use_wm,
            no_env_file=True,
            cache_dir=cache_dir,
            provider_available=provider_available,
            baseline_ref=baseline_ref,
            make_judge=lambda: None,
            make_translator=lambda: None,
            make_recoverer=lambda: None,
            make_verifier=lambda: None,
        )
        return  # _run_batch always sys.exit()s; this is just for the type-checker.

    de_path, en_path = _resolve_single_path(de_path, en_path)
    de_path, en_path = _resolve_sync_pair(de_path, en_path)
    de_path, en_path = de_path.resolve(), en_path.resolve()
    baseline_from = _parse_baseline_from(baseline_from_spec) if baseline_from_spec else None
    if baseline_ref is not None and baseline_from is not None:
        raise click.UsageError(
            "--baseline and --baseline-from are mutually exclusive (one pins a ref, the "
            "other pins the deck's pre-rename location)."
        )

    watermark_cache: SyncWatermarkCache | None = None
    if use_wm:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
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
            detect_rename=True,
            ledger=_load_ledger_if(ledger, de_path),
        )
        if ledger and plan.ledger_skipped:
            click.echo(
                f"ledger: skipped {plan.ledger_skipped} slide(s) trusted in-sync "
                "(byte-stable since their last recorded confirmation).",
                err=True,
            )
        if watermark_cache is not None:
            recorded_commit = watermark_cache.get_synced_commit(str(de_path), str(en_path))
        if explain:
            explain_text = render_explain(
                de_path, en_path, plan=plan, watermark_cache=watermark_cache
            )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()

    exit_code = _plan_exit_code(plan)
    rebaseline_hint = use_wm and _is_stale_but_consistent(
        de_path, en_path, plan, provider_available=provider_available
    )
    cold_baseline_hint = (
        baseline_ref is None and plan.baseline_source == "git-head" and plan.is_noop
    )

    if explain:
        click.echo(explain_text)
    elif as_json:
        click.echo(
            json.dumps(
                _to_dict(
                    plan,
                    None,
                    None,
                    mode,
                    exit_code,
                    rebaseline_hint=rebaseline_hint,
                    cold_baseline_hint=cold_baseline_hint,
                ),
                indent=2,
            )
        )
    else:
        _print_human(plan, None, None, mode="dry-run")

    if rebaseline_hint and not as_json:
        click.echo(_rebaseline_hint_text(de_path, recorded_commit), err=True)
    if cold_baseline_hint and not as_json:
        click.echo(_cold_baseline_hint_text(de_path), err=True)
    sys.exit(exit_code)


@slides_sync_group.command("report")
@_DECK_ARG
@_EN_ARG
@click.option("--json", "as_json", is_flag=True, help="Emit the ReconciliationReport as JSON.")
@click.option(
    "--explain",
    is_flag=True,
    help="Human-readable content-anchor diagnostic (a read-only superset of the report).",
)
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help="Diff against an explicit git ref (e.g. HEAD~1) instead of git HEAD.",
)
@click.option(
    "--baseline-from",
    "baseline_from_spec",
    default=None,
    metavar="PATH[@REF]",
    help=(
        "Diff a RENAMED deck against its pre-rename half PATH (the old folder/stem; "
        "@REF defaults to HEAD). For a rename the auto-detection can't recover."
    ),
)
@click.option(
    "--use-watermark",
    is_flag=True,
    help="Opt back into the structural watermark as the baseline (default: git HEAD).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the watermark (only with --use-watermark).",
)
@click.option(
    "--ledger",
    is_flag=True,
    help=(
        "Consult the per-slide consistency ledger (<topic>/.clm/sync-ledger.json, #448): "
        "skip slides byte-stable since a recorded confirmation so a sync paid for last "
        "round is not re-litigated against an older baseline. Single pair only (P1)."
    ),
)
@click.option(
    "--no-env-file",
    is_flag=True,
    help="Accepted for symmetry; report reads no .env (it never calls a model).",
)
def sync_report_cmd(
    de_path: Path,
    en_path: Path | None,
    as_json: bool,
    explain: bool,
    baseline_ref: str | None,
    baseline_from_spec: str | None,
    use_watermark: bool,
    cache_dir: Path | None,
    ledger: bool,
    no_env_file: bool,
) -> None:
    """Return the read-only tiered reconciliation report (writes nothing, no model).

    The primary agent verb: it states *what reconciliation is necessary*, partitioned
    into mechanical / assisted / ambiguity tiers (with ``is_clean`` / ``needs_model``
    / ``needs_agent``), and never calls a model or needs an API key. The default
    baseline is git ``HEAD``; the watermark is a demoted, opt-in accelerator
    (``--use-watermark`` or ``--baseline REF``). A renamed deck recovers its baseline
    automatically (committed rename → HEAD^, uncommitted rename → matched predecessor);
    ``--baseline-from PATH[@REF]`` pins it explicitly when the rename can't be detected.
    """
    _run_report(
        de_path,
        en_path,
        as_json=as_json,
        explain=explain,
        baseline_ref=baseline_ref,
        baseline_from_spec=baseline_from_spec,
        use_watermark=use_watermark,
        cache_dir=cache_dir,
        ledger=ledger,
    )


@slides_sync_group.command("verify")
@_DECK_ARG
@_EN_ARG
@click.option("--json", "as_json", is_flag=True, help="Emit the verify result as JSON.")
def sync_verify_cmd(de_path: Path, en_path: Path | None, as_json: bool) -> None:
    """Structural integrity check (no model, no watermark, writes nothing).

    Confirms the pair is a valid split — byte-identical shared cells, header parity,
    clean alignment, ``de_id == en_id`` symmetry, no duplicate ids — and warns on an
    id'd cell dropped vs git ``HEAD``. Exit ``0`` = sound (warnings allowed), ``2`` =
    corrupt. Answers "did this edit corrupt the pair?", not "is it in sync?".
    """
    _run_verify(de_path, en_path, as_json=as_json)


@slides_sync_group.command("apply")
@_DECK_ARG
@_EN_ARG
@click.option(
    "--yes", "-y", "yes", is_flag=True, help="Confirm a writing run over a directory (batch)."
)
@click.option(
    "--use-watermark/--no-watermark",
    "use_watermark",
    default=True,
    help="Use the watermark as the baseline accelerator (default on for apply).",
)
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help="Diff against an explicit git ref (single pair only).",
)
@click.option(
    "--baseline-from",
    "baseline_from_spec",
    default=None,
    metavar="PATH[@REF]",
    help="Diff a renamed deck against its pre-rename half PATH (single pair only).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the watermark.",
)
@click.option(
    "--ledger",
    is_flag=True,
    help=(
        "Use the per-slide consistency ledger (#448): skip slides byte-stable since a "
        "recorded confirmation (no re-litigation) before applying, AND — on a fully "
        "clean pass — record the now-in-sync slides back to the ledger. Single pair (P1)."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit the apply result as JSON.")
def sync_apply_cmd(
    de_path: Path,
    en_path: Path | None,
    yes: bool,
    use_watermark: bool,
    baseline_ref: str | None,
    baseline_from_spec: str | None,
    cache_dir: Path | None,
    ledger: bool,
    as_json: bool,
) -> None:
    """Apply the deterministic tier-1 reconciliation — writes, but never calls a model.

    Applies only the **mechanical** tier: ``move`` / ``remove`` / ``retag``, the
    language-neutral verbatim propagation, and the unambiguous id-migration. Every
    item that needs a model — a ``add`` / ``edit`` / cold-start / ambiguous
    ``realign`` — is left as **residue**: nothing is written for it, it is reported,
    and the command exits non-zero pointing you at ``report`` / ``task`` / ``accept``.
    (Contrast ``autopilot``, which calls the embedded models for those tiers.)

    \b
    Needs no API key. Uses the watermark as a baseline accelerator and advances it on a
    fully clean pass (``--no-watermark`` ignores it, falling back to git HEAD). A
    directory is a batch sweep (gated by ``--yes``). Review writes with ``git diff`` and
    confirm soundness with ``clm slides sync verify``.

    With ``--ledger`` the consistency ledger (#448) is both **read** (skip slides
    byte-stable since a recorded confirmation) and **written**: a fully-clean apply —
    no deferred residue, the watermark fully advanced — records the now-in-sync
    localized slides back to ``<topic>/.clm/sync-ledger.json`` (``confirmed_by=apply``,
    gated on structural ``verify``). A pass with residue records nothing (the deck is
    not fully reconciled); resolve the residue and re-apply, or ``baseline bless
    --ledger``.
    """
    if de_path.is_dir():
        if en_path is not None:
            raise click.UsageError(
                f"{de_path} is a directory (batch apply), which takes a single directory "
                "argument; do not pass a second path."
            )
        if baseline_from_spec is not None:
            raise click.UsageError(
                "--baseline-from pins ONE deck's pre-rename half, so it is single-pair "
                "only; run it per deck. (--baseline REF works over a directory.)"
            )
        if ledger:
            raise click.UsageError(
                "--ledger is single-pair in this release (P1); run `apply --ledger` per "
                "deck. A batch ledger overlay is a planned follow-up."
            )
        _run_apply_batch(
            de_path,
            yes=yes,
            use_watermark=use_watermark,
            cache_dir=cache_dir,
            as_json=as_json,
            baseline_ref=baseline_ref,
        )
        return  # _run_apply_batch always sys.exit()s; this is just for the type-checker.

    de_path, en_path = _resolve_single_path(de_path, en_path)
    de_path, en_path = _resolve_sync_pair(de_path, en_path)
    de_path, en_path = de_path.resolve(), en_path.resolve()
    baseline_from = _parse_baseline_from(baseline_from_spec) if baseline_from_spec else None
    if baseline_ref is not None and baseline_from is not None:
        raise click.UsageError(
            "--baseline and --baseline-from are mutually exclusive (one pins a ref, the "
            "other pins the deck's pre-rename location)."
        )

    watermark_cache: SyncWatermarkCache | None = None
    if use_watermark:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
    try:
        plan, result = _apply_deterministic(
            de_path,
            en_path,
            watermark_cache=watermark_cache,
            baseline_ref=baseline_ref,
            baseline_from=baseline_from,
            ledger=ledger,
        )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()

    if ledger and plan.ledger_skipped:
        click.echo(
            f"ledger: skipped {plan.ledger_skipped} slide(s) trusted in-sync "
            "(byte-stable since their last recorded confirmation).",
            err=True,
        )
    ledger_recorded = _maybe_record_ledger(ledger, plan, result, de_path, en_path)
    exit_code = _apply_exit_code(plan, result)
    _emit_apply(plan, result, de_path, as_json=as_json, ledger_recorded=ledger_recorded)
    sys.exit(exit_code)


def _maybe_record_ledger(
    enabled: bool, plan: SyncPlan, result: ApplyResult, de_path: Path, en_path: Path
) -> int | None:
    """Record the now-in-sync slides to the ledger after a fully-clean apply (#448 P1).

    "Emit the watermark as the ledger" (design §8 P1): record **only when the watermark
    fully advanced** — ``watermark_recorded`` with nothing deferred and no tag hold,
    i.e. the exact full-advance condition (a *partial* advance pins deferred / tag-only
    conflicts at the old baseline, so the deck is not fully in sync and recording it
    would wrongly trust those slides). ``record_pair`` re-gates on structural ``verify``
    and reads the post-apply files. Returns the recorded count, ``0`` when the pass was
    not fully clean (so a ``--json`` consumer sees the ledger was consulted but nothing
    banked), or ``None`` when ``--ledger`` was off.
    """
    if not enabled:
        return None
    fully_clean = result.watermark_recorded and result.deferred == 0 and not plan.tag_holds
    if not fully_clean:
        return 0
    from clm.slides import sync_ledger

    rec = sync_ledger.record_pair(
        de_path, en_path, confirmed_by="apply", confirmed_oracle="structural"
    )
    recorded = 0 if rec.refused else rec.recorded
    if recorded:
        click.echo(
            f"ledger: recorded {recorded} slide(s) confirmed in-sync (confirmed_by=apply).",
            err=True,
        )
    return recorded


def _apply_deterministic(
    de_path: Path,
    en_path: Path,
    *,
    watermark_cache: SyncWatermarkCache | None,
    baseline_ref: str | None = None,
    baseline_from: tuple[Path, Path, str] | None = None,
    ledger: bool = False,
) -> tuple[SyncPlan, ApplyResult]:
    """Build the plan and apply only its deterministic tier-1 work (no model, epic #440).

    ``provider_available=False`` is deliberate: this verb has no model, so a cold pair
    is classified as residue (a ``refuse``) rather than a mint candidate — apply never
    mints. ``apply_plan(deterministic_only=True)`` then applies move/remove/retag, the
    neutral propagation, and the unambiguous id-migration, and *defers* (does not error
    on) every model-requiring item. The watermark advances only on a fully clean pass.
    """
    plan = build_sync_plan(
        de_path,
        en_path,
        watermark_cache=watermark_cache,
        provider_available=False,
        baseline_ref=baseline_ref,
        baseline_from=baseline_from,
        detect_rename=True,
        ledger=_load_ledger_if(ledger, de_path),
    )
    result = apply_plan(
        plan,
        judge=None,
        translator=None,
        recoverer=None,
        verifier=None,
        watermark_cache=watermark_cache,
        deterministic_only=True,
    )
    return plan, result


def _apply_residue(plan: SyncPlan) -> list[ReconciliationItem]:
    """The tier-2/3 report items model-free apply left untouched (the residue)."""
    report = build_report(plan, with_excerpts=False)
    return [*report.assisted, *report.ambiguity]


def _residue_hint_lines(residue: list[ReconciliationItem], de_name: str) -> list[str]:
    """Next-step lines for apply residue, split by whether the item has a *model* task.

    A `conflict` / `issue` is not frameable (:data:`_FRAMEABLE_KINDS`) — it needs *your*
    judgement, not a model — so pointing it at `task --item ID` over-promises. Frameable
    residue (edit / add / realign / mint / adopt / reconcile) goes to `task` → model →
    `accept`; the rest goes to editing the deck + re-`report`.
    """
    framed = [it for it in residue if it.kind in _FRAMEABLE_KINDS]
    human = [it for it in residue if it.kind not in _FRAMEABLE_KINDS]
    lines: list[str] = []
    if framed:
        lines.append(
            f"  {len(framed)} need a model: `clm slides sync task {de_name} --item ID` → "
            "a model → `accept` (or `autopilot`)."
        )
    if human:
        lines.append(
            f"  {len(human)} need your judgement (conflict / issue): edit the deck so the "
            f"halves agree, then re-run `clm slides sync report {de_name}`."
        )
    return lines


def _emit_apply(
    plan: SyncPlan,
    result: ApplyResult,
    de_path: Path,
    *,
    as_json: bool,
    ledger_recorded: int | None = None,
) -> None:
    """Print what a single-pair model-free apply wrote and what residue remains."""
    residue = _apply_residue(plan)
    if as_json:
        payload: dict = {
            "de_path": str(plan.de_path),
            "en_path": str(plan.en_path),
            "mode": "apply",
            "exit_code": _apply_exit_code(plan, result),
            "apply": _apply_dict(result),
            "residue": [
                {
                    "item": it.item,
                    "kind": it.kind,
                    "tier": it.tier,
                    "slide_id": it.slide_id,
                    "reason": it.reason,
                }
                for it in residue
            ],
        }
        if ledger_recorded is not None:
            # The ledger contract: what the overlay skipped (consulted) and what a
            # fully-clean apply banked back, so a --json consumer sees both.
            payload["ledger"] = {
                "skipped": plan.ledger_skipped,
                "recorded": ledger_recorded,
            }
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(_outcome_line(result))
    for line in _cold_deferral_lines(result):
        click.echo(line)
    for err in result.errors:
        click.echo(f"  error: {err}")
    if residue:
        click.echo("")
        click.echo(f"residue — {len(residue)} item(s) need a model or your judgement:")
        for it in residue:
            sid = f" {it.slide_id}" if it.slide_id else ""
            click.echo(f"  {it.item}  [{it.tier}/{it.kind}]{sid}: {it.reason or '(see report)'}")
        for line in _residue_hint_lines(residue, de_path.name):
            click.echo(line)
    if result.applied > 0:
        click.echo(
            f"Review the propagated changes with `git diff` and confirm with "
            f"`clm slides sync verify {de_path.name}`."
        )


def _run_apply_batch(
    root: Path,
    *,
    yes: bool,
    use_watermark: bool,
    cache_dir: Path | None,
    as_json: bool,
    baseline_ref: str | None = None,
) -> None:
    """Model-free tier-1 apply over every split pair under ``root`` (no model, epic #440).

    The agent-path twin of the autopilot batch (:func:`_run_batch`) — no judge /
    translator / verifier / recoverer is ever constructed, so it needs no API key and
    no env load. Continue-on-error: a pair that raises is recorded (exit 2) and the
    sweep proceeds; the process exit is the worst per-pair code. A writing sweep is
    gated behind ``--yes`` (or, without ``--json``, an interactive confirm).
    """
    pairs, solos = iter_split_pairs(find_split_slide_files_recursive(root))
    for solo in solos:
        tag = split_lang_tag(solo)
        other = "EN" if tag == "de" else "DE"
        click.echo(f"warning: skipping {solo.name} — no {other} twin found under {root}.", err=True)
    if not pairs:
        if as_json:
            click.echo(
                json.dumps(
                    {"mode": "apply", "root": str(root), "exit_code": 0, "pairs": []}, indent=2
                )
            )
        else:
            click.echo(f"no split-format deck pairs found under {root}.")
        sys.exit(0)

    if not yes:
        if as_json:
            raise click.UsageError(
                f"a writing batch over {len(pairs)} pair(s) needs --yes (cannot prompt "
                "with --json); add --yes, or preview with `clm slides sync report`."
            )
        click.confirm(
            f"About to apply the deterministic tier-1 sync to {len(pairs)} deck pair(s) "
            f"under {root} — this writes to the working tree. Continue?",
            abort=True,
        )

    watermark_cache: SyncWatermarkCache | None = None
    if use_watermark:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)

    results: list[_PairResult] = []
    try:
        for i, (de_path, en_path) in enumerate(pairs, 1):
            if not as_json:
                click.echo(f"[{i}/{len(pairs)}] {_deck_label(de_path, root)} …", err=True)
            try:
                plan, result = _apply_deterministic(
                    de_path, en_path, watermark_cache=watermark_cache, baseline_ref=baseline_ref
                )
                exit_code = _apply_exit_code(plan, result)
                results.append(_PairResult(de_path, en_path, plan, result, None, exit_code, None))
            except Exception as exc:  # continue-on-error: one bad pair must not abort the sweep
                results.append(
                    _PairResult(
                        de_path, en_path, None, None, None, 2, f"{type(exc).__name__}: {exc}"
                    )
                )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()

    exit_code = max((r.exit_code for r in results), default=0)
    _emit_batch(root, "apply", results, exit_code, as_json=as_json)
    sys.exit(exit_code)


@slides_sync_group.command("task")
@_DECK_ARG
@_EN_ARG
@click.option(
    "--item",
    "item_id",
    default=None,
    metavar="ID",
    help=(
        "Frame a single report item by its stable id (from `report --json`). "
        "Default: every frameable tier-2/3 item."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit the SyncTask(s) as JSON.")
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help="Diff against an explicit git ref (e.g. HEAD~1) instead of git HEAD.",
)
@click.option(
    "--baseline-from",
    "baseline_from_spec",
    default=None,
    metavar="PATH[@REF]",
    help="Diff a RENAMED deck against its pre-rename half PATH (the old folder/stem).",
)
@click.option(
    "--use-watermark",
    is_flag=True,
    help="Opt back into the structural watermark as the baseline (default: git HEAD).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the watermark (only with --use-watermark).",
)
def sync_task_cmd(
    de_path: Path,
    en_path: Path | None,
    item_id: str | None,
    as_json: bool,
    baseline_ref: str | None,
    baseline_from_spec: str | None,
    use_watermark: bool,
    cache_dir: Path | None,
) -> None:
    """Emit a framed model task for a tier-2/3 item (read-only, no model, no key).

    For an ``assisted`` (edit / new-slide) or ``ambiguity`` (``realign``) item from the
    report, ``task`` emits everything a model needs to do the job and nothing more: the
    ``instructions`` (system prompt), the ready-to-send ``prompt``, the ``inputs``, the
    ``answer_schema`` the answer must match, and the ``validator`` ``clm slides sync
    accept`` will run on it. **The engine never calls a model** — you run the prompt
    through whatever model you choose (or do it by hand), then pipe the answer to
    ``accept``. ``--item ID`` selects one item (ids come from ``report --json``);
    omitting it frames every frameable item. Single pair only.
    """
    if de_path.is_dir():
        raise click.UsageError(
            "`task` operates on a single deck pair, not a directory; pass one half "
            "(or the deck stem). Use `report` over a directory for a read-only sweep."
        )
    de_path, en_path = _resolve_single_path(de_path, en_path)
    de_path, en_path = _resolve_sync_pair(de_path, en_path)
    de_path, en_path = de_path.resolve(), en_path.resolve()
    baseline_from = _parse_baseline_from(baseline_from_spec) if baseline_from_spec else None
    if baseline_ref is not None and baseline_from is not None:
        raise click.UsageError(
            "--baseline and --baseline-from are mutually exclusive (one pins a ref, the "
            "other pins the deck's pre-rename location)."
        )

    prog_lang = _resolve_prog_lang(de_path)
    guidance_by_lang, _used = resolve_guidance_by_lang(
        de_path.parent, explicit={"de": None, "en": None}
    )
    # Default baseline is git HEAD (the demoted-watermark read surface); --use-watermark
    # opts the accelerator back in, and --baseline REF dominates either way.
    watermark_cache: SyncWatermarkCache | None = None
    if use_watermark:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
    try:
        plan = build_sync_plan(
            de_path,
            en_path,
            watermark_cache=watermark_cache,
            # Issue #438: the agent IS the verifier (`accept` runs the validator), so a
            # cold pair always frames as a task — never gated on an embedded key.
            provider_available=True,
            baseline_ref=baseline_ref,
            baseline_from=baseline_from,
            detect_rename=True,
        )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()

    if item_id is not None:
        try:
            task = build_task(plan, item_id, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang)
        except KeyError:
            raise click.UsageError(
                f"no report item with id {item_id!r}. List the ids with "
                f"`clm slides sync report {de_path.name} --json`."
            ) from None
        except TaskUnavailable as exc:
            # There IS such an item, but it has no framed model task (a cold-start pair
            # or a hand-judged ambiguity). Be honest: exit non-zero with the next step.
            if as_json:
                click.echo(
                    json.dumps({"item": item_id, "available": False, "reason": str(exc)}, indent=2)
                )
            else:
                click.echo(f"no framed model task for {item_id!r}: {exc}", err=True)
            sys.exit(2)
        _emit_tasks([task], unframed=[], as_json=as_json)
        sys.exit(0)

    tasks, unframed = build_tasks(plan, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang)
    _emit_tasks(tasks, unframed=unframed, as_json=as_json)
    sys.exit(0)


def _emit_tasks(tasks: list[SyncTask], *, unframed: list, as_json: bool) -> None:
    """Print the framed tasks (and any unframed tier-2/3 items) for ``task``."""
    if as_json:
        click.echo(
            json.dumps(
                {
                    "tasks": [t.model_dump(mode="json") for t in tasks],
                    "unframed": [
                        {
                            "item": it.item,
                            "kind": it.kind,
                            "tier": it.tier,
                            "slide_id": it.slide_id,
                            "reason": it.reason,
                        }
                        for it in unframed
                    ],
                },
                indent=2,
            )
        )
        return
    if not tasks and not unframed:
        click.echo(
            "no model tasks: every tier-2/3 item is clean or mechanical "
            "(run `clm slides sync report` to confirm)."
        )
        return
    for t in tasks:
        click.echo(f"=== {t.item}  [{t.tier}/{t.kind}]  validator={t.validator} ===")
        if t.slide_id:
            click.echo(f"slide_id: {t.slide_id}")
        if t.direction:
            click.echo(f"direction: {t.direction}")
        click.echo("\n# instructions (system prompt)")
        click.echo(t.instructions)
        click.echo("\n# prompt (send to a model of your choice)")
        click.echo(t.prompt)
        click.echo(
            f"\n# the answer must match validator={t.validator}; then run "
            f"`clm slides sync accept --item {t.item} --answer -`\n"
        )
    for it in unframed:
        click.echo(
            f"--- {it.item}  [{it.tier}/{it.kind}] needs your judgement: "
            f"{it.reason or '(see `clm slides sync report`)'}"
        )


@slides_sync_group.command("accept")
@_DECK_ARG
@_EN_ARG
@click.option(
    "--item",
    "item_id",
    required=True,
    metavar="ID",
    help="The report item the answer is for (the same id `task --item ID` framed).",
)
@click.option(
    "--answer",
    "answer_path",
    required=True,
    metavar="FILE",
    help="Path to the model's answer (JSON matching the task's answer_schema), or '-' for stdin.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the accept result as JSON.")
@click.option(
    "--baseline",
    "baseline_ref",
    default=None,
    metavar="REF",
    help="Diff against an explicit git ref (e.g. HEAD~1) instead of git HEAD.",
)
@click.option(
    "--baseline-from",
    "baseline_from_spec",
    default=None,
    metavar="PATH[@REF]",
    help="Diff a RENAMED deck against its pre-rename half PATH (the old folder/stem).",
)
@click.option(
    "--use-watermark",
    is_flag=True,
    help="Opt back into the structural watermark as the baseline (default: git HEAD).",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory holding the watermark (only with --use-watermark).",
)
def sync_accept_cmd(
    de_path: Path,
    en_path: Path | None,
    item_id: str,
    answer_path: str,
    as_json: bool,
    baseline_ref: str | None,
    baseline_from_spec: str | None,
    use_watermark: bool,
    cache_dir: Path | None,
) -> None:
    """Validate a model's answer for one item and write it to **both** halves (no model).

    Takes the answer the agent produced for the framed ``task`` (``--answer FILE`` or
    ``-`` for stdin, JSON matching the task's ``answer_schema``), runs it through the
    deterministic ``validator`` the task named, and writes it to both split halves iff
    it passes — maintaining ``de_id == en_id`` and neutral byte-identity. On a
    validation failure it rejects with the precise reason and **writes nothing**. The
    engine never calls a model: the model ran between ``task`` and ``accept``.

    \b
    Accepts an ``edit`` (judge verdict / re-translation), an ``add`` (translated new
    slide), a ``realign`` (alignment map), and a cold-start ``mint`` / ``adopt`` /
    ``reconcile`` (correspondence verdicts). A hand-judged ``conflict`` / ``issue`` has no
    model task, so it is not accepted — it says so with the next step. Run ``clm slides
    sync verify`` after to confirm the write is sound.
    """
    if de_path.is_dir():
        raise click.UsageError(
            "`accept` operates on a single deck pair, not a directory; pass one half "
            "(or the deck stem)."
        )
    de_path, en_path = _resolve_single_path(de_path, en_path)
    de_path, en_path = _resolve_sync_pair(de_path, en_path)
    de_path, en_path = de_path.resolve(), en_path.resolve()
    baseline_from = _parse_baseline_from(baseline_from_spec) if baseline_from_spec else None
    if baseline_ref is not None and baseline_from is not None:
        raise click.UsageError(
            "--baseline and --baseline-from are mutually exclusive (one pins a ref, the "
            "other pins the deck's pre-rename location)."
        )

    answer = _read_answer(answer_path)

    watermark_cache: SyncWatermarkCache | None = None
    if use_watermark:
        cache_root = resolve_cache_dir(cli_override=cache_dir)
        watermark_cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
    try:
        plan = build_sync_plan(
            de_path,
            en_path,
            watermark_cache=watermark_cache,
            # Issue #438: matches `task`/`report` — the agent's answer (validated here)
            # is the verifier, so cold-pair candidacy is never gated on an embedded key.
            provider_available=True,
            baseline_ref=baseline_ref,
            baseline_from=baseline_from,
            detect_rename=True,
        )
    finally:
        if watermark_cache is not None:
            watermark_cache.close()

    try:
        result = accept_answer(plan, item_id, answer)
    except KeyError:
        raise click.UsageError(
            f"no report item with id {item_id!r}. List the ids with "
            f"`clm slides sync report {de_path.name} --json`."
        ) from None
    except AcceptRejected as exc:
        _emit_accept_failure(item_id, str(exc), outcome="rejected", as_json=as_json)
        sys.exit(2)
    except AcceptUnavailable as exc:
        _emit_accept_failure(item_id, str(exc), outcome="unavailable", as_json=as_json)
        sys.exit(2)

    _emit_accept_result(result, de_path, as_json=as_json)
    sys.exit(0)


def _read_answer(answer_path: str) -> object:
    """Read + JSON-parse the model answer from a file or stdin (``-``)."""
    raw = sys.stdin.read() if answer_path == "-" else Path(answer_path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"--answer is not valid JSON: {exc}") from exc


def _emit_accept_failure(item_id: str, reason: str, *, outcome: str, as_json: bool) -> None:
    """Report a rejected / unavailable accept (writes nothing); exit handled by caller."""
    if as_json:
        click.echo(
            json.dumps({"item": item_id, "applied": False, "outcome": outcome, "reason": reason})
        )
    else:
        click.echo(f"not accepted ({outcome}): {reason}", err=True)


def _emit_accept_result(result: AcceptResult, de_path: Path, *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return
    click.echo(f"accepted {result.item} [{result.kind}]: {result.detail}")
    if result.changed:
        click.echo(
            f"Review the change with `git diff` and confirm it is sound with "
            f"`clm slides sync verify {de_path.name}`."
        )


# The legacy all-in-one command (``slides_sync_cmd``) is the agent-less human's escape
# hatch — the only verb that drives the embedded models. It now lives in the sibling
# ``sync_autopilot`` module and is registered on the group LAZILY (``_AUTOPILOT_SPEC``
# above), so importing THIS module never pulls in the model clients (epic #440 decision
# B). The PEP 562 hook below keeps ``from ...slides.sync import slides_sync_cmd`` working
# for existing tests and the lazy spec without a module-level import of the autopilot
# module — the attribute is resolved (and ``sync_autopilot`` imported) only on access.


def __getattr__(name: str) -> object:
    if name == "slides_sync_cmd":
        from clm.cli.commands.slides.sync_autopilot import slides_sync_cmd

        return slides_sync_cmd
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
