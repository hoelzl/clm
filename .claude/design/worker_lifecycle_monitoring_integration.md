# Worker Lifecycle Monitoring Integration

**Version**: 1.0
**Date**: 2025-11-15
**Purpose**: Integrate worker lifecycle management with existing progress tracking and database monitoring

## Overview

This document extends the worker management design to fully integrate worker lifecycle events with the existing monitoring system, ensuring users can track worker startup, shutdown, and configuration changes through:

1. **Database events table** - Persistent audit log of lifecycle events
2. **ProgressTracker integration** - Real-time logging during execution
3. **Dashboard queries** - SQL queries for monitoring tools

## Database Schema Extensions

### New Table: `worker_events`

```sql
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

    -- Session tracking (links to worker sessions if using DB approach)
    session_id INTEGER,

    -- Correlation with worker record
    FOREIGN KEY (worker_id) REFERENCES workers(id)
);

CREATE INDEX IF NOT EXISTS idx_worker_events_type ON worker_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_worker_events_worker ON worker_events(worker_id, created_at);
CREATE INDEX IF NOT EXISTS idx_worker_events_time ON worker_events(created_at);
```

### Extended `workers` Table

Add columns to track more lifecycle information:

```sql
-- Add to existing workers table via migration
ALTER TABLE workers ADD COLUMN execution_mode TEXT;  -- 'docker' or 'direct'
ALTER TABLE workers ADD COLUMN config TEXT;          -- JSON with worker config
ALTER TABLE workers ADD COLUMN session_id TEXT;      -- Links to lifecycle session
ALTER TABLE workers ADD COLUMN managed_by TEXT;      -- 'clx build', 'clx start-services', etc.
```

## Event Logging API

### WorkerEventLogger Class

```python
"""Worker lifecycle event logging."""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum

from clx.infrastructure.database.job_queue import JobQueue

logger = logging.getLogger(__name__)


class WorkerEventType(Enum):
    """Worker lifecycle event types."""
    WORKER_STARTING = 'worker_starting'
    WORKER_REGISTERED = 'worker_registered'
    WORKER_READY = 'worker_ready'
    WORKER_STOPPING = 'worker_stopping'
    WORKER_STOPPED = 'worker_stopped'
    WORKER_FAILED = 'worker_failed'
    POOL_STARTING = 'pool_starting'
    POOL_STARTED = 'pool_started'
    POOL_STOPPING = 'pool_stopping'
    POOL_STOPPED = 'pool_stopped'


class WorkerEventLogger:
    """Log worker lifecycle events to database."""

    def __init__(self, db_path: Path, session_id: Optional[str] = None):
        """Initialize event logger.

        Args:
            db_path: Path to database
            session_id: Optional session identifier for grouping events
        """
        self.db_path = db_path
        self.session_id = session_id
        self.job_queue = JobQueue(db_path)

    def log_event(
        self,
        event_type: WorkerEventType,
        worker_type: str,
        message: str,
        worker_id: Optional[int] = None,
        execution_mode: Optional[str] = None,
        **metadata
    ) -> int:
        """Log a worker lifecycle event.

        Args:
            event_type: Type of event
            worker_type: Worker type (notebook, plantuml, drawio)
            message: Human-readable message
            worker_id: Optional worker ID (for worker-specific events)
            execution_mode: Optional execution mode (docker/direct)
            **metadata: Additional event-specific metadata

        Returns:
            Event ID
        """
        conn = self.job_queue._get_conn()

        # Add common metadata
        metadata['timestamp'] = datetime.now().isoformat()
        if self.session_id:
            metadata['session_id'] = self.session_id

        cursor = conn.execute(
            """
            INSERT INTO worker_events (
                event_type, worker_id, worker_type, execution_mode,
                message, metadata, session_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type.value,
                worker_id,
                worker_type,
                execution_mode,
                message,
                json.dumps(metadata),
                self.session_id
            )
        )

        event_id = cursor.lastrowid
        conn.commit()

        # Also log to application logger
        log_level = logging.INFO
        if event_type == WorkerEventType.WORKER_FAILED:
            log_level = logging.ERROR
        elif event_type in (WorkerEventType.WORKER_STOPPING, WorkerEventType.WORKER_STOPPED):
            log_level = logging.DEBUG

        logger.log(log_level, f"[{event_type.value}] {message}")

        return event_id

    def log_worker_starting(
        self,
        worker_type: str,
        execution_mode: str,
        index: int,
        config: Dict[str, Any]
    ) -> int:
        """Log worker starting event."""
        return self.log_event(
            WorkerEventType.WORKER_STARTING,
            worker_type=worker_type,
            message=f"Starting {execution_mode} worker {worker_type}-{index}",
            execution_mode=execution_mode,
            index=index,
            config=config
        )

    def log_worker_registered(
        self,
        worker_type: str,
        worker_id: int,
        executor_id: str,
        execution_mode: str
    ) -> int:
        """Log worker registered event."""
        return self.log_event(
            WorkerEventType.WORKER_REGISTERED,
            worker_type=worker_type,
            message=f"Worker {worker_type} #{worker_id} registered (executor: {executor_id[:12]})",
            worker_id=worker_id,
            execution_mode=execution_mode,
            executor_id=executor_id
        )

    def log_worker_ready(
        self,
        worker_type: str,
        worker_id: int,
        execution_mode: str
    ) -> int:
        """Log worker ready event."""
        return self.log_event(
            WorkerEventType.WORKER_READY,
            worker_type=worker_type,
            message=f"Worker {worker_type} #{worker_id} ready to accept jobs",
            worker_id=worker_id,
            execution_mode=execution_mode
        )

    def log_worker_stopping(
        self,
        worker_type: str,
        worker_id: int,
        reason: str = "shutdown"
    ) -> int:
        """Log worker stopping event."""
        return self.log_event(
            WorkerEventType.WORKER_STOPPING,
            worker_type=worker_type,
            message=f"Stopping worker {worker_type} #{worker_id} ({reason})",
            worker_id=worker_id,
            reason=reason
        )

    def log_worker_stopped(
        self,
        worker_type: str,
        worker_id: int,
        jobs_processed: int,
        uptime_seconds: float
    ) -> int:
        """Log worker stopped event."""
        return self.log_event(
            WorkerEventType.WORKER_STOPPED,
            worker_type=worker_type,
            message=f"Worker {worker_type} #{worker_id} stopped (processed {jobs_processed} jobs in {uptime_seconds:.1f}s)",
            worker_id=worker_id,
            jobs_processed=jobs_processed,
            uptime_seconds=uptime_seconds
        )

    def log_worker_failed(
        self,
        worker_type: str,
        error: str,
        worker_id: Optional[int] = None,
        **details
    ) -> int:
        """Log worker failed event."""
        return self.log_event(
            WorkerEventType.WORKER_FAILED,
            worker_type=worker_type,
            message=f"Worker {worker_type} failed: {error}",
            worker_id=worker_id,
            error=error,
            **details
        )

    def log_pool_starting(
        self,
        worker_configs: list,
        total_workers: int
    ) -> int:
        """Log pool starting event."""
        return self.log_event(
            WorkerEventType.POOL_STARTING,
            worker_type='all',
            message=f"Starting worker pool with {total_workers} worker(s)",
            total_workers=total_workers,
            configs=[
                {
                    'worker_type': c.worker_type,
                    'execution_mode': c.execution_mode,
                    'count': c.count
                }
                for c in worker_configs
            ]
        )

    def log_pool_started(
        self,
        worker_count: int,
        duration_seconds: float
    ) -> int:
        """Log pool started event."""
        return self.log_event(
            WorkerEventType.POOL_STARTED,
            worker_type='all',
            message=f"Worker pool started with {worker_count} worker(s) in {duration_seconds:.1f}s",
            worker_count=worker_count,
            duration_seconds=duration_seconds
        )

    def log_pool_stopping(self) -> int:
        """Log pool stopping event."""
        return self.log_event(
            WorkerEventType.POOL_STOPPING,
            worker_type='all',
            message="Stopping worker pool"
        )

    def log_pool_stopped(
        self,
        workers_stopped: int,
        duration_seconds: float
    ) -> int:
        """Log pool stopped event."""
        return self.log_event(
            WorkerEventType.POOL_STOPPED,
            worker_type='all',
            message=f"Worker pool stopped ({workers_stopped} worker(s) in {duration_seconds:.1f}s)",
            workers_stopped=workers_stopped,
            duration_seconds=duration_seconds
        )
```

## ProgressTracker Integration

### Enhanced ProgressTracker for Worker Lifecycle

```python
"""Extended progress tracker with worker lifecycle tracking."""

from clx.infrastructure.workers.progress_tracker import ProgressTracker

class WorkerLifecycleProgressTracker(ProgressTracker):
    """Progress tracker extended with worker lifecycle events."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._workers: Dict[int, Dict] = {}  # worker_id -> worker info
        self._worker_events: List[Dict] = []

    def worker_starting(
        self,
        worker_type: str,
        execution_mode: str,
        index: int
    ) -> None:
        """Record worker starting event."""
        with self._lock:
            event = {
                'timestamp': datetime.now(),
                'event': 'worker_starting',
                'worker_type': worker_type,
                'execution_mode': execution_mode,
                'index': index
            }
            self._worker_events.append(event)

        logger.info(
            f"Starting {execution_mode} worker: {worker_type}-{index}"
        )

    def worker_registered(
        self,
        worker_id: int,
        worker_type: str,
        execution_mode: str,
        executor_id: str
    ) -> None:
        """Record worker registration event."""
        with self._lock:
            self._workers[worker_id] = {
                'worker_type': worker_type,
                'execution_mode': execution_mode,
                'executor_id': executor_id,
                'registered_at': datetime.now(),
                'jobs_processed': 0
            }

            event = {
                'timestamp': datetime.now(),
                'event': 'worker_registered',
                'worker_id': worker_id,
                'worker_type': worker_type,
                'execution_mode': execution_mode
            }
            self._worker_events.append(event)

        logger.info(
            f"✓ Worker {worker_type} #{worker_id} registered "
            f"({execution_mode}, executor: {executor_id[:12]})"
        )

    def worker_stopped(
        self,
        worker_id: int,
        jobs_processed: int
    ) -> None:
        """Record worker stopped event."""
        with self._lock:
            if worker_id in self._workers:
                worker = self._workers[worker_id]
                uptime = (datetime.now() - worker['registered_at']).total_seconds()

                event = {
                    'timestamp': datetime.now(),
                    'event': 'worker_stopped',
                    'worker_id': worker_id,
                    'jobs_processed': jobs_processed,
                    'uptime_seconds': uptime
                }
                self._worker_events.append(event)

                logger.info(
                    f"Worker #{worker_id} stopped "
                    f"(processed {jobs_processed} jobs in {uptime:.1f}s)"
                )

                del self._workers[worker_id]

    def pool_starting(self, total_workers: int) -> None:
        """Record pool starting event."""
        with self._lock:
            event = {
                'timestamp': datetime.now(),
                'event': 'pool_starting',
                'total_workers': total_workers
            }
            self._worker_events.append(event)

        logger.info(f"Starting worker pool ({total_workers} worker(s))...")

    def pool_started(self, worker_count: int, duration: float) -> None:
        """Record pool started event."""
        with self._lock:
            event = {
                'timestamp': datetime.now(),
                'event': 'pool_started',
                'worker_count': worker_count,
                'duration_seconds': duration
            }
            self._worker_events.append(event)

        logger.info(
            f"✓ Worker pool started ({worker_count} worker(s) in {duration:.1f}s)"
        )

    def _log_progress(self) -> None:
        """Enhanced progress logging with worker status."""
        # Call parent implementation for job progress
        super()._log_progress()

        # Add worker status if we have active workers
        with self._lock:
            if self._workers:
                worker_summary = self._get_worker_summary()
                if worker_summary:
                    logger.info(f"  Workers: {worker_summary}")

    def _get_worker_summary(self) -> str:
        """Get summary of active workers."""
        worker_counts = defaultdict(int)
        for worker in self._workers.values():
            worker_counts[worker['worker_type']] += 1

        parts = []
        for worker_type, count in sorted(worker_counts.items()):
            parts.append(f"{count} {worker_type}")

        return ", ".join(parts)

    def get_summary(self) -> Dict:
        """Get summary including worker statistics."""
        summary = super().get_summary()

        with self._lock:
            summary['workers'] = {
                'active': len(self._workers),
                'by_type': dict(
                    (wtype, sum(1 for w in self._workers.values() if w['worker_type'] == wtype))
                    for wtype in set(w['worker_type'] for w in self._workers.values())
                ),
                'events': len(self._worker_events)
            }

        return summary
```

## WorkerLifecycleManager Integration

### Updated WorkerLifecycleManager with Event Logging

```python
class WorkerLifecycleManager:
    """Manage worker lifecycle with comprehensive event logging."""

    def __init__(
        self,
        config: WorkersManagementConfig,
        db_path: Path,
        workspace_path: Path,
        session_id: Optional[str] = None,
        progress_tracker: Optional[ProgressTracker] = None
    ):
        """Initialize lifecycle manager."""
        self.config = config
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.session_id = session_id or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Event logger
        self.event_logger = WorkerEventLogger(db_path, session_id=self.session_id)

        # Progress tracker
        self.progress_tracker = progress_tracker

        # ... rest of initialization ...

    def start_managed_workers(self) -> List[WorkerInfo]:
        """Start workers with comprehensive event logging."""
        start_time = time.time()

        # Get worker configurations
        worker_configs = self._adjust_configs_for_reuse(
            self.config.get_all_worker_configs()
        )

        total_workers = sum(c.count for c in worker_configs)

        # Log pool starting
        self.event_logger.log_pool_starting(worker_configs, total_workers)
        if self.progress_tracker:
            self.progress_tracker.pool_starting(total_workers)

        logger.info(f"Starting {total_workers} managed worker(s)...")

        # Start each worker with logging
        started_workers = []
        for config in worker_configs:
            for index in range(config.count):
                # Log worker starting
                self.event_logger.log_worker_starting(
                    worker_type=config.worker_type,
                    execution_mode=config.execution_mode,
                    index=index,
                    config=config.__dict__
                )

                if self.progress_tracker:
                    self.progress_tracker.worker_starting(
                        worker_type=config.worker_type,
                        execution_mode=config.execution_mode,
                        index=index
                    )

                try:
                    # Start worker
                    worker_info = self._start_single_worker(config, index)

                    if worker_info:
                        # Log successful registration
                        self.event_logger.log_worker_registered(
                            worker_type=config.worker_type,
                            worker_id=worker_info.db_worker_id,
                            executor_id=worker_info.executor_id,
                            execution_mode=config.execution_mode
                        )

                        if self.progress_tracker:
                            self.progress_tracker.worker_registered(
                                worker_id=worker_info.db_worker_id,
                                worker_type=config.worker_type,
                                execution_mode=config.execution_mode,
                                executor_id=worker_info.executor_id
                            )

                        started_workers.append(worker_info)

                        # Log worker ready
                        self.event_logger.log_worker_ready(
                            worker_type=config.worker_type,
                            worker_id=worker_info.db_worker_id,
                            execution_mode=config.execution_mode
                        )

                except Exception as e:
                    # Log failure
                    self.event_logger.log_worker_failed(
                        worker_type=config.worker_type,
                        error=str(e),
                        index=index,
                        execution_mode=config.execution_mode
                    )
                    logger.error(
                        f"Failed to start {config.worker_type}-{index}: {e}",
                        exc_info=True
                    )

        # Log pool started
        duration = time.time() - start_time
        self.event_logger.log_pool_started(len(started_workers), duration)

        if self.progress_tracker:
            self.progress_tracker.pool_started(len(started_workers), duration)

        self.managed_workers = started_workers
        return started_workers

    def stop_managed_workers(self) -> None:
        """Stop workers with event logging."""
        if not self.managed_workers:
            return

        start_time = time.time()

        self.event_logger.log_pool_stopping()
        logger.info(f"Stopping {len(self.managed_workers)} worker(s)...")

        stopped_count = 0
        for worker_info in self.managed_workers:
            try:
                # Log stopping
                self.event_logger.log_worker_stopping(
                    worker_type=worker_info.worker_type,
                    worker_id=worker_info.db_worker_id,
                    reason="managed shutdown"
                )

                # Get stats before stopping
                jobs_processed = self._get_worker_jobs_processed(worker_info.db_worker_id)
                uptime = (datetime.now() - datetime.fromisoformat(worker_info.started_at)).total_seconds()

                # Stop worker
                self._stop_single_worker(worker_info)

                # Log stopped
                self.event_logger.log_worker_stopped(
                    worker_type=worker_info.worker_type,
                    worker_id=worker_info.db_worker_id,
                    jobs_processed=jobs_processed,
                    uptime_seconds=uptime
                )

                if self.progress_tracker:
                    self.progress_tracker.worker_stopped(
                        worker_id=worker_info.db_worker_id,
                        jobs_processed=jobs_processed
                    )

                stopped_count += 1

            except Exception as e:
                logger.error(
                    f"Error stopping worker #{worker_info.db_worker_id}: {e}",
                    exc_info=True
                )

        duration = time.time() - start_time
        self.event_logger.log_pool_stopped(stopped_count, duration)

        self.managed_workers.clear()

    def _get_worker_jobs_processed(self, worker_id: int) -> int:
        """Get number of jobs processed by worker."""
        conn = self.job_queue._get_conn()
        cursor = conn.execute(
            "SELECT jobs_processed FROM workers WHERE id = ?",
            (worker_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0
```

## Dashboard Query Examples

### SQL Queries for Monitoring

```sql
-- Recent worker lifecycle events
SELECT
    event_type,
    worker_type,
    message,
    created_at
FROM worker_events
WHERE created_at > datetime('now', '-1 hour')
ORDER BY created_at DESC;

-- Worker startup failures
SELECT
    worker_type,
    message,
    metadata,
    created_at
FROM worker_events
WHERE event_type = 'worker_failed'
  AND created_at > datetime('now', '-1 day')
ORDER BY created_at DESC;

-- Worker pool sessions
SELECT
    session_id,
    COUNT(*) as event_count,
    MIN(created_at) as started_at,
    MAX(created_at) as ended_at
FROM worker_events
WHERE session_id IS NOT NULL
GROUP BY session_id
ORDER BY started_at DESC
LIMIT 10;

-- Current active workers with statistics
SELECT
    w.id,
    w.worker_type,
    w.execution_mode,
    w.status,
    w.jobs_processed,
    w.jobs_failed,
    w.started_at,
    w.last_heartbeat,
    ROUND((julianday('now') - julianday(w.started_at)) * 24 * 60 * 60) as uptime_seconds
FROM workers w
WHERE w.status IN ('idle', 'busy')
ORDER BY w.worker_type, w.id;

-- Worker lifecycle summary (start to stop)
WITH worker_lifecycle AS (
    SELECT
        worker_id,
        worker_type,
        MIN(CASE WHEN event_type = 'worker_registered' THEN created_at END) as registered_at,
        MAX(CASE WHEN event_type = 'worker_stopped' THEN created_at END) as stopped_at,
        MAX(CASE WHEN event_type = 'worker_stopped' THEN json_extract(metadata, '$.jobs_processed') END) as jobs_processed
    FROM worker_events
    WHERE worker_id IS NOT NULL
    GROUP BY worker_id, worker_type
)
SELECT
    worker_id,
    worker_type,
    registered_at,
    stopped_at,
    ROUND((julianday(stopped_at) - julianday(registered_at)) * 24 * 60 * 60) as lifetime_seconds,
    jobs_processed
FROM worker_lifecycle
WHERE stopped_at IS NOT NULL
ORDER BY registered_at DESC
LIMIT 20;

-- Job processing by worker
SELECT
    w.id as worker_id,
    w.worker_type,
    w.execution_mode,
    COUNT(j.id) as total_jobs,
    SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END) as completed,
    SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END) as failed,
    AVG(ROUND((julianday(j.completed_at) - julianday(j.started_at)) * 24 * 60 * 60, 2)) as avg_duration_seconds
FROM workers w
LEFT JOIN jobs j ON j.worker_id = w.id
WHERE w.started_at > datetime('now', '-1 day')
GROUP BY w.id, w.worker_type, w.execution_mode
ORDER BY total_jobs DESC;
```

## Usage Examples

### In clx build

```python
async def main(...):
    # ... setup ...

    # Create progress tracker with worker lifecycle support
    progress_tracker = WorkerLifecycleProgressTracker(
        progress_interval=10.0,
        long_job_threshold=60.0,
        show_worker_details=True
    )

    # Create lifecycle manager with tracker
    worker_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=db_path,
        workspace_path=output_dir,
        session_id=f"build-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        progress_tracker=progress_tracker
    )

    # Start workers (events logged automatically)
    if worker_manager.should_start_workers():
        worker_manager.start_managed_workers()

    # Start progress tracking
    progress_tracker.start_progress_logging()

    try:
        # Process course
        await course.process_all(backend)

    finally:
        # Stop progress tracking
        progress_tracker.stop_progress_logging()

        # Stop workers (events logged automatically)
        worker_manager.stop_managed_workers()

        # Log final summary
        progress_tracker.log_summary()
```

### Example Output

```
[INFO] Starting worker pool (6 worker(s))...
[INFO] Starting docker worker: notebook-0
[INFO] Starting docker worker: notebook-1
[INFO] ✓ Worker notebook #1 registered (docker, executor: a1b2c3d4e5f6)
[INFO] ✓ Worker notebook #2 registered (docker, executor: f6e5d4c3b2a1)
[INFO] Starting direct worker: plantuml-0
[INFO] ✓ Worker plantuml #3 registered (direct, executor: direct-plant...)
[INFO] Starting direct worker: drawio-0
[INFO] ✓ Worker drawio #4 registered (direct, executor: direct-drawi...)
[INFO] ✓ Worker pool started (6 worker(s) in 3.2s)
[INFO] Progress: 0/50 jobs completed | 4 active | 0 failed (0%)
[INFO]   Workers: 2 notebook, 2 plantuml, 2 drawio
[INFO]   └─ Worker #1: Processing notebook job #15 (2.3s elapsed) [example.ipynb]
[INFO]   └─ Worker #2: Processing notebook job #16 (1.8s elapsed) [demo.ipynb]
[INFO]   └─ Worker #3: Processing plantuml job #17 (0.5s elapsed) [diagram.puml]
[INFO]   └─ Worker #4: Processing drawio job #18 (1.2s elapsed) [flow.drawio]
[INFO] Progress: 25/50 jobs completed | 4 active | 0 failed (50%)
[INFO] Progress: 50/50 jobs completed | 0 active | 0 failed (100%)
[INFO] ✓ All 50 jobs completed successfully in 45.3s (30 notebook, 10 plantuml, 10 drawio)
[INFO] Stopping 6 worker(s)...
[INFO] Worker #1 stopped (processed 15 jobs in 42.1s)
[INFO] Worker #2 stopped (processed 15 jobs in 42.0s)
[INFO] Worker #3 stopped (processed 10 jobs in 41.8s)
[INFO] Worker #4 stopped (processed 10 jobs in 41.7s)
```

## Migration

### Database Migration

```python
# In schema.py

DATABASE_VERSION = 3  # Increment version

def migrate_database(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """Migrate database from one version to another."""
    # ... existing migrations ...

    # Migration from v2 to v3: Add worker_events table
    if from_version < 3 <= to_version:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS worker_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                worker_id INTEGER,
                worker_type TEXT NOT NULL,
                execution_mode TEXT,
                message TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id INTEGER,
                FOREIGN KEY (worker_id) REFERENCES workers(id)
            );

            CREATE INDEX IF NOT EXISTS idx_worker_events_type ON worker_events(event_type, created_at);
            CREATE INDEX IF NOT EXISTS idx_worker_events_worker ON worker_events(worker_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_worker_events_time ON worker_events(created_at);

            ALTER TABLE workers ADD COLUMN execution_mode TEXT;
            ALTER TABLE workers ADD COLUMN config TEXT;
            ALTER TABLE workers ADD COLUMN session_id TEXT;
            ALTER TABLE workers ADD COLUMN managed_by TEXT;

            INSERT OR IGNORE INTO schema_version (version) VALUES (3);
        """)
        conn.commit()
```

## Benefits

1. **Comprehensive Audit Trail**: All worker lifecycle events persisted in database
2. **Real-time Monitoring**: ProgressTracker provides live updates during execution
3. **Debugging Support**: Query events to understand what happened and when
4. **Dashboard Ready**: SQL queries make it easy to build monitoring dashboards
5. **Session Tracking**: Group events by session for analyzing specific builds
6. **Performance Analysis**: Track worker startup times, job throughput, etc.

## Next Steps

1. Add `worker_events` table to schema
2. Implement `WorkerEventLogger` class
3. Extend `ProgressTracker` for worker lifecycle
4. Update `WorkerLifecycleManager` to use event logging
5. Add example dashboard queries to documentation
6. Create monitoring dashboard (optional future work)
