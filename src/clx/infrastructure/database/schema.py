"""SQLite schema for job queue and worker management.

This module provides the database schema for the new SQLite-based orchestration
system that replaces RabbitMQ. It includes tables for jobs, results cache, and
workers.
"""

import sqlite3
from pathlib import Path
from typing import Optional

DATABASE_VERSION = 3

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
    correlation_id TEXT,  -- Optional correlation ID for tracing

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
    avg_processing_time REAL,

    -- v3 schema additions
    execution_mode TEXT,
    config TEXT,
    session_id TEXT,
    managed_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(worker_type, status);

-- Worker lifecycle events (audit log)
CREATE TABLE IF NOT EXISTS worker_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'worker_starting',     -- Worker process/container starting
        'worker_registered',   -- Worker successfully registered in DB
        'worker_ready',        -- Worker ready to accept jobs
        'worker_stopping',     -- Worker shutdown initiated
        'worker_stopped',      -- Worker successfully stopped
        'worker_failed',       -- Worker failed to start or crashed
        'pool_starting',       -- Worker pool starting
        'pool_started',        -- Worker pool fully started
        'pool_stopping',       -- Worker pool shutdown initiated
        'pool_stopped'         -- Worker pool fully stopped
    )),

    -- Worker identification
    worker_id INTEGER,                    -- NULL for pool-level events
    worker_type TEXT NOT NULL,
    execution_mode TEXT,                  -- 'docker' or 'direct'

    -- Event details
    message TEXT,                         -- Human-readable message
    metadata TEXT,                        -- JSON with event-specific details

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Session tracking
    session_id TEXT,

    -- Correlation with worker record
    FOREIGN KEY (worker_id) REFERENCES workers(id)
);

CREATE INDEX IF NOT EXISTS idx_worker_events_type ON worker_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_worker_events_worker ON worker_events(worker_id, created_at);
CREATE INDEX IF NOT EXISTS idx_worker_events_time ON worker_events(created_at);
CREATE INDEX IF NOT EXISTS idx_worker_events_session ON worker_events(session_id, created_at);

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

    # Check if migration is needed
    current_version = get_schema_version(conn)
    if current_version and current_version < DATABASE_VERSION:
        migrate_database(conn, current_version, DATABASE_VERSION)
    else:
        # Record schema version for new databases
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


def migrate_database(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """Migrate database from one version to another.

    Args:
        conn: SQLite connection
        from_version: Current schema version
        to_version: Target schema version
    """
    if from_version == to_version:
        return

    # Migration from v1 to v2: Add correlation_id column
    if from_version < 2 <= to_version:
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN correlation_id TEXT")
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (2,)
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            # Column might already exist
            if "duplicate column name" not in str(e).lower():
                raise

    # Migration from v2 to v3: Add worker_events table and extend workers table
    if from_version < 3 <= to_version:
        conn.executescript("""
            -- Create worker_events table
            CREATE TABLE IF NOT EXISTS worker_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                worker_id INTEGER,
                worker_type TEXT NOT NULL,
                execution_mode TEXT,
                message TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                FOREIGN KEY (worker_id) REFERENCES workers(id)
            );

            CREATE INDEX IF NOT EXISTS idx_worker_events_type ON worker_events(event_type, created_at);
            CREATE INDEX IF NOT EXISTS idx_worker_events_worker ON worker_events(worker_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_worker_events_time ON worker_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_worker_events_session ON worker_events(session_id, created_at);

            -- Add new columns to workers table
            ALTER TABLE workers ADD COLUMN execution_mode TEXT;
            ALTER TABLE workers ADD COLUMN config TEXT;
            ALTER TABLE workers ADD COLUMN session_id TEXT;
            ALTER TABLE workers ADD COLUMN managed_by TEXT;

            INSERT OR IGNORE INTO schema_version (version) VALUES (3);
        """)
        conn.commit()
