"""Tests for :mod:`clm.core.output_write_registry`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.core.output_write_registry import (
    DEFAULT_HASH_LIMIT_MB,
    OutputWriteRegistry,
    WriteOutcome,
    _resolve_hash_limit_bytes,
    is_image_path,
)


def _abs(tmp_path: Path, *parts: str) -> Path:
    p = tmp_path.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class TestIsImagePath:
    def test_img_segment_in_middle(self):
        assert is_image_path(Path("/course/topic_x/img/diagram.png"))

    def test_img_segment_at_start(self):
        assert is_image_path(Path("img/diagram.png"))

    def test_no_img_segment(self):
        assert not is_image_path(Path("/course/topic_x/data/notes.md"))

    def test_substring_match_does_not_count(self):
        # "imgur" contains "img" as a substring, but parts split on separator
        assert not is_image_path(Path("/course/topic_x/imgur/diagram.png"))

    def test_nested_img(self):
        assert is_image_path(Path("/course/topic_x/img/charts/diagram.png"))


class TestResolveHashLimitBytes:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", raising=False)
        assert _resolve_hash_limit_bytes() == DEFAULT_HASH_LIMIT_MB * 1024 * 1024

    def test_explicit_value(self, monkeypatch):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "10")
        assert _resolve_hash_limit_bytes() == 10 * 1024 * 1024

    def test_zero_disables_hashing(self, monkeypatch):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        assert _resolve_hash_limit_bytes() == 0

    def test_invalid_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "not-a-number")
        assert _resolve_hash_limit_bytes() == DEFAULT_HASH_LIMIT_MB * 1024 * 1024

    def test_negative_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "-5")
        assert _resolve_hash_limit_bytes() == DEFAULT_HASH_LIMIT_MB * 1024 * 1024


class TestRecordWriteBytes:
    def test_first_write(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        result = registry.record_write(out, content=b"hello", source=Path("/src/a.txt"))

        assert result.outcome is WriteOutcome.FIRST_WRITE
        assert result.entry.output_path == out
        assert result.entry.first_writer_source == Path("/src/a.txt")
        assert result.entry.last_writer_source == Path("/src/a.txt")
        assert result.entry.dedup_count == 0
        assert result.entry.conflict_count == 0
        assert result.entry.content_hash  # non-empty hex string
        assert registry.total_dedups == 0
        assert registry.total_conflicts == 0

    def test_dedup_identical_second_write(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"hello", source=Path("/src/topic_1/a.txt"))
        result = registry.record_write(out, content=b"hello", source=Path("/src/topic_2/a.txt"))

        assert result.outcome is WriteOutcome.DEDUP
        assert result.entry.first_writer_source == Path("/src/topic_1/a.txt")
        assert result.entry.last_writer_source == Path("/src/topic_2/a.txt")
        assert result.entry.dedup_count == 1
        assert result.entry.conflict_count == 0
        assert registry.total_dedups == 1
        assert registry.total_conflicts == 0

    def test_conflict_on_differing_second_write(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"version 1", source=Path("/src/topic_1/a.txt"))
        result = registry.record_write(out, content=b"version 2", source=Path("/src/topic_2/a.txt"))

        assert result.outcome is WriteOutcome.CONFLICT
        # first_writer_source preserved; last_writer reflects the conflicting write
        assert result.entry.first_writer_source == Path("/src/topic_1/a.txt")
        assert result.entry.last_writer_source == Path("/src/topic_2/a.txt")
        assert result.entry.conflict_count == 1
        assert result.entry.dedup_count == 0
        assert registry.total_conflicts == 1
        assert registry.total_dedups == 0
        # content_hash now reflects last-writer-wins
        assert result.entry.content_hash == result.entry.last_writer_hash

    def test_three_writes_dedup_then_conflict(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        r1 = registry.record_write(out, content=b"A", source=Path("/s1"))
        r2 = registry.record_write(out, content=b"A", source=Path("/s2"))
        r3 = registry.record_write(out, content=b"B", source=Path("/s3"))

        assert r1.outcome is WriteOutcome.FIRST_WRITE
        assert r2.outcome is WriteOutcome.DEDUP
        assert r3.outcome is WriteOutcome.CONFLICT
        assert registry.total_dedups == 1
        assert registry.total_conflicts == 1

    def test_conflict_then_dedup_against_new_winner(self, tmp_path):
        # Once a conflict happens, the registry tracks the *latest* hash.
        # A subsequent write that matches the latest hash is a dedup, not a conflict.
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"A", source=Path("/s1"))
        registry.record_write(out, content=b"B", source=Path("/s2"))
        result = registry.record_write(out, content=b"B", source=Path("/s3"))

        assert result.outcome is WriteOutcome.DEDUP
        assert result.entry.dedup_count == 1
        assert result.entry.conflict_count == 1

    def test_distinct_output_paths_are_independent(self, tmp_path):
        registry = OutputWriteRegistry()
        out1 = _abs(tmp_path, "out", "a.txt")
        out2 = _abs(tmp_path, "out", "b.txt")
        r1 = registry.record_write(out1, content=b"same", source=Path("/s"))
        r2 = registry.record_write(out2, content=b"same", source=Path("/s"))
        assert r1.outcome is WriteOutcome.FIRST_WRITE
        assert r2.outcome is WriteOutcome.FIRST_WRITE
        assert len(registry.entries) == 2


class TestRecordWriteContentSource:
    def test_hashes_from_disk(self, tmp_path):
        registry = OutputWriteRegistry()
        src = _abs(tmp_path, "src", "a.txt")
        src.write_bytes(b"hello from disk")
        out = _abs(tmp_path, "out", "a.txt")
        result = registry.record_write(out, content_source=src, source=src)

        assert result.outcome is WriteOutcome.FIRST_WRITE
        assert result.entry.content_hash

    def test_dedup_across_content_and_content_source(self, tmp_path):
        # An in-memory write and an on-disk write with identical bytes must dedup.
        registry = OutputWriteRegistry()
        src = _abs(tmp_path, "src", "a.txt")
        src.write_bytes(b"hello")
        out = _abs(tmp_path, "out", "a.txt")

        r1 = registry.record_write(out, content=b"hello", source=Path("/mem"))
        r2 = registry.record_write(out, content_source=src, source=src)

        assert r1.outcome is WriteOutcome.FIRST_WRITE
        assert r2.outcome is WriteOutcome.DEDUP


class TestArgumentValidation:
    def test_requires_absolute_output_path(self, tmp_path):
        registry = OutputWriteRegistry()
        with pytest.raises(ValueError, match="absolute"):
            registry.record_write(Path("out/a.txt"), content=b"x")

    def test_requires_exactly_one_content_source(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        with pytest.raises(ValueError, match="exactly one"):
            registry.record_write(out)
        src = _abs(tmp_path, "src", "a.txt")
        src.write_bytes(b"x")
        with pytest.raises(ValueError, match="exactly one"):
            registry.record_write(out, content=b"x", content_source=src)


class TestLargeFileFastPath:
    def test_large_in_memory_skips_hashing(self, tmp_path, monkeypatch):
        # Drop the limit to 1 byte so anything triggers the large-file path.
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.bin")
        result = registry.record_write(out, content=b"big payload", source=Path("/s"))

        assert result.outcome is WriteOutcome.FIRST_WRITE
        assert result.entry.is_large_file
        assert result.entry.content_hash == ""

    def test_second_large_write_is_collision_not_dedup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.bin")
        registry.record_write(out, content=b"payload-A", source=Path("/s1"))
        result = registry.record_write(out, content=b"payload-A", source=Path("/s2"))

        # We can't compare contents above the limit, so even byte-identical
        # writes register as collisions (paranoid by design).
        assert result.outcome is WriteOutcome.LARGE_FILE_COLLISION
        assert result.entry.is_large_file
        assert registry.large_file_collision_count == 1

    def test_large_file_collision_counts_increment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.bin")
        registry.record_write(out, content=b"A", source=Path("/s1"))
        registry.record_write(out, content=b"B", source=Path("/s2"))
        registry.record_write(out, content=b"C", source=Path("/s3"))
        assert registry.large_file_collision_count == 2

    def test_path_equality_fast_path_via_content_source(self, tmp_path, monkeypatch):
        # Real on-disk file; threshold is bytes, not megabytes.
        # Use a 1 KB file and set the limit to 0 to force the large-file branch.
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()
        src = _abs(tmp_path, "src", "big.bin")
        src.write_bytes(b"x" * 1024)
        out = _abs(tmp_path, "out", "big.bin")

        result = registry.record_write(out, content_source=src, source=src)
        assert result.outcome is WriteOutcome.FIRST_WRITE
        assert result.entry.is_large_file


class TestSnapshotsAndClear:
    def test_entries_returns_copy(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"x", source=Path("/s"))

        snapshot = registry.entries
        snapshot[Path("/fake")] = snapshot[out]
        assert Path("/fake") not in registry.entries

    def test_conflict_entries_empty_when_none(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"x", source=Path("/s"))
        registry.record_write(out, content=b"x", source=Path("/s"))
        assert registry.conflict_entries == []

    def test_conflict_entries_contains_conflicts(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"x", source=Path("/s1"))
        registry.record_write(out, content=b"y", source=Path("/s2"))
        conflicts = registry.conflict_entries
        assert len(conflicts) == 1
        assert conflicts[0].output_path == out

    def test_clear_resets_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.bin")
        registry.record_write(out, content=b"A", source=Path("/s1"))
        registry.record_write(out, content=b"B", source=Path("/s2"))

        assert registry.large_file_collision_count == 1
        assert len(registry.entries) == 1

        registry.clear()
        assert registry.large_file_collision_count == 0
        assert registry.entries == {}
        assert registry.total_dedups == 0
        assert registry.total_conflicts == 0

    def test_get_returns_none_for_unknown_path(self, tmp_path):
        registry = OutputWriteRegistry()
        assert registry.get(_abs(tmp_path, "out", "missing.txt")) is None

    def test_get_returns_entry_for_known_path(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        registry.record_write(out, content=b"x", source=Path("/s"))
        assert registry.get(out) is not None


class TestHashLimitWiring:
    def test_default_limit_is_50_mb(self, monkeypatch):
        monkeypatch.delenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", raising=False)
        registry = OutputWriteRegistry()
        assert registry.hash_limit_bytes == 50 * 1024 * 1024

    def test_env_override_is_picked_up_at_construction(self, monkeypatch):
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "10")
        registry = OutputWriteRegistry()
        assert registry.hash_limit_bytes == 10 * 1024 * 1024


class TestIsDestinationIdentical:
    def test_returns_false_when_dest_missing(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "missing.txt")
        assert registry.is_destination_identical(out, content=b"hello") is False

    def test_returns_false_when_dest_is_directory(self, tmp_path):
        registry = OutputWriteRegistry()
        out = tmp_path / "out" / "subdir"
        out.mkdir(parents=True)
        assert registry.is_destination_identical(out, content=b"hello") is False

    def test_size_mismatch_short_circuits_without_hashing(self, tmp_path, monkeypatch):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        out.write_bytes(b"different size content")

        call_count = {"n": 0}

        def spy_hash_file(path):
            call_count["n"] += 1
            return ""

        monkeypatch.setattr("clm.core.output_write_registry._hash_file", spy_hash_file)

        assert registry.is_destination_identical(out, content=b"short") is False
        assert call_count["n"] == 0

    def test_size_matches_but_content_differs(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        out.write_bytes(b"hello")
        assert registry.is_destination_identical(out, content=b"world") is False

    def test_identical_via_content(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        out.write_bytes(b"hello")
        assert registry.is_destination_identical(out, content=b"hello") is True

    def test_identical_via_content_source(self, tmp_path):
        registry = OutputWriteRegistry()
        src = _abs(tmp_path, "src", "a.txt")
        src.write_bytes(b"hello from disk")
        out = _abs(tmp_path, "out", "a.txt")
        out.write_bytes(b"hello from disk")
        assert registry.is_destination_identical(out, content_source=src) is True

    def test_above_hash_limit_returns_false(self, monkeypatch, tmp_path):
        # Tiny limit so even a 100-byte file is "above limit".
        monkeypatch.setenv("CLM_OUTPUT_DEDUP_HASH_LIMIT_MB", "0")
        registry = OutputWriteRegistry()
        # hash_limit_bytes is now 0 — every non-empty source is "above limit".
        out = _abs(tmp_path, "out", "a.txt")
        out.write_bytes(b"hello")
        assert registry.is_destination_identical(out, content=b"hello") is False

    def test_relative_output_path_raises(self):
        registry = OutputWriteRegistry()
        with pytest.raises(ValueError, match="must be absolute"):
            registry.is_destination_identical(Path("rel/out.txt"), content=b"x")

    def test_both_content_and_content_source_raises(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        src = _abs(tmp_path, "src", "a.txt")
        src.write_bytes(b"x")
        with pytest.raises(ValueError, match="exactly one"):
            registry.is_destination_identical(out, content=b"x", content_source=src)

    def test_neither_content_nor_content_source_raises(self, tmp_path):
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        with pytest.raises(ValueError, match="exactly one"):
            registry.is_destination_identical(out)

    def test_does_not_modify_registry_state(self, tmp_path):
        # Pure query — must not be mistaken for a recorded write.
        registry = OutputWriteRegistry()
        out = _abs(tmp_path, "out", "a.txt")
        out.write_bytes(b"hello")
        registry.is_destination_identical(out, content=b"hello")
        assert registry.get(out) is None
        assert registry.total_dedups == 0
        assert registry.total_conflicts == 0
