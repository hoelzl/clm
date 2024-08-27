import logging

from clx_faststream_backend.correlation_ids import (new_correlation_id,
                                                    clear_correlation_ids,
                                                    correlation_ids,
                                                    remove_correlation_id, )


def test_clear_correlation_ids():
    new_correlation_id()
    assert len(correlation_ids) != 0

    clear_correlation_ids()
    assert len(correlation_ids) == 0


def test_new_correlation_id():
    clear_correlation_ids()

    cid = new_correlation_id(my_data="My Data")

    assert cid is not None
    data = correlation_ids.get(cid)
    assert data == {"correlation_id": cid, "my_data": "My Data"}


def test_new_correlation_ids_are_different():
    cids = set()
    for i in range(10):
        cids.add(new_correlation_id())
    assert len(cids) == 10


def test_remove_correlation_id_removes_existing_correlation_id():
    clear_correlation_ids()
    cid1 = new_correlation_id()
    cid2 = new_correlation_id()
    assert set(correlation_ids.keys()) == {cid1, cid2}

    remove_correlation_id(cid1)
    assert set(correlation_ids.keys()) == {cid2}


def test_remove_correlation_id_warns_on_non_existing_correlation_id(caplog):
    clear_correlation_ids()

    caplog.set_level(logging.DEBUG)
    remove_correlation_id("non-existing-correlation-id")
    assert len(caplog.record_tuples) == 1
    assert caplog.record_tuples[0][1] == logging.DEBUG
    assert "non-existing-correlation-id" in caplog.record_tuples[0][2]