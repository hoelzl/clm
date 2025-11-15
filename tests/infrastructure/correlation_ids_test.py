import asyncio
import logging

from clx.infrastructure.messaging.correlation_ids import (
    CorrelationData,
    all_correlation_ids,
    new_correlation_id,
    clear_correlation_ids,
    active_correlation_ids,
    remove_correlation_id,
)


async def test_clear_correlation_ids():
    await new_correlation_id()
    assert len(active_correlation_ids) != 0

    await clear_correlation_ids()
    assert len(active_correlation_ids) == 0


async def test_new_correlation_id():
    await clear_correlation_ids()

    cid = await new_correlation_id()

    assert cid is not None
    assert cid in active_correlation_ids
    data = all_correlation_ids.get(cid)
    assert data == CorrelationData(
        correlation_id=cid,
        task=asyncio.current_task(),
        start_time=data.start_time,
    )


async def test_new_correlation_ids_are_different():
    cids = set()
    for i in range(10):
        new_cid = await new_correlation_id()
        cids.add(new_cid)
    assert len(cids) == 10


async def test_remove_correlation_id_removes_existing_correlation_id():
    await clear_correlation_ids()
    cid1 = await new_correlation_id()
    cid2 = await new_correlation_id()
    assert set(active_correlation_ids.keys()) == {cid1, cid2}
    assert set(all_correlation_ids.keys()) == {cid1, cid2}

    await remove_correlation_id(cid1)

    assert set(active_correlation_ids.keys()) == {cid2}
    assert set(all_correlation_ids.keys()) == {cid1, cid2}


async def test_remove_correlation_id_warns_on_non_existing_correlation_id(caplog):
    await clear_correlation_ids()

    caplog.set_level(logging.DEBUG, logger='clx.infrastructure.messaging.correlation_ids')
    await remove_correlation_id("non-existing-correlation-id")
    assert len(caplog.record_tuples) == 1
    assert caplog.record_tuples[0][1] == logging.DEBUG
    assert "non-existing-correlation-id" in caplog.record_tuples[0][2]
