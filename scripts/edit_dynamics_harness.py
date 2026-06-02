#!/usr/bin/env python3
"""Edit-dynamics fault-injection harness for the split-language + voiceover workflow.

Where ``sync_corpus_harness.py`` measures *static* state over the real corpus,
this harness probes *editing dynamics*: it applies a catalogue of realistic
author edits to parallel ``.de.py`` / ``.en.py`` decks (and their voiceover
companions), runs the command path a user would take, and classifies the result

    preserve      — the correspondence/safety invariants still hold
    break-loud    — an invariant broke, but the op signalled it (raised /
                    non-zero exit / warning / deferred)
    break-silent  — an invariant broke and the op reported success

The **break-silent** rows are the work-list: every footgun where data diverges
or is lost with no signal to the author. The harness executably verifies the
"correspondence is preserved only if structural changes funnel through ``sync``"
thesis (design doc §5) and is the cross-command property suite CI lacks.

Design: ``docs/claude/design/split-voiceover-hardening.md`` §6.

The synthetic arm is pure (no corpus, no network): the translator/judge are the
same counting, no-LLM stand-ins the sync unit tests use. Run it for the table::

    python scripts/edit_dynamics_harness.py            # human table
    python scripts/edit_dynamics_harness.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Allow running both as a script and as an importable module (pytest backstop).
if __package__ in (None, ""):  # pragma: no cover - import shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clm.infrastructure.llm.cache import SyncWatermarkCache  # noqa: E402
from clm.infrastructure.llm.ollama_client import SyncProposal  # noqa: E402
from clm.notebooks.slide_parser import parse_cells  # noqa: E402
from clm.slides.assign_ids import AssignOptions, assign_ids_in_file  # noqa: E402
from clm.slides.split import (  # noqa: E402
    SplitError,
    UnifyError,
    split_in_file,
    split_text,
    unify_texts,
)
from clm.slides.sync_apply import ApplyResult, apply_plan  # noqa: E402
from clm.slides.sync_plan import SyncPlan, build_sync_plan, watermark_rows  # noqa: E402
from clm.slides.validator import validate_file  # noqa: E402
from clm.slides.voiceover_tools import (  # noqa: E402
    VoiceoverError,
    companion_path,
    extract_voiceover,
    inline_voiceover,
    merge_voiceover_text,
)

# Verdict constants.
PRESERVE = "preserve"
BREAK_LOUD = "break-loud"
BREAK_SILENT = "break-silent"
ERROR = "error"


# ---------------------------------------------------------------------------
# No-LLM mocks (verbatim from tests/slides/test_sync_limitations.py)
# ---------------------------------------------------------------------------


@dataclass
class CountingTranslator:
    """Records every call and returns the source body verbatim (no network)."""

    prompt_version: str = "counting"
    calls: list[tuple[str, str, str, str]] = field(default_factory=list)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.calls.append((role, source_lang, target_lang, source_body))
        return source_body


@dataclass
class CountingJudge:
    """Records every call and never rewrites (no network)."""

    prompt_version: str = "counting"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def propose(
        self, source_body: str, target_body: str, *, source_lang: str, target_lang: str
    ) -> SyncProposal:
        self.calls.append((source_lang, target_lang))
        return SyncProposal(verdict="in_sync", proposed_text=target_body)


# ---------------------------------------------------------------------------
# Deck builders (split-format cells; matches tests/slides/test_split.py shapes)
# ---------------------------------------------------------------------------

# The bilingual preamble; ``split_text`` rewrites it to header_de / header_en.
HEADER_PREAMBLE = (
    '# j2 from \'macros.j2\' import header\n# {{ header("Titel DE", "Title EN") }}\n\n'
)


def slide_cell(lang: str, title: str, *, sid: str | None = None, bullet: str | None = None) -> str:
    """A single localized slide cell in split form (optionally id-less)."""
    id_attr = f' slide_id="{sid}"' if sid else ""
    body = bullet if bullet is not None else f"- {lang.upper()} Bullet"
    return (
        f'# %% [markdown] lang="{lang}" tags=["slide"]{id_attr}\n#\n# ## {title}\n#\n# {body}\n\n'
    )


def voiceover_cell(lang: str, *, sid: str, text: str | None = None) -> str:
    """A voiceover cell *inside a slide file* (carries slide_id, not for_slide)."""
    body = text if text is not None else f"Voiceover {lang.upper()} for {sid}"
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{sid}"\n#\n# {body}\n\n'


def shared_code(name: str = "x", value: str = "1") -> str:
    """A language-neutral shared code cell (no lang attribute)."""
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def bilingual_pair(slug: str, de_title: str, en_title: str) -> str:
    """A DE-then-EN slide pair with a shared slide_id and targetable bullets."""
    return slide_cell("de", de_title, sid=slug, bullet=f"- DE {slug}") + slide_cell(
        "en", en_title, sid=slug, bullet=f"- EN {slug}"
    )


def baseline_bilingual(
    slugs: tuple[str, ...] = ("intro", "setup", "demo"),
    *,
    with_voiceover: tuple[str, ...] = (),
    with_shared_code: bool = False,
) -> str:
    """A clean bilingual deck: the canonical ``unify``-able shape."""
    parts: list[str] = [HEADER_PREAMBLE]
    if with_shared_code:
        parts.append(shared_code("api_key", '"k"'))
    for s in slugs:
        parts.append(bilingual_pair(s, f"Titel {s}", f"Title {s}"))
        if s in with_voiceover:
            parts.append(voiceover_cell("de", sid=s) + voiceover_cell("en", sid=s))
    return "".join(parts)


# ---------------------------------------------------------------------------
# The split-pair under test
# ---------------------------------------------------------------------------


@dataclass
class SplitPair:
    """A ``.de.py`` / ``.en.py`` pair, optionally with voiceover companions."""

    de: str
    en: str
    de_companion: str | None = None
    en_companion: str | None = None

    @classmethod
    def from_bilingual(cls, bilingual: str) -> SplitPair:
        de, en = split_text(bilingual)
        return cls(de=de, en=en)

    def write(self, dirpath: Path, stem: str = "slides_demo") -> tuple[Path, Path]:
        de_path = dirpath / f"{stem}.de.py"
        en_path = dirpath / f"{stem}.en.py"
        de_path.write_text(self.de, encoding="utf-8", newline="\n")
        en_path.write_text(self.en, encoding="utf-8", newline="\n")
        if self.de_companion is not None:
            companion_path(de_path).write_text(self.de_companion, encoding="utf-8", newline="\n")
        if self.en_companion is not None:
            companion_path(en_path).write_text(self.en_companion, encoding="utf-8", newline="\n")
        return de_path, en_path


# ---------------------------------------------------------------------------
# Inspectors — read invariant-relevant facts out of slide / companion text
# ---------------------------------------------------------------------------


def slide_ids(text: str) -> list[str | None]:
    """Ordered slide_ids of slide-start cells (the #162 cross-language join key)."""
    return [c.slide_id for c in parse_cells(text) if c.metadata.is_slide_start]


def all_ids(text: str) -> list[str | None]:
    """Ordered slide_ids of every id-bearing cell."""
    return [c.slide_id for c in parse_cells(text) if c.slide_id is not None]


def for_slides(text: str) -> list[str]:
    """Ordered ``for_slide`` keys of every voiceover cell in a companion."""
    return [c.metadata.for_slide for c in parse_cells(text) if c.metadata.for_slide is not None]


def slide_titles(text: str, lang: str) -> list[str]:
    """Ordered ``## …`` titles of slide-start cells of one language (data-loss probe)."""
    out: list[str] = []
    for c in parse_cells(text):
        if not c.metadata.is_slide_start or c.lang not in (lang, None):
            continue
        for line in c.content.splitlines():
            stripped = line.lstrip("# ").strip()
            if stripped:
                out.append(stripped)
                break
    return out


def id_parity(de: str, en: str) -> str | None:
    """Return a detail string if the #162 invariant is violated, else ``None``."""
    de_ids, en_ids = slide_ids(de), slide_ids(en)
    if de_ids == en_ids:
        return None
    return f"de_ids={de_ids} != en_ids={en_ids}"


# ---------------------------------------------------------------------------
# Command-path runners — each reproduces exactly what a user/agent would invoke
# ---------------------------------------------------------------------------


@dataclass
class SyncOutcome:
    de: str
    en: str
    plan: SyncPlan
    result: ApplyResult
    translator: CountingTranslator
    judge: CountingJudge


def _seed(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    """Seed the membership-widened watermark exactly as ``_record_watermark`` does."""
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


def run_sync(
    baseline: SplitPair, mutated: SplitPair, dirpath: Path, stem: str = "slides_demo"
) -> SyncOutcome:
    """Seed the watermark from ``baseline``, write ``mutated``, then plan+apply.

    This is the safe funnel: the path that keeps both halves consistent.
    """
    de_path = dirpath / f"{stem}.de.py"
    en_path = dirpath / f"{stem}.en.py"
    de_path.write_text(baseline.de, encoding="utf-8", newline="\n")
    en_path.write_text(baseline.en, encoding="utf-8", newline="\n")
    cache = SyncWatermarkCache(dirpath / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(mutated.de, encoding="utf-8", newline="\n")
        en_path.write_text(mutated.en, encoding="utf-8", newline="\n")
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()
    return SyncOutcome(
        de=de_path.read_text(encoding="utf-8"),
        en=en_path.read_text(encoding="utf-8"),
        plan=plan,
        result=result,
        translator=translator,
        judge=judge,
    )


def run_assign_ids_pair(pair: SplitPair, workdir: Path, stem: str = "slides_demo") -> SplitPair:
    """Write a split pair to disk and run per-file ``assign-ids`` on each half.

    Mirrors the realistic ``clm slides assign-ids slides/`` flow (sorted: the
    ``.de.py`` half then ``.en.py``). On disk the twin-aware reuse (#162
    defensive) can fire, so the second half adopts the first half's minted ids.
    """
    de_path, en_path = pair.write(workdir, stem=stem)
    assign_ids_in_file(de_path, AssignOptions())
    assign_ids_in_file(en_path, AssignOptions())
    return SplitPair(
        de=de_path.read_text(encoding="utf-8"),
        en=en_path.read_text(encoding="utf-8"),
    )


# ---------------------------------------------------------------------------
# Classification framework
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """One (mutation, command-path) result, classified."""

    name: str
    path: str
    verdict: str
    violated: list[str] = field(default_factory=list)
    signaled: bool = False
    signal: str = ""
    detail: str = ""
    expected: str = ""
    asserted: bool = True

    @property
    def drifted(self) -> bool:
        """Observed verdict diverged from the frozen baseline (regression *or* fix)."""
        return self.verdict != self.expected


def classify(violated: list[str], signaled: bool) -> str:
    if not violated:
        return PRESERVE
    return BREAK_LOUD if signaled else BREAK_SILENT


def _fresh_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="clm-edit-dyn-")


# ---------------------------------------------------------------------------
# Mutation catalogue
#
# Each mutation applies one realistic author edit, runs the command path the
# author would take, and returns ``(violated, signaled, signal, detail)``:
#   violated  — list of invariant names that broke ([] = held)
#   signaled  — did the op signal the problem to the *user* (raise / non-zero
#               exit / warning / deferred)? A counter buried in a result object
#               the CLI ignores is NOT a user-facing signal.
#   signal    — human description of the signal (empty when not signalled)
#   detail    — what happened, for the table
# ---------------------------------------------------------------------------

_RetTuple = tuple[list[str], bool, str, str]


def _base() -> SplitPair:
    return SplitPair.from_bilingual(baseline_bilingual())


def _split_header(macro: str) -> str:
    """A split (single-language) preamble — replace the bilingual header macro."""
    return HEADER_PREAMBLE.replace('# {{ header("Titel DE", "Title EN") }}', f"# {{{{ {macro} }}}}")


def _sync_signaled(out: SyncOutcome) -> tuple[bool, str]:
    if out.result.errors:
        return True, f"errors={out.result.errors}"
    if out.plan.has_errors:
        return True, "plan has error issues"
    if out.result.deferred:
        return True, f"deferred={out.result.deferred}"
    return False, ""


# --- sync funnel (expected: preserve) --------------------------------------


def m_add_one_half(workdir: Path) -> _RetTuple:
    base = _base()
    mutated = SplitPair(de=base.de + slide_cell("de", "Neuer Slide", bullet="- DE neu"), en=base.en)
    out = run_sync(base, mutated, workdir)
    detail = id_parity(out.de, out.en)
    sig, msg = _sync_signaled(out)
    return (
        (["id_parity"] if detail else []),
        sig,
        msg,
        detail or f"id minted on both: {slide_ids(out.de)}",
    )


def m_reorder_one_half(workdir: Path) -> _RetTuple:
    base = _base()
    de2, _ = split_text(
        HEADER_PREAMBLE
        + bilingual_pair("demo", "Titel demo", "Title demo")
        + bilingual_pair("setup", "Titel setup", "Title setup")
        + bilingual_pair("intro", "Titel intro", "Title intro")
    )
    out = run_sync(base, SplitPair(de=de2, en=base.en), workdir)
    detail = id_parity(out.de, out.en)
    sig, msg = _sync_signaled(out)
    return (
        (["id_parity"] if detail else []),
        sig,
        msg,
        detail or f"move mirrored: {slide_ids(out.en)}",
    )


def m_delete_one_half(workdir: Path) -> _RetTuple:
    base = _base()
    de2, _ = split_text(
        HEADER_PREAMBLE
        + bilingual_pair("intro", "Titel intro", "Title intro")
        + bilingual_pair("demo", "Titel demo", "Title demo")
    )
    out = run_sync(base, SplitPair(de=de2, en=base.en), workdir)
    detail = id_parity(out.de, out.en)
    sig, msg = _sync_signaled(out)
    return (
        (["id_parity"] if detail else []),
        sig,
        msg,
        detail or f"remove mirrored: {slide_ids(out.en)}",
    )


def m_edit_both_halves(workdir: Path) -> _RetTuple:
    base = _base()
    mutated = SplitPair(
        de=base.de.replace("- DE setup", "- DE setup EDITED"),
        en=base.en.replace("- EN setup", "- EN setup EDITED"),
    )
    out = run_sync(base, mutated, workdir)
    detail = id_parity(out.de, out.en)
    sig, msg = _sync_signaled(out)
    return (
        (["id_parity"] if detail else []),
        sig,
        msg,
        detail or f"conflict deferred={out.result.deferred}, no divergent write",
    )


def m_copy_paste_dup(workdir: Path) -> _RetTuple:
    base = _base()
    dup = slide_cell("de", "Titel setup", sid="setup", bullet="- DE setup")
    out = run_sync(base, SplitPair(de=base.de + dup, en=base.en), workdir)
    detail = id_parity(out.de, out.en)
    sig, msg = _sync_signaled(out)
    return (
        (["id_parity"] if detail else []),
        sig,
        msg,
        detail or f"re-mint on both: {slide_ids(out.de)}",
    )


def m_hand_edit_id_then_sync(workdir: Path) -> _RetTuple:
    base = _base()
    mutated = SplitPair(de=base.de.replace('slide_id="setup"', 'slide_id="renamed"'), en=base.en)
    out = run_sync(base, mutated, workdir)
    detail = id_parity(out.de, out.en)
    sig, msg = _sync_signaled(out)
    return (
        (["id_parity"] if detail else []),
        sig,
        msg,
        detail or "sync heals via remove+add mirrored to en (en localized content regenerated)",
    )


# --- per-file assign-ids + no-gate (expected: break-silent) ----------------


def m_add_then_assign_ids_per_file(workdir: Path) -> _RetTuple:
    # Author adds the SAME new slide to BOTH halves (id-less), then runs
    # per-file assign-ids on each. Divergent headings would slug divergently —
    # the #162 defensive (twin-aware reuse) keeps the id in parity.
    base = _base()
    de = base.de + slide_cell("de", "Neuer Abschnitt", bullet="- DE neu")
    en = base.en + slide_cell("en", "New Section", bullet="- EN new")
    result = run_assign_ids_pair(SplitPair(de=de, en=en), workdir)
    detail = id_parity(result.de, result.en)
    return (
        (["id_parity"] if detail else []),
        False,
        "",
        detail or f"twin-aware reuse: ids matched {slide_ids(result.de)}",
    )


def m_born_split_assign_ids(workdir: Path) -> _RetTuple:
    de = _split_header('header_de("Titel DE")') + slide_cell("de", "Mein Thema")
    en = _split_header('header_en("Title EN")') + slide_cell("en", "My Topic")
    result = run_assign_ids_pair(SplitPair(de=de, en=en), workdir)
    detail = id_parity(result.de, result.en)
    return (
        (["id_parity"] if detail else []),
        False,
        "",
        detail or f"twin-aware reuse: ids matched {slide_ids(result.de)}",
    )


def m_commit_without_sync(workdir: Path) -> _RetTuple:
    # Author adds a slide to one half and commits without ever syncing. The
    # #162 detective (the pre-commit gate runs `clm validate`) now catches the
    # divergence loudly even though `build` itself stays permissive.
    base = _base()
    de = base.de + slide_cell("de", "Neuer Slide", sid="extra", bullet="- DE neu")
    de_path, _ = SplitPair(de=de, en=base.en).write(workdir)
    detail = id_parity(de, base.en)
    findings = validate_file(de_path).findings
    caught = any(f.category == "pairing" and "slide_id" in f.message for f in findings)
    return (
        (["id_parity"] if detail else []),
        caught,
        "validate (detective) reports slide_id divergence" if caught else "",
        f"{detail}; detective {'CATCHES it' if caught else 'misses it'}" if detail else "in sync",
    )


# --- voiceover round-trip (expected: preserve) -----------------------------


def m_extract_inline_round_trip(workdir: Path) -> _RetTuple:
    de, _ = split_text(baseline_bilingual(with_voiceover=("intro", "setup")))
    slide = workdir / "slides_demo.de.py"
    slide.write_text(de, encoding="utf-8", newline="\n")
    extract_voiceover(slide)
    ires = inline_voiceover(slide)
    after = slide.read_text(encoding="utf-8")
    restored = after == de and not companion_path(slide).exists()
    sig = ires.unmatched_cells > 0 or ires.relocated_cells > 0
    return (
        ([] if restored else ["round_trip"]),
        sig,
        "",
        "byte-identical restore" if restored else f"diverged; unmatched={ires.unmatched_cells}",
    )


def m_unify_split_round_trip(workdir: Path) -> _RetTuple:
    bil = baseline_bilingual(with_voiceover=("intro",), with_shared_code=True)
    try:
        de, en = split_text(bil)
        rt = unify_texts(de, en)
    except (SplitError, UnifyError) as e:
        return ["round_trip"], True, f"{type(e).__name__}: {e}", "raised"
    return (
        ([] if rt == bil else ["round_trip"]),
        False,
        "",
        "byte-identical" if rt == bil else "diverged",
    )


# --- cross-command voiceover seams (expected: break-silent) ----------------


def m_extract_then_split(workdir: Path) -> _RetTuple:
    bil = baseline_bilingual(with_voiceover=("intro", "setup"))
    slide = workdir / "slides_demo.py"
    slide.write_text(bil, encoding="utf-8", newline="\n")
    extract_voiceover(slide)  # bilingual voiceover_demo.py
    bilingual_comp = companion_path(slide)
    split_in_file(slide)  # slides_demo.de.py / .en.py
    de_comp = companion_path(workdir / "slides_demo.de.py")
    en_comp = companion_path(workdir / "slides_demo.en.py")
    orphaned = bilingual_comp.exists() and not de_comp.exists() and not en_comp.exists()
    return (
        (["companion_orphan"] if orphaned else []),
        False,
        "",
        "bilingual voiceover_demo.py orphaned; no .de/.en companion; split is silent"
        if orphaned
        else "companion handled",
    )


def m_inline_after_rename(workdir: Path) -> _RetTuple:
    de, _ = split_text(baseline_bilingual(with_voiceover=("intro",)))
    slide = workdir / "slides_demo.de.py"
    slide.write_text(de, encoding="utf-8", newline="\n")
    extract_voiceover(slide)
    comp = companion_path(slide)
    txt = slide.read_text(encoding="utf-8").replace('slide_id="intro"', 'slide_id="introduction"')
    slide.write_text(txt, encoding="utf-8", newline="\n")
    ires = inline_voiceover(slide)
    # Fixed: the unmatched cell is retained in the companion (recoverable),
    # not destroyed; the CLI also exits non-zero on unmatched.
    vo_recoverable = comp.exists() and "Voiceover DE for intro" in comp.read_text(encoding="utf-8")
    destroyed = ires.unmatched_cells > 0 and not vo_recoverable
    signaled = ires.companion_retained or ires.unmatched_cells > 0
    return (
        (["companion_destroyed"] if destroyed else []),
        signaled,
        "companion_retained + unmatched>0 (CLI exits non-zero)" if signaled else "",
        f"companion retained with unmatched={ires.unmatched_cells}, recoverable"
        if not destroyed
        else f"companion unlinked with unmatched={ires.unmatched_cells}; data lost",
    )


def m_re_extract_over_edited_companion(workdir: Path) -> _RetTuple:
    de, _ = split_text(baseline_bilingual(with_voiceover=("intro",)))
    slide = workdir / "slides_demo.de.py"
    slide.write_text(de, encoding="utf-8", newline="\n")
    extract_voiceover(slide)
    comp = companion_path(slide)
    comp.write_text(
        comp.read_text(encoding="utf-8").replace("Voiceover DE for intro", "HAND EDITED narration"),
        encoding="utf-8",
        newline="\n",
    )
    # Re-add a voiceover cell to the slide so the empty-vo early-return does not fire.
    slide.write_text(
        slide.read_text(encoding="utf-8") + voiceover_cell("de", sid="setup", text="second VO"),
        encoding="utf-8",
        newline="\n",
    )
    # Fixed: re-extract refuses without force rather than clobbering the
    # hand-edited companion.
    try:
        extract_voiceover(slide)
        refused = False
    except VoiceoverError:
        refused = True
    preserved = "HAND EDITED narration" in comp.read_text(encoding="utf-8")
    return (
        ([] if preserved else ["companion_clobbered"]),
        refused,
        "VoiceoverError: refused without --force" if refused else "",
        "hand-edit preserved (extract refused without --force)"
        if preserved
        else "hand-edited companion content clobbered on re-extract",
    )


# --- build-merge primitive (observe-only: the `build` command is the arm) ---


def m_build_merge_unmatched(workdir: Path) -> _RetTuple:
    de, _ = split_text(baseline_bilingual(with_voiceover=("intro",)))
    slide = workdir / "slides_demo.de.py"
    slide.write_text(de, encoding="utf-8", newline="\n")
    extract_voiceover(slide)
    comp = companion_path(slide)
    slide_renamed = slide.read_text(encoding="utf-8").replace(
        'slide_id="intro"', 'slide_id="introduction"'
    )
    merged, unmatched = merge_voiceover_text(slide_renamed, comp.read_text(encoding="utf-8"))
    dropped = "Voiceover DE for intro" not in merged
    # merge_voiceover_text RETURNS unmatched; the build consumer is log-only/exit-0.
    return (
        (["narration_drop"] if dropped else []),
        False,
        "",
        f"VO dropped from output; unmatched={unmatched}; build merge is log-only",
    )


@dataclass
class Mutation:
    name: str
    path: str
    expected: str  # frozen baseline verdict (drift = regression or fix)
    asserted: bool  # whether the CI backstop freezes this row + main() exits on its drift
    run: Callable[[Path], _RetTuple]


MUTATIONS: list[Mutation] = [
    # The safe funnel — these MUST stay preserve (engine regression guard).
    Mutation("add-slide-one-half", "sync", PRESERVE, True, m_add_one_half),
    Mutation("reorder-one-half", "sync", PRESERVE, True, m_reorder_one_half),
    Mutation("delete-one-half", "sync", PRESERVE, True, m_delete_one_half),
    Mutation("edit-both-halves", "sync", PRESERVE, True, m_edit_both_halves),
    Mutation("copy-paste-dup", "sync", PRESERVE, True, m_copy_paste_dup),
    Mutation("hand-edit-id-then-sync", "sync", PRESERVE, True, m_hand_edit_id_then_sync),
    Mutation(
        "extract-inline-round-trip", "extract+inline", PRESERVE, True, m_extract_inline_round_trip
    ),
    Mutation("unify-split-round-trip", "split+unify", PRESERVE, True, m_unify_split_round_trip),
    # #162 defensive landed: per-file assign-ids on a split half is now
    # twin-aware (adopts the sibling's id instead of minting a divergent slug),
    # so both assign-ids rows flipped break-silent -> preserve.
    Mutation(
        "add-then-assign-ids-per-file",
        "assign-ids",
        PRESERVE,
        True,
        m_add_then_assign_ids_per_file,
    ),
    Mutation("born-split-assign-ids", "assign-ids", PRESERVE, True, m_born_split_assign_ids),
    # #162 detective landed: the pre-commit gate (`clm validate`) now catches a
    # committed split-half divergence — break-silent -> break-loud.
    Mutation("commit-without-sync", "no-gate", BREAK_LOUD, True, m_commit_without_sync),
    Mutation("extract-then-split", "extract+split", BREAK_SILENT, True, m_extract_then_split),
    # Tier-1 data-loss fixes landed: inline now retains the companion with the
    # unmatched cell (recoverable) and exits non-zero; extract refuses to clobber
    # an existing companion without --force. Both flipped break-silent -> preserve.
    Mutation("inline-after-rename", "inline", PRESERVE, True, m_inline_after_rename),
    Mutation(
        "re-extract-over-edited-companion",
        "extract",
        PRESERVE,
        True,
        m_re_extract_over_edited_companion,
    ),
    # Observe-only — the build-merge primitive reports; the `build` arm is TODO.
    Mutation("build-merge-unmatched", "build-merge", BREAK_SILENT, False, m_build_merge_unmatched),
]

# Deferred catalogue entries (design §6) — added once their behaviour is verified
# so a frozen verdict is never a guess: ``split-code-cell`` (def-my-fun id
# migration, expected preserve), ``rename-function-while-splitting`` (construct
# match fails -> defer/recover, expected break-loud), ``edit-heading-then-force``
# (per-file ``assign-ids --force`` regenerating divergently). Add as their own
# Mutation rows; the framework above needs no change.


def run(filter_path: str | None = None) -> list[Outcome]:
    """Run the mutation catalogue, classifying each (mutation, path) result."""
    outcomes: list[Outcome] = []
    for m in MUTATIONS:
        if filter_path is not None and m.path != filter_path:
            continue
        with _fresh_dir() as d:
            workdir = Path(d)
            try:
                violated, signaled, signal, detail = m.run(workdir)
                verdict = classify(violated, signaled)
            except Exception as e:  # harness / engine bug — surfaced, never swallowed
                verdict, violated, signaled, signal = ERROR, ["exception"], False, ""
                detail = f"{type(e).__name__}: {e}"
        outcomes.append(
            Outcome(
                name=m.name,
                path=m.path,
                verdict=verdict,
                violated=violated,
                signaled=signaled,
                signal=signal,
                detail=detail,
                expected=m.expected,
                asserted=m.asserted,
            )
        )
    return outcomes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def asserted_drift(outcomes: list[Outcome]) -> list[Outcome]:
    """Asserted rows whose observed verdict diverged from the frozen baseline."""
    return [o for o in outcomes if o.asserted and o.drifted]


def to_dict(outcomes: list[Outcome]) -> dict:
    return {
        "counts": dict(Counter(o.verdict for o in outcomes)),
        "drift": [o.name for o in asserted_drift(outcomes)],
        "rows": [
            {
                "mutation": o.name,
                "path": o.path,
                "verdict": o.verdict,
                "expected": o.expected,
                "asserted": o.asserted,
                "violated": o.violated,
                "signaled": o.signaled,
                "signal": o.signal,
                "detail": o.detail,
            }
            for o in outcomes
        ],
    }


def render(outcomes: list[Outcome]) -> str:
    counts = Counter(o.verdict for o in outcomes)
    lines = [
        "edit-dynamics fault-injection harness",
        "",
        f"  preserve={counts.get(PRESERVE, 0)}  break-loud={counts.get(BREAK_LOUD, 0)}  "
        f"break-silent={counts.get(BREAK_SILENT, 0)}  error={counts.get(ERROR, 0)}",
        "",
        f"  {'PATH':<16}{'MUTATION':<34}{'VERDICT':<14}NOTE",
        f"  {'-' * 14:<16}{'-' * 32:<34}{'-' * 12:<14}{'-' * 30}",
    ]
    for o in outcomes:
        marker = " !!DRIFT" if (o.asserted and o.drifted) else ("  (obs)" if not o.asserted else "")
        verdict = o.verdict + (f"→{o.expected}" if (o.asserted and o.drifted) else "")
        lines.append(f"  {o.path:<16}{o.name:<34}{verdict:<14}{o.detail}{marker}")

    work = [o for o in outcomes if o.verdict == BREAK_SILENT]
    if work:
        lines += ["", "BREAK-SILENT work-list (footguns to harden):"]
        for o in work:
            lines.append(f"  · [{o.path}] {o.name} — {o.detail}")

    drift = asserted_drift(outcomes)
    if drift:
        lines += ["", "DRIFT (asserted verdict changed — regression or a fix landed):"]
        for o in drift:
            lines.append(f"  ! {o.name}: expected {o.expected}, observed {o.verdict} — {o.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a human table")
    parser.add_argument("--path", default=None, help="run only mutations on this command path")
    args = parser.parse_args(argv)

    outcomes = run(filter_path=args.path)
    if args.json:
        print(json.dumps(to_dict(outcomes), indent=2))
    else:
        print(render(outcomes))
    # Exit non-zero only on DRIFT of an asserted row: a safe-funnel mutation that
    # regressed, or a known break that a fix flipped (update the baseline then).
    return 1 if asserted_drift(outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
