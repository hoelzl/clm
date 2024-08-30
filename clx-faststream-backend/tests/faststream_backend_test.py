import asyncio
import logging
from pathlib import Path
from time import time

import pytest
from attrs import define, field

from clx_common.messaging.notebook_classes import NotebookPayload
from clx_common.messaging.correlation_ids import (
    active_correlation_ids,
    clear_correlation_ids,
    new_correlation_id,
)
from clx_faststream_backend.faststream_backend import FastStreamBackend

NOTEBOOK_TEXT = """\
# j2 from 'macros.j2' import header
# {{ header("Test DE", "Test EN") }}

# %% [markdown] lang="en" tags=["subslide"]
#
# ## This is a Heading

# %% [markdown] lang="de" tags=["subslide"]
#
# ## Das ist eine Überschrift

# %%
print("Hello World")
"""


@pytest.mark.broker
@pytest.mark.slow
async def test_wait_for_completion_waits():
    backend = FastStreamBackend()
    try:
        await backend.start()
        # Get a correlation ID so that shutdown times out.
        await new_correlation_id()
        start_time = time()
        wait_time = 2
        clean_shut_down = await backend.wait_for_completion(wait_time)
        end_time = time()

        assert not clean_shut_down
        assert (end_time - start_time) >= wait_time
    finally:
        await clear_correlation_ids()
        await backend.shutdown()


@pytest.mark.broker
@pytest.mark.slow
async def test_wait_for_completion_ends_before_timeout():
    # Check that wait for completion actually stops waiting soon after
    # the last outstanding correlation ID is returned

    # To test this, we await backend.wait_for_completion() with a timeout of 10s
    # after starting a task that clears the correlation_ids after one second
    # We should see that the time to finish is much less than 10s.

    async def clear_correlation_ids_after_delay():
        await asyncio.sleep(1)
        await clear_correlation_ids()

    backend = FastStreamBackend(stale_cid_scan_interval=0.2)
    try:
        await backend.start()
        # Get a correlation ID so that shutdown times out.
        await new_correlation_id()
        start_time = time()
        wait_time = 10
        loop = asyncio.get_running_loop()
        clear_cids_task = loop.create_task(clear_correlation_ids_after_delay())
        clean_shut_down = await backend.wait_for_completion(wait_time)
        end_time = time()
        await clear_cids_task

        assert clean_shut_down
        assert (end_time - start_time) < wait_time / 2
    finally:
        await clear_correlation_ids()
        await backend.shutdown()


@pytest.mark.broker
@pytest.mark.slow
async def test_notebook_files_are_processed(tmp_path, caplog):
    correlation_id = await new_correlation_id()
    payload = NotebookPayload(
        data=NOTEBOOK_TEXT,
        correlation_id=correlation_id,
        input_file=str(tmp_path / "test_notebook.py"),
        input_file_name="test_notebook.py",
        output_file=str(tmp_path / "A Test Notebook.py"),
        kind="completed",
        prog_lang="python",
        language="en",
        format="code",
        other_files={},
    )
    async with FastStreamBackend(stale_cid_scan_interval=0.2) as backend:
        from clx_common.messaging.correlation_ids import (
            all_correlation_ids,
            active_correlation_ids,
        )

        print(all_correlation_ids, active_correlation_ids)
        await backend.send_message("notebook-processor", payload)
        print(all_correlation_ids, active_correlation_ids)
        completed_successfully = await backend.wait_for_completion(10.0)
        print(all_correlation_ids, active_correlation_ids)

        assert completed_successfully
        notebook_path = Path(payload.output_file)
        assert notebook_path.exists()
        assert "<b>Test EN</b>" in notebook_path.read_text()
        # Ensure that the backend shuts down
        await clear_correlation_ids()


@pytest.mark.broker
@pytest.mark.slow
async def test_stale_correlation_ids_are_collected(caplog):
    _cid = await new_correlation_id()
    caplog.set_level(logging.WARNING)
    async with FastStreamBackend(
        stale_cid_max_lifetime=0.2, stale_cid_scan_interval=0.1
    ):
        assert len(active_correlation_ids) == 1

        await asyncio.sleep(0.5)

        assert len(active_correlation_ids) == 0
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"
        assert "Removing stale correlation id" in caplog.records[0].message


@define
class CidCollector:
    num_cids: list = field(factory=list)

    def __call__(self, cids):
        self.num_cids.append(len(cids))


@pytest.mark.broker
@pytest.mark.slow
async def test_active_correlation_ids_are_periodically_reported():
    await new_correlation_id()
    cid_collector = CidCollector()
    async with FastStreamBackend(
        cid_reporter_interval=0.1,
        start_cid_reporter=True,
        cid_reporter_fun=cid_collector,
    ):
        assert len(active_correlation_ids) == 1

        await asyncio.sleep(0.25)

        assert len(cid_collector.num_cids) >= 1
        assert cid_collector.num_cids[0] == 1
        await clear_correlation_ids()
