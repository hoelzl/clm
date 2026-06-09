"""Corpus mutation oracle for ``clm slides sync`` (#289 P4 / architecture review #288).

The corpus *no-op* backstop (`test_sync_corpus_noop.py`) proves a clean pair does
not churn — it asserts nothing about whether an actual edit propagates. This file
is the missing **positive** oracle over real decks: for the first few corpus pairs
that are post-sync-clean, apply one scripted one-sided EN-half mutation per
change-type and assert the #269 cardinal invariant — the change is **propagated**
to the DE half **or alerted** (errors / deferral, watermark held); it is never
silently dropped while the run reports "decks already consistent".

The baseline is a watermark seeded from the pre-mutation pair (so each mutation is
a genuine one-sided change since the last sync), the translator/judge are static
no-network doubles, and everything runs on temp copies — the course repo is never
touched. Marked ``slow`` + ``integration`` and skipped when the corpus is absent
(CI, fresh clone); point at a corpus with ``CLM_SYNC_CORPUS_DIR``.
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


def _corpus_dir() -> Path | None:
    """The sync corpus root, or ``None`` when unavailable (mirrors the noop test)."""
    candidates: list[Path] = []
    env = os.environ.get("CLM_SYNC_CORPUS_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(r"C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides"))
    for cand in candidates:
        if not cand.is_dir():
            continue
        for de_path in cand.rglob("*.de.py"):
            en_path = de_path.with_name(de_path.name[: -len(".de.py")] + ".en.py")
            if en_path.exists():
                return cand
    return None


_CORPUS = _corpus_dir()

pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(_CORPUS is None, reason="PythonCourses sync corpus not available"),
]


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


# ---------------------------------------------------------------------------
# Clean-pair discovery + the per-mutation sync runner
# ---------------------------------------------------------------------------


# The cell-class features each mutation needs in its target pairs. Voiceover
# COMPANION FILES (voiceover_*.de.py — separate sidecars, not decks) are
# excluded from pairing, exactly like the CLI batch sweep does.
_FEATURES = ("neutral", "idless_loc", "vo", "keyed_md")


def _pair_features(de_path: Path) -> dict[str, int]:
    """Count each mutation-relevant cell class in the DE half (cheap, parse-only)."""
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
            plan, _result, _de, _en = _sync_pair(tmp, de_text, en_text, en_text)
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


def _sync_pair(tmp: Path, de_text: str, en_text: str, en_mutated: str, judge=None):
    """Seed a watermark from (de, en), apply the mutated EN half, run one sync."""
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de_text, encoding="utf-8")
    en_path.write_text(en_text, encoding="utf-8")
    wm = SyncWatermarkCache(tmp / "clm-llm.sqlite")
    try:
        _record_watermark(wm, de_path, en_path)
        en_path.write_text(en_mutated, encoding="utf-8")
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
# Scripted mutations (cell-level edits of the real EN half)
# ---------------------------------------------------------------------------


def _cells(text: str) -> tuple[str, list[RawCell]]:
    return split_cells(text, comment_token_for_path(Path("deck.en.py")))


def _append_body_line(cell: RawCell, line: str) -> None:
    """Insert ``line`` as the last non-blank body line (keeps trailing blanks)."""
    trailing = 0
    for body_line in reversed(cell.lines[1:]):
        if body_line == "":
            trailing += 1
        else:
            break
    cell.lines.insert(len(cell.lines) - trailing, line)


def _mutate_neutral_edit(en_text: str) -> str | None:
    """Edit the body of the first language-neutral code cell."""
    preamble, cells = _cells(en_text)
    for cell in cells:
        meta = cell.metadata
        if not meta.is_j2 and meta.lang is None and meta.cell_type == "code":
            _append_body_line(cell, f"# {_MARKER}")
            return reconstruct(preamble, cells)
    return None


def _mutate_neutral_add(en_text: str) -> str | None:
    """Insert a brand-new language-neutral code cell after the first one."""
    preamble, cells = _cells(en_text)
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


def _mutate_idless_localized_edit(en_text: str) -> str | None:
    """Edit the body of the first id-less localized EN cell (code or markdown)."""
    preamble, cells = _cells(en_text)
    for cell in cells:
        meta = cell.metadata
        if not meta.is_j2 and meta.lang == "en" and role_of(meta) is None:
            _append_body_line(cell, f"# {_MARKER}")
            return reconstruct(preamble, cells)
    return None


def _mutate_keyed_remove(en_text: str) -> tuple[str, str, str] | None:
    """Delete the first voiceover/notes companion; returns (text, slide_id, role)."""
    preamble, cells = _cells(en_text)
    for i, cell in enumerate(cells):
        meta = cell.metadata
        role = role_of(meta)
        if meta.lang == "en" and role in ("voiceover", "notes") and meta.slide_id:
            sid = meta.slide_id
            del cells[i]
            return reconstruct(preamble, cells), sid, role
    return None


def _mutate_retag(en_text: str) -> tuple[str, str] | None:
    """Add a tag to the first keyed EN markdown cell; returns (text, slide_id)."""
    preamble, cells = _cells(en_text)
    for cell in cells:
        meta = cell.metadata
        if meta.lang == "en" and role_of(meta) is not None and meta.slide_id:
            new_tags = [*meta.tags, _MARKER]
            cell.lines[0] = set_header_tags(cell.lines[0], new_tags)
            cell.metadata = parse_cell_header(cell.lines[0])
            return reconstruct(preamble, cells), meta.slide_id
    return None


def _mutate_keyed_edit(en_text: str) -> str | None:
    """Edit the body of the first voiceover/notes companion (judge-reconciled)."""
    preamble, cells = _cells(en_text)
    for cell in cells:
        meta = cell.metadata
        if meta.lang == "en" and role_of(meta) in ("voiceover", "notes") and meta.slide_id:
            _append_body_line(cell, f"# {_MARKER}")
            return reconstruct(preamble, cells)
    return None


# ---------------------------------------------------------------------------
# The oracle: every mutation propagates or alerts — never a silent drop
# ---------------------------------------------------------------------------


def _run_mutation(pairs, tmp_path, mutate, check):
    """Apply ``mutate`` to each selected pair; assert propagate-or-alert via ``check``.

    ``check(plan, result, de_after)`` returns True when the change demonstrably
    reached the DE half. The pairs were selected for carrying the mutation's
    target cell class, so the mutation must find a target in every one.
    """
    if not pairs:
        pytest.skip("no clean corpus pair carries this mutation's cell class")
    propagated_somewhere = False
    for i, (name, de_text, en_text) in enumerate(pairs):
        prepared = mutate(en_text)
        assert prepared is not None, (
            f"{name}: selected for this cell class but the mutation found no target — "
            "selection and mutation disagree (test bug)"
        )
        en_mutated, judge = prepared
        tmp = tmp_path / f"m{i}"
        tmp.mkdir()
        plan, result, de_after, _ = _sync_pair(tmp, de_text, en_text, en_mutated, judge=judge)
        propagated = check(plan, result, de_after)
        # The #269 cardinal invariant, on a REAL deck: propagated or alerted,
        # never silently dropped while the run reports "decks already consistent".
        assert not _falsely_consistent(plan, result, propagated), (
            f"{name}: mutation silently dropped\nplan: {plan.summary()}"
        )
        assert propagated or _alerted(plan, result), (
            f"{name}: neither propagated nor alerted\nplan: {plan.summary()}\n"
            f"errors: {result.errors}"
        )
        if propagated:
            propagated_somewhere = True
            assert result.watermark_recorded, f"{name}: propagated but watermark held"
    # The clean common case on real decks is propagation, not an alert; if every
    # single pair only alerted, the engine has regressed into refuse-everything.
    assert propagated_somewhere, "mutation alerted on every pair — nothing propagated"


def test_neutral_code_edit_propagates(corpus_pairs, tmp_path):
    _run_mutation(
        corpus_pairs["neutral"],
        tmp_path,
        lambda en: (m, None) if (m := _mutate_neutral_edit(en)) else None,
        lambda plan, result, de_after: f"# {_MARKER}" in de_after,
    )


def test_neutral_code_add_propagates(corpus_pairs, tmp_path):
    _run_mutation(
        corpus_pairs["neutral"],
        tmp_path,
        lambda en: (m, None) if (m := _mutate_neutral_add(en)) else None,
        lambda plan, result, de_after: f'print("{_MARKER}")' in de_after,
    )


def test_idless_localized_edit_is_retranslated(corpus_pairs, tmp_path):
    _run_mutation(
        corpus_pairs["idless_loc"],
        tmp_path,
        lambda en: (m, None) if (m := _mutate_idless_localized_edit(en)) else None,
        lambda plan, result, de_after: _XL in de_after,
    )


def _de_has_key(de_text: str, sid: str, role: str) -> bool:
    """Whether the DE half still carries a cell with this ``(slide_id, role)``."""
    _preamble, cells = split_cells(de_text, comment_token_for_path(Path("deck.de.py")))
    return any(c.metadata.slide_id == sid and role_of(c.metadata) == role for c in cells)


def test_keyed_companion_remove_propagates(corpus_pairs, tmp_path):
    state: dict[str, tuple[str, str]] = {}

    def mutate(en_text):
        prepared = _mutate_keyed_remove(en_text)
        if prepared is None:
            return None
        text, sid, role = prepared
        state["key"] = (sid, role)
        return text, None

    def check(plan, result, de_after):
        sid, role = state["key"]
        # The DE twin — the cell with the SAME (slide_id, role) — is gone. The
        # raw id string may legitimately survive on the slide cell that shares it.
        return result.applied_remove >= 1 and not _de_has_key(de_after, sid, role)

    _run_mutation(corpus_pairs["vo"], tmp_path, mutate, check)


def test_keyed_retag_mirrors(corpus_pairs, tmp_path):
    _run_mutation(
        corpus_pairs["keyed_md"],
        tmp_path,
        lambda en: (m[0], None) if (m := _mutate_retag(en)) else None,
        lambda plan, result, de_after: result.applied_retag >= 1 and _MARKER in de_after,
    )


def test_keyed_companion_edit_is_judge_reconciled(corpus_pairs, tmp_path):
    _run_mutation(
        corpus_pairs["vo"],
        tmp_path,
        lambda en: (m, _StaticJudge()) if (m := _mutate_keyed_edit(en)) else None,
        lambda plan, result, de_after: _JUDGED in de_after,
    )
