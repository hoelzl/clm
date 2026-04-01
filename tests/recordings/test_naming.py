"""Tests for recordings workflow naming helpers."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from clm.recordings.workflow.naming import (
    DEFAULT_RAW_SUFFIX,
    final_filename,
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
        assert raw_filename("my_topic") == "my_topic--RAW.mp4"

    def test_custom_ext(self):
        assert raw_filename("my_topic", ext=".mkv") == "my_topic--RAW.mkv"

    def test_custom_suffix(self):
        assert raw_filename("my_topic", raw_suffix="__RAW") == "my_topic__RAW.mp4"

    def test_sanitizes_name(self):
        result = raw_filename("topic with $pecial chars!")
        assert "$" not in result
        assert "!" not in result
        assert result.endswith("--RAW.mp4")


class TestFinalFilename:
    def test_default(self):
        assert final_filename("my_topic") == "my_topic.mp4"

    def test_custom_ext(self):
        assert final_filename("my_topic", ext=".mkv") == "my_topic.mkv"

    def test_sanitizes_name(self):
        result = final_filename("topic<1>")
        assert "<" not in result
        assert result.endswith(".mp4")


class TestParseRawStem:
    def test_raw_stem(self):
        base, is_raw = parse_raw_stem("my_topic--RAW")
        assert base == "my_topic"
        assert is_raw is True

    def test_non_raw_stem(self):
        base, is_raw = parse_raw_stem("my_topic")
        assert base == "my_topic"
        assert is_raw is False

    def test_custom_suffix(self):
        base, is_raw = parse_raw_stem("topic__UNPROCESSED", raw_suffix="__UNPROCESSED")
        assert base == "topic"
        assert is_raw is True

    def test_partial_match_not_raw(self):
        base, is_raw = parse_raw_stem("topic--RA")
        assert base == "topic--RA"
        assert is_raw is False

    def test_suffix_in_middle_not_matched(self):
        base, is_raw = parse_raw_stem("--RAW_topic")
        assert is_raw is False

    def test_default_suffix_constant(self):
        assert DEFAULT_RAW_SUFFIX == "--RAW"
