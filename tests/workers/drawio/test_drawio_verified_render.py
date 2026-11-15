"""Reproducibility check for Draw.io renders (issue #1245).

A Draw.io PNG export is byte-deterministic when it works, but under load it
can exit 0 with a partial image (same dimensions, tiles missing). The worker
therefore accepts a PNG only when it is confirmed: equal to the image already
on disk, or reproduced by a second render. These tests drive
``_verified_render`` with scripted renders and the worker end to end with a
scripted ``_render_once``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clm.infrastructure.database.job_queue import Job
from clm.infrastructure.database.schema import init_database
from clm.workers.drawio import drawio_worker
from clm.workers.drawio.drawio_worker import (
    MAX_VERIFY_RENDERS,
    NonReproducibleRenderError,
    _read_reference,
    _verified_render,
)

GOOD = b"\x89PNG good render"
PARTIAL = b"\x89PNG partial"
OTHER = b"\x89PNG yet another"


def _scripted(*renders: bytes) -> AsyncMock:
    return AsyncMock(side_effect=list(renders))


async def _run(renders: AsyncMock, reference: bytes | None, fmt: str = "png") -> bytes:
    with patch.object(drawio_worker, "_render_once", renders):
        return await _verified_render("<mxfile/>", fmt, reference, "test")


class TestVerifiedRender:
    @pytest.mark.asyncio
    async def test_match_with_existing_output_costs_one_render(self):
        renders = _scripted(GOOD)
        assert await _run(renders, reference=GOOD) == GOOD
        assert renders.await_count == 1

    @pytest.mark.asyncio
    async def test_without_reference_two_matching_renders_are_accepted(self):
        renders = _scripted(GOOD, GOOD)
        assert await _run(renders, reference=None) == GOOD
        assert renders.await_count == 2

    @pytest.mark.asyncio
    async def test_bad_reference_is_replaced_by_reproduced_render(self):
        # The incident in #1245: img-generated/ held the partial image.
        renders = _scripted(GOOD, GOOD)
        assert await _run(renders, reference=PARTIAL) == GOOD
        assert renders.await_count == 2

    @pytest.mark.asyncio
    async def test_partial_first_render_is_outvoted(self):
        renders = _scripted(PARTIAL, GOOD, GOOD)
        assert await _run(renders, reference=None) == GOOD
        assert renders.await_count == 3

    @pytest.mark.asyncio
    async def test_third_render_may_confirm_the_first(self):
        renders = _scripted(GOOD, PARTIAL, GOOD)
        assert await _run(renders, reference=None) == GOOD

    @pytest.mark.asyncio
    async def test_disagreeing_renders_raise(self):
        renders = _scripted(GOOD, PARTIAL, OTHER)
        with pytest.raises(NonReproducibleRenderError):
            await _run(renders, reference=None)
        assert renders.await_count == MAX_VERIFY_RENDERS

    @pytest.mark.asyncio
    async def test_unverified_format_renders_once(self):
        renders = _scripted(b"%PDF one")
        assert await _run(renders, reference=None, fmt="pdf") == b"%PDF one"
        assert renders.await_count == 1


class TestReadReference:
    def test_missing_file_is_no_reference(self, tmp_path: Path):
        assert _read_reference(tmp_path / "absent.png") is None

    def test_empty_file_is_no_reference(self, tmp_path: Path):
        f = tmp_path / "empty.png"
        f.write_bytes(b"")
        assert _read_reference(f) is None

    def test_existing_file_is_the_reference(self, tmp_path: Path):
        f = tmp_path / "img.png"
        f.write_bytes(GOOD)
        assert _read_reference(f) == GOOD


@pytest.fixture
def worker(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    init_database(db_path)
    w = drawio_worker.DrawioWorker(1, db_path)
    yield w
    w.job_queue.close()


def _job(tmp_path: Path, output_file: Path) -> Job:
    input_file = tmp_path / "diagram.drawio"
    input_file.write_text("<mxfile>content</mxfile>")
    return Job(
        id=7,
        job_type="drawio",
        input_file=str(input_file),
        output_file=str(output_file),
        content_hash="hash",
        payload={},
        status="processing",
        created_at=datetime.now(),
    )


class TestWorkerIntegration:
    @pytest.mark.asyncio
    async def test_bad_output_on_disk_heals(self, worker, tmp_path: Path):
        output = tmp_path / "img-generated" / "diagram.png"
        output.parent.mkdir()
        output.write_bytes(PARTIAL)
        with (
            patch.object(drawio_worker, "_render_once", _scripted(GOOD, GOOD)),
            patch.object(worker.job_queue, "add_to_cache") as add_to_cache,
        ):
            await worker._process_job_async(_job(tmp_path, output))
        assert output.read_bytes() == GOOD
        add_to_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_reproducible_render_leaves_output_and_cache_alone(
        self, worker, tmp_path: Path
    ):
        output = tmp_path / "diagram.png"
        output.write_bytes(GOOD)
        with (
            patch.object(drawio_worker, "_render_once", _scripted(PARTIAL, OTHER, b"third")),
            patch.object(worker.job_queue, "add_to_cache") as add_to_cache,
        ):
            with pytest.raises(NonReproducibleRenderError):
                await worker._process_job_async(_job(tmp_path, output))
        assert output.read_bytes() == GOOD
        add_to_cache.assert_not_called()
