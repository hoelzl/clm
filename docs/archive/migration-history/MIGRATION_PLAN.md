# CLM Architecture Migration: Detailed Implementation Plan

This document provides a step-by-step implementation guide for migrating CLM from RabbitMQ to SQLite-based orchestration.

## **REVISION NOTE (2025-11-12)**

**We are taking a DIRECT APPROACH instead of the dual-mode strategy originally planned.**

**Why the change**: The dual-mode approach (running both RabbitMQ and SQLite) proved too complex:
- Workers with `USE_SQLITE_QUEUE` environment variable still loaded FastStream/RabbitMQ code
- Docker images contained mixed code paths causing startup failures
- Maintaining both paths added debugging overhead

**New Strategy**:
1. ✅ Phase 1 COMPLETED: SQLite infrastructure built and tested (28 passing tests)
2. 🔄 Phase 2 IN PROGRESS: Remove RabbitMQ from workers entirely (SQLite-only)
3. Phase 3: Update backend to use SQLite JobQueue
4. Phase 4: Remove RabbitMQ infrastructure completely

**Progress tracked in**: [MIGRATION_TODO.md](./MIGRATION_TODO.md)

---

## Original Plan (for reference)

Each phase is designed to:
1. Result in a fully functional, testable system
2. Be implementable in small, reviewable commits
3. Include clear testing criteria
4. Allow rollback if issues arise

## Phase 1: Add SQLite Job Queue Infrastructure

**Duration**: 1-2 days
**Risk**: Low
**Goal**: Create SQLite job queue that runs alongside RabbitMQ

### Step 1.1: Create Database Schema

**Files to Create**:
- `clm-common/src/clm_common/database/schema.py`

**Tasks**:
1. Define SQLite schema for `jobs`, `results_cache`, and `workers` tables
2. Create migration/initialization functions
3. Add indexes for performance

**Code Example**:
```python
# clm-common/src/clm_common/database/schema.py
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
    """Initialize database with schema."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL mode for better concurrency
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
```

**Testing**:
```python
# tests/test_schema.py
def test_database_initialization():
    with tempfile.NamedTemporaryFile() as f:
        conn = init_database(Path(f.name))

        # Verify tables exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert 'jobs' in tables
        assert 'results_cache' in tables
        assert 'workers' in tables
```

---

### Step 1.2: Create Job Queue Manager

**Files to Create**:
- `clm-common/src/clm_common/database/job_queue.py`

**Tasks**:
1. Create `JobQueue` class for managing jobs
2. Implement methods: `add_job()`, `get_next_job()`, `update_job_status()`
3. Add caching logic: `check_cache()`, `add_to_cache()`
4. Thread-safe operations with proper locking

**Code Example**:
```python
# clm-common/src/clm_common/database/job_queue.py
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

@dataclass
class Job:
    id: int
    job_type: str
    status: str
    input_file: str
    output_file: str
    content_hash: str
    payload: Dict[str, Any]
    created_at: datetime
    attempts: int = 0

class JobQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def add_job(
        self,
        job_type: str,
        input_file: str,
        output_file: str,
        content_hash: str,
        payload: Dict[str, Any],
        priority: int = 0
    ) -> int:
        """Add a new job to the queue."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                job_type, status, input_file, output_file,
                content_hash, payload, priority
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?)
            """,
            (job_type, input_file, output_file, content_hash,
             json.dumps(payload), priority)
        )
        conn.commit()
        return cursor.lastrowid

    def check_cache(self, output_file: str, content_hash: str) -> Optional[Dict[str, Any]]:
        """Check if result exists in cache."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT result_metadata FROM results_cache
            WHERE output_file = ? AND content_hash = ?
            """,
            (output_file, content_hash)
        )
        row = cursor.fetchone()

        if row:
            # Update access statistics
            conn.execute(
                """
                UPDATE results_cache
                SET last_accessed = CURRENT_TIMESTAMP,
                    access_count = access_count + 1
                WHERE output_file = ? AND content_hash = ?
                """,
                (output_file, content_hash)
            )
            conn.commit()
            return json.loads(row[0]) if row[0] else None

        return None

    def add_to_cache(
        self,
        output_file: str,
        content_hash: str,
        result_metadata: Dict[str, Any]
    ):
        """Add result to cache."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO results_cache
            (output_file, content_hash, result_metadata)
            VALUES (?, ?, ?)
            """,
            (output_file, content_hash, json.dumps(result_metadata))
        )
        conn.commit()

    def get_next_job(self, job_type: str, worker_id: Optional[int] = None) -> Optional[Job]:
        """Get next pending job for the given type."""
        conn = self._get_conn()

        # Use transaction to atomically get and update job
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'pending' AND job_type = ? AND attempts < max_attempts
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (job_type,)
            )
            row = cursor.fetchone()

            if not row:
                conn.rollback()
                return None

            # Update job status
            conn.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    started_at = CURRENT_TIMESTAMP,
                    worker_id = ?,
                    attempts = attempts + 1
                WHERE id = ?
                """,
                (worker_id, row['id'])
            )
            conn.commit()

            return Job(
                id=row['id'],
                job_type=row['job_type'],
                status='processing',
                input_file=row['input_file'],
                output_file=row['output_file'],
                content_hash=row['content_hash'],
                payload=json.loads(row['payload']),
                created_at=datetime.fromisoformat(row['created_at']),
                attempts=row['attempts'] + 1
            )
        except Exception:
            conn.rollback()
            raise

    def update_job_status(
        self,
        job_id: int,
        status: str,
        error: Optional[str] = None
    ):
        """Update job status."""
        conn = self._get_conn()

        if status == 'completed':
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = CURRENT_TIMESTAMP, error = NULL
                WHERE id = ?
                """,
                (status, job_id)
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?
                WHERE id = ?
                """,
                (status, error, job_id)
            )

        conn.commit()

    def get_job_stats(self) -> Dict[str, Any]:
        """Get statistics about jobs."""
        conn = self._get_conn()

        stats = {}
        for status in ['pending', 'processing', 'completed', 'failed']:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = ?",
                (status,)
            )
            stats[status] = cursor.fetchone()[0]

        return stats
```

**Testing**:
```python
# tests/test_job_queue.py
def test_job_queue_operations():
    with tempfile.NamedTemporaryFile() as f:
        conn = init_database(Path(f.name))
        queue = JobQueue(Path(f.name))

        # Add job
        job_id = queue.add_job(
            job_type='notebook',
            input_file='test.py',
            output_file='test.ipynb',
            content_hash='abc123',
            payload={'lang': 'python'}
        )
        assert job_id > 0

        # Get next job
        job = queue.get_next_job('notebook')
        assert job is not None
        assert job.job_type == 'notebook'
        assert job.status == 'processing'

        # Update status
        queue.update_job_status(job.id, 'completed')

        # Verify stats
        stats = queue.get_job_stats()
        assert stats['completed'] == 1
```

---

### Step 1.3: Add Feature Flag and Dual-Queue Support

**Files to Modify**:
- `clm-faststream-backend/src/clm_faststream_backend/faststream_backend.py`
- `clm/src/clm/course_files/base.py`

**Tasks**:
1. Add `USE_SQLITE_QUEUE` environment variable
2. Modify backend to write to both RabbitMQ and SQLite when flag is enabled
3. Keep existing RabbitMQ behavior as default
4. Add logging to track which queue is being used

**Code Example**:
```python
# clm-faststream-backend/src/clm_faststream_backend/faststream_backend.py

import os
from pathlib import Path
from clm_common.database.job_queue import JobQueue
from clm_common.database.schema import init_database

class FastStreamBackend:
    def __init__(self, ...):
        # Existing initialization
        ...

        # Add SQLite support
        self.use_sqlite = os.getenv('USE_SQLITE_QUEUE', 'false').lower() == 'true'
        if self.use_sqlite:
            db_path = Path(os.getenv('CLM_DB_PATH', 'clm_jobs.db'))
            init_database(db_path)
            self.job_queue = JobQueue(db_path)
            logger.info(f"SQLite job queue enabled: {db_path}")

    async def process_notebook(self, operation: ProcessNotebookOperation) -> None:
        """Process notebook with dual-queue support."""
        # Check SQLite cache first if enabled
        if self.use_sqlite:
            cached = self.job_queue.check_cache(
                str(operation.output_file),
                operation.content_hash
            )
            if cached:
                logger.info(f"Cache hit for {operation.output_file}")
                # Write cached result
                await self._write_cached_result(operation, cached)
                return

        # Check existing database cache
        cached_result = self.database_manager.lookup_cached_result(...)
        if cached_result:
            # Existing cache logic
            ...
            return

        # Add job to SQLite if enabled
        if self.use_sqlite:
            job_id = self.job_queue.add_job(
                job_type='notebook',
                input_file=str(operation.input_file),
                output_file=str(operation.output_file),
                content_hash=operation.content_hash,
                payload={
                    'kind': operation.kind.value,
                    'prog_lang': operation.prog_lang.value,
                    'language': operation.language.value,
                    'format': operation.format.value,
                    'template_dir': str(operation.template_dir),
                    'other_files': operation.other_files,
                }
            )
            logger.debug(f"Added job {job_id} to SQLite queue")

        # Send to RabbitMQ (existing logic)
        correlation_id = self.new_correlation_id()
        payload = NotebookPayload(...)
        await self._publish_notebook(payload, correlation_id)
```

**Testing**:
1. Test with `USE_SQLITE_QUEUE=false` (default, RabbitMQ only)
2. Test with `USE_SQLITE_QUEUE=true` (writes to both)
3. Verify jobs appear in SQLite database
4. Verify RabbitMQ still works as before

---

### Step 1.4: Run End-to-End Tests

**Tasks**:
1. Run full test suite with `USE_SQLITE_QUEUE=false`
2. Run full test suite with `USE_SQLITE_QUEUE=true`
3. Verify both produce same outputs
4. Check SQLite database has correct job records

**Commands**:
```bash
# Test with RabbitMQ only (existing behavior)
docker-compose up -d
pytest tests/

# Test with dual queues
USE_SQLITE_QUEUE=true docker-compose up -d
pytest tests/

# Inspect SQLite database
sqlite3 clm_jobs.db "SELECT * FROM jobs"
```

**Success Criteria**:
- ✅ All existing tests pass
- ✅ Jobs appear in SQLite when flag is enabled
- ✅ Cache lookups work correctly
- ✅ No performance degradation

---

## Phase 2: Create Worker Pool Manager

**Duration**: 2-3 days
**Risk**: Medium
**Goal**: Implement long-lived worker pools that poll SQLite

### Step 2.1: Create Worker Base Class

**Files to Create**:
- `clm-common/src/clm_common/workers/worker_base.py`

**Tasks**:
1. Create abstract `Worker` class
2. Implement job polling loop
3. Add heartbeat mechanism
4. Handle graceful shutdown

**Code Example**:
```python
# clm-common/src/clm_common/workers/worker_base.py
import time
import signal
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from clm_common.database.job_queue import JobQueue, Job

logger = logging.getLogger(__name__)

class Worker(ABC):
    def __init__(
        self,
        worker_id: int,
        worker_type: str,
        db_path: Path,
        poll_interval: float = 0.1
    ):
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.job_queue = JobQueue(db_path)
        self.running = True

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info(f"Worker {self.worker_id} shutting down...")
        self.running = False

    def _update_heartbeat(self):
        """Update worker heartbeat in database."""
        conn = self.job_queue._get_conn()
        conn.execute(
            """
            UPDATE workers
            SET last_heartbeat = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (self.worker_id,)
        )
        conn.commit()

    def _update_status(self, status: str):
        """Update worker status."""
        conn = self.job_queue._get_conn()
        conn.execute(
            "UPDATE workers SET status = ? WHERE id = ?",
            (status, self.worker_id)
        )
        conn.commit()

    @abstractmethod
    def process_job(self, job: Job) -> None:
        """Process a job. Must be implemented by subclass."""
        pass

    def run(self):
        """Main worker loop."""
        logger.info(f"Worker {self.worker_id} ({self.worker_type}) started")
        self._update_status('idle')

        while self.running:
            try:
                # Get next job
                job = self.job_queue.get_next_job(self.worker_type, self.worker_id)

                if job is None:
                    # No jobs available
                    self._update_heartbeat()
                    time.sleep(self.poll_interval)
                    continue

                # Process job
                logger.info(f"Worker {self.worker_id} processing job {job.id}")
                self._update_status('busy')

                try:
                    self.process_job(job)
                    self.job_queue.update_job_status(job.id, 'completed')
                    logger.info(f"Worker {self.worker_id} completed job {job.id}")

                    # Update stats
                    conn = self.job_queue._get_conn()
                    conn.execute(
                        """
                        UPDATE workers
                        SET jobs_processed = jobs_processed + 1
                        WHERE id = ?
                        """,
                        (self.worker_id,)
                    )
                    conn.commit()

                except Exception as e:
                    logger.error(f"Worker {self.worker_id} failed job {job.id}: {e}")
                    self.job_queue.update_job_status(job.id, 'failed', str(e))

                    # Update stats
                    conn = self.job_queue._get_conn()
                    conn.execute(
                        """
                        UPDATE workers
                        SET jobs_failed = jobs_failed + 1
                        WHERE id = ?
                        """,
                        (self.worker_id,)
                    )
                    conn.commit()

                finally:
                    self._update_status('idle')
                    self._update_heartbeat()

            except Exception as e:
                logger.error(f"Worker {self.worker_id} error: {e}")
                time.sleep(1)  # Back off on errors

        logger.info(f"Worker {self.worker_id} stopped")
```

**Testing**:
```python
# tests/test_worker_base.py
class TestWorker(Worker):
    def process_job(self, job):
        # Simulate work
        time.sleep(0.1)

def test_worker_lifecycle():
    # Setup database
    with tempfile.NamedTemporaryFile() as f:
        init_database(Path(f.name))
        queue = JobQueue(Path(f.name))

        # Register worker
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ('test', 'test-1', 'idle')
        )
        worker_id = cursor.lastrowid
        conn.commit()

        # Add job
        queue.add_job('test', 'in.txt', 'out.txt', 'hash123', {})

        # Run worker in thread
        worker = TestWorker(worker_id, 'test', Path(f.name))
        thread = threading.Thread(target=worker.run)
        thread.start()

        # Wait for job completion
        time.sleep(0.5)
        worker.running = False
        thread.join()

        # Verify job completed
        stats = queue.get_job_stats()
        assert stats['completed'] == 1
```

---

### Step 2.2: Implement Worker Pool Manager

**Files to Create**:
- `clm-common/src/clm_common/workers/pool_manager.py`

**Tasks**:
1. Create `WorkerPoolManager` class
2. Implement container management (start, stop, restart)
3. Add health monitoring
4. Implement auto-restart for hung workers

**Code Example**:
```python
# clm-common/src/clm_common/workers/pool_manager.py
import docker
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from clm_common.database.job_queue import JobQueue

logger = logging.getLogger(__name__)

@dataclass
class WorkerConfig:
    worker_type: str
    image: str
    count: int
    memory_limit: str = '1g'
    max_job_time: int = 600  # seconds

class WorkerPoolManager:
    def __init__(
        self,
        db_path: Path,
        workspace_path: Path,
        worker_configs: List[WorkerConfig]
    ):
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.worker_configs = worker_configs
        self.docker_client = docker.from_env()
        self.job_queue = JobQueue(db_path)
        self.workers: Dict[str, List[docker.models.containers.Container]] = {}
        self.running = True

    def start_pools(self):
        """Start all worker pools."""
        for config in self.worker_configs:
            logger.info(f"Starting {config.count} {config.worker_type} workers")
            self.workers[config.worker_type] = []

            for i in range(config.count):
                container = self._start_worker(config, i)
                self.workers[config.worker_type].append(container)

                # Register in database
                conn = self.job_queue._get_conn()
                conn.execute(
                    """
                    INSERT INTO workers (worker_type, container_id, status)
                    VALUES (?, ?, 'idle')
                    """,
                    (config.worker_type, container.id)
                )
                conn.commit()

    def _start_worker(
        self,
        config: WorkerConfig,
        index: int
    ) -> docker.models.containers.Container:
        """Start a single worker container."""
        container_name = f"clm-{config.worker_type}-{index}"

        container = self.docker_client.containers.run(
            config.image,
            name=container_name,
            detach=True,
            remove=False,
            mem_limit=config.memory_limit,
            volumes={
                str(self.workspace_path): {'bind': '/workspace', 'mode': 'rw'},
                str(self.db_path): {'bind': '/db/jobs.db', 'mode': 'rw'}
            },
            environment={
                'WORKER_TYPE': config.worker_type,
                'DB_PATH': '/db/jobs.db',
                'LOG_LEVEL': 'INFO'
            },
            network='clm_app-network'  # Connect to same network as before
        )

        logger.info(f"Started worker container: {container_name} ({container.id[:12]})")
        return container

    def monitor_health(self):
        """Monitor worker health and restart if needed."""
        while self.running:
            try:
                conn = self.job_queue._get_conn()
                cursor = conn.execute(
                    """
                    SELECT id, worker_type, container_id, status, last_heartbeat
                    FROM workers
                    WHERE status IN ('busy', 'idle')
                    """
                )

                for row in cursor.fetchall():
                    worker_id = row[0]
                    container_id = row[2]
                    last_heartbeat = row[4]

                    # Check if heartbeat is stale (no update in 30 seconds)
                    if self._is_heartbeat_stale(last_heartbeat, 30):
                        logger.warning(f"Worker {worker_id} has stale heartbeat, checking...")

                        # Check container stats
                        try:
                            container = self.docker_client.containers.get(container_id)
                            stats = container.stats(stream=False)
                            cpu_percent = self._calculate_cpu_percent(stats)

                            # If CPU < 1% and status is busy, worker is hung
                            if cpu_percent < 1.0 and row[3] == 'busy':
                                logger.error(f"Worker {worker_id} is hung, restarting...")
                                self._restart_worker(worker_id, container_id)

                        except docker.errors.NotFound:
                            logger.error(f"Container {container_id[:12]} not found, restarting...")
                            self._restart_worker(worker_id, container_id)

                time.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                time.sleep(10)

    def _is_heartbeat_stale(self, last_heartbeat: str, threshold_seconds: int) -> bool:
        """Check if heartbeat timestamp is older than threshold."""
        from datetime import datetime, timedelta

        heartbeat_time = datetime.fromisoformat(last_heartbeat)
        now = datetime.now()
        return (now - heartbeat_time).total_seconds() > threshold_seconds

    def _calculate_cpu_percent(self, stats: dict) -> float:
        """Calculate CPU usage percentage from Docker stats."""
        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                   stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                      stats['precpu_stats']['system_cpu_usage']

        if system_delta > 0:
            return (cpu_delta / system_delta) * 100.0
        return 0.0

    def _restart_worker(self, worker_id: int, container_id: str):
        """Restart a worker."""
        try:
            # Stop old container
            container = self.docker_client.containers.get(container_id)
            container.stop(timeout=5)
            container.remove()
        except Exception as e:
            logger.error(f"Error stopping container: {e}")

        # Update database
        conn = self.job_queue._get_conn()
        conn.execute(
            "UPDATE workers SET status = 'dead' WHERE id = ?",
            (worker_id,)
        )
        conn.commit()

        # Start new worker (find the config)
        # This is simplified; real implementation would track which config to use
        # For now, we'll implement manual restart via CLI

    def stop_pools(self):
        """Stop all worker pools."""
        self.running = False

        for worker_type, containers in self.workers.items():
            logger.info(f"Stopping {worker_type} workers")
            for container in containers:
                try:
                    container.stop(timeout=10)
                    container.remove()
                except Exception as e:
                    logger.error(f"Error stopping container: {e}")
```

---

### Step 2.3: Update Worker Services to Poll SQLite

**Files to Modify**:
- `services/notebook-processor/src/nb/notebook_server.py`
- `services/drawio-converter/src/drawio_converter/drawio_converter.py`
- `services/plantuml-converter/src/plantuml_converter/plantuml_converter.py`

**Tasks**:
1. Add SQLite polling mode alongside RabbitMQ mode
2. Use `USE_SQLITE_QUEUE` environment variable to switch
3. Keep RabbitMQ mode as default

**Code Example** (for notebook processor):
```python
# services/notebook-processor/src/nb/notebook_server.py
import os
import sys
from pathlib import Path

# Check mode
USE_SQLITE = os.getenv('USE_SQLITE_QUEUE', 'false').lower() == 'true'

if USE_SQLITE:
    # SQLite worker mode
    from clm_common.workers.worker_base import Worker
    from clm_common.database.job_queue import Job
    from nb.notebook_processor import NotebookProcessor

    class NotebookWorker(Worker):
        def __init__(self, worker_id: int, db_path: Path):
            super().__init__(worker_id, 'notebook', db_path)
            self.processor = NotebookProcessor()

        def process_job(self, job: Job):
            """Process a notebook job."""
            # Read input file
            input_path = Path(job.input_file)
            with open(input_path, 'r', encoding='utf-8') as f:
                notebook_text = f.read()

            # Process
            result = self.processor.process_notebook(
                notebook_text=notebook_text,
                kind=job.payload['kind'],
                prog_lang=job.payload['prog_lang'],
                language=job.payload['language'],
                format=job.payload['format'],
                template_dir=Path(job.payload['template_dir']),
                other_files=job.payload.get('other_files', {})
            )

            # Write output file
            output_path = Path(job.output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(result)

            # Add to cache
            self.job_queue.add_to_cache(
                str(output_path),
                job.content_hash,
                {'format': job.payload['format']}
            )

    # Get worker ID from database or create new
    if __name__ == "__main__":
        db_path = Path(os.getenv('DB_PATH', '/db/jobs.db'))

        # Register worker (simplified; real implementation would be handled by pool manager)
        from clm_common.database.job_queue import JobQueue
        queue = JobQueue(db_path)
        conn = queue._get_conn()
        cursor = conn.execute(
            "INSERT INTO workers (worker_type, container_id, status) VALUES (?, ?, ?)",
            ('notebook', os.getenv('HOSTNAME', 'unknown'), 'idle')
        )
        worker_id = cursor.lastrowid
        conn.commit()

        # Start worker
        worker = NotebookWorker(worker_id, db_path)
        worker.run()

else:
    # RabbitMQ mode (existing code)
    from nb.notebook_server import start_rabbitmq_server

    if __name__ == "__main__":
        start_rabbitmq_server()
```

---

### Step 2.4: Test Worker Pools

**Tasks**:
1. Build updated Docker images
2. Start worker pools with `WorkerPoolManager`
3. Add jobs to SQLite queue
4. Verify workers process jobs correctly
5. Test health monitoring and restart

**Commands**:
```bash
# Build images
docker-compose build

# Start worker pools
USE_SQLITE_QUEUE=true python -m clm_common.workers.pool_manager

# In another terminal, add test jobs
sqlite3 clm_jobs.db "INSERT INTO jobs (job_type, status, input_file, output_file, content_hash, payload) VALUES ('notebook', 'pending', 'test.py', 'test.ipynb', 'abc123', '{}')"

# Monitor workers
watch "sqlite3 clm_jobs.db 'SELECT * FROM workers'"
watch "sqlite3 clm_jobs.db 'SELECT id, job_type, status FROM jobs'"
```

**Success Criteria**:
- ✅ Workers start successfully
- ✅ Workers poll and process jobs
- ✅ Jobs complete correctly
- ✅ Health monitoring detects issues
- ✅ Auto-restart works

---

## Phase 3: Switch Services to SQLite

**Duration**: 3-4 days
**Risk**: Medium
**Goal**: Migrate all job processing from RabbitMQ to SQLite

### Step 3.1: Migrate PlantUML Service

**Why PlantUML first?**: Simplest service, fewer dependencies

**Tasks**:
1. Update CLI to use SQLite for PlantUML jobs
2. Disable RabbitMQ for PlantUML
3. Test thoroughly
4. Monitor for issues

**Files to Modify**:
- `clm/src/clm/course_files/plantuml_file.py`

**Changes**:
```python
# clm/src/clm/course_files/plantuml_file.py

async def process(self, backend: Backend) -> None:
    """Process PlantUML file using SQLite queue."""
    if hasattr(backend, 'job_queue'):  # SQLite mode
        # Check cache
        cached = backend.job_queue.check_cache(
            str(self.output_file),
            self.content_hash
        )
        if cached:
            # Use cached result
            return

        # Add job
        backend.job_queue.add_job(
            job_type='plantuml',
            input_file=str(self.source_file),
            output_file=str(self.output_file),
            content_hash=self.content_hash,
            payload={}
        )
    else:
        # Fall back to RabbitMQ (existing code)
        await backend.process_plantuml(...)
```

**Testing**:
1. Process course with PlantUML files
2. Verify images are generated correctly
3. Compare output with RabbitMQ version
4. Check cache works

---

### Step 3.2: Migrate DrawIO Service

Similar process to PlantUML.

---

### Step 3.3: Migrate Notebook Processor

**Tasks**:
1. Update notebook processing to use SQLite
2. Test all combinations:
   - Languages: en, de
   - Modes: speaker, participant
   - Formats: notebook, html
   - Programming languages: Python, C#, Java, TypeScript, C++
3. Verify templates work correctly
4. Check error handling

---

### Step 3.4: Full Integration Testing

**Tasks**:
1. Process complete course with all file types
2. Test watch mode
3. Test concurrent processing
4. Stress test with many files
5. Verify all outputs are identical to RabbitMQ version

**Success Criteria**:
- ✅ All file types process correctly
- ✅ Cache works as expected
- ✅ Performance is equal or better
- ✅ No regressions in functionality

---

## Phase 4: Remove RabbitMQ Infrastructure

**Duration**: 1 day
**Risk**: Low
**Goal**: Clean up RabbitMQ code and dependencies

### Step 4.1: Remove RabbitMQ from docker-compose

**Files to Modify**:
- `docker-compose.yaml`

**Changes**:
```yaml
# Remove these services:
# - rabbitmq
# - rabbitmq-exporter
# - prometheus (optional, can keep)
# - grafana (optional, can keep)
# - loki (optional, can keep)

# Simplified docker-compose.yaml
services:
  notebook-processor:
    build: ./services/notebook-processor
    environment:
      - USE_SQLITE_QUEUE=true
      - DB_PATH=/db/jobs.db
    volumes:
      - ./data:/workspace
      - ./clm_jobs.db:/db/jobs.db
    # Remove depends_on rabbitmq

  drawio-converter:
    # Similar changes

  plantuml-converter:
    # Similar changes
```

---

### Step 4.2: Remove clm-faststream-backend Package

**Tasks**:
1. Delete `clm-faststream-backend/` directory
2. Remove from dependencies in other packages
3. Update imports

---

### Step 4.3: Remove RabbitMQ Dependencies

**Files to Modify**:
- All `pyproject.toml` files

**Changes**:
```toml
# Remove these dependencies:
# - faststream
# - aio-pika
# - rabbitmq-related packages
```

---

### Step 4.4: Update Documentation

**Tasks**:
1. Update README
2. Remove RabbitMQ setup instructions
3. Add SQLite architecture documentation
4. Update deployment guide

---

## Phase 5: Consolidate Packages

**Duration**: 2-3 days
**Risk**: Medium
**Goal**: Merge all packages into single `clm` package

### Step 5.1: Merge clm-common into clm

**Tasks**:
1. Move `clm-common/src/clm_common/` to `clm/src/clm/common/`
2. Update all imports
3. Remove `clm-common` package
4. Update `pyproject.toml`

**Migration Script**:
```bash
# Move files
mv clm-common/src/clm_common/* clm/src/clm/common/

# Update imports (use sed or similar)
find clm -type f -name "*.py" -exec sed -i 's/from clm_common/from clm.common/g' {} +
find clm -type f -name "*.py" -exec sed -i 's/import clm_common/import clm.common/g' {} +

# Update tests
mv clm-common/tests/* clm/tests/common/

# Remove old package
rm -rf clm-common/
```

---

### Step 5.2: Merge clm-cli into clm

**Tasks**:
1. Move `clm-cli/src/clm_cli/` to `clm/src/clm/cli/`
2. Update entry points in `pyproject.toml`
3. Update imports
4. Test CLI still works

**Entry Point**:
```toml
[project.scripts]
clm = "clm.cli.main:cli"
```

---

### Step 5.3: Reorganize Package Structure

**Final Structure**:
```
clm/
├── pyproject.toml
├── README.md
├── ARCHITECTURE_PROPOSAL.md
├── MIGRATION_PLAN.md
├── src/
│   └── clm/
│       ├── __init__.py
│       ├── cli/              # CLI commands
│       │   ├── main.py
│       │   ├── build.py
│       │   ├── status.py
│       │   └── workers.py
│       ├── core/             # Core course logic
│       │   ├── course.py
│       │   ├── course_spec.py
│       │   └── course_files/
│       ├── database/         # SQLite management
│       │   ├── schema.py
│       │   ├── job_queue.py
│       │   └── cache.py
│       ├── workers/          # Worker pool management
│       │   ├── worker_base.py
│       │   ├── pool_manager.py
│       │   └── health_monitor.py
│       └── messaging/        # Message types (Payloads, Results)
│           ├── payloads.py
│           └── results.py
├── services/                 # Worker implementations
│   ├── notebook-processor/
│   ├── drawio-converter/
│   └── plantuml-converter/
└── tests/
```

---

## Phase 6: Add Enhanced Monitoring

**Duration**: 1-2 days
**Risk**: Low
**Goal**: Add built-in CLI monitoring commands

### Step 6.1: Add `clm status` Command

**File to Create**:
- `clm/src/clm/cli/status.py`

**Features**:
```bash
$ clm status

CLM System Status
=================

Workers:
  notebook: 2 running, 0 idle, 0 busy, 0 hung
  drawio:   1 running, 1 idle, 0 busy, 0 hung
  plantuml: 1 running, 1 idle, 0 busy, 0 hung

Jobs:
  Pending:    5
  Processing: 2
  Completed:  1234
  Failed:     3

Cache:
  Hit rate:   87.3%
  Entries:    456
  Size:       123 MB

Recent Errors:
  [2025-01-15 14:23:45] Job 789 failed: Kernel timeout
  [2025-01-15 14:20:12] Job 756 failed: Invalid syntax
```

---

### Step 6.2: Add `clm workers` Command

**Features**:
```bash
$ clm workers list

ID  Type      Container       Status  Jobs  Uptime
==  ========  ==============  ======  ====  ======
1   notebook  abc123def456    busy    45    2h 34m
2   notebook  def789ghi012    idle    38    2h 34m
3   drawio    ghi345jkl678    idle    102   2h 34m
4   plantuml  jkl901mno234    idle    89    2h 34m

$ clm workers restart 1
Restarting worker 1...
Worker 1 restarted successfully.
```

---

### Step 6.3: Add `clm jobs` Command

**Features**:
```bash
$ clm jobs list --status failed

ID    Type      Input File                Status  Error
====  ========  ========================  ======  ===================
789   notebook  slides/module_001/...     failed  Kernel timeout
756   notebook  slides/module_002/...     failed  Invalid syntax

$ clm jobs retry 789
Retrying job 789...
Job 789 added back to queue.
```

---

### Step 6.4: Add `clm cache` Command

**Features**:
```bash
$ clm cache stats

Cache Statistics:
  Total entries: 456
  Hit rate: 87.3%
  Size: 123 MB
  Oldest entry: 2025-01-10 08:23:45
  Newest entry: 2025-01-15 14:45:12

$ clm cache clear
Clear cache? [y/N] y
Cache cleared (456 entries removed).
```

---

## Phase 7: Final Testing and Documentation

**Duration**: 2-3 days
**Risk**: Low

### Testing Checklist

- [ ] Unit tests for all new components
- [ ] Integration tests for end-to-end workflows
- [ ] Performance comparison (before vs after)
- [ ] Stress testing with large courses
- [ ] Test all programming languages
- [ ] Test all output combinations
- [ ] Test error scenarios
- [ ] Test worker restart scenarios
- [ ] Test cache invalidation
- [ ] Test watch mode

### Documentation Updates

- [ ] Update README with new architecture
- [ ] Document SQLite schema
- [ ] Document CLI commands
- [ ] Add troubleshooting guide
- [ ] Update deployment instructions
- [ ] Add architecture diagrams
- [ ] Document migration process
- [ ] Create developer guide

---

## Rollback Plan

If issues arise during migration:

1. **Phase 1-2**: Disable `USE_SQLITE_QUEUE` flag, use RabbitMQ
2. **Phase 3**: Revert changes to specific service
3. **Phase 4+**: Restore from git history

---

## Success Metrics

After migration, verify:

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Packages | 4 | 1 | 1 |
| Docker Services | 8 | 3 | ≤4 |
| Startup Time | ~30s | TBD | <10s |
| Memory Usage | ~1.5GB | TBD | <800MB |
| Build Time | X | TBD | ≤X |
| Test Time | Y | TBD | ≤Y |
| Lines of Code | Z | TBD | <Z |

---

## Conclusion

This migration plan provides a safe, incremental path to simplify the CLM architecture while maintaining all functionality. Each phase can be tested independently, and rollback is possible at any point.

The key is to move slowly and test thoroughly at each step. The dual-queue approach in Phase 1-2 allows us to validate the new system before removing the old one.
