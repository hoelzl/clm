"""Tests for database schema initialization."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from clx.infrastructure.database.schema import (
    DATABASE_VERSION,
    SCHEMA_SQL,
    get_schema_version,
    init_database,
    migrate_database,
)


def test_database_initialization():
    """Test that database is initialized with correct schema."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Verify tables exist
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "jobs" in tables, "jobs table should exist"
        assert "results_cache" in tables, "results_cache table should exist"
        assert "workers" in tables, "workers table should exist"
        assert "schema_version" in tables, "schema_version table should exist"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_database_indexes():
    """Test that required indexes are created."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Verify indexes exist
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}

        assert "idx_jobs_status" in indexes, "jobs status index should exist"
        assert "idx_jobs_content_hash" in indexes, "jobs content_hash index should exist"
        assert "idx_cache_lookup" in indexes, "cache lookup index should exist"
        assert "idx_workers_status" in indexes, "workers status index should exist"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_wal_mode_enabled():
    """Test that DELETE mode is enabled for cross-platform compatibility.

    Note: DELETE mode is used instead of WAL because WAL doesn't work reliably
    with Docker volume mounts on Windows due to shared memory file coordination
    issues across OS boundaries.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]

        assert mode.lower() == "wal", "WAL mode should be enabled for better concurrency"

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_foreign_keys_enabled():
    """Test that foreign keys are enabled."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Get table info
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        # Check required columns exist with correct types
        assert "id" in columns
        assert "job_type" in columns
        assert "status" in columns
        assert "input_file" in columns
        assert "output_file" in columns
        assert "content_hash" in columns
        assert "payload" in columns
        assert "created_at" in columns
        assert "attempts" in columns
        assert "max_attempts" in columns

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_status_constraint():
    """Test that job status constraint is enforced."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Valid status should work
        conn.execute(
            "INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("notebook", "pending", "in.txt", "out.txt", "hash123", "{}"),
        )
        conn.commit()

        # Invalid status should fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("notebook", "invalid_status", "in.txt", "out.txt", "hash123", "{}"),
            )
            conn.commit()

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_results_cache_unique_constraint():
    """Test that results_cache enforces unique constraint."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        conn = init_database(db_path)

        # Insert first entry
        conn.execute(
            "INSERT INTO results_cache (output_file, content_hash, result_metadata) "
            "VALUES (?, ?, ?)",
            ("out.txt", "hash123", "{}"),
        )
        conn.commit()

        # Inserting duplicate should fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO results_cache (output_file, content_hash, result_metadata) "
                "VALUES (?, ?, ?)",
                ("out.txt", "hash123", "{}"),
            )
            conn.commit()

        conn.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_idempotent_initialization():
    """Test that running init_database multiple times is safe."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    try:
        # Initialize twice
        conn1 = init_database(db_path)
        conn1.close()

        conn2 = init_database(db_path)

        # Should still work
        cursor = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "jobs" in tables

        conn2.close()
    finally:
        db_path.unlink(missing_ok=True)


class TestSchemaConstants:
    """Test schema-related constants."""

    def test_schema_sql_is_string(self):
        """SCHEMA_SQL should be a non-empty string."""
        assert isinstance(SCHEMA_SQL, str)
        assert len(SCHEMA_SQL) > 0

    def test_schema_sql_creates_worker_events_table(self):
        """SCHEMA_SQL should create worker_events table."""
        assert "CREATE TABLE IF NOT EXISTS worker_events" in SCHEMA_SQL


class TestGetSchemaVersion:
    """Test get_schema_version function."""

    def test_get_schema_version_empty_database(self, tmp_path):
        """Should return None for empty database."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))

        version = get_schema_version(conn)
        assert version is None

        conn.close()

    def test_get_schema_version_multiple_versions(self, tmp_path):
        """Should return the highest version."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.commit()

        version = get_schema_version(conn)
        assert version == 3

        conn.close()


class TestMigrateDatabase:
    """Test database migration function."""

    def test_migrate_same_version_no_op(self, tmp_path):
        """Migration from same version to same version should be no-op."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Should not raise
        migrate_database(conn, DATABASE_VERSION, DATABASE_VERSION)

        conn.close()

    def test_migrate_v1_to_v2_adds_correlation_id(self, tmp_path):
        """Migration from v1 to v2 should add correlation_id column."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create minimal v1 schema without correlation_id
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.commit()

        # Run migration
        migrate_database(conn, 1, 2)

        # Verify column was added
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "correlation_id" in columns

        conn.close()

    def test_migrate_v2_to_v3_adds_worker_events(self, tmp_path):
        """Migration from v2 to v3 should add worker_events table."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create v2 schema
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                correlation_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE workers (
                id INTEGER PRIMARY KEY,
                worker_type TEXT NOT NULL,
                container_id TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.commit()

        # Run migration
        migrate_database(conn, 2, 3)

        # Verify worker_events table was created
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_events'"
        )
        assert cursor.fetchone() is not None

        # Verify new columns in workers table
        cursor = conn.execute("PRAGMA table_info(workers)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "execution_mode" in columns
        assert "session_id" in columns
        assert "managed_by" in columns

        conn.close()

    def test_migrate_v3_to_v4_adds_cancellation_columns(self, tmp_path):
        """Migration from v3 to v4 should add cancellation columns."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create v3 schema
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                correlation_id TEXT
            )
        """)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.commit()

        # Run migration
        migrate_database(conn, 3, 4)

        # Verify new columns were added
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "cancelled_at" in columns
        assert "cancelled_by" in columns

        conn.close()

    def test_migrate_handles_duplicate_column_error(self, tmp_path):
        """Migration should handle duplicate column errors gracefully."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create schema that already has the column
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                correlation_id TEXT
            )
        """)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.commit()

        # Should not raise even though column already exists
        migrate_database(conn, 1, 2)

        conn.close()

    def test_migrate_full_chain(self, tmp_path):
        """Should successfully migrate through all versions."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create minimal v1 schema
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE workers (
                id INTEGER PRIMARY KEY,
                worker_type TEXT NOT NULL,
                container_id TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.commit()

        # Migrate from v1 to current
        migrate_database(conn, 1, DATABASE_VERSION)

        # Verify final schema version
        version = get_schema_version(conn)
        assert version == DATABASE_VERSION

        conn.close()

    def test_migrate_v3_to_v4_handles_existing_columns(self, tmp_path):
        """Migration v3 to v4 should handle case where columns already exist."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))

        # Create schema that already has cancellation columns
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                correlation_id TEXT,
                cancelled_at TIMESTAMP,
                cancelled_by TEXT
            )
        """)
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.commit()

        # Should not raise even though columns already exist
        migrate_database(conn, 3, 4)

        conn.close()
