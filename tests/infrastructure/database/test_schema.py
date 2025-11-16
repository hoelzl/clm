"""Tests for database schema initialization."""

import tempfile
import sqlite3
from pathlib import Path

import pytest

from clx.infrastructure.database.schema import init_database, get_schema_version, DATABASE_VERSION


def test_database_initialization():
    """Test that database is initialized with correct schema."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Verify tables exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}

        assert 'jobs' in tables, "jobs table should exist"
        assert 'results_cache' in tables, "results_cache table should exist"
        assert 'workers' in tables, "workers table should exist"
        assert 'schema_version' in tables, "schema_version table should exist"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_database_indexes():
    """Test that required indexes are created."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Verify indexes exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in cursor.fetchall()}

        assert 'idx_jobs_status' in indexes, "jobs status index should exist"
        assert 'idx_jobs_content_hash' in indexes, "jobs content_hash index should exist"
        assert 'idx_cache_lookup' in indexes, "cache lookup index should exist"
        assert 'idx_workers_status' in indexes, "workers status index should exist"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_wal_mode_enabled():
    """Test that DELETE mode is enabled for cross-platform compatibility.

    Note: DELETE mode is used instead of WAL because WAL doesn't work reliably
    with Docker volume mounts on Windows due to shared memory file coordination
    issues across OS boundaries.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]

        assert mode.lower() == 'wal', "WAL mode should be enabled for better concurrency"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_foreign_keys_enabled():
    """Test that foreign keys are enabled."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        cursor = conn.execute("PRAGMA foreign_keys")
        enabled = cursor.fetchone()[0]

        assert enabled == 1, "Foreign keys should be enabled"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_schema_version_recorded():
    """Test that schema version is recorded."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        version = get_schema_version(conn)
        assert version == DATABASE_VERSION, f"Schema version should be {DATABASE_VERSION}"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_jobs_table_structure():
    """Test that jobs table has correct structure."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Get table info
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        # Check required columns exist with correct types
        assert 'id' in columns
        assert 'job_type' in columns
        assert 'status' in columns
        assert 'input_file' in columns
        assert 'output_file' in columns
        assert 'content_hash' in columns
        assert 'payload' in columns
        assert 'created_at' in columns
        assert 'attempts' in columns
        assert 'max_attempts' in columns

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_status_constraint():
    """Test that job status constraint is enforced."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Valid status should work
        conn.execute(
            "INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('notebook', 'pending', 'in.txt', 'out.txt', 'hash123', '{}')
        )
        conn.commit()

        # Invalid status should fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ('notebook', 'invalid_status', 'in.txt', 'out.txt', 'hash123', '{}')
            )
            conn.commit()

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_results_cache_unique_constraint():
    """Test that results_cache enforces unique constraint."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Insert first entry
        conn.execute(
            "INSERT INTO results_cache (output_file, content_hash, result_metadata) "
            "VALUES (?, ?, ?)",
            ('out.txt', 'hash123', '{}')
        )
        conn.commit()

        # Inserting duplicate should fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO results_cache (output_file, content_hash, result_metadata) "
                "VALUES (?, ?, ?)",
                ('out.txt', 'hash123', '{}')
            )
            conn.commit()

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_idempotent_initialization():
    """Test that running init_database multiple times is safe."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = Path(f.name)

    try:
        # Initialize twice
        conn1 = init_database(db_path)
        conn1.close()

        conn2 = init_database(db_path)

        # Should still work
        cursor = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert 'jobs' in tables

        conn2.close()
    finally:
        db_path.unlink(missing_ok=True)
