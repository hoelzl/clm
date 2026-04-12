"""Tests for recordings workflow naming helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from clm.recordings.workflow.naming import (
    DEFAULT_RAW_SUFFIX,
    final_filename,
    find_existing_recordings,
    parse_part,
    parse_raw_stem,
    raw_filename,
    recording_relative_dir,
)


class TestRecordingRelativeDir:
    def test_basic(self):
        result = recording_relative_dir("python-basics", "Week 1")
        assert result == PurePosixPath("python-basics/Week 1")

    def test_sanitizes_special_chars(self):
        result = recording_relative_dir("my/course", "section<1>")
        assert "/" not in result.parts[0]
        assert "<" not in str(result.parts[1])

    def test_preserves_underscores_and_hyphens(self):
        result = recording_relative_dir("ml-course_v2", "week_01")
        assert result == PurePosixPath("ml-course_v2/week_01")

    def test_sanitizes_csharp(self):
        result = recording_relative_dir("C# Basics", "Intro")
        assert "CSharp" in str(result.parts[0])


class TestRawFilename:
    def test_default(self):
        assert raw_filename("my_deck") == "my_deck--RAW.mp4"

    def test_custom_ext(self):
        assert raw_filename("my_deck", ext=".mkv") == "my_deck--RAW.mkv"

    def test_custom_suffix(self):
        assert raw_filename("my_deck", raw_suffix="__RAW") == "my_deck__RAW.mp4"

    def test_sanitizes_name(self):
        result = raw_filename("deck with $pecial chars!")
        assert "$" not in result
        assert "!" not in result
        assert result.endswith("--RAW.mp4")

    def test_part_zero_no_suffix(self):
        assert raw_filename("03 Intro", part=0) == "03 Intro--RAW.mp4"

    def test_part_one(self):
        assert raw_filename("03 Intro", part=1) == "03 Intro (part 1)--RAW.mp4"

    def test_part_two_mkv(self):
        assert raw_filename("03 Intro", ext=".mkv", part=2) == "03 Intro (part 2)--RAW.mkv"

    def test_part_one_german(self):
        assert raw_filename("03 Intro", part=1, lang="de") == "03 Intro (Teil 1)--RAW.mp4"

    def test_part_two_german(self):
        assert (
            raw_filename("03 Intro", ext=".mkv", part=2, lang="de") == "03 Intro (Teil 2)--RAW.mkv"
        )


class TestFinalFilename:
    def test_default(self):
        assert final_filename("my_deck") == "my_deck.mp4"

    def test_custom_ext(self):
        assert final_filename("my_deck", ext=".mkv") == "my_deck.mkv"

    def test_sanitizes_name(self):
        result = final_filename("deck<1>")
        assert "<" not in result
        assert result.endswith(".mp4")

    def test_part_zero_no_suffix(self):
        assert final_filename("03 Intro", part=0) == "03 Intro.mp4"

    def test_part_one(self):
        assert final_filename("03 Intro", part=1) == "03 Intro (part 1).mp4"

    def test_part_one_german(self):
        assert final_filename("03 Intro", part=1, lang="de") == "03 Intro (Teil 1).mp4"


class TestParseRawStem:
    def test_raw_stem(self):
        base, is_raw = parse_raw_stem("my_deck--RAW")
        assert base == "my_deck"
        assert is_raw is True

    def test_non_raw_stem(self):
        base, is_raw = parse_raw_stem("my_deck")
        assert base == "my_deck"
        assert is_raw is False

    def test_custom_suffix(self):
        base, is_raw = parse_raw_stem("deck__UNPROCESSED", raw_suffix="__UNPROCESSED")
        assert base == "deck"
        assert is_raw is True

    def test_partial_match_not_raw(self):
        base, is_raw = parse_raw_stem("deck--RA")
        assert base == "deck--RA"
        assert is_raw is False

    def test_suffix_in_middle_not_matched(self):
        base, is_raw = parse_raw_stem("--RAW_deck")
        assert is_raw is False

    def test_default_suffix_constant(self):
        assert DEFAULT_RAW_SUFFIX == "--RAW"

    def test_raw_stem_with_part(self):
        base, is_raw = parse_raw_stem("03 Intro (part 1)--RAW")
        assert base == "03 Intro (part 1)"
        assert is_raw is True

    def test_non_raw_stem_with_part(self):
        base, is_raw = parse_raw_stem("03 Intro (part 2)")
        assert base == "03 Intro (part 2)"
        assert is_raw is False

    def test_raw_stem_with_teil(self):
        base, is_raw = parse_raw_stem("03 Intro (Teil 1)--RAW")
        assert base == "03 Intro (Teil 1)"
        assert is_raw is True


class TestParsePart:
    def test_no_part(self):
        base, part = parse_part("03 Intro")
        assert base == "03 Intro"
        assert part == 0

    def test_part_one(self):
        base, part = parse_part("03 Intro (part 1)")
        assert base == "03 Intro"
        assert part == 1

    def test_part_large_number(self):
        base, part = parse_part("Deck Name (part 12)")
        assert base == "Deck Name"
        assert part == 12

    def test_not_a_part_suffix(self):
        base, part = parse_part("Something (notes)")
        assert base == "Something (notes)"
        assert part == 0

    def test_teil_one(self):
        base, part = parse_part("03 Intro (Teil 1)")
        assert base == "03 Intro"
        assert part == 1

    def test_teil_large_number(self):
        base, part = parse_part("Deck Name (Teil 5)")
        assert base == "Deck Name"
        assert part == 5

    def test_round_trip_raw(self):
        """raw_filename → parse_raw_stem → parse_part recovers original values."""
        name = raw_filename("03 Intro", ext=".mp4", part=3)
        stem = name.removesuffix(".mp4")
        base_with_part, is_raw = parse_raw_stem(stem)
        assert is_raw is True
        base, part = parse_part(base_with_part)
        assert base == "03 Intro"
        assert part == 3

    def test_round_trip_raw_german(self):
        """German raw_filename → parse_raw_stem → parse_part recovers original values."""
        name = raw_filename("03 Intro", ext=".mp4", part=2, lang="de")
        stem = name.removesuffix(".mp4")
        base_with_part, is_raw = parse_raw_stem(stem)
        assert is_raw is True
        base, part = parse_part(base_with_part)
        assert base == "03 Intro"
        assert part == 2


class TestFindExistingRecordings:
    def test_empty_directory(self, tmp_path: Path):
        assert find_existing_recordings(tmp_path, "03 Intro") == {}

    def test_nonexistent_directory(self, tmp_path: Path):
        assert find_existing_recordings(tmp_path / "nope", "03 Intro") == {}

    def test_finds_unsuffixed(self, tmp_path: Path):
        (tmp_path / "03 Intro--RAW.mkv").write_bytes(b"v")
        result = find_existing_recordings(tmp_path, "03 Intro")
        assert 0 in result
        assert result[0].name == "03 Intro--RAW.mkv"

    def test_finds_part_2(self, tmp_path: Path):
        (tmp_path / "03 Intro (part 2)--RAW.mkv").write_bytes(b"v")
        result = find_existing_recordings(tmp_path, "03 Intro")
        assert 2 in result
        assert result[2].name == "03 Intro (part 2)--RAW.mkv"

    def test_finds_multiple_parts(self, tmp_path: Path):
        (tmp_path / "03 Intro--RAW.mkv").write_bytes(b"v0")
        (tmp_path / "03 Intro (part 1)--RAW.mkv").write_bytes(b"v1")
        (tmp_path / "03 Intro (part 3)--RAW.mkv").write_bytes(b"v3")
        result = find_existing_recordings(tmp_path, "03 Intro")
        assert sorted(result.keys()) == [0, 1, 3]

    def test_ignores_non_matching_names(self, tmp_path: Path):
        (tmp_path / "04 Other--RAW.mkv").write_bytes(b"v")
        (tmp_path / "03 Intro.mkv").write_bytes(b"v")  # not raw
        result = find_existing_recordings(tmp_path, "03 Intro")
        assert result == {}

    def test_ignores_non_raw_files(self, tmp_path: Path):
        (tmp_path / "03 Intro.mkv").write_bytes(b"v")
        result = find_existing_recordings(tmp_path, "03 Intro")
        assert result == {}

    def test_finds_german_teil_suffix(self, tmp_path: Path):
        (tmp_path / "03 Intro (Teil 2)--RAW.mkv").write_bytes(b"v")
        result = find_existing_recordings(tmp_path, "03 Intro")
        assert 2 in result
        assert result[2].name == "03 Intro (Teil 2)--RAW.mkv"
