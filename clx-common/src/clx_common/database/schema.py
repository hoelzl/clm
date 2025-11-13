"""SQLite schema for job queue and worker management.

This module provides the database schema for the new SQLite-based orchestration
system that replaces RabbitMQ. It includes tables for jobs, results cache, and
workers.
"""

import sqlite3
from pathlib import Path
from typing import Optional

DATABASE_VERSION = 1

SCHEMA_SQL = """
-- Jobs table (replaces message queue)
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
    priority INTEGER DEFAULT 0,

    input_file TEXT NOT NULL,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,  -- JSON

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    worker_id INTEGER,

    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error TEXT,
    traceback TEXT,

    FOREIGN KEY (worker_id) REFERENCES workers(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, job_type);
CREATE INDEX IF NOT EXISTS idx_jobs_content_hash ON jobs(content_hash);

-- Results cache table
CREATE TABLE IF NOT EXISTS results_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result_metadata TEXT,  -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,

    UNIQUE(output_file, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_cache_lookup ON results_cache(output_file, content_hash);

-- Workers table
CREATE TABLE IF NOT EXISTS workers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_type TEXT NOT NULL,
    container_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('idle', 'busy', 'hung', 'dead')),

    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cpu_usage REAL,
    memory_usage REAL,

    jobs_processed INTEGER DEFAULT 0,
    jobs_failed INTEGER DEFAULT 0,
    avg_processing_time REAL
);

CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(worker_type, status);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize database with schema.

    Args:
        db_path: Path to SQLite database file

    Returns:
        SQLite connection object
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    # Use DELETE journal mode for cross-platform compatibility
    # WAL mode doesn't work reliably with Docker volume mounts on Windows
    # due to shared memory file coordination issues across OS boundaries
    conn.execute("PRAGMA journal_mode=DELETE")

    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys=ON")

    # Execute schema
    conn.executescript(SCHEMA_SQL)

    # Record schema version
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
        (DATABASE_VERSION,)
    )
    conn.commit()

    return conn


def get_schema_version(conn: sqlite3.Connection) -> Optional[int]:
    """Get current schema version from database.

    Args:
        conn: SQLite connection

    Returns:
        Schema version number or None if not found
    """
    try:
        cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return None
