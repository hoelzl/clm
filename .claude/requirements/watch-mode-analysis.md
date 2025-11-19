# CLX Watch Mode: Comprehensive Analysis and Recommendations

**Date**: 2025-11-19 (Updated after rebase to v0.4.0)
**Author**: Claude (AI Assistant)
**Status**: Requirements Analysis
**Branch**: `claude/add-watch-mode-01Xtc6R8YMjo71ei1iouPG1e`
**CLX Version**: 0.4.0 (unified package architecture)

---

## Executive Summary

The CLX watch mode exists but is **functionally broken** after the architectural rewrite from RabbitMQ to SQLite. While the file monitoring infrastructure still works, the implementation lacks critical features needed for a usable development experience:

### Critical Issues

1. **No debouncing** - Text editors trigger multiple events per save, creating duplicate jobs
2. **No job cancellation** - Running jobs cannot be stopped when file changes again
3. **No selective output** - Watch mode generates all formats (HTML, notebooks, code) even when only quick feedback is needed
4. **No state consistency** - Partial outputs left behind when interrupted
5. **No feedback loop** - Users have no visibility into what's happening

### Key Findings

- **Architecture is sound**: SQLite job queue + persistent workers is well-suited for watch mode
- **Worker management works**: Workers can persist across file changes efficiently
- **Caching exists**: Content-hash based caching prevents redundant processing
- **Gaps are in orchestration**: Need event debouncing, job lifecycle, and user feedback

### Recommendation

Implement a **phased approach** with three milestones:

1. **Phase 1 (MVP)**: Event debouncing + fast-mode processing (skip HTML)
2. **Phase 2 (Refinement)**: Job cancellation + state consistency
3. **Phase 3 (Polish)**: User feedback + intelligent batching

Estimated effort: **2-4 days** for Phase 1, **1-2 weeks** for complete implementation.

---

## Note on v0.4.0 Architectural Changes

**Important**: This analysis was updated after rebasing to CLX v0.4.0, which introduced a unified package architecture:

- **Workers consolidated**: Moved from separate `services/*/` packages to `src/clx/workers/`
- **Module paths changed**: `python -m nb` → `python -m clx.workers.notebook`
- **Installation via extras**: `pip install clx[notebook]`, `clx[plantuml]`, `clx[drawio]`
- **Error tracking added**: FileEventHandler now stops after 10 errors (partial FR7 implementation)

These architectural improvements **do not affect** the core watch mode analysis:
- The fundamental issues (no debouncing, no job cancellation, all formats generated) remain
- The proposed solutions in this document are still valid and applicable
- Code examples have been updated to reflect v0.4.0 module paths

For full v0.4.0 migration details, see `MIGRATION_V0.4.md` in the repository root.

---

## Current Implementation Analysis

### 1. Architecture Overview

```
File System Change
    ↓
Watchdog Observer (monitors filesystem)
    ↓
FileEventHandler (src/clx/cli/file_event_handler.py)
    ↓ [Filters: ignore temp files, .git, __pycache__, etc.]
    ↓
Event Methods: on_modified(), on_created(), on_deleted(), on_moved()
    ↓
loop.create_task(handle_event(...))  ← Spawns async task IMMEDIATELY
    ↓
Course.process_file(backend, path)
    ↓
file.get_processing_operation(output_root)
    ↓ [NotebookFile generates 10 operations: 2 langs × (2 HTML + 1 notebook + 1 code) × 2 modes + 2 speaker]
    ↓
operation.execute(backend)
    ↓
Backend checks cache → Submits jobs to SQLite queue
    ↓
Workers (persistent) poll queue and process jobs
```

### 2. Worker Lifecycle in Watch Mode

**Current behavior** (from `src/clx/cli/main.py:142-229`):

```python
# Workers start BEFORE watch mode
lifecycle_manager = WorkerLifecycleManager(...)
started_workers = lifecycle_manager.start_managed_workers()

# Watch mode runs with persistent workers
observer = Observer()
observer.schedule(event_handler, data_dir, recursive=True)
observer.start()

# Keep running until SIGINT/SIGTERM
while not shut_down:
    await asyncio.sleep(1)

# Workers stop AFTER watch mode exits
lifecycle_manager.stop_managed_workers(started_workers)
```

**Key observations**:
- ✅ Workers persist across file changes (no startup overhead)
- ✅ Workers can be reused from previous sessions (if `reuse_workers=true`)
- ✅ Worker health monitoring via heartbeats (30-second timeout)
- ❌ Workers cannot be dynamically scaled up/down during watch
- ❌ No mechanism to interrupt running jobs

### 3. Event Handling Flow

**File modified event** (`src/clx/cli/file_event_handler.py:86-89`):

```python
@staticmethod
async def on_file_modified(course: Course, backend: Backend, path: Path):
    logger.info(f"On file modified: {path}")
    if course.find_course_file(path):
        await course.process_file(backend, path)
```

**Processing a notebook** (`src/clx/core/course_files/notebook_file.py:29-45`):

```python
async def get_processing_operation(self, target_dir: Path) -> Operation:
    return Concurrently(
        ProcessNotebookOperation(...)
        for lang, format_, mode, output_dir in output_specs(
            self.course, target_dir, self.skip_html
        )
    )
```

**Output specifications** (`src/clx/infrastructure/utils/path_utils.py:203-232`):

For a **single notebook** with `skip_html=False`:
1. DE/EN × HTML × code-along/completed = **4 HTML outputs**
2. DE/EN × notebook × code-along/completed = **4 notebook outputs**
3. DE/EN × code × completed = **2 code outputs**
4. DE/EN × HTML × speaker = **2 HTML speaker outputs**
5. DE/EN × notebook × speaker = **2 notebook speaker outputs**

**Total: 10 operations per notebook** (14 if including speaker variants)

With `skip_html=True`: **6 operations** (notebooks + code only)

### 4. Job Queue Behavior

**Job submission** (`src/clx/infrastructure/backends/sqlite_backend.py`):

```python
async def execute_operation(self, operation: Operation, payload: Payload) -> None:
    # 1. Check database cache (pickled results)
    if db_manager.has_cached_result(...):
        write_cached_result_and_return()

    # 2. Check SQLite results_cache (content hash)
    if job_queue.check_cache(output_file, content_hash):
        return  # Output file should exist

    # 3. Check workers available
    if no_workers_available():
        raise RuntimeError("No workers available")

    # 4. Submit job to queue
    job_id = job_queue.add_job(...)

    # 5. Track active job (but don't wait for completion!)
    self.active_jobs[job_id] = {...}
```

**Critical finding**: Jobs are submitted but **not awaited** in watch mode!

The `wait_for_completion()` method is only called:
- During initial `process_all()` (full course build)
- During `process_stage()` (stage-based builds)
- During backend shutdown (cleanup)

**In watch mode**: File changes submit jobs asynchronously, workers process in background, no explicit wait.

### 5. Caching Mechanisms

**Two-tier caching**:

1. **Database cache** (`clx_cache.db`):
   - Stores pickled `ProcessedFile` objects
   - Key: `(input_file, content_hash, output_metadata)`
   - Bypass: `--ignore-db` flag

2. **Job queue cache** (`clx_jobs.db` → `results_cache` table):
   - Stores result metadata (not full results)
   - Key: `(output_file, content_hash)`
   - Purpose: Prevent duplicate job submission

**Impact on watch mode**:
- ✅ Prevents redundant processing if content unchanged
- ✅ Fast cache hits (direct file write, no job submission)
- ⚠️ Cache key includes output metadata (change in config invalidates cache)

### 6. What Works

1. **File monitoring**: Watchdog correctly detects file changes
2. **File filtering**: Temporary files and build directories are ignored
3. **Worker persistence**: Workers remain running throughout session
4. **Worker reuse**: Can leverage workers from previous `clx build` sessions
5. **Content hashing**: Identical file content uses cache
6. **Async job submission**: Non-blocking job submission works correctly
7. **Error tracking** (v0.4.0): FileEventHandler tracks errors and stops after 10 failures (partial FR7 implementation)

### 7. What Doesn't Work

1. **No debouncing**: Each file save triggers immediate job submission
   ```
   User edits file.ipynb in VSCode
   → VSCode auto-save: on_modified event
   → 10 jobs submitted to queue

   User continues editing (0.5s later)
   → Another auto-save: on_modified event
   → 10 MORE jobs submitted (20 total)

   User saves manually (1s later)
   → Final save: on_modified event
   → 10 MORE jobs submitted (30 total!)
   ```

2. **No job cancellation**: Jobs in queue or processing cannot be aborted
   - Job status: `pending → processing → completed/failed`
   - No `cancelled` status in schema
   - Workers don't check for cancellation signals

3. **All formats generated**: Cannot disable expensive operations in watch mode
   - HTML generation requires notebook execution (slow)
   - No way to skip HTML in watch mode specifically
   - `skip_html` is per-topic config (not runtime flag)

4. **No state consistency**: Interrupted processing leaves partial outputs
   - If user changes file while job is processing
   - Old output files remain (stale state)
   - No cleanup mechanism

5. **No feedback**: User has no idea what's happening
   - No progress indication
   - No completion notification
   - No error reporting (except logs)

---

## Architectural Constraints & Capabilities

### What the Current Architecture Enables

1. **Persistent Workers** (`src/clx/infrastructure/workers/lifecycle_manager.py`)
   - ✅ Workers can run for hours/days
   - ✅ No startup overhead on file changes
   - ✅ Health monitoring via heartbeats
   - ✅ Graceful shutdown on SIGTERM/SIGINT

2. **Content-Based Caching**
   - ✅ Content hash prevents duplicate work
   - ✅ Fast cache hits (< 1ms)
   - ✅ Persistent across sessions

3. **Async Job Processing**
   - ✅ Non-blocking job submission
   - ✅ Workers process jobs concurrently
   - ✅ Semaphore limits concurrency (`CLX_MAX_CONCURRENCY`)

4. **SQLite Job Queue** (`src/clx/infrastructure/database/job_queue.py`)
   - ✅ Thread-safe with WAL mode
   - ✅ Persistent queue (survives crashes)
   - ✅ Priority support (not currently used)
   - ✅ Correlation IDs for tracing

### What the Current Architecture Prevents

1. **Job Interruption**
   - ❌ No cancellation mechanism in job queue
   - ❌ Workers don't check for cancellation
   - ❌ No `cancelled` job status

2. **Dynamic Worker Scaling**
   - ⚠️ Workers started before watch mode
   - ⚠️ Cannot easily scale up/down during watch
   - (Could be implemented but not current behavior)

3. **Immediate Feedback**
   - ❌ No completion callbacks in backend
   - ❌ No progress events emitted
   - (Could use correlation IDs to track)

### Key Design Decisions to Preserve

1. **Workers persist across file changes** - Fast response time, efficient resource use
2. **Content-hash caching** - Essential for performance
3. **Async job submission** - Enables responsive UI
4. **SQLite job queue** - Simple, reliable, no external dependencies

---

## Requirements for Usable Watch Mode

### Functional Requirements

#### FR1: Event Debouncing (CRITICAL)
- **Requirement**: Coalesce rapid file changes into single processing event
- **Rationale**: Text editors trigger multiple saves per user action
- **Target**: 100-500ms debounce window (configurable)
- **Example**:
  ```
  User types and saves → 100ms debounce → Process once
  Multiple rapid saves → Collapsed into single event
  ```

#### FR2: Fast Feedback Mode (CRITICAL)
- **Requirement**: Skip expensive operations during watch mode
- **Rationale**: HTML generation takes 5-30 seconds per notebook (execution)
- **Target**: Process notebooks in < 2 seconds for quick feedback
- **Implementation**: `--watch-mode=fast` skips HTML, generates only notebooks
- **Options**:
  - `fast`: Notebooks only (no execution, no HTML)
  - `normal`: Notebooks + HTML (default)
  - `minimal`: Notebooks for current language only

#### FR3: Job Cancellation (HIGH)
- **Requirement**: Cancel in-flight jobs when file changes again
- **Rationale**: User keeps editing, old jobs become obsolete
- **Target**: Cancel jobs within 1 second of new file change
- **Scope**:
  - Cancel `pending` jobs in queue
  - Signal `processing` jobs to abort (cooperative)
  - Clean up partial outputs

#### FR4: State Consistency (HIGH)
- **Requirement**: Output directory always reflects latest source
- **Rationale**: Stale outputs confuse users
- **Target**: Atomic output updates (write temp, move on success)
- **Cleanup**: Remove outputs when source file deleted

#### FR5: User Feedback (MEDIUM)
- **Requirement**: Show processing status to user
- **Rationale**: Users need to know when build is complete
- **Target**: Log messages indicating start/progress/completion
- **Options**:
  - Console logs (MVP)
  - Progress bar (nice-to-have)
  - Desktop notifications (future)

#### FR6: Intelligent Batching (MEDIUM)
- **Requirement**: Batch multiple file changes into single rebuild
- **Rationale**: `git checkout` changes many files simultaneously
- **Target**: Detect multi-file changes, process as batch
- **Implementation**: 500ms-1s batching window after first change

#### FR7: Error Recovery (MEDIUM)
- **Requirement**: Watch mode continues after processing errors
- **Rationale**: Syntax errors during development are normal
- **Target**: Log error, continue watching
- **Scope**: Don't crash watch mode on job failures

#### FR8: Selective Rebuilds (LOW)
- **Requirement**: Only rebuild affected outputs
- **Rationale**: Changing one notebook shouldn't rebuild entire course
- **Target**: Already implemented (file-level operations)
- **Status**: ✅ Current implementation already does this

### Non-Functional Requirements

#### NFR1: Performance
- Debounced event processing: < 100ms overhead
- Job cancellation: < 1s from signal to abort
- Cache hit: < 1ms to detect and skip
- Watch mode startup: < 5s with worker reuse

#### NFR2: Reliability
- No job loss during cancellation
- No corrupted outputs (atomic writes)
- Watch mode survives worker crashes
- Graceful shutdown on Ctrl+C

#### NFR3: Resource Usage
- Workers: Reuse existing workers (no spawn storm)
- Memory: Bounded job queue (limit pending jobs)
- Disk: Cleanup old job records periodically

#### NFR4: Developer Experience
- Clear log messages
- Configurable watch mode behavior
- Easy to enable/disable features
- Documented in user guide

---

## Proposed Solutions

### Solution 1: MVP - Debouncing + Fast Mode (RECOMMENDED FOR PHASE 1)

**Scope**: Implement minimal viable watch mode with essential features

**Changes**:

1. **Event Debouncer** (`src/clx/cli/file_event_handler.py`):
   ```python
   class FileEventHandler(PatternMatchingEventHandler):
       def __init__(self, ..., debounce_delay: float = 0.3):
           self.debounce_delay = debounce_delay
           self.pending_events: Dict[Path, asyncio.Task] = {}

       async def on_file_modified(self, ..., path: Path):
           # Cancel previous pending task for this file
           if path in self.pending_events:
               self.pending_events[path].cancel()

           # Schedule debounced processing
           async def debounced_process():
               await asyncio.sleep(self.debounce_delay)
               del self.pending_events[path]
               await self._do_process(path)

           task = asyncio.create_task(debounced_process())
           self.pending_events[path] = task
   ```

2. **Fast Watch Mode** (`src/clx/cli/main.py`):
   ```python
   @click.option("--watch-mode",
                 type=click.Choice(["fast", "normal"]),
                 default="fast",
                 help="Watch mode processing: fast (notebooks only) or normal (all formats)")

   def build(..., watch, watch_mode):
       if watch:
           # Override skip_html for all topics in watch mode
           if watch_mode == "fast":
               for section in course.sections:
                   for topic in section.topics:
                       topic.skip_html = True
   ```

3. **Updated output_specs** (already supports `skip_html`):
   - No changes needed, `skip_html=True` reduces 10 ops → 6 ops
   - Skips HTML generation (the slowest part)

**Benefits**:
- ✅ Solves duplicate job submission
- ✅ Provides fast feedback (2-5s instead of 10-30s)
- ✅ Minimal code changes
- ✅ No schema changes required

**Limitations**:
- ❌ Doesn't cancel already-submitted jobs
- ❌ No state consistency guarantees
- ❌ No progress feedback

**Effort**: **1-2 days**

---

### Solution 2: Full Implementation - Cancellation + Consistency

**Scope**: Complete watch mode with job cancellation and state management

**Changes**:

1. **Job Cancellation** (`src/clx/infrastructure/database/schema.py`):
   ```sql
   -- Add 'cancelled' status
   status TEXT NOT NULL CHECK(status IN (
       'pending', 'processing', 'completed', 'failed', 'cancelled'
   ))

   -- Add cancellation tracking
   cancelled_at TIMESTAMP,
   cancelled_by TEXT,  -- correlation_id of superseding job
   ```

2. **Cancel Method** (`src/clx/infrastructure/database/job_queue.py`):
   ```python
   def cancel_jobs_for_file(self, input_file: str) -> List[int]:
       """Cancel all pending/processing jobs for a file."""
       conn = self._get_conn()

       # Mark pending jobs as cancelled
       conn.execute("""
           UPDATE jobs
           SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
           WHERE input_file = ? AND status = 'pending'
       """, (input_file,))

       # Mark processing jobs as cancelled (workers must cooperate)
       conn.execute("""
           UPDATE jobs
           SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
           WHERE input_file = ? AND status = 'processing'
       """, (input_file,))

       # Return cancelled job IDs
       ...
   ```

3. **Worker Cooperative Cancellation** (`src/clx/workers/notebook/worker.py`):
   ```python
   async def process_job(self, job: Job):
       # Check if cancelled before starting
       if self.job_queue.is_job_cancelled(job.id):
           logger.info(f"Job {job.id} was cancelled, skipping")
           return

       # Check periodically during long-running operations
       for step in processing_steps:
           if self.job_queue.is_job_cancelled(job.id):
               logger.info(f"Job {job.id} cancelled mid-processing")
               raise JobCancelledException(job.id)
           await execute_step(step)
   ```

4. **Atomic Output Writes** (`src/clx/core/operations/process_notebook.py`):
   ```python
   async def execute(self, backend: Backend):
       # Write to temp file
       temp_output = output_file.with_suffix(output_file.suffix + ".tmp")

       result = await backend.execute_and_wait(...)

       # Atomic move on success
       temp_output.replace(output_file)
   ```

5. **Debounced Event Handler with Cancellation**:
   ```python
   async def on_file_modified(self, ..., path: Path):
       # Cancel existing jobs for this file
       cancelled_ids = backend.job_queue.cancel_jobs_for_file(str(path))
       if cancelled_ids:
           logger.info(f"Cancelled {len(cancelled_ids)} obsolete jobs")

       # Debounce as in Solution 1
       ...
   ```

**Benefits**:
- ✅ No redundant work (cancelled jobs don't run)
- ✅ Consistent output state
- ✅ Faster feedback (don't wait for obsolete jobs)
- ✅ Resource efficient (workers skip cancelled jobs)

**Limitations**:
- ⚠️ Schema migration required
- ⚠️ Worker code changes (all 3 workers)
- ⚠️ More complex error handling

**Effort**: **4-6 days**

---

### Solution 3: Advanced - Batching + Feedback

**Scope**: Intelligent batching and user feedback

**Changes**:

1. **Batch Detector** (`src/clx/cli/file_event_handler.py`):
   ```python
   class BatchingEventHandler(FileEventHandler):
       def __init__(self, ..., batch_window: float = 1.0):
           self.batch_window = batch_window
           self.batch_files: Set[Path] = set()
           self.batch_timer: Optional[asyncio.Task] = None

       async def on_file_modified(self, ..., path: Path):
           self.batch_files.add(path)

           # Reset batch timer
           if self.batch_timer:
               self.batch_timer.cancel()

           async def process_batch():
               await asyncio.sleep(self.batch_window)
               files = list(self.batch_files)
               self.batch_files.clear()
               logger.info(f"Processing batch of {len(files)} files")
               await self._process_batch(files)

           self.batch_timer = asyncio.create_task(process_batch())
   ```

2. **Progress Tracking**:
   ```python
   async def _process_batch(self, files: List[Path]):
       total_jobs = 0
       correlation_id = generate_correlation_id()

       # Submit all jobs with same correlation_id
       for file in files:
           ops = await course.process_file(backend, file, correlation_id)
           total_jobs += count_operations(ops)

       # Track progress
       logger.info(f"Submitted {total_jobs} jobs [correlation_id: {correlation_id}]")

       # Wait for completion and report
       await backend.wait_for_jobs(correlation_id)
       logger.info(f"✓ Batch complete: {len(files)} files processed")
   ```

3. **Rich Console Output** (optional):
   ```python
   from rich.progress import Progress, SpinnerColumn, TextColumn

   with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
       task = progress.add_task(f"Processing {len(files)} files...", total=None)
       await backend.wait_for_jobs(correlation_id)
       progress.update(task, description=f"✓ Complete: {len(files)} files")
   ```

**Benefits**:
- ✅ Handles multi-file changes efficiently (git checkout)
- ✅ User visibility into processing status
- ✅ Better resource utilization (batch scheduling)

**Limitations**:
- ⚠️ Adds complexity
- ⚠️ Requires careful tuning (batch window size)

**Effort**: **2-3 days** (on top of Solution 2)

---

## Detailed Design: Recommended Implementation

### Phase 1: MVP (Week 1)

#### 1.1 Event Debouncing

**File**: `src/clx/cli/file_event_handler.py`

```python
class FileEventHandler(PatternMatchingEventHandler):
    def __init__(
        self,
        backend,
        course,
        data_dir,
        loop,
        debounce_delay: float = 0.3,  # NEW
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.course = course
        self.backend = backend
        self.data_dir = data_dir
        self.loop = loop
        self.debounce_delay = debounce_delay  # NEW
        self._pending_tasks: Dict[Path, asyncio.Task] = {}  # NEW

    def on_modified(self, event):
        src_path = Path(event.src_path)
        if is_ignored_file(src_path) or is_ignored_dir_for_course(src_path):
            return

        # NEW: Debounced task creation
        self._schedule_debounced_task(
            self.on_file_modified,
            "on_modified",
            src_path
        )

    def _schedule_debounced_task(self, method, event_name: str, *args):
        """Schedule a debounced task for file processing."""
        # Use first arg (path) as key
        key = args[0] if args else None

        # Cancel previous task for this path
        if key in self._pending_tasks:
            prev_task = self._pending_tasks[key]
            if not prev_task.done():
                prev_task.cancel()
                logger.debug(f"Cancelled previous {event_name} task for {key}")

        # Schedule new debounced task
        async def debounced_execution():
            try:
                await asyncio.sleep(self.debounce_delay)
                # Remove from pending before executing
                if key in self._pending_tasks:
                    del self._pending_tasks[key]
                # Execute the actual handler
                await self.handle_event(method, event_name, *args)
            except asyncio.CancelledError:
                logger.debug(f"Debounced task cancelled for {key}")
                raise

        task = self.loop.create_task(debounced_execution())
        if key:
            self._pending_tasks[key] = task

    # Update other event handlers similarly
    def on_created(self, event): ...  # Use _schedule_debounced_task
    def on_deleted(self, event): ...  # Use _schedule_debounced_task
```

**Configuration** (`src/clx/cli/main.py`):

```python
@click.option(
    "--debounce",
    type=float,
    default=0.3,
    help="Debounce delay for file changes in watch mode (seconds)"
)
def build(..., watch, debounce):
    if watch:
        event_handler = FileEventHandler(
            course=course,
            backend=backend,
            data_dir=data_dir,
            loop=loop,
            patterns=["*"],
            debounce_delay=debounce,  # NEW
        )
```

#### 1.2 Fast Watch Mode

**File**: `src/clx/cli/main.py`

```python
@click.option(
    "--watch-mode",
    type=click.Choice(["fast", "normal", "minimal"]),
    default="fast",
    help="Watch mode processing speed: "
         "fast (notebooks only, no HTML), "
         "normal (all formats), "
         "minimal (notebooks for default language only)"
)
def build(..., watch, watch_mode):
    """Build/process a course from a specification file."""

    # ... existing setup code ...

    # NEW: Configure watch mode behavior
    if watch:
        logger.info(f"Watch mode enabled with {watch_mode} processing")

        if watch_mode in ("fast", "minimal"):
            # Skip HTML generation for all topics
            for section in course.sections:
                for topic in section.topics:
                    topic.skip_html = True
            logger.info("HTML generation disabled in watch mode")

        if watch_mode == "minimal":
            # Only process default language (e.g., 'en')
            # This would require changes to output_specs()
            # For MVP, just document this as future enhancement
            logger.warning("Minimal mode not yet implemented, using fast mode")
```

#### 1.3 Testing

**Test file**: `tests/cli/test_watch_mode_debounce.py`

```python
import asyncio
import pytest
from pathlib import Path
from clx.cli.file_event_handler import FileEventHandler

@pytest.mark.asyncio
async def test_debounce_multiple_events(tmp_path, mock_course, mock_backend):
    """Test that multiple rapid events are coalesced."""
    loop = asyncio.get_running_loop()

    handler = FileEventHandler(
        backend=mock_backend,
        course=mock_course,
        data_dir=tmp_path,
        loop=loop,
        debounce_delay=0.1,
        patterns=["*"]
    )

    test_file = tmp_path / "test.ipynb"
    test_file.write_text("test")

    # Simulate rapid file changes
    for _ in range(5):
        handler.on_modified(FileModifiedEvent(str(test_file)))
        await asyncio.sleep(0.02)  # 20ms between events

    # Wait for debounce to complete
    await asyncio.sleep(0.2)

    # Should only process once
    assert mock_backend.process_file_call_count == 1
```

**Effort**: **1-2 days**

---

### Phase 2: Job Cancellation (Week 2)

#### 2.1 Schema Changes

**File**: `src/clx/infrastructure/database/schema.py`

```python
DATABASE_VERSION = 4  # Increment version

# Update jobs table CHECK constraint
status TEXT NOT NULL CHECK(status IN (
    'pending', 'processing', 'completed', 'failed', 'cancelled'
)),

# Add cancellation tracking
cancelled_at TIMESTAMP,
cancelled_by TEXT,  -- correlation_id of superseding job
```

**Migration** (`migrate_database()`):

```python
if from_version < 4 <= to_version:
    conn.executescript("""
        -- Add cancelled_at and cancelled_by columns
        ALTER TABLE jobs ADD COLUMN cancelled_at TIMESTAMP;
        ALTER TABLE jobs ADD COLUMN cancelled_by TEXT;

        INSERT OR IGNORE INTO schema_version (version) VALUES (4);
    """)
    conn.commit()

    # Note: Can't modify CHECK constraint in SQLite, so document that
    # 'cancelled' is now a valid status (enforced at app level)
```

#### 2.2 Job Queue Methods

**File**: `src/clx/infrastructure/database/job_queue.py`

```python
def cancel_jobs_for_file(
    self,
    input_file: str,
    cancelled_by: Optional[str] = None
) -> List[int]:
    """Cancel all pending/processing jobs for an input file.

    Args:
        input_file: Path to input file
        cancelled_by: Correlation ID of superseding job

    Returns:
        List of cancelled job IDs
    """
    conn = self._get_conn()

    # Find jobs to cancel
    cursor = conn.execute(
        """
        SELECT id FROM jobs
        WHERE input_file = ?
        AND status IN ('pending', 'processing')
        ORDER BY id
        """,
        (input_file,)
    )
    job_ids = [row[0] for row in cursor.fetchall()]

    if not job_ids:
        return []

    # Mark as cancelled
    conn.execute(
        """
        UPDATE jobs
        SET status = 'cancelled',
            cancelled_at = CURRENT_TIMESTAMP,
            cancelled_by = ?
        WHERE id IN ({})
        """.format(','.join('?' * len(job_ids))),
        (cancelled_by, *job_ids)
    )

    logger.info(
        f"Cancelled {len(job_ids)} jobs for {input_file}"
        + (f" [superseded_by: {cancelled_by}]" if cancelled_by else "")
    )

    return job_ids

def is_job_cancelled(self, job_id: int) -> bool:
    """Check if a job has been cancelled."""
    conn = self._get_conn()
    cursor = conn.execute(
        "SELECT status FROM jobs WHERE id = ?",
        (job_id,)
    )
    row = cursor.fetchone()
    return row and row[0] == 'cancelled'
```

#### 2.3 Backend Integration

**File**: `src/clx/infrastructure/backends/sqlite_backend.py`

```python
async def execute_operation(self, operation: Operation, payload: Payload) -> None:
    # ... existing cache checks ...

    # NEW: Cancel existing jobs for this input file before submitting new one
    if self.job_queue:
        cancelled_ids = self.job_queue.cancel_jobs_for_file(
            str(payload.input_file),
            cancelled_by=correlation_id
        )
        if cancelled_ids:
            logger.info(
                f"Cancelled {len(cancelled_ids)} obsolete jobs "
                f"for {payload.input_file}"
            )

    # ... submit new job as before ...
```

#### 2.4 Worker Cooperative Cancellation

**File**: `src/clx/workers/notebook/worker.py` (v0.4.0: workers now in unified package)

```python
class NotebookWorker(WorkerBase):
    async def process_job(self, job: Job):
        # Check if cancelled before starting
        if self.job_queue.is_job_cancelled(job.id):
            logger.info(f"Job {job.id} was cancelled before processing, skipping")
            return

        try:
            # ... process notebook ...

            # For long-running operations, check cancellation periodically
            # (This would require refactoring notebook_processor.py)

        except Exception as e:
            # Check if cancellation caused the error
            if self.job_queue.is_job_cancelled(job.id):
                logger.info(f"Job {job.id} cancelled during processing")
                # Don't mark as failed, leave as cancelled
                return
            raise
```

**Similar changes** for PlantUML and DrawIO workers.

**Effort**: **3-4 days**

---

### Phase 3: Polish & Feedback (Week 3)

#### 3.1 Progress Logging

**File**: `src/clx/cli/file_event_handler.py`

```python
async def on_file_modified(self, course: Course, backend: Backend, path: Path):
    logger.info(f"⟳ File modified: {path.name}")

    if course.find_course_file(path):
        logger.info(f"⚙ Processing {path.name}...")
        await course.process_file(backend, path)
        logger.info(f"✓ Submitted jobs for {path.name}")
    else:
        logger.debug(f"File not in course: {path}")
```

#### 3.2 Completion Notifications

**Option A: Polling-based** (simpler):

```python
class FileEventHandler:
    def __init__(self, ..., enable_completion_logging: bool = True):
        self.enable_completion_logging = enable_completion_logging
        self._active_correlations: Set[str] = set()
        self._completion_task: Optional[asyncio.Task] = None

    async def on_file_modified(self, ..., path: Path):
        correlation_id = generate_correlation_id()
        self._active_correlations.add(correlation_id)

        await course.process_file(backend, path, correlation_id)

        # Start completion monitoring
        if self.enable_completion_logging and not self._completion_task:
            self._completion_task = asyncio.create_task(
                self._monitor_completions()
            )

    async def _monitor_completions(self):
        """Monitor job completions and log when done."""
        while True:
            await asyncio.sleep(1)

            for correlation_id in list(self._active_correlations):
                # Check if all jobs for this correlation are complete
                if self._all_jobs_complete(correlation_id):
                    logger.info(f"✓ Processing complete for {correlation_id}")
                    self._active_correlations.remove(correlation_id)
```

**Option B: Event-based** (more complex, better UX):
- Workers emit completion events
- Backend aggregates events by correlation_id
- Callback when all jobs complete

**Effort**: **1-2 days**

---

## Recommended Phased Approach

### Timeline & Milestones

| Phase | Duration | Features | Status |
|-------|----------|----------|--------|
| **Phase 1: MVP** | 2 days | Debouncing + Fast mode | 🟡 Recommended |
| **Phase 2: Refinement** | 4 days | Job cancellation + State consistency | 🟢 High value |
| **Phase 3: Polish** | 2 days | Progress feedback + Batching | 🔵 Nice-to-have |

### Decision Tree

```
Is watch mode critical for your workflow?
├─ YES → Start with Phase 1 immediately
│   ├─ Do you edit files rapidly? → Phase 2 essential
│   └─ Do you git checkout often? → Phase 3 useful
└─ NO → Defer until user demand increases
```

### Risk Assessment

**Low Risk** (Phase 1):
- ✅ No schema changes
- ✅ Backwards compatible
- ✅ Easy to revert
- ✅ Minimal worker changes

**Medium Risk** (Phase 2):
- ⚠️ Schema migration required
- ⚠️ Worker changes (all 3 services)
- ⚠️ More complex error handling
- ✅ Can be tested incrementally

**Low Risk** (Phase 3):
- ✅ Optional features
- ✅ No breaking changes
- ⚠️ May need tuning (batch timings)

---

## Alternative Approaches Considered

### Alternative 1: Restart Workers on File Change

**Idea**: Kill workers, restart with fresh state

**Rejected because**:
- ❌ Slow (startup overhead)
- ❌ Wasteful (workers are stateless anyway)
- ❌ Doesn't solve debouncing problem

### Alternative 2: Single-Threaded Sequential Processing

**Idea**: Process files one at a time in watch mode

**Rejected because**:
- ❌ Slow (no parallelism)
- ❌ Defeats purpose of worker architecture
- ❌ Doesn't scale to multi-file changes

### Alternative 3: In-Memory Queue (No Database)

**Idea**: Use in-memory queue for watch mode, skip SQLite

**Rejected because**:
- ❌ Loses persistence (crash = data loss)
- ❌ Duplicates infrastructure
- ❌ Harder to debug (no audit trail)
- ✅ Would be slightly faster
- **Verdict**: Not worth the complexity

### Alternative 4: File Watcher Debouncing Library

**Idea**: Use `watchdog`'s built-in debouncing (if available)

**Investigated**:
- Watchdog has `PatternMatchingEventHandler` but no built-in debouncing
- External library: `watchdog_debounce` (unmaintained)
- **Verdict**: Custom implementation is cleaner

---

## Open Questions & Future Considerations

### Q1: Should watch mode support incremental notebook execution?

**Context**: Currently, notebooks are executed fresh each time. Could we cache execution state?

**Trade-offs**:
- ✅ Pro: Faster feedback (don't re-run entire notebook)
- ❌ Con: Complex state management (kernel state)
- ❌ Con: May produce incorrect results (stale state)

**Recommendation**: Not for MVP. Consider in future if demand is high.

### Q2: How to handle section/course-level changes?

**Context**: Changing `course.yaml` affects entire course structure

**Current behavior**: Not detected (YAML not in watched directories)

**Recommendation**:
- Phase 1: Document that course.yaml changes require restart
- Phase 2: Watch course.yaml, trigger full rebuild on change

### Q3: Should we support watch mode for multiple courses?

**Context**: User might have multiple course directories

**Recommendation**: Not for MVP. Single course is sufficient.

### Q4: How to handle output directory changes?

**Context**: User might change `--output-dir` during watch

**Recommendation**: Require restart if output directory changes

### Q5: Integration with IDEs (VSCode, PyCharm)?

**Context**: Could provide IDE plugins for better UX

**Future enhancement**:
- VSCode extension: Show build status in status bar
- Language Server Protocol: Real-time error highlighting
- **Verdict**: Out of scope for now

---

## Testing Strategy

### Unit Tests

1. **Debouncing**:
   - Multiple rapid events → Single processing
   - Events separated by > debounce delay → Multiple processing
   - Cancellation of pending tasks works correctly

2. **Job Cancellation**:
   - `cancel_jobs_for_file()` marks jobs as cancelled
   - `is_job_cancelled()` returns correct status
   - Workers skip cancelled jobs

3. **Fast Mode**:
   - `skip_html=True` reduces operation count
   - Output directory structure correct

### Integration Tests

1. **Watch Mode Flow**:
   - Start watch mode
   - Modify file
   - Verify correct jobs submitted
   - Verify workers process jobs
   - Verify outputs created

2. **Debounce Integration**:
   - Simulate text editor (multiple rapid saves)
   - Verify only one batch of jobs submitted
   - Verify correct final output

3. **Cancellation Integration**:
   - Submit jobs
   - Modify file again before completion
   - Verify old jobs cancelled
   - Verify new jobs processed

### E2E Tests

1. **Realistic Workflow**:
   - User edits notebook
   - Auto-save triggers multiple events
   - Watch mode processes once (debounced)
   - Output appears within 2-5 seconds

2. **Git Checkout Scenario**:
   - Multiple files change simultaneously
   - Batch processing handles all files
   - Correct outputs for all files

3. **Error Recovery**:
   - Introduce syntax error in notebook
   - Watch mode logs error, continues watching
   - Fix error, verify reprocessing works

---

## Documentation Requirements

### User Guide Updates

**File**: `docs/user-guide/quick-start.md`

```markdown
### Watch Mode

CLX can automatically rebuild your course when files change:

    clx build course.yaml --watch

This is useful during development for quick feedback.

#### Watch Mode Options

- `--watch-mode=fast` (default): Process notebooks only, skip HTML generation
- `--watch-mode=normal`: Process all formats (slower)
- `--debounce=0.3`: Adjust debounce delay (seconds)

#### Tips

- Use fast mode for rapid iteration
- Use normal mode to see final HTML output
- Watch mode skips expensive operations for speed
- Press Ctrl+C to stop watch mode
```

### Developer Guide Updates

**File**: `docs/developer-guide/architecture.md`

Add section:

```markdown
## Watch Mode Architecture

Watch mode uses `watchdog` to monitor file changes and trigger incremental rebuilds.

### Event Flow

1. File system change detected by `watchdog.Observer`
2. `FileEventHandler` receives event
3. Event is debounced (default 300ms)
4. Jobs submitted to SQLite queue
5. Persistent workers process jobs
6. Outputs written atomically

### Debouncing Strategy

Multiple rapid file changes are coalesced into a single processing event:

- Text editors trigger multiple saves per user action
- Debounce window: 300ms (configurable)
- Per-file debouncing (changing file A doesn't debounce file B)

### Job Cancellation

When a file changes again before processing completes:

1. Pending jobs for that file are marked `cancelled`
2. Processing jobs receive cancellation signal
3. Workers cooperatively abort (check `is_job_cancelled()`)
4. New jobs submitted with fresh content
```

---

## Success Criteria

### Phase 1 Success Metrics

- ✅ Text editor auto-save triggers single processing event (not 5-10)
- ✅ Watch mode processes notebooks in < 3 seconds (fast mode)
- ✅ Debounce delay is configurable
- ✅ No duplicate job submissions
- ✅ Tests pass with 100% coverage

### Phase 2 Success Metrics

- ✅ Obsolete jobs are cancelled within 1 second
- ✅ Workers skip cancelled jobs
- ✅ No partial/stale outputs left behind
- ✅ Schema migration works correctly
- ✅ Performance overhead < 5%

### Phase 3 Success Metrics

- ✅ Users receive completion notifications
- ✅ Batch processing handles multi-file changes
- ✅ Log output is clear and actionable
- ✅ Error recovery works (watch mode continues after failures)

---

## Conclusion & Recommendation

**Current State**: Watch mode infrastructure exists but lacks critical features for usability.

**Recommended Action**: **Implement Phase 1 (MVP)** immediately

**Rationale**:
1. **High impact, low risk**: Solves 80% of usability issues with minimal changes
2. **No breaking changes**: Backwards compatible, easy to test
3. **Quick wins**: Debouncing + fast mode provide immediate value
4. **Foundation for future**: Enables Phase 2/3 if needed

**Next Steps**:
1. Review this analysis with stakeholders
2. Get approval for Phase 1 implementation
3. Create feature branch: `feature/watch-mode-mvp`
4. Implement debouncing (1 day)
5. Implement fast mode (0.5 days)
6. Write tests (0.5 days)
7. Update documentation (0.5 days)
8. Merge and deploy

**Estimated Total Effort**: **2-3 days** for Phase 1

---

## Appendix A: Code Locations Reference

### Key Files to Modify (Phase 1)

| File | Purpose | Changes |
|------|---------|---------|
| `src/clx/cli/file_event_handler.py` | Event handling | Add debouncing logic |
| `src/clx/cli/main.py` | CLI entry point | Add `--watch-mode` flag |
| `tests/cli/test_watch_mode.py` | Tests | Add debounce tests |
| `docs/user-guide/quick-start.md` | Documentation | Document watch mode usage |

### Key Files to Modify (Phase 2)

| File | Purpose | Changes |
|------|---------|---------|
| `src/clx/infrastructure/database/schema.py` | Database schema | Add cancelled status |
| `src/clx/infrastructure/database/job_queue.py` | Job queue | Add cancellation methods |
| `src/clx/infrastructure/backends/sqlite_backend.py` | Backend | Cancel jobs before submit |
| `src/clx/workers/*/worker.py` | Workers | Check for cancellation |

### Key Files to Modify (Phase 3)

| File | Purpose | Changes |
|------|---------|---------|
| `src/clx/cli/file_event_handler.py` | Event handling | Add batching logic |
| `src/clx/infrastructure/backends/sqlite_backend.py` | Backend | Add completion tracking |
| `src/clx/cli/main.py` | CLI | Enhanced logging |

---

## Appendix B: Configuration Options

### Proposed CLI Options

```bash
clx build course.yaml \
    --watch \                          # Enable watch mode
    --watch-mode=fast \                # Processing speed (fast/normal)
    --debounce=0.3 \                   # Debounce delay in seconds
    --watch-batch-window=1.0           # (Phase 3) Batch window
```

### Proposed Environment Variables

```bash
# Watch mode configuration
CLX_WATCH_DEBOUNCE=0.3           # Debounce delay
CLX_WATCH_MODE=fast              # Default watch mode
CLX_WATCH_BATCH_WINDOW=1.0       # (Phase 3) Batch window
CLX_WATCH_ENABLE_NOTIFICATIONS=1 # (Phase 3) Desktop notifications
```

---

**Document Version**: 1.1 (Updated for v0.4.0)
**Last Updated**: 2025-11-19
**Status**: Ready for review
