import sqlite3

import pytest

from clx_common.database.db_operations import DatabaseManager
from clx_common.messaging.base_classes import Result


# Assuming Result class structure. Adjust as necessary.
class TestResult(Result):
    data: str
    metadata: str

    def result_bytes(self) -> bytes:
        return self.data.encode("utf-8")

    def output_metadata(self):
        return self.metadata


def create_result(
    output_file="output_file",
    input_file="input_file",
    content_hash="test_hash",
    data="test_data",
    metadata="test_metadata",
    correlation_id="cor123"
):
    return TestResult(
        output_file=output_file,
        input_file=input_file,
        content_hash=content_hash,
        data=data,
        metadata=metadata,
        correlation_id=correlation_id
    )


@pytest.fixture
def db_manager():
    with DatabaseManager(":memory:") as manager:
        yield manager


def test_init_db(db_manager):
    cursor = db_manager.conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_files'"
    )
    assert cursor.fetchone() is not None


def test_store_result(db_manager):
    result = create_result()
    db_manager.store_result("test.txt", "hash123", "corr123", result)

    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT * FROM processed_files")
    db_result = cursor.fetchone()
    assert db_result is not None
    assert db_result[1] == "test.txt"
    assert db_result[2] == "hash123"
    assert db_result[3] == "corr123"
    assert db_result[5] == "test_metadata"


def test_store_latest_result(db_manager):
    result = create_result()
    # Store multiple results
    for i in range(5):
        db_manager.store_latest_result(
            "test.txt", f"hash{i}", f"corr{i}", result, retain_count=3
        )

    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM processed_files")
    count = cursor.fetchone()[0]
    assert count == 4  # Retain 3 old + 1 new


def test_get_result(db_manager):
    result = create_result()
    db_manager.store_result("test.txt", "hash123", "corr123", result)

    retrieved_result = db_manager.get_result("test.txt", "hash123", "test_metadata")
    assert retrieved_result is not None
    assert isinstance(retrieved_result, TestResult)
    assert retrieved_result.data == "test_data"

    non_existent = db_manager.get_result("non_existent.txt", "hash456", "test_metadata")
    assert non_existent is None


def test_remove_old_entries(db_manager):
    result = create_result()
    # Store multiple results
    for i in range(5):
        db_manager.store_result("test.txt", f"hash{i}", f"corr{i}", result)

    db_manager.remove_old_entries("test.txt")

    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM processed_files")
    count = cursor.fetchone()[0]
    assert count == 1  # Only the latest entry should remain


def test_get_newest_entry(db_manager):
    result = create_result()
    # Store multiple results
    for i in range(3):
        db_manager.store_result("test.txt", f"hash{i}", f"corr{i}", result)

    newest_result = db_manager.get_newest_entry("test.txt", "test_metadata")
    assert newest_result is not None
    assert isinstance(newest_result, TestResult)
    assert newest_result.data == "test_data"

    non_existent = db_manager.get_newest_entry("non_existent.txt", "test_metadata")
    assert non_existent is None


def test_context_manager(db_manager):
    assert db_manager.conn is not None

    # Check if connection is open by executing a simple query
    cursor = db_manager.conn.cursor()
    cursor.execute("SELECT 1")
    assert cursor.fetchone() == (1,)

    with db_manager:
        pass

    # Try to execute a query on a closed connection
    with pytest.raises(sqlite3.ProgrammingError):
        cursor = db_manager.conn.cursor()
        cursor.execute("SELECT 1")
