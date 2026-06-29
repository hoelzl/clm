"""Issue #458 end-to-end: a pure markdown re-wrap of a ``//`` deck is not an edit.

The ``#`` version (``test_sync_reflow_no_edit``) proved Python/Rust decks; this proves
the C++/C#/Java/TS (``//``) decks #458 extended the reflow benefit to. The deck files
use a ``.cpp`` extension so ``comment_token_for_path`` resolves ``"//"``, and the cells
use the ``// %%`` boundary + ``// ``-prefixed prose.
"""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import build_sync_plan, ordered_sync_cells


def _slide(lang: str, sid: str, body: str) -> str:
    return f'// %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.cpp"
    en_path = tmp_path / "deck.en.cpp"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    for lang, path in (("de", de_path), ("en", en_path)):
        # Parse with the // token so the watermark seed keys cells the same way the
        # plan (comment_token_for_path) will — otherwise the seed mis-parses the deck.
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8"), "//"), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells],
        )


_DE_WRAPPED = _slide(
    "de", "intro", "// Dies ist ein langer Absatz der\n// über mehrere Zeilen umgebrochen ist."
)
_EN = _slide("en", "intro", "// This is a long paragraph that\n// wraps across several lines.")


def test_slash_reflow_only_produces_no_edit(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(tmp_path, _DE_WRAPPED, _EN)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        # Re-wrap the DE paragraph at a different column — same words, new breaks.
        de_path.write_text(
            _slide(
                "de",
                "intro",
                "// Dies ist ein langer Absatz\n// der über mehrere Zeilen umgebrochen ist.",
            ),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        assert [p for p in plan.proposals if p.kind == "edit"] == []
    finally:
        cache.close()


def test_slash_real_word_change_still_edits(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(tmp_path, _DE_WRAPPED, _EN)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        de_path.write_text(
            _slide(
                "de",
                "intro",
                "// Dies ist ein KURZER Absatz der\n// über mehrere Zeilen umgebrochen ist.",
            ),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        assert any(p.kind == "edit" and p.slide_id == "intro" for p in plan.proposals)
    finally:
        cache.close()
