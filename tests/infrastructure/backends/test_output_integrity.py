"""A job-cache hit must not reuse an output file whose content changed (#799 C6, C11).

The acceptance test from #799: a deliberately torn output file is rejected
rather than reused. Workers record each file's SHA-256 in the job-cache entry;
the host checks it before skipping the job.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from clm.infrastructure.utils.output_files import (
    COMPANION_DIGESTS_KEY,
    OUTPUT_DIGEST_KEY,
    recorded_digests,
    unusable_outputs,
    write_output_file,
    write_output_text,
)
from tests.infrastructure.backends.test_companion_outputs import (
    COMPANIONS,
    _backend,
    _DeckPayload,
    _Op,
)

MAIN = "int main() { return 0; }\n"


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _cache(temp_db: Path, payload: _DeckPayload, metadata: dict) -> None:
    queue = JobQueue(temp_db)
    try:
        queue.add_to_cache(payload.output_file, payload.content_hash(), metadata)
    finally:
        queue.close()


async def _submits_a_job(backend, payload: _DeckPayload) -> bool:
    try:
        await backend.execute_operation(_Op(), payload)
        return len(backend.active_jobs) == 1
    finally:
        backend.active_jobs.clear()
        await backend.shutdown()


class TestJobCacheHit:
    @pytest.mark.asyncio
    async def test_a_torn_output_file_is_rebuilt(self, temp_db, temp_workspace) -> None:
        """The #799 acceptance test: a half-written file must not count as a hit."""
        payload = _DeckPayload()
        _cache(temp_db, payload, {"format": "code", OUTPUT_DIGEST_KEY: _sha(MAIN)})
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(MAIN.encode("utf-8")[:10])

        assert await _submits_a_job(_backend(temp_db, temp_workspace), payload)

    @pytest.mark.asyncio
    async def test_an_intact_output_file_is_reused(self, temp_db, temp_workspace) -> None:
        payload = _DeckPayload()
        _cache(temp_db, payload, {"format": "code", OUTPUT_DIGEST_KEY: _sha(MAIN)})
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(MAIN.encode("utf-8"))

        assert not await _submits_a_job(_backend(temp_db, temp_workspace), payload)

    @pytest.mark.asyncio
    async def test_an_entry_without_digests_still_only_needs_the_file(
        self, temp_db, temp_workspace
    ) -> None:
        """Entries written before this change carry no digest; don't rebuild them all."""
        payload = _DeckPayload()
        _cache(temp_db, payload, {"format": "code"})
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True)
        output_path.write_text("whatever", encoding="utf-8")

        assert not await _submits_a_job(_backend(temp_db, temp_workspace), payload)

    @pytest.mark.asyncio
    async def test_a_changed_companion_file_is_rebuilt(self, temp_db, temp_workspace) -> None:
        payload = _DeckPayload()
        _cache(
            temp_db,
            payload,
            {
                "format": "code",
                OUTPUT_DIGEST_KEY: _sha(MAIN),
                "companion_files": COMPANIONS,
                COMPANION_DIGESTS_KEY: {name: _sha(name) for name in COMPANIONS},
            },
        )
        output_path = temp_workspace / payload.output_file
        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(MAIN.encode("utf-8"))
        (output_path.parent / COMPANIONS[0]).write_text(COMPANIONS[0], encoding="utf-8")
        (output_path.parent / COMPANIONS[1]).write_text("stale", encoding="utf-8")

        assert await _submits_a_job(_backend(temp_db, temp_workspace), payload)


class TestOutputFiles:
    def test_text_is_written_without_newline_translation(self, tmp_path: Path) -> None:
        path = tmp_path / "deck.html"

        digest = write_output_text(path, "a\nb\n")

        assert path.read_bytes() == b"a\nb\n"
        assert digest == hashlib.sha256(b"a\nb\n").hexdigest()

    def test_a_write_replaces_the_file_and_leaves_no_temp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "img.png"
        path.write_bytes(b"old content that is longer")

        write_output_file(path, b"new")

        assert path.read_bytes() == b"new"
        assert [p.name for p in tmp_path.iterdir()] == ["img.png"]

    def test_a_failed_write_keeps_the_previous_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of C11: a crash mid-write must not leave a torn file in place."""
        path = tmp_path / "img.png"
        path.write_bytes(b"previous")

        def fail(*_args, **_kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("clm.infrastructure.utils.path_utils.os.replace", fail)
        with pytest.raises(OSError):
            write_output_file(path, b"new")

        assert path.read_bytes() == b"previous"

    def test_unusable_outputs_names_each_problem(self, tmp_path: Path) -> None:
        good = tmp_path / "good"
        good.write_bytes(b"x")
        torn = tmp_path / "torn"
        torn.write_bytes(b"x")
        missing = tmp_path / "missing"

        problems = unusable_outputs(
            {
                good: hashlib.sha256(b"x").hexdigest(),
                torn: hashlib.sha256(b"xy").hexdigest(),
                missing: None,
            }
        )

        assert problems == [
            (torn, "content differs from the cached result"),
            (missing, "missing"),
        ]

    def test_malformed_digests_are_treated_as_absent(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        companion = tmp_path / "c.hpp"

        assert recorded_digests(
            out, [companion], {OUTPUT_DIGEST_KEY: 3, COMPANION_DIGESTS_KEY: "nope"}
        ) == {out: None, companion: None}
