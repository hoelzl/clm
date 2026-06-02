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
    build_sync_plan,
    ordered_sync_cells,
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


def _tags_of(path: Path, sid: str) -> set[str]:
    cell = next(
        c for c in parse_cells(path.read_text(encoding="utf-8")) if c.metadata.slide_id == sid
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
