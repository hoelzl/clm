"""Tests for cross-language tag-only sync — the ``retag`` proposal (Issue #198).

A tag-only edit (e.g. adding ``keep``/``alt``) is invisible to the content hash,
so the body-diff classifier reports the cell as ``same``. These tests cover the
dedicated ``retag`` path that detects such a one-sided tag drift against the
watermark's recorded tag set and mirrors it onto the other half — no LLM, since
tags are language-independent.
"""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import apply_plan
from clm.slides.sync_plan import (
    BaselineCell,
    CurrentCell,
    SyncPlan,
    _maybe_retag,  # noqa: PLC2701 (white-box unit)
    _retag_direction,  # noqa: PLC2701 (white-box unit)
    build_sync_plan,
    ordered_sync_cells,
    watermark_rows,
    watermark_tag_map,
)
from clm.slides.sync_writeback import FileState, set_header_tags

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _md(lang: str, sid: str, tags: list[str], body: str) -> str:
    taglist = ", ".join(f'"{t}"' for t in tags)
    return f'# %% [markdown] lang="{lang}" tags=[{taglist}] slide_id="{sid}"\n{body}\n'


def _code(lang: str, sid: str | None, tags: list[str], body: str) -> str:
    attrs = f'lang="{lang}"'
    if tags:
        attrs += " tags=[" + ", ".join(f'"{t}"' for t in tags) + "]"
    if sid:
        attrs += f' slide_id="{sid}"'
    return f"# %% {attrs}\n{body}\n"


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path, *, tags: bool = True):
    """Record the current on-disk state as the watermark baseline.

    With ``tags=True`` the tag set is recorded too (the #198 column), keyed by the
    same position the row uses. ``tags=False`` simulates a pre-#198 watermark.
    """
    for lang, path in (("de", de_path), ("en", en_path)):
        synced = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in synced],
            tags={c.position: c.tags for c in synced} if tags else None,
        )


def _seed_widened(
    cache: SyncWatermarkCache, de_path: Path, en_path: Path, *, record_tags: bool = True
):
    """Record the current on-disk state as the **membership-widened** watermark.

    Mirrors ``sync_apply._record_watermark`` exactly (every non-j2 cell, the
    neutral cells once under ``shared``), so the baseline carries the id-less
    localized cells the Tier C pass keys on — which ``_seed_watermark`` (built from
    ``ordered_sync_cells``) deliberately omits. ``record_tags=False`` simulates a
    pre-#198 watermark with no recorded tag sets.
    """
    de_cells = parse_cells(de_path.read_text(encoding="utf-8"))
    en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
    de_rows, en_rows = watermark_rows(de_cells), watermark_rows(en_cells)
    de_tags, en_tags = watermark_tag_map(de_cells), watermark_tag_map(en_cells)
    for lang, rows, tags in (
        ("de", de_rows["de"], de_tags["de"]),
        ("en", en_rows["en"], en_tags["en"]),
        ("shared", de_rows["shared"], de_tags["shared"]),
    ):
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=rows,
            tags=tags if record_tags else None,
        )


def _tags_of(path: Path, sid: str) -> set[str]:
    cell = next(
        c for c in parse_cells(path.read_text(encoding="utf-8")) if c.metadata.slide_id == sid
    )
    return set(cell.metadata.tags)


def _idless_code_tags(path: Path, needle: str) -> set[str]:
    """Tags of the (id-less) code cell whose body contains ``needle``."""
    cell = next(
        c
        for c in parse_cells(path.read_text(encoding="utf-8"))
        if c.metadata.cell_type == "code" and needle in c.content
    )
    return set(cell.metadata.tags)


# ---------------------------------------------------------------------------
# Classifier — retag detection
# ---------------------------------------------------------------------------


class TestRetagClassification:
    def test_one_sided_markdown_tag_add_is_retag(self, tmp_path: Path):
        de = _md("de", "vec", ["subslide"], "# ## Vektoren\n# - Punkt")
        en = _md("en", "vec", ["subslide"], "# ## Vectors\n# - point")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "vec", ["subslide", "keep"], "# ## Vectors\n# - point"), encoding="utf-8"
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 1
            assert plan.count("edit") == 0
            (p,) = [p for p in plan.proposals if p.kind == "retag"]
            assert p.direction == "en->de"
            assert p.slide_id == "vec"
        finally:
            cache.close()

    def test_localized_code_tag_add_is_retag(self, tmp_path: Path):
        # An id'd localized code cell (CODE_ROLE) — the same shape as the #198 hit,
        # promoted with a slide_id so it has a cross-language identity.
        de = _md("de", "s", ["slide"], "# ## S") + _code("de", "inv", [], 'a = invoke("Frage")')
        en = _md("en", "s", ["slide"], "# ## S") + _code("en", "inv", [], 'a = invoke("question")')
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "s", ["slide"], "# ## S")
                + _code("en", "inv", ["keep"], 'a = invoke("question")'),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 1
            (p,) = [p for p in plan.proposals if p.kind == "retag"]
            assert p.direction == "en->de"
            assert p.role == "code"
        finally:
            cache.close()

    def test_both_sides_tag_change_is_warning_not_retag(self, tmp_path: Path):
        de = _md("de", "vec", ["subslide"], "# ## Vektoren")
        en = _md("en", "vec", ["subslide"], "# ## Vectors")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(_md("de", "vec", ["subslide", "keep"], "# ## Vektoren"), "utf-8")
            en_path.write_text(_md("en", "vec", ["subslide", "alt"], "# ## Vectors"), "utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0
            assert any("tags changed on both decks" in i.reason for i in plan.issues)
        finally:
            cache.close()

    def test_pre_198_watermark_without_tags_skips_retag(self, tmp_path: Path):
        de = _md("de", "vec", ["subslide"], "# ## Vektoren")
        en = _md("en", "vec", ["subslide"], "# ## Vectors")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path, tags=False)  # no recorded tags
            en_path.write_text(_md("en", "vec", ["subslide", "keep"], "# ## Vectors"), "utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            # Direction undeterminable without a tag baseline → never guessed.
            assert plan.count("retag") == 0
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# Apply — retag writes only the header, mirrors tags, advances watermark
# ---------------------------------------------------------------------------


class TestRetagApply:
    def test_retag_mirrors_tags_and_advances_watermark(self, tmp_path: Path):
        de = _md("de", "vec", ["subslide"], "# ## Vektoren\n# - Punkt")
        en = _md("en", "vec", ["subslide"], "# ## Vectors\n# - point")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "vec", ["subslide", "keep"], "# ## Vectors\n# - point"), "utf-8"
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            # No judge / translator needed — a tag mirror is language-independent.
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            assert result.applied_retag == 1
            assert result.errors == []
            assert result.watermark_recorded
            # DE now carries the keep tag; its body is untouched.
            assert _tags_of(de_path, "vec") == {"subslide", "keep"}
            assert "# - Punkt" in de_path.read_text(encoding="utf-8")
        finally:
            cache.close()

    def test_retag_is_idempotent(self, tmp_path: Path):
        de = _md("de", "vec", ["subslide"], "# ## Vektoren")
        en = _md("en", "vec", ["subslide"], "# ## Vectors")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(_md("en", "vec", ["subslide", "keep"], "# ## Vectors"), "utf-8")
            apply_plan(
                build_sync_plan(de_path, en_path, watermark_cache=cache),
                judge=None,
                watermark_cache=cache,
            )
            # Second run: both halves now agree and the watermark recorded the tags.
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan2.count("retag") == 0
            assert plan2.is_noop
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# Header / FileState primitives
# ---------------------------------------------------------------------------


class TestHeaderTagPrimitives:
    def test_set_header_tags_insert_replace_remove(self):
        assert set_header_tags('# %% lang="de"', ["keep"]) == '# %% lang="de" tags=["keep"]'
        assert (
            set_header_tags(
                '# %% [markdown] lang="de" tags=["subslide"] slide_id="x"', ["subslide", "keep"]
            )
            == '# %% [markdown] lang="de" tags=["subslide", "keep"] slide_id="x"'
        )
        assert set_header_tags('# %% lang="de" tags=["keep"]', []) == '# %% lang="de"'

    def test_replace_cell_tags_keeps_body_and_slide_id(self, tmp_path: Path):
        path = tmp_path / "deck.de.py"
        path.write_text(_md("de", "vec", ["subslide"], "# ## Vektoren\n# - Punkt"), "utf-8")
        state = FileState.load(path)
        assert state.replace_cell_tags("vec", "subslide", ["subslide", "keep"]) is True
        state.flush()
        text = path.read_text(encoding="utf-8")
        assert 'tags=["subslide", "keep"]' in text
        assert 'slide_id="vec"' in text
        assert "# - Punkt" in text

    def test_replace_cell_tags_missing_returns_false(self, tmp_path: Path):
        path = tmp_path / "deck.de.py"
        path.write_text(_md("de", "vec", ["subslide"], "# ## Vektoren"), "utf-8")
        state = FileState.load(path)
        assert state.replace_cell_tags("nope", "subslide", ["keep"]) is False
        assert state.dirty is False


# ---------------------------------------------------------------------------
# _maybe_retag — direct unit coverage of the decision rules
# ---------------------------------------------------------------------------


def _cur(tags: set[str]) -> CurrentCell:
    return CurrentCell(
        position=0,
        slide_id="x",
        role="slide",
        content_hash="h",
        line_number=1,
        tags=frozenset(tags),
    )


def _base(tags: set[str] | None) -> BaselineCell:
    t = frozenset(tags) if tags is not None else None
    return BaselineCell(position=0, slide_id="x", role="slide", content_hash="h", tags=t)


class TestMaybeRetag:
    def _plan(self) -> SyncPlan:
        return SyncPlan(
            de_path=Path("d.de.py"), en_path=Path("d.en.py"), baseline_source="watermark"
        )

    def test_de_changed_emits_de_to_en(self):
        plan = self._plan()
        _maybe_retag(
            plan,
            ("x", "slide"),
            "slide",
            _cur({"slide", "keep"}),
            _cur({"slide"}),
            _base({"slide"}),
            _base({"slide"}),
        )
        assert [p.kind for p in plan.proposals] == ["retag"]
        assert plan.proposals[0].direction == "de->en"

    def test_no_change_no_proposal(self):
        plan = self._plan()
        _maybe_retag(
            plan,
            ("x", "slide"),
            "slide",
            _cur({"slide"}),
            _cur({"slide"}),
            _base({"slide"}),
            _base({"slide"}),
        )
        assert plan.proposals == []

    def test_unknown_baseline_skips(self):
        plan = self._plan()
        _maybe_retag(
            plan,
            ("x", "slide"),
            "slide",
            _cur({"slide", "keep"}),
            _cur({"slide"}),
            _base(None),
            _base({"slide"}),
        )
        assert plan.proposals == []

    def test_preexisting_divergence_neither_changed_no_proposal(self):
        # Halves already differed at baseline and neither moved — not this edit's
        # doing, so retag stays out of it (the validator flags the standing drift).
        plan = self._plan()
        _maybe_retag(
            plan,
            ("x", "slide"),
            "slide",
            _cur({"slide", "keep"}),
            _cur({"slide"}),
            _base({"slide", "keep"}),
            _base({"slide"}),
        )
        assert plan.proposals == []


class TestRetagDirection:
    """The shared one-sided-drift decision rule (id'd and id-less paths)."""

    def test_de_only_change(self):
        assert _retag_direction(frozenset({"keep"}), frozenset(), frozenset(), frozenset()) == (
            "de->en"
        )

    def test_en_only_change(self):
        assert _retag_direction(frozenset(), frozenset({"keep"}), frozenset(), frozenset()) == (
            "en->de"
        )

    def test_both_changed(self):
        assert (
            _retag_direction(frozenset({"keep"}), frozenset({"alt"}), frozenset(), frozenset())
            == "both"
        )

    def test_already_consistent(self):
        assert _retag_direction(frozenset({"keep"}), frozenset({"keep"}), frozenset(), None) is None

    def test_unknown_baseline(self):
        assert _retag_direction(frozenset({"keep"}), frozenset(), None, frozenset()) is None

    def test_preexisting_divergence_neither_moved(self):
        # de differs from en, but each matches its own baseline → not this edit.
        assert (
            _retag_direction(frozenset({"keep"}), frozenset(), frozenset({"keep"}), frozenset())
            is None
        )


# ---------------------------------------------------------------------------
# Tier C — id-less localized cells (Issue #198 / #190 item 3)
# ---------------------------------------------------------------------------

# The exact #198 hit: an id-less localized code cell (``response = llm.invoke(...)``
# with different prose per language) that gained ``keep`` on one half. The per-cell
# engine can't key it (no slide_id), so the tag was dropped until Tier C.
_DE_INVOKE = 'response = llm.invoke("Was steht in Kapitel 3 über lineare Regression?")'
_EN_INVOKE = 'response = llm.invoke("What does Chapter 3 say about linear regression?")'


class TestIdlessLocalizedRetagClassification:
    def test_idless_localized_code_tag_add_is_retag(self, tmp_path: Path):
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            # Author adds keep to EN only (would previously be dropped).
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, ["keep"], _EN_INVOKE),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 1
            assert plan.count("edit") == 0
            (p,) = [p for p in plan.proposals if p.kind == "retag"]
            assert p.direction == "en->de"
            assert p.slide_id is None  # id-less
            assert p.role == "code"
            assert p.tags == ("keep",)
        finally:
            cache.close()

    def test_idless_localized_markdown_tag_add_is_retag(self, tmp_path: Path):
        # An id-less localized markdown cell (lang set, no slide_id, no narrative tag).
        de = _md("de", "s", ["slide"], "# ## S") + '# %% [markdown] lang="de"\n# Hinweis\n'
        en = _md("en", "s", ["slide"], "# ## S") + '# %% [markdown] lang="en"\n# Note\n'
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            de_path.write_text(
                _md("de", "s", ["slide"], "# ## S")
                + '# %% [markdown] lang="de" tags=["alt"]\n# Hinweis\n',
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 1
            (p,) = [p for p in plan.proposals if p.kind == "retag"]
            assert p.direction == "de->en"
            assert p.role == "markdown"
            assert p.tags == ("alt",)
        finally:
            cache.close()

    def test_idless_localized_both_sides_is_warning(self, tmp_path: Path):
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            de_path.write_text(
                _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, ["keep"], _DE_INVOKE),
                encoding="utf-8",
            )
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, ["alt"], _EN_INVOKE),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0
            assert any(
                "id-less localized" in i.reason and "both decks" in i.reason for i in plan.issues
            )
        finally:
            cache.close()

    def test_pre_198_watermark_skips_idless_retag(self, tmp_path: Path):
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path, record_tags=False)
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, ["keep"], _EN_INVOKE),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0  # no tag baseline → direction undeterminable
        finally:
            cache.close()

    def test_structural_drift_skips_idless_retag(self, tmp_path: Path):
        # A tag edit AND a new id-less localized cell on the same side: the stream
        # length no longer matches the baseline, so positional pairing is unsound
        # and the tag is conservatively not mirrored (the validator still flags it).
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG")
                + _code("en", None, ["keep"], _EN_INVOKE)
                + _code("en", None, [], "print(response)"),  # a NEW id-less localized cell
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0
        finally:
            cache.close()

    def test_no_change_no_idless_retag(self, tmp_path: Path):
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, ["keep"], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, ["keep"], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0
            assert plan.is_noop
        finally:
            cache.close()

    def test_body_edit_skips_idless_retag(self, tmp_path: Path):
        # A combined body + tag edit on an id-less localized cell: the body hash no
        # longer matches the baseline, so the tag is NOT mirrored here (the
        # structural pass re-translates the body and carries the source tags along).
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG")
                + _code(
                    "en", None, ["keep"], 'response = llm.invoke("a totally rewritten prompt")'
                ),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0  # body changed → not a tag-only drift
        finally:
            cache.close()

    def test_identical_body_idless_cells_are_not_auto_mirrored(self, tmp_path: Path):
        # Two id-less localized code cells with BYTE-IDENTICAL bodies. The body-hash
        # anchor cannot tell them apart (a swap leaves every position's hash matching),
        # so even a clean one-sided tag edit is declined for safety — mirroring it could
        # write the tag onto the wrong twin after an undetectable reorder. The validator
        # surfaces the resulting asymmetry instead.
        de = (
            _md("de", "s", ["slide"], "# ## S")
            + _code("de", None, [], "dup = 1")
            + _code("de", None, [], "dup = 1")
        )
        en = (
            _md("en", "s", ["slide"], "# ## S")
            + _code("en", None, [], "dup = 1")
            + _code("en", None, [], "dup = 1")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "s", ["slide"], "# ## S")
                + _code("en", None, ["keep"], "dup = 1")
                + _code("en", None, [], "dup = 1"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0  # non-unique body → declined, not guessed
        finally:
            cache.close()

    def test_identical_body_reorder_plus_concurrent_edit_no_wrong_mirror(self, tmp_path: Path):
        # The reviewer's worst case: two identical-body id-less cells, one swapped on DE
        # (a no-op visually, since bodies match) while EN independently edits a tag. The
        # body-uniqueness guard declines both positions, so EN never gains a tag its
        # author didn't write — no cross-deck divergence introduced by sync.
        de = (
            _md("de", "s", ["slide"], "# ## S")
            + _code("de", None, ["keep"], "dup = 1")
            + _code("de", None, [], "dup = 1")
        )
        en = (
            _md("en", "s", ["slide"], "# ## S")
            + _code("en", None, ["keep"], "dup = 1")
            + _code("en", None, [], "dup = 1")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            # DE: swap the two identical-body cells (keep moves to the 2nd).
            de_path.write_text(
                _md("de", "s", ["slide"], "# ## S")
                + _code("de", None, [], "dup = 1")
                + _code("de", None, ["keep"], "dup = 1"),
                encoding="utf-8",
            )
            # EN: independently add alt to the FIRST cell.
            en_path.write_text(
                _md("en", "s", ["slide"], "# ## S")
                + _code("en", None, ["keep", "alt"], "dup = 1")
                + _code("en", None, [], "dup = 1"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            assert result.applied_retag == 0  # nothing mirrored onto an ambiguous twin
            # EN keeps exactly what its author wrote; sync introduced no divergence.
            en_text = en_path.read_text(encoding="utf-8")
            assert 'tags=["keep", "alt"]' in en_text
        finally:
            cache.close()

    def test_reorder_with_differing_tags_never_mirrors_wrong_cell(self, tmp_path: Path):
        # Two id-less localized code cells with DIFFERENT tags. _streams_aligned
        # cannot tell them apart (both role-less code), so without the per-cell
        # body-hash anchor a one-sided reorder would mirror the wrong tags. The
        # anchor catches the body mismatch at each position and declines.
        de_cells = (
            _md("de", "s", ["slide"], "# ## S")
            + _code("de", None, ["keep"], "xval = 1")
            + _code("de", None, [], "yval = 2")
        )
        en_cells = (
            _md("en", "s", ["slide"], "# ## S")
            + _code("en", None, ["keep"], "xval = 1")
            + _code("en", None, [], "yval = 2")
        )
        de_path, en_path = _write_pair(tmp_path, de_cells, en_cells)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            # Reorder EN's two id-less code cells (tags travel with their bodies).
            en_path.write_text(
                _md("en", "s", ["slide"], "# ## S")
                + _code("en", None, [], "yval = 2")
                + _code("en", None, ["keep"], "xval = 1"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 0  # no wrong mirror despite aligned streams
        finally:
            cache.close()


class TestIdlessLocalizedRetagApply:
    def test_apply_mirrors_idless_code_tag_and_advances(self, tmp_path: Path):
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, ["keep"], _EN_INVOKE),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            assert result.applied_retag == 1
            assert result.errors == []
            assert result.watermark_recorded
            # DE id-less code cell now carries keep; its (German) body is untouched.
            assert _idless_code_tags(de_path, "Kapitel 3") == {"keep"}
            assert "Kapitel 3" in de_path.read_text(encoding="utf-8")
            # Second run is a no-op — the watermark recorded the mirrored tags.
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan2.count("retag") == 0
            assert plan2.is_noop
        finally:
            cache.close()

    def test_apply_mirrors_idless_tag_removal(self, tmp_path: Path):
        # Symmetric: removing a tag on one half is mirrored too.
        de = _md("de", "rag", ["slide"], "# ## RAG") + _code("de", None, ["keep"], _DE_INVOKE)
        en = _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, ["keep"], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "rag", ["slide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            assert result.applied_retag == 1
            assert result.errors == []
            assert _idless_code_tags(de_path, "Kapitel 3") == set()
        finally:
            cache.close()

    def test_mixed_idd_and_idless_retag_one_pass(self, tmp_path: Path):
        # An id'd slide gains a tag AND an id-less code cell gains a tag, same
        # direction, one pass — both mirror without a judge/translator.
        de = _md("de", "rag", ["subslide"], "# ## RAG") + _code("de", None, [], _DE_INVOKE)
        en = _md("en", "rag", ["subslide"], "# ## RAG") + _code("en", None, [], _EN_INVOKE)
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_widened(cache, de_path, en_path)
            en_path.write_text(
                _md("en", "rag", ["subslide", "keep"], "# ## RAG")
                + _code("en", None, ["keep"], _EN_INVOKE),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("retag") == 2  # one id'd (rag/subslide), one id-less (code)
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            assert result.applied_retag == 2
            assert result.errors == []
            assert _tags_of(de_path, "rag") == {"subslide", "keep"}
            assert _idless_code_tags(de_path, "Kapitel 3") == {"keep"}
        finally:
            cache.close()
