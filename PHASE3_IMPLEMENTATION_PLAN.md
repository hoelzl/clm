# Phase 3: Backend Integration - Implementation Plan

## Current Architecture (RabbitMQ-based)

**Flow:**
1. CLI creates `FastStreamBackend()`
2. `course.process_all(backend)` submits operations
3. Backend publishes messages to RabbitMQ with correlation IDs
4. Workers process messages and publish results back
5. Backend handlers receive results and write files
6. Correlation IDs track completion
7. CLI waits for all correlation IDs to complete

**Key Components:**
- `FastStreamBackend` - manages RabbitMQ connection and publishing
- Correlation IDs - track pending operations
- `active_correlation_ids` - dict of pending operations
- Result handlers - receive results from RabbitMQ and write files
- `wait_for_completion()` - waits for correlation IDs to be empty

## Target Architecture (SQLite-based)

**Flow:**
1. CLI creates `FastStreamBackend()` (simplified, no RabbitMQ)
2. `course.process_all(backend)` submits operations
3. Backend adds jobs to SQLite queue
4. Workers poll SQLite, process jobs, write output files
5. Workers update job status in SQLite
6. Backend polls SQLite for job completion
7. CLI waits for all jobs to complete

**Key Changes:**
- No RabbitMQ connection or publishing
- No correlation IDs (use job IDs instead)
- No result handlers (workers write files directly)
- Poll SQLite jobs table for completion

## Implementation Strategy

### Option A: Complete Rewrite (Recommended)
Create a new `SqliteBackend` class that's simpler and SQLite-only.

**Pros:**
- Clean, simple code
- No legacy RabbitMQ baggage
- Easier to understand and maintain
- Can co-exist with FastStreamBackend during migration

**Cons:**
- More work upfront
- Need to update CLI to use new backend

### Option B: Modify FastStreamBackend (Risky)
Remove RabbitMQ from existing `FastStreamBackend`.

**Pros:**
- Less code to write
- CLI doesn't need changes

**Cons:**
- Complex code with mixed concerns
- Hard to test both modes
- Risk breaking existing functionality

## Recommended Approach: Option A

Create `SqliteBackend` as a new, clean implementation.

### Step 1: Create SqliteBackend Class

```python
# clx-faststream-backend/src/clx_faststream_backend/sqlite_backend.py

import asyncio
import logging
from pathlib import Path
from typing import Dict
from attrs import define
from clx_common.backends.local_ops_backend import LocalOpsBackend
from clx_common.database.job_queue import JobQueue
from clx_common.database.schema import init_database
from clx_common.database.db_operations import DatabaseManager
from clx_common.operation import Operation
from clx_common.messaging.base_classes import Payload

logger = logging.getLogger(__name__)


@define
class SqliteBackend(LocalOpsBackend):
    """SQLite-based backend for job queue orchestration.

    This backend submits jobs to a SQLite database and waits for
    workers to complete them. It's a simpler alternative to the
    RabbitMQ-based FastStreamBackend.
    """

    db_path: Path = Path('clx_jobs.db')
    workspace_path: Path = Path.cwd()
    job_queue: JobQueue | None = None
    db_manager: DatabaseManager | None = None
    ignore_db: bool = False
    active_jobs: Dict[int, Dict] = {}  # job_id -> job info
    poll_interval: float = 0.5  # seconds

    def __attrs_post_init__(self):
        """Initialize SQLite database and job queue."""
        init_database(self.db_path)
        self.job_queue = JobQueue(self.db_path)
        logger.info(f"Initialized SQLite backend with database: {self.db_path}")

    async def start(self):
        """Start the backend (no-op for SQLite, kept for compatibility)."""
        logger.debug("SQLite backend started")

    async def execute_operation(self, operation: Operation, payload: Payload) -> None:
        """Submit a job to the SQLite queue.

        Args:
            operation: Operation to execute
            payload: Payload data for the job
        """
        # Check cache first
        if not self.ignore_db and self.db_manager:
            result = self.db_manager.get_result(
                payload.input_file,
                payload.content_hash(),
                payload.output_metadata()
            )
            if result:
                logger.debug(
                    f"Cache hit for {payload.input_file} -> {payload.output_file}"
                )
                # Write cached result
                output_file = Path(payload.output_file)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_text(result.data, encoding='utf-8')
                return

        # Check SQLite cache
        if self.job_queue:
            cached = self.job_queue.check_cache(
                str(payload.output_file),
                payload.content_hash()
            )
            if cached:
                logger.debug(f"SQLite cache hit for {payload.output_file}")
                # Output file should already exist from previous run
                return

        # Map service to job type
        service_to_job_type = {
            "notebook-processor": "notebook",
            "drawio-converter": "drawio",
            "plantuml-converter": "plantuml"
        }

        service_name = operation.service_name
        if service_name not in service_to_job_type:
            raise ValueError(f"Unknown service: {service_name}")

        job_type = service_to_job_type[service_name]

        # Prepare payload dict specific to job type
        payload_dict = payload.to_dict()  # Assuming payload has to_dict() method

        # Add job to queue
        job_id = self.job_queue.add_job(
            job_type=job_type,
            input_file=str(payload.input_file),
            output_file=str(payload.output_file),
            content_hash=payload.content_hash(),
            payload=payload_dict
        )

        # Track active job
        self.active_jobs[job_id] = {
            'job_type': job_type,
            'input_file': str(payload.input_file),
            'output_file': str(payload.output_file),
            'correlation_id': payload.correlation_id
        }

        logger.debug(
            f"Added job {job_id} ({job_type}): {payload.input_file} -> {payload.output_file}"
        )

    async def wait_for_completion(self, timeout: float = 1200.0) -> None:
        """Wait for all submitted jobs to complete.

        Args:
            timeout: Maximum time to wait in seconds (default: 20 minutes)

        Raises:
            TimeoutError: If jobs don't complete within timeout
        """
        if not self.active_jobs:
            return

        logger.info(f"Waiting for {len(self.active_jobs)} job(s) to complete...")
        start_time = asyncio.get_event_loop().time()

        while self.active_jobs:
            # Check each active job
            completed_jobs = []

            for job_id, job_info in self.active_jobs.items():
                # Query job status from database
                conn = self.job_queue._get_conn()
                cursor = conn.execute(
                    "SELECT status, error FROM jobs WHERE id = ?",
                    (job_id,)
                )
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"Job {job_id} not found in database")
                    completed_jobs.append(job_id)
                    continue

                status = row[0]
                error = row[1]

                if status == 'completed':
                    logger.info(
                        f"Job {job_id} completed: {job_info['input_file']} -> {job_info['output_file']}"
                    )
                    completed_jobs.append(job_id)

                    # Add to database cache if applicable
                    if not self.ignore_db and self.db_manager:
                        # Read output file and store in database
                        output_path = Path(job_info['output_file'])
                        if output_path.exists():
                            data = output_path.read_text(encoding='utf-8')
                            # Store in database (simplified - actual implementation would use proper Result object)
                            # self.db_manager.add_result(...)

                elif status == 'failed':
                    logger.error(
                        f"Job {job_id} failed: {job_info['input_file']} -> {job_info['output_file']}\n"
                        f"Error: {error}"
                    )
                    completed_jobs.append(job_id)

                    # Track error for reporting
                    # Could integrate with handler_errors system

            # Remove completed jobs
            for job_id in completed_jobs:
                del self.active_jobs[job_id]

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"Jobs did not complete within {timeout} seconds. "
                    f"{len(self.active_jobs)} job(s) still pending."
                )

            # Wait before polling again
            if self.active_jobs:
                await asyncio.sleep(self.poll_interval)

        logger.info("All jobs completed successfully")

    async def shutdown(self):
        """Shutdown the backend."""
        logger.debug("Shutting down SQLite backend")
        # Wait for remaining jobs with shorter timeout
        try:
            await asyncio.wait_for(self.wait_for_completion(), timeout=5.0)
        except TimeoutError:
            logger.warning(
                f"Shutdown timeout - {len(self.active_jobs)} job(s) still pending"
            )
```

### Step 2: Add to_dict() Methods to Payload Classes

Payload classes need to serialize to dict for SQLite storage.

### Step 3: Update CLI to Use SqliteBackend

Make `SqliteBackend` an option in the CLI:

```python
# clx-cli/src/clx_cli/main.py

@click.option(
    "--use-sqlite",
    is_flag=True,
    default=False,
    help="Use SQLite backend instead of RabbitMQ"
)
async def main(ctx, ..., use_sqlite):
    ...
    with DatabaseManager(db_path, force_init=force_db_init) as db_manager:
        if use_sqlite:
            backend = SqliteBackend(
                db_path=db_path,
                workspace_path=output_dir,
                db_manager=db_manager,
                ignore_db=ignore_db
            )
        else:
            backend = FastStreamBackend(
                db_manager=db_manager,
                ignore_db=ignore_db
            )

        async with backend:
            ...
            await course.process_all(backend)
            await backend.wait_for_completion()  # Explicit wait
```

### Step 4: Write Tests

```python
# tests/test_sqlite_backend.py

import pytest
from pathlib import Path
from clx_faststream_backend.sqlite_backend import SqliteBackend

@pytest.mark.asyncio
async def test_sqlite_backend_initialization():
    backend = SqliteBackend(db_path=Path('test.db'))
    assert backend.job_queue is not None

@pytest.mark.asyncio
async def test_sqlite_backend_job_submission():
    backend = SqliteBackend(db_path=Path('test.db'))
    # Create mock operation and payload
    # Submit job
    # Verify job in database

@pytest.mark.asyncio
async def test_sqlite_backend_wait_for_completion():
    backend = SqliteBackend(db_path=Path('test.db'))
    # Submit jobs
    # Simulate worker processing
    # Wait for completion
    # Verify completion
```

## Migration Timeline

1. **Phase 3.1**: Create `SqliteBackend` class
2. **Phase 3.2**: Add to_dict() methods to payloads
3. **Phase 3.3**: Write comprehensive tests
4. **Phase 3.4**: Add CLI option for SQLite backend
5. **Phase 3.5**: Test end-to-end with real course
6. **Phase 3.6**: Make SQLite the default
7. **Phase 3.7**: Remove FastStreamBackend (Phase 4)

## Testing Strategy

1. Unit tests for `SqliteBackend` class
2. Integration tests with mock workers
3. End-to-end tests with real workers and course files
4. Performance comparison with RabbitMQ
5. Error handling tests

## Risks and Mitigation

**Risk**: Breaking existing CLI functionality
**Mitigation**: Keep both backends, add flag to choose

**Risk**: Jobs not completing (workers not running)
**Mitigation**: Good error messages, diagnostic tools

**Risk**: Performance degradation
**Mitigation**: Benchmark and optimize polling interval

## Success Criteria

- [ ] SqliteBackend can submit jobs
- [ ] SqliteBackend can wait for completion
- [ ] CLI can use SqliteBackend with flag
- [ ] All tests pass
- [ ] End-to-end course processing works
- [ ] Performance equal or better than RabbitMQ
