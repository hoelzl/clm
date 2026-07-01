"""Corpus mutation oracle for ``clm slides sync`` (#289 P4 / #443 Phase B / #520 Phase 0).

The corpus *no-op* backstop (`test_sync_corpus_noop.py`) proves a clean pair does
not churn — it asserts nothing about whether an actual edit propagates. This file
is the missing **positive** oracle over real decks, in three arms:

- **One-sided mutations, EN half** (the original #289 suite): for the first few
  corpus pairs that are post-sync-clean, apply one scripted EN-half mutation per
  change-type and assert the #269 cardinal invariant — the change is
  **propagated** to the DE half **or alerted** (errors / deferral, watermark
  held); it is never silently dropped while the run reports "decks already
  consistent".
- **One-sided mutations, DE half** (#520 Phase 0): the same mutations applied to
  the DE half. The engine infers direction per cell from watermark drift, so
  DE→EN propagation must satisfy the same invariant; known DE/EN asymmetries
  (the #403 arc) are exactly what this arm pins down.
- **Both-sided mutations** (#520 Phase 0): the same cell edited *differently* on
  both halves since the last sync — a true conflict. The engine must never
  resolve one silently: keyed conflicts defer (watermark held for that key),
  id-less localized conflicts without a direction signal are plan errors, and
  the §7a shared-cell divergence auto-heal must converge *with a warning*, never
  silently. Both-sided-but-identical edits are NOT a conflict (report #4).

The baseline is a watermark seeded from the pre-mutation pair (so each mutation
is a genuine change since the last sync), the translator/judge are static
no-network doubles, and everything runs on temp copies — the course repo is
never touched. Runs in CI on the committed synthetic corpus
(``tests/data/sync_corpus``); a real PythonCourses checkout (env
``CLM_SYNC_CORPUS_DIR`` or the maintainer path) still wins for release-time
realism, adding the ``slow`` marker.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import SyncProposal
from clm.notebooks.slide_parser import comment_token_for_path, parse_cell_header
from clm.slides.raw_cells import RawCell, reconstruct, split_cells
from clm.slides.sync_apply import _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator
from clm.slides.sync_writeback import role_of, set_header_tags

# How many clean pairs each mutation is exercised on (the first N in path order
# that carry the mutation's target cell class and verify as post-sync-clean).
_N_PER_FEATURE = 3

_MARKER = "corpus-mutation-9bd1"  # greppable, never collides with real content
_XL = "<<CORPUS-XL-9bd1>>"  # the static translator's output for a re-translation
_JUDGED = f"# {_MARKER}-judged"  # the static judge's proposed target body
# Divergent per-half markers for the both-sided (conflict) arm.
_MARKER_DE = f"# {_MARKER}-de-side"
_MARKER_EN = f"# {_MARKER}-en-side"

_OTHER_SIDE = {"en": "de", "de": "en"}


# A tiny in-repo corpus (one bilingual deck carrying every mutation cell class,
# in post-sync-clean shape) committed so the oracle runs in CI — see Phase B of
# ``docs/claude/sync-corpus-mutation-443-investigation-handover.md``. It is the
# *fallback*: a real PythonCourses checkout (env or the maintainer path) still
# wins so the release-time run keeps its full-scale realism.
_BUNDLED_CORPUS = Path(__file__).resolve().parents[1] / "data" / "sync_corpus"


def _has_pair(cand: Path) -> bool:
    if not cand.is_dir():
        return False
    for de_path in cand.rglob("*.de.py"):
        if de_path.with_name(de_path.name[: -len(".de.py")] + ".en.py").exists():
            return True
    return False


def _corpus_dir() -> tuple[Path, bool] | None:
    """Resolve the sync corpus root and whether it is the bundled synthetic one.

    Order: ``CLM_SYNC_CORPUS_DIR`` env, the maintainer's local PythonCourses
    checkout, then the committed synthetic corpus (always present). Returns
    ``(root, is_bundled)`` — ``is_bundled`` drives the ``slow`` marker below.
    """
    external: list[Path] = []
    env = os.environ.get("CLM_SYNC_CORPUS_DIR")
    if env:
        external.append(Path(env))
    external.append(Path(r"C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides"))
    bundled = _BUNDLED_CORPUS.resolve()
    for cand in external:
        if _has_pair(cand):
            return cand, cand.resolve() == bundled
    if _has_pair(_BUNDLED_CORPUS):
        return _BUNDLED_CORPUS, True
    return None


_resolved = _corpus_dir()
_CORPUS = _resolved[0] if _resolved else None
_BUNDLED = _resolved[1] if _resolved else False

# The real PythonCourses corpus is large (rglob + a plan build per pair) — hence
# ``slow``, and CI (which excludes ``slow`` from every job) never runs it. The
# bundled synthetic corpus is one tiny deck and runs in seconds, so on the
# fallback we drop ``slow`` and the ``integration`` CI job picks the oracle up.
# No ``skipif``: the bundled corpus is always present.
pytestmark = [pytest.mark.integration]
if not _BUNDLED:
    pytestmark.append(pytest.mark.slow)


class _StaticJudge:
    """An update-always judge with a fixed proposed body (no network)."""

    def propose(self, source_body, target_body, *, source_lang, target_lang):  # noqa: ANN001
        return SyncProposal(verdict="update", proposed_text=_JUDGED)


def _alerted(plan, result) -> bool:
    return (
        plan.has_errors
        or result.has_errors
        or result.deferred > 0
        or any(i.severity == "error" for i in plan.issues)
    )


def _falsely_consistent(plan, result, propagated: bool) -> bool:
    """The forbidden state: reported consistent while a change was NOT handled."""
    return plan.is_noop and not propagated and not _alerted(plan, result)


def _warning_issues(plan) -> list:
    return [i for i in plan.issues if i.severity == "warning"]


# ---------------------------------------------------------------------------
# Clean-pair discovery + the per-mutation sync runner
# ---------------------------------------------------------------------------


# The cell-class features each mutation needs in its target pairs. Voiceover
# COMPANION FILES (voiceover_*.de.py — separate sidecars, not decks) are
# excluded from pairing, exactly like the CLI batch sweep does.
_FEATURES = ("neutral", "idless_loc", "idless_narr", "vo", "keyed_md")


def _pair_features(de_path: Path) -> dict[str, int]:
    """Count each mutation-relevant cell class in the DE half (cheap, parse-only).

    Counting one half suffices: pairs must verify post-sync-clean before a
    mutation runs, and a clean pair mirrors its cell classes across halves —
    so the DE census stands in for both the EN-side and DE-side arms.
    """
    from clm.notebooks.slide_parser import parse_cells

    cells = parse_cells(de_path.read_text(encoding="utf-8"), comment_token_for_path(de_path))
    out = dict.fromkeys(_FEATURES, 0)
    for c in cells:
        meta = c.metadata
        if meta.is_j2:
            continue
        if meta.lang is None and meta.cell_type == "code":
            out["neutral"] += 1
        if meta.lang == "de":
            role = role_of(meta)
            if role is None:
                out["idless_loc"] += 1
            elif role in ("voiceover", "notes") and not meta.slide_id:
                out["idless_narr"] += 1
            elif meta.slide_id:
                if role in ("voiceover", "notes"):
                    out["vo"] += 1
                if meta.cell_type != "code":
                    out["keyed_md"] += 1
    return out


@pytest.fixture(scope="module")
def corpus_pairs(tmp_path_factory) -> dict[str, list[tuple[str, str, str]]]:
    """Per feature, the first clean corpus pairs carrying that cell class.

    ``(name, de_text, en_text)`` triples, up to ``_N_PER_FEATURE`` per feature.
    A pair qualifies when its seeded plan is a **no-op** (seeding the watermark
    from the current files makes the baseline identical to them, so "no-op"
    selects exactly the post-sync-clean pairs) — each scripted mutation is then
    a genuine one-sided change since the last sync. The no-op check is cached
    per pair; the scan stops once every feature bucket is full.
    """
    assert _CORPUS is not None
    scratch = tmp_path_factory.mktemp("select")
    buckets: dict[str, list[tuple[str, str, str]]] = {key: [] for key in _FEATURES}
    noop_cache: dict[Path, tuple[str, str] | None] = {}

    def _clean_texts(de_path: Path, en_path: Path) -> tuple[str, str] | None:
        if de_path not in noop_cache:
            de_text = de_path.read_text(encoding="utf-8")
            en_text = en_path.read_text(encoding="utf-8")
            tmp = scratch / f"pair{len(noop_cache)}"
            tmp.mkdir()
            plan, _result, _de, _en = _sync_pair(tmp, de_text, en_text)
            noop_cache[de_path] = (de_text, en_text) if plan.is_noop else None
        return noop_cache[de_path]

    for de_path in sorted(_CORPUS.rglob("*.de.py")):
        if all(len(buckets[key]) >= _N_PER_FEATURE for key in _FEATURES):
            break
        if de_path.name.startswith("voiceover_"):
            continue  # a voiceover sidecar, not a deck
        en_path = de_path.with_name(de_path.name[: -len(".de.py")] + ".en.py")
        if not en_path.exists():
            continue
        features = _pair_features(de_path)
        wanted = [
            key for key in _FEATURES if features[key] > 0 and len(buckets[key]) < _N_PER_FEATURE
        ]
        if not wanted:
            continue
        texts = _clean_texts(de_path, en_path)
        if texts is None:
            continue
        for key in wanted:
            buckets[key].append((de_path.name, texts[0], texts[1]))
    if all(not pairs for pairs in buckets.values()):
        pytest.skip("no post-sync-clean pairs in the corpus")
    return buckets


def _sync_pair(
    tmp: Path,
    de_text: str,
    en_text: str,
    *,
    de_mutated: str | None = None,
    en_mutated: str | None = None,
    judge=None,
    newer: str | None = None,
):
    """Seed a watermark from (de, en), write the mutated half/halves, run one sync.

    ``newer`` pins which half carries the younger mtime ("de"/"en") — the §7a
    shared-cell divergence auto-heal picks its winner by mtime, and files
    written in quick succession otherwise tie nondeterministically.
    """
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de_text, encoding="utf-8")
    en_path.write_text(en_text, encoding="utf-8")
    wm = SyncWatermarkCache(tmp / "clm-llm.sqlite")
    try:
        _record_watermark(wm, de_path, en_path)
        if de_mutated is not None:
            de_path.write_text(de_mutated, encoding="utf-8")
        if en_mutated is not None:
            en_path.write_text(en_mutated, encoding="utf-8")
        if newer is not None:
            base = min(de_path.stat().st_mtime, en_path.stat().st_mtime)
            older, younger = (en_path, de_path) if newer == "de" else (de_path, en_path)
            os.utime(older, (base, base))
            os.utime(younger, (base + 10, base + 10))
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(
            plan,
            judge=judge,
            translator=StaticSlideTranslator(mapping={}, default=_XL),
            watermark_cache=wm,
        )
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Scripted mutations (cell-level edits of one half, parametrized by side)
# ---------------------------------------------------------------------------


def _cells(text: str, side: str) -> tuple[str, list[RawCell]]:
    return split_cells(text, comment_token_for_path(Path(f"deck.{side}.py")))


def _append_body_line(cell: RawCell, line: str) -> None:
    """Insert ``line`` as the last non-blank body line (keeps trailing blanks)."""
    trailing = 0
    for body_line in reversed(cell.lines[1:]):
        if body_line == "":
            trailing += 1
        else:
            break
    cell.lines.insert(len(cell.lines) - trailing, line)


def _mutate_neutral_edit(text: str, side: str, line: str = f"# {_MARKER}") -> str | None:
    """Edit the body of the first language-neutral code cell."""
    preamble, cells = _cells(text, side)
    for cell in cells:
        meta = cell.metadata
        if not meta.is_j2 and meta.lang is None and meta.cell_type == "code":
            _append_body_line(cell, line)
            return reconstruct(preamble, cells)
    return None


def _mutate_neutral_add(text: str, side: str) -> str | None:
    """Insert a brand-new language-neutral code cell after the first one."""
    preamble, cells = _cells(text, side)
    for i, cell in enumerate(cells):
        meta = cell.metadata
        if not meta.is_j2 and meta.lang is None and meta.cell_type == "code":
            header = "# %%"
            new = RawCell(
                lines=[header, f'print("{_MARKER}")', ""],
                line_number=0,
                metadata=parse_cell_header(header),
            )
            cells.insert(i + 1, new)
            return reconstruct(preamble, cells)
    return None


def _mutate_idless_localized_edit(text: str, side: str, line: str = f"# {_MARKER}") -> str | None:
    """Edit the body of the first id-less localized cell (code or markdown)."""
    preamble, cells = _cells(text, side)
    for cell in cells:
        meta = cell.metadata
        if not meta.is_j2 and meta.lang == side and role_of(meta) is None:
            _append_body_line(cell, line)
            return reconstruct(preamble, cells)
    return None


def _mutate_idless_narrative_edit(text: str, side: str, line: str = f"# {_MARKER}") -> str | None:
    """Edit the body of the first id-less voiceover/notes narrative cell."""
    preamble, cells = _cells(text, side)
    for cell in cells:
        meta = cell.metadata
        if (
            not meta.is_j2
            and meta.lang == side
            and role_of(meta) in ("voiceover", "notes")
            and not meta.slide_id
        ):
            _append_body_line(cell, line)
            return reconstruct(preamble, cells)
    return None


def _mutate_keyed_remove(text: str, side: str) -> tuple[str, str, str] | None:
    """Delete the first voiceover/notes companion; returns (text, slide_id, role)."""
    preamble, cells = _cells(text, side)
    for i, cell in enumerate(cells):
        meta = cell.metadata
        role = role_of(meta)
        if meta.lang == side and role in ("voiceover", "notes") and meta.slide_id:
            sid = meta.slide_id
            del cells[i]
            return reconstruct(preamble, cells), sid, role
    return None


def _mutate_retag(text: str, side: str) -> tuple[str, str] | None:
    """Add a tag to the first keyed markdown cell; returns (text, slide_id)."""
    preamble, cells = _cells(text, side)
    for cell in cells:
        meta = cell.metadata
        if meta.lang == side and role_of(meta) is not None and meta.slide_id:
            new_tags = [*meta.tags, _MARKER]
            cell.lines[0] = set_header_tags(cell.lines[0], new_tags)
            cell.metadata = parse_cell_header(cell.lines[0])
            return reconstruct(preamble, cells), meta.slide_id
    return None


def _mutate_keyed_edit(text: str, side: str, line: str = f"# {_MARKER}") -> str | None:
    """Edit the body of the first voiceover/notes companion (judge-reconciled)."""
    preamble, cells = _cells(text, side)
    for cell in cells:
        meta = cell.metadata
        if meta.lang == side and role_of(meta) in ("voiceover", "notes") and meta.slide_id:
            _append_body_line(cell, line)
            return reconstruct(preamble, cells)
    return None


def _keyed_cells(text: str, side: str) -> list[tuple[str, str, str]]:
    """Every keyed non-code cell on this half as ``(slide_id, role, cell_type)``."""
    _preamble, cells = _cells(text, side)
    out: list[tuple[str, str, str]] = []
    for cell in cells:
        meta = cell.metadata
        role = role_of(meta)
        if meta.lang == side and role is not None and meta.slide_id and meta.cell_type != "code":
            out.append((meta.slide_id, role, meta.cell_type))
    return out


def _common_keyed(
    de_text: str, en_text: str, *, roles: tuple[str, ...] | None = None
) -> tuple[str, str] | None:
    """The first ``(slide_id, role)`` keyed cell present on BOTH halves.

    A both-sided mutation must hit the *same* logical cell on each half;
    picking each half's "first" independently can select two different cells
    (real decks carry asymmetric companions — id'd on one half only, the #443
    shape — which are a different scenario with its own alert).
    """
    en_keys = {(sid, role) for sid, role, _ in _keyed_cells(en_text, "en")}
    for sid, role, _cell_type in _keyed_cells(de_text, "de"):
        if roles is not None and role not in roles:
            continue
        if (sid, role) in en_keys:
            return sid, role
    return None


def _edit_keyed_at(text: str, side: str, sid: str, role: str, line: str) -> str | None:
    """Append ``line`` to the body of the cell keyed (sid, role) on this half."""
    preamble, cells = _cells(text, side)
    for cell in cells:
        meta = cell.metadata
        if meta.lang == side and meta.slide_id == sid and role_of(meta) == role:
            _append_body_line(cell, line)
            return reconstruct(preamble, cells)
    return None


def _remove_keyed_at(text: str, side: str, sid: str, role: str) -> str | None:
    """Delete the cell keyed (sid, role) on this half."""
    preamble, cells = _cells(text, side)
    for i, cell in enumerate(cells):
        meta = cell.metadata
        if meta.lang == side and meta.slide_id == sid and role_of(meta) == role:
            del cells[i]
            return reconstruct(preamble, cells)
    return None


def _half_has_key(text: str, side: str, sid: str, role: str) -> bool:
    """Whether this half still carries a cell with this ``(slide_id, role)``."""
    _preamble, cells = _cells(text, side)
    return any(c.metadata.slide_id == sid and role_of(c.metadata) == role for c in cells)


# ---------------------------------------------------------------------------
# The one-sided oracle: every mutation propagates or alerts — never a silent
# drop. Parametrized over the mutated half: the engine infers direction per
# cell from watermark drift, so DE-side edits must propagate DE→EN exactly as
# EN-side edits propagate EN→DE.
# ---------------------------------------------------------------------------


def _run_mutation(pairs, tmp_path, mutate, check, side: str = "en"):
    """Apply ``mutate`` to each selected pair's ``side`` half; assert propagate-or-alert.

    ``mutate(text)`` receives the mutated half's text and returns
    ``(mutated_text, judge)`` or None. ``check(plan, result, twin_after)``
    returns True when the change demonstrably reached the OTHER half. The pairs
    were selected for carrying the mutation's target cell class, so the
    mutation must find a target in every one.
    """
    if not pairs:
        pytest.skip("no clean corpus pair carries this mutation's cell class")
    propagated_somewhere = False
    for i, (name, de_text, en_text) in enumerate(pairs):
        source_text = en_text if side == "en" else de_text
        prepared = mutate(source_text)
        assert prepared is not None, (
            f"{name}: selected for this cell class but the mutation found no target — "
            "selection and mutation disagree (test bug)"
        )
        mutated, judge = prepared
        tmp = tmp_path / f"m{i}"
        tmp.mkdir()
        mutated_kwargs = {"en_mutated": mutated} if side == "en" else {"de_mutated": mutated}
        plan, result, de_after, en_after = _sync_pair(
            tmp, de_text, en_text, judge=judge, **mutated_kwargs
        )
        twin_after = de_after if side == "en" else en_after
        propagated = check(plan, result, twin_after)
        # The #269 cardinal invariant, on a REAL deck: propagated or alerted,
        # never silently dropped while the run reports "decks already consistent".
        assert not _falsely_consistent(plan, result, propagated), (
            f"{name} [{side}-side]: mutation silently dropped\nplan: {plan.summary()}"
        )
        assert propagated or _alerted(plan, result), (
            f"{name} [{side}-side]: neither propagated nor alerted\n"
            f"plan: {plan.summary()}\nerrors: {result.errors}"
        )
        if propagated:
            propagated_somewhere = True
            assert result.watermark_recorded, f"{name} [{side}-side]: propagated but watermark held"
    # The clean common case on real decks is propagation, not an alert; if every
    # single pair only alerted, the engine has regressed into refuse-everything.
    assert propagated_somewhere, "mutation alerted on every pair — nothing propagated"


@pytest.mark.parametrize("side", ["en", "de"])
def test_neutral_code_edit_propagates(corpus_pairs, tmp_path, side):
    _run_mutation(
        corpus_pairs["neutral"],
        tmp_path,
        lambda text: (m, None) if (m := _mutate_neutral_edit(text, side)) else None,
        lambda plan, result, twin_after: f"# {_MARKER}" in twin_after,
        side=side,
    )


@pytest.mark.parametrize("side", ["en", "de"])
def test_neutral_code_add_propagates(corpus_pairs, tmp_path, side):
    _run_mutation(
        corpus_pairs["neutral"],
        tmp_path,
        lambda text: (m, None) if (m := _mutate_neutral_add(text, side)) else None,
        lambda plan, result, twin_after: f'print("{_MARKER}")' in twin_after,
        side=side,
    )


@pytest.mark.parametrize("side", ["en", "de"])
def test_idless_localized_edit_is_retranslated(corpus_pairs, tmp_path, side):
    _run_mutation(
        corpus_pairs["idless_loc"],
        tmp_path,
        lambda text: (m, None) if (m := _mutate_idless_localized_edit(text, side)) else None,
        lambda plan, result, twin_after: _XL in twin_after,
        side=side,
    )


@pytest.mark.parametrize("side", ["en", "de"])
def test_idless_narrative_edit_is_judge_reconciled(corpus_pairs, tmp_path, side):
    # Id-less voiceover/notes narratives travel the positional vo_anchor path
    # (#199/#403), not the keyed diff — the arm of the engine where one-sided
    # edits have historically gone missing (#443 was next door). Narrative
    # edits are judge-reconciled (without a judge the engine alerts instead);
    # a silent drop never satisfies #269.
    _run_mutation(
        corpus_pairs["idless_narr"],
        tmp_path,
        lambda text: (
            (m, _StaticJudge()) if (m := _mutate_idless_narrative_edit(text, side)) else None
        ),
        lambda plan, result, twin_after: _JUDGED in twin_after,
        side=side,
    )


@pytest.mark.parametrize("side", ["en", "de"])
def test_keyed_companion_remove_propagates(corpus_pairs, tmp_path, side):
    state: dict[str, tuple[str, str]] = {}

    def mutate(text):
        prepared = _mutate_keyed_remove(text, side)
        if prepared is None:
            return None
        mutated, sid, role = prepared
        state["key"] = (sid, role)
        return mutated, None

    def check(plan, result, twin_after):
        sid, role = state["key"]
        # The twin — the cell with the SAME (slide_id, role) — is gone. The
        # raw id string may legitimately survive on the slide cell that shares it.
        return result.applied_remove >= 1 and not _half_has_key(
            twin_after, _OTHER_SIDE[side], sid, role
        )

    _run_mutation(corpus_pairs["vo"], tmp_path, mutate, check, side=side)


@pytest.mark.parametrize("side", ["en", "de"])
def test_keyed_retag_mirrors(corpus_pairs, tmp_path, side):
    _run_mutation(
        corpus_pairs["keyed_md"],
        tmp_path,
        lambda text: (m[0], None) if (m := _mutate_retag(text, side)) else None,
        lambda plan, result, twin_after: result.applied_retag >= 1 and _MARKER in twin_after,
        side=side,
    )


@pytest.mark.parametrize("side", ["en", "de"])
def test_keyed_companion_edit_is_judge_reconciled(corpus_pairs, tmp_path, side):
    _run_mutation(
        corpus_pairs["vo"],
        tmp_path,
        lambda text: (m, _StaticJudge()) if (m := _mutate_keyed_edit(text, side)) else None,
        lambda plan, result, twin_after: _JUDGED in twin_after,
        side=side,
    )


# ---------------------------------------------------------------------------
# The both-sided oracle: a true conflict (the same cell edited differently on
# both halves since the last sync) must never be resolved silently. Keyed
# conflicts defer; id-less localized conflicts without a direction signal are
# plan errors; the §7a shared-cell auto-heal converges with a warning. In every
# case the run must not report "decks already consistent".
# ---------------------------------------------------------------------------


def _run_both_sided(pairs, tmp_path, mutate_both, check_outcome, *, newer=None):
    """Apply ``mutate_both`` to each selected pair; assert via ``check_outcome``.

    ``mutate_both(de_text, en_text)`` returns ``(de_mutated, en_mutated, judge)``
    or None — None skips the pair, because a both-sided mutation needs its
    target present on BOTH halves and the (one-half) bucket selection cannot
    guarantee that (real decks carry asymmetric companions, the #443 shape).
    ``check_outcome(name, plan, result, de_after, en_after)`` raises on
    violation. Unlike the one-sided arm there is no propagated_somewhere
    requirement — the correct outcome for a true conflict is an alert, not a
    propagation.
    """
    if not pairs:
        pytest.skip("no clean corpus pair carries this mutation's cell class")
    exercised = 0
    for i, (name, de_text, en_text) in enumerate(pairs):
        prepared = mutate_both(de_text, en_text)
        if prepared is None:
            continue
        de_mutated, en_mutated, judge = prepared
        tmp = tmp_path / f"c{i}"
        tmp.mkdir()
        plan, result, de_after, en_after = _sync_pair(
            tmp,
            de_text,
            en_text,
            de_mutated=de_mutated,
            en_mutated=en_mutated,
            judge=judge,
            newer=newer,
        )
        check_outcome(name, plan, result, de_after, en_after)
        exercised += 1
    if not exercised:
        pytest.skip("no selected pair carries this mutation's target on both halves")


def test_keyed_md_both_edited_defers_and_preserves_both(corpus_pairs, tmp_path):
    # Divergent edits to the same keyed markdown cell → Proposal kind="conflict";
    # with no decisions the apply DEFERS (deferred counts as alerted) and holds
    # the watermark for that key. Neither half's edit may be overwritten.
    def mutate_both(de_text, en_text):
        key = _common_keyed(de_text, en_text)
        if key is None:
            return None
        sid, role = key
        de_mutated = _edit_keyed_at(de_text, "de", sid, role, _MARKER_DE)
        en_mutated = _edit_keyed_at(en_text, "en", sid, role, _MARKER_EN)
        if de_mutated is None or en_mutated is None:
            return None
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        assert not _falsely_consistent(plan, result, propagated=False), (
            f"{name}: both-sided keyed edit reported consistent\nplan: {plan.summary()}"
        )
        assert _alerted(plan, result), (
            f"{name}: both-sided keyed edit neither deferred nor errored\nplan: {plan.summary()}"
        )
        assert _MARKER_DE in de_after, f"{name}: DE edit silently overwritten"
        assert _MARKER_EN in en_after, f"{name}: EN edit silently overwritten"

    _run_both_sided(corpus_pairs["keyed_md"], tmp_path, mutate_both, check_outcome)


def test_keyed_companion_both_edited_defers_and_preserves_both(corpus_pairs, tmp_path):
    # The same conflict on a keyed voiceover/notes companion (the narrative
    # keyed path — #443's neighborhood). judge=None keeps the defer: a judge
    # could legitimately down-grade equivalent halves, and these are not.
    def mutate_both(de_text, en_text):
        key = _common_keyed(de_text, en_text, roles=("voiceover", "notes"))
        if key is None:
            return None
        sid, role = key
        de_mutated = _edit_keyed_at(de_text, "de", sid, role, _MARKER_DE)
        en_mutated = _edit_keyed_at(en_text, "en", sid, role, _MARKER_EN)
        if de_mutated is None or en_mutated is None:
            return None
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        assert not _falsely_consistent(plan, result, propagated=False), (
            f"{name}: both-sided companion edit reported consistent\nplan: {plan.summary()}"
        )
        assert _alerted(plan, result), (
            f"{name}: both-sided companion edit neither deferred nor errored\n"
            f"plan: {plan.summary()}"
        )
        assert _MARKER_DE in de_after, f"{name}: DE edit silently overwritten"
        assert _MARKER_EN in en_after, f"{name}: EN edit silently overwritten"

    _run_both_sided(corpus_pairs["vo"], tmp_path, mutate_both, check_outcome)


def test_keyed_companion_remove_vs_edit_alerts(corpus_pairs, tmp_path):
    # One half removes the keyed companion, the other edits it — the
    # remove-vs-edit conflict. Auto-resolving either way loses data: the
    # engine must alert, keep the DE edit, and not resurrect/remove anything.
    state: dict[str, tuple[str, str]] = {}

    def mutate_both(de_text, en_text):
        key = _common_keyed(de_text, en_text, roles=("voiceover", "notes"))
        if key is None:
            return None
        sid, role = key
        de_mutated = _edit_keyed_at(de_text, "de", sid, role, _MARKER_DE)
        en_mutated = _remove_keyed_at(en_text, "en", sid, role)
        if de_mutated is None or en_mutated is None:
            return None
        state["key"] = (sid, role)
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        sid, role = state["key"]
        assert not _falsely_consistent(plan, result, propagated=False), (
            f"{name}: remove-vs-edit reported consistent\nplan: {plan.summary()}"
        )
        assert _alerted(plan, result), (
            f"{name}: remove-vs-edit neither deferred nor errored\nplan: {plan.summary()}"
        )
        assert _MARKER_DE in de_after and _half_has_key(de_after, "de", sid, role), (
            f"{name}: the edited DE companion was removed despite the conflict"
        )

    _run_both_sided(corpus_pairs["vo"], tmp_path, mutate_both, check_outcome)


def test_neutral_both_edited_converges_with_warning(corpus_pairs, tmp_path, monkeypatch):
    # A shared (language-neutral) cell edited differently on both halves is §7a
    # divergence: the DEFAULT policy auto-heals toward the newer half's content
    # — acceptable only because it is LOUD (a warning issue) and convergent.
    # This pins that contract: convergence to the pinned-newer (EN) content,
    # never a silent split-brain and never a silent no-op. (In "error" mode the
    # run errors instead; the default is what unattended syncs actually get.)
    monkeypatch.delenv("CLM_SYNC__SHARED_DIVERGENCE", raising=False)

    def mutate_both(de_text, en_text):
        de_mutated = _mutate_neutral_edit(de_text, "de", _MARKER_DE)
        en_mutated = _mutate_neutral_edit(en_text, "en", _MARKER_EN)
        if de_mutated is None or en_mutated is None:
            return None
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        converged = _MARKER_EN in de_after and _MARKER_DE not in de_after
        assert not _falsely_consistent(plan, result, propagated=converged), (
            f"{name}: shared-cell divergence reported consistent\nplan: {plan.summary()}"
        )
        if _alerted(plan, result):
            # Error-path outcome (e.g. an mtime tie despite pinning): loud, and
            # nothing may have been half-written.
            assert _MARKER_DE in de_after and _MARKER_EN in en_after, (
                f"{name}: divergence errored but an edit was still overwritten"
            )
            return
        # Auto-heal outcome: the pinned-newer EN content must have won on BOTH
        # halves, and the losing DE edit's disappearance must be announced.
        assert converged and _MARKER_EN in en_after, (
            f"{name}: divergence auto-heal did not converge to the newer half\n"
            f"plan: {plan.summary()}"
        )
        assert _warning_issues(plan), (
            f"{name}: divergence auto-healed silently — no warning issue\nplan: {plan.summary()}"
        )

    _run_both_sided(corpus_pairs["neutral"], tmp_path, mutate_both, check_outcome, newer="en")


def test_idless_localized_both_edited_alerts_and_preserves_both(corpus_pairs, tmp_path):
    # An id-less localized cell edited on both halves has no keyed baseline and
    # (with no other drift in the pair) no direction signal: the engine must
    # refuse loudly — a conflict proposal that defers, or a plan error — and
    # must not pick a side.
    def mutate_both(de_text, en_text):
        de_mutated = _mutate_idless_localized_edit(de_text, "de", _MARKER_DE)
        en_mutated = _mutate_idless_localized_edit(en_text, "en", _MARKER_EN)
        if de_mutated is None or en_mutated is None:
            return None
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        assert not _falsely_consistent(plan, result, propagated=False), (
            f"{name}: both-sided id-less edit reported consistent\nplan: {plan.summary()}"
        )
        assert _alerted(plan, result), (
            f"{name}: both-sided id-less edit neither deferred nor errored\nplan: {plan.summary()}"
        )
        assert _MARKER_DE in de_after, f"{name}: DE edit silently overwritten"
        assert _MARKER_EN in en_after, f"{name}: EN edit silently overwritten"

    _run_both_sided(corpus_pairs["idless_loc"], tmp_path, mutate_both, check_outcome)


def test_idless_narrative_both_edited_alerts_and_preserves_both(corpus_pairs, tmp_path):
    # Both-sided divergence on an id-less voiceover/notes narrative (the
    # positional vo_anchor arm). Same contract: alert, pick no side.
    def mutate_both(de_text, en_text):
        de_mutated = _mutate_idless_narrative_edit(de_text, "de", _MARKER_DE)
        en_mutated = _mutate_idless_narrative_edit(en_text, "en", _MARKER_EN)
        if de_mutated is None or en_mutated is None:
            return None
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        assert not _falsely_consistent(plan, result, propagated=False), (
            f"{name}: both-sided narrative edit reported consistent\nplan: {plan.summary()}"
        )
        assert _alerted(plan, result), (
            f"{name}: both-sided narrative edit neither deferred nor errored\n"
            f"plan: {plan.summary()}"
        )
        assert _MARKER_DE in de_after, f"{name}: DE edit silently overwritten"
        assert _MARKER_EN in en_after, f"{name}: EN edit silently overwritten"

    _run_both_sided(corpus_pairs["idless_narr"], tmp_path, mutate_both, check_outcome)


def test_neutral_both_edited_identically_is_not_a_conflict(corpus_pairs, tmp_path):
    # Both halves of a shared (language-neutral) cell edited to byte-equal
    # content is NOT a divergence — the halves still agree, there is nothing to
    # reconcile (report #4). The run must neither defer nor error, and the
    # (identical) edits must survive. Localized cells have no byte-equal
    # notion across halves, so this contract only exists for shared cells.
    same_line = f"# {_MARKER}-same"

    def mutate_both(de_text, en_text):
        de_mutated = _mutate_neutral_edit(de_text, "de", same_line)
        en_mutated = _mutate_neutral_edit(en_text, "en", same_line)
        if de_mutated is None or en_mutated is None:
            return None
        return de_mutated, en_mutated, None

    def check_outcome(name, plan, result, de_after, en_after):
        assert not plan.has_errors and not result.has_errors, (
            f"{name}: identical both-sided edits errored\nplan: {plan.summary()}\n"
            f"errors: {result.errors}"
        )
        assert result.deferred == 0, (
            f"{name}: identical both-sided edits were deferred as a conflict\n"
            f"plan: {plan.summary()}"
        )
        assert same_line in de_after and same_line in en_after, (
            f"{name}: an identical both-sided edit was reverted"
        )

    _run_both_sided(corpus_pairs["neutral"], tmp_path, mutate_both, check_outcome)
