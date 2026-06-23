"""Issue #429 end-to-end: a pure markdown re-wrap must not read as an edit.

The unit tests in ``test_reflow_hash`` pin the hash; this drives the change
classifier (``build_sync_plan``) to prove the incident — a soft re-wrap of a
markdown narrative cell — produces no ``edit`` proposal, while a real word change
still does.
"""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import build_sync_plan, ordered_sync_cells


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells],
        )


_DE_WRAPPED = _slide(
    "de", "intro", "# Dies ist ein langer Absatz der\n# über mehrere Zeilen umgebrochen ist."
)
_EN = _slide("en", "intro", "# This is a long paragraph that\n# wraps across several lines.")


def test_reflow_only_produces_no_edit(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(tmp_path, _DE_WRAPPED, _EN)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        # Re-wrap the DE paragraph at a different column — same words, new breaks.
        de_path.write_text(
            _slide(
                "de",
                "intro",
                "# Dies ist ein langer Absatz\n# der über mehrere Zeilen umgebrochen ist.",
            ),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        assert [p for p in plan.proposals if p.kind == "edit"] == []
    finally:
        cache.close()


def test_real_word_change_still_edits(tmp_path: Path) -> None:
    de_path, en_path = _write_pair(tmp_path, _DE_WRAPPED, _EN)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        # Change an actual word (not just the wrap) — a genuine edit.
        de_path.write_text(
            _slide(
                "de",
                "intro",
                "# Dies ist ein KURZER Absatz der\n# über mehrere Zeilen umgebrochen ist.",
            ),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        assert any(p.kind == "edit" and p.slide_id == "intro" for p in plan.proposals)
    finally:
        cache.close()
