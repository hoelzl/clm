"""Per-deck notebook-execution telemetry, persisted across builds (issue #330).

The xeus-cpp / clang-repl kernels crash in two distinct ways: deterministic
cumulative-JIT crashes that reproduce on every attempt, and transient flakes
that pass on retry. The retry loop in ``notebook_processor`` hides both: a
deck that only passed on attempt 3 looks identical to a clean pass, and a
deck that failed six times identically looks like any other failure. This
module records *which* class each non-clean execution belonged to, so kernel
flakiness becomes observable over time and ``clm kernel-triage`` can re-test
known-bad decks after a kernel upgrade.

Storage is a small SQLite database (default ``clm_telemetry.db``) that lives
next to ``clm_cache.db`` but is deliberately a separate file: clearing or
deleting the execution cache must not erase the flake history.

Only non-clean executions are recorded (passed-after-retry, suppressed
failures, and final failures). Clean first-attempt passes are the
overwhelming majority and recording them would add a write per executed deck
for no diagnostic value — a deck's absence from the telemetry for a build
that executed it *is* the clean signal.

Rows are written HOST-side (``SqliteBackend``) from telemetry the worker
attaches to its result/error messages; workers never open this database
(Docker workers cannot reach host paths).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default file name; resolved next to the cache database by the CLI entry
# point (``clm --telemetry-db-path`` overrides).
DEFAULT_TELEMETRY_DB_NAME = "clm_telemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_file TEXT NOT NULL,
    prog_lang TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    worker_image_identity TEXT NOT NULL DEFAULT '',
    -- 'passed_after_retry' | 'failed' | 'suppressed_failure'
    outcome TEXT NOT NULL,
    -- 'flaky' (passed on a retry) | 'deterministic' (every attempt failed
    -- the same way at the same cell) | 'mixed' (attempts failed differently)
    classification TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    -- Failure type of the LAST failed attempt:
    -- 'cell_execution_error' | 'dead_kernel' | 'startup_timeout' |
    -- 'cell_timeout' | 'other' | '' (no failure recorded)
    failure_type TEXT NOT NULL DEFAULT '',
    failing_cell_index INTEGER,
    error_message TEXT NOT NULL DEFAULT '',
    -- JSON list with one record per failed attempt
    attempts_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_file
    ON execution_telemetry (input_file, created_at);
"""


@dataclass
class TelemetryEvent:
    """One recorded non-clean execution of a deck."""

    input_file: str
    outcome: str
    classification: str
    attempts: int
    failure_type: str = ""
    failing_cell_index: int | None = None
    error_message: str = ""
    prog_lang: str = ""
    language: str = ""
    content_hash: str = ""
    worker_image_identity: str = ""
    attempts_detail: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""


def default_telemetry_db_path(cache_db_path: Path) -> Path:
    """Resolve the default telemetry database path next to the cache db."""
    return cache_db_path.parent / DEFAULT_TELEMETRY_DB_NAME


class ExecutionTelemetryStore:
    """Append/query access to the execution-telemetry database.

    Connections are opened per call: writes happen only for non-clean
    executions (rare), and short-lived connections avoid any lifecycle
    wiring through the backend. WAL + a generous busy timeout keep
    concurrent host-side writers safe.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        if not self._initialized:
            conn.executescript(_SCHEMA)
            conn.commit()
            self._initialized = True
        return conn

    def record_event(self, event: TelemetryEvent) -> None:
        """Persist one telemetry event. Never raises (best-effort logging)."""
        try:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO execution_telemetry (
                        input_file, prog_lang, language, content_hash,
                        worker_image_identity, outcome, classification,
                        attempts, failure_type, failing_cell_index,
                        error_message, attempts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.input_file,
                        event.prog_lang,
                        event.language,
                        event.content_hash,
                        event.worker_image_identity,
                        event.outcome,
                        event.classification,
                        event.attempts,
                        event.failure_type,
                        event.failing_cell_index,
                        event.error_message[:2000],
                        json.dumps(event.attempts_detail),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            # Telemetry must never fail a build.
            logger.warning("Could not record execution telemetry for %s: %s", event.input_file, exc)

    @staticmethod
    def _row_to_event(row: tuple) -> TelemetryEvent:
        try:
            attempts_detail = json.loads(row[11]) if row[11] else []
        except json.JSONDecodeError:
            attempts_detail = []
        return TelemetryEvent(
            input_file=row[0],
            prog_lang=row[1],
            language=row[2],
            content_hash=row[3],
            worker_image_identity=row[4],
            outcome=row[5],
            classification=row[6],
            attempts=row[7],
            failure_type=row[8] or "",
            failing_cell_index=row[9],
            error_message=row[10] or "",
            attempts_detail=attempts_detail,
            created_at=row[12] or "",
        )

    _SELECT = """
        SELECT input_file, prog_lang, language, content_hash,
               worker_image_identity, outcome, classification, attempts,
               failure_type, failing_cell_index, error_message,
               attempts_json, created_at
        FROM execution_telemetry
    """

    def events(
        self,
        *,
        input_file: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[TelemetryEvent]:
        """Query events, newest first.

        Args:
            input_file: Restrict to one deck (exact source-path match).
            since: ISO-8601 UTC lower bound on ``created_at`` (inclusive).
            limit: Maximum number of rows.
        """
        if not self.db_path.exists():
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if input_file is not None:
            clauses.append("input_file = ?")
            params.append(input_file)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        sql = self._SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
        return [self._row_to_event(row) for row in rows]

    def problem_files(self, *, since: str | None = None) -> dict[str, list[TelemetryEvent]]:
        """All decks with recorded events, mapped to their events (newest first)."""
        result: dict[str, list[TelemetryEvent]] = {}
        for event in self.events(since=since):
            result.setdefault(event.input_file, []).append(event)
        return result
