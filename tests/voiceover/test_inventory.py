"""Tests for clm.voiceover.inventory lookup helpers."""

from __future__ import annotations

import json
from pathlib import Path

from clm.voiceover.inventory import (
    InventoryEntry,
    find_videos_for_slide,
    load_inventory,
)


def _write_inventory(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


class TestLoadInventory:
    def test_parses_minimal_entry(self, tmp_path: Path):
        inv = tmp_path / "inv.json"
        _write_inventory(
            inv,
            [
                {
                    "path": str(tmp_path / "v.mp4"),
                    "matched_slide": "slides/a/slides.py",
                    "match_score": 0.9,
                    "freshness": "fresh",
                }
            ],
        )

        entries = load_inventory(inv)
        assert len(entries) == 1
        e = entries[0]
        assert e.video_path.name == "v.mp4"
        assert e.matched_slide == Path("slides/a/slides.py")
        assert e.match_score == 0.9
        assert e.freshness == "fresh"

    def test_skips_entries_without_path(self, tmp_path: Path):
        inv = tmp_path / "inv.json"
        _write_inventory(
            inv,
            [
                {"matched_slide": "slides/a.py"},  # no path
                {"path": str(tmp_path / "v.mp4"), "matched_slide": "slides/a.py"},
            ],
        )
        entries = load_inventory(inv)
        assert len(entries) == 1

    def test_accepts_null_matched_slide(self, tmp_path: Path):
        inv = tmp_path / "inv.json"
        _write_inventory(
            inv,
            [{"path": str(tmp_path / "v.mp4")}],
        )
        entries = load_inventory(inv)
        assert entries[0].matched_slide is None


class TestFindVideosForSlide:
    def test_finds_single_video(self, tmp_path: Path):
        slide = tmp_path / "slides" / "a" / "slides.py"
        slide.parent.mkdir(parents=True)
        slide.write_text("# %%\n", encoding="utf-8")

        video = tmp_path / "v.mp4"
        video.write_bytes(b"")

        entries = [
            InventoryEntry(
                video_path=video,
                matched_slide=Path("slides/a/slides.py"),
                match_score=None,
                freshness=None,
                raw={},
            ),
        ]
        found = find_videos_for_slide(entries, slide, inventory_base=tmp_path)
        assert len(found) == 1
        assert found[0].video_path == video

    def test_finds_multi_part_videos_in_order(self, tmp_path: Path):
        slide = tmp_path / "slides.py"
        slide.write_text("# %%\n", encoding="utf-8")

        entries = [
            InventoryEntry(
                video_path=Path(f"part{i}.mp4"),
                matched_slide=Path("slides.py"),
                match_score=None,
                freshness=None,
                raw={},
            )
            for i in range(1, 4)
        ]
        found = find_videos_for_slide(entries, slide, inventory_base=tmp_path)
        assert [e.video_path.name for e in found] == ["part1.mp4", "part2.mp4", "part3.mp4"]

    def test_ignores_unrelated_entries(self, tmp_path: Path):
        slide = tmp_path / "a.py"
        slide.write_text("# %%\n", encoding="utf-8")
        other = tmp_path / "b.py"
        other.write_text("# %%\n", encoding="utf-8")

        entries = [
            InventoryEntry(
                video_path=Path("v1.mp4"),
                matched_slide=Path("a.py"),
                match_score=None,
                freshness=None,
                raw={},
            ),
            InventoryEntry(
                video_path=Path("v2.mp4"),
                matched_slide=Path("b.py"),
                match_score=None,
                freshness=None,
                raw={},
            ),
        ]
        found = find_videos_for_slide(entries, slide, inventory_base=tmp_path)
        assert len(found) == 1
        assert found[0].video_path.name == "v1.mp4"

    def test_absolute_matched_slide_path(self, tmp_path: Path):
        slide = tmp_path / "slides.py"
        slide.write_text("# %%\n", encoding="utf-8")

        entries = [
            InventoryEntry(
                video_path=Path("v.mp4"),
                matched_slide=slide.resolve(),
                match_score=None,
                freshness=None,
                raw={},
            ),
        ]
        found = find_videos_for_slide(entries, slide, inventory_base=tmp_path)
        assert len(found) == 1
