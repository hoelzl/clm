"""A held jobs-DB lock must not freeze the Worker API's event loop (#799 C10).

Before, register, claim, heartbeat, cancelled, unregister, activate and the
cache routes ran their SQLite calls on the event loop: while another process
held the write lock, every other worker's request waited behind them.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest

from clm.infrastructure.api.server import WorkerApiServer
from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.database.schema import init_database
from tests.infrastructure.api.conftest import auth_headers

LOCK_SECONDS = 1.5


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    init_database(path)
    return path


def _hold_write_lock(db_path: Path, locked: threading.Event) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        locked.set()
        time.sleep(LOCK_SECONDS)
        conn.execute("ROLLBACK")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_a_heartbeat_waiting_on_the_lock_leaves_the_loop_free(db_path: Path) -> None:
    queue = JobQueue(db_path)
    worker_id = (
        queue._get_conn()
        .execute(
            "INSERT INTO workers (worker_type, container_id, status) "
            "VALUES ('notebook', 'c0ffee', 'idle') RETURNING id"
        )
        .fetchone()[0]
    )
    queue.close()
    server = WorkerApiServer(db_path)
    app = server._create_app()
    # ASGITransport doesn't run the lifespan, which sets this up.
    app.state.job_queue = JobQueue(db_path)
    app.state.db_path = db_path
    transport = httpx.ASGITransport(app=app)
    locked = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(db_path, locked))
    holder.start()
    try:
        assert locked.wait(timeout=10)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=auth_headers(server)
        ) as client:
            request = asyncio.create_task(
                client.post("/api/worker/heartbeat", json={"worker_id": worker_id})
            )
            # The first yield runs the request up to the lock. On the loop,
            # that blocks this sleep until the lock is released.
            start = time.monotonic()
            await asyncio.sleep(0.1)
            stalled = time.monotonic() - start

            response = await request
    finally:
        holder.join(timeout=10)

    assert response.status_code == 200
    # On the loop, this sleep would only end once the lock was released.
    assert stalled < LOCK_SECONDS / 2


def test_has_answers_without_loading_the_notebook(tmp_path: Path) -> None:
    import nbformat

    db = tmp_path / "cache.db"
    key = {"input_file": "deck.py", "content_hash": "h", "language": "en", "prog_lang": "python"}
    with ExecutedNotebookCache(db) as cache:
        assert cache.has(**key) is False
        cache.store(executed_notebook=nbformat.v4.new_notebook(), **key)

        assert cache.has(**key) is True
        assert cache.has(**{**key, "language": "de"}) is False
