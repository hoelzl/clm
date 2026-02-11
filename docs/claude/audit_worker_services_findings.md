# CLM Worker Services Code Quality Audit Report

## Executive Summary

The three worker services (notebook-processor, plantuml-converter, drawio-converter) exhibit **massive code duplication**, **inconsistent error handling patterns**, and **several architectural issues** that impact maintainability and reliability.

**Key Metrics:**
- ~270-280 lines per worker file with 60-70% duplication
- 103 total logging statements across services (inconsistent patterns)
- 3 separate implementations of nearly identical event loop management
- 3 separate implementations of worker registration with retry logic
- 0 test files for worker services

---

## 1. CRITICAL: Code Duplication in Worker Classes

### Issue Summary
The three worker classes are nearly identical, with 60-70% code overlap. This violates DRY principle and creates maintenance burden.

### Files Affected
- `/home/user/clm/services/notebook-processor/src/nb/notebook_worker.py` (268 lines)
- `/home/user/clm/services/plantuml-converter/src/plantuml_converter/plantuml_worker.py` (250 lines)
- `/home/user/clm/services/drawio-converter/src/drawio_converter/drawio_worker.py` (273 lines)

### Duplicated Methods/Functions

#### 1.1 Event Loop Management
**Exact duplication across all 3 workers** - `_get_or_create_loop()` method:

```python
# IDENTICAL in all 3 workers (lines 48-65 / 54-65 range)
def _get_or_create_loop(self):
    """Get or create the event loop for this worker."""
    if self._loop is None or self._loop.is_closed():
        try:
            self._loop = asyncio.get_running_loop()
            logger.debug(f"Worker {self.worker_id}: Using existing event loop")
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            logger.debug(f"Worker {self.worker_id}: Created new event loop")
    return self._loop
```

#### 1.2 Job Processing Structure
`process_job()` method implementation is identical across all workers:

```python
# Same pattern in all 3 (lines 67-88 range)
def process_job(self, job: Job):
    loop = self._get_or_create_loop()
    try:
        loop.run_until_complete(self._process_job_async(job))
    except Exception as e:
        logger.error(f"Worker {self.worker_id} error in event loop for job {job.id}: {e}", exc_info=True)
        raise
```

#### 1.3 Cleanup Method
`cleanup()` method is **byte-for-byte identical** in all 3 workers (lines 168-186 / 172-190):

```python
# IDENTICAL duplication
def cleanup(self):
    if self._loop is not None and not self._loop.is_closed():
        logger.debug(f"Worker {self.worker_id}: Closing event loop")
        try:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception as e:
            logger.warning(f"Worker {self.worker_id}: Error during loop cleanup: {e}")
        finally:
            self._loop.close()
            self._loop = None
```

#### 1.4 Main Entry Point
`main()` function is ~95% identical across all workers (lines 238-268):

```python
# Same structure in all 3
def main():
    logger.info("Starting [notebook/plantuml/drawio] worker in SQLite mode")
    if not DB_PATH.exists():
        logger.info(f"Initializing database at {DB_PATH}")
        init_database(DB_PATH)
    worker_id = register_worker(DB_PATH)
    worker = [NotebookWorker/PlantUmlWorker/DrawioWorker](worker_id, DB_PATH)
    try:
        worker.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
        worker.stop()
    except Exception as e:
        logger.error(f"Worker crashed: {e}", exc_info=True)
        raise
    finally:
        worker.cleanup()
        logger.info("Worker cleanup completed")
```

### Impact
- Bugs fixed in one worker won't be fixed in others
- Changes to event loop handling require 3 separate modifications
- Increased testing burden (3x the code to test)
- Maintenance nightmare as codebase evolves

### Recommended Fix
Move all common code to base `Worker` class in `clm.infrastructure.workers.worker_base`:
- Move `_get_or_create_loop()` and `_loop` attribute to base class
- Move `process_job()` wrapper logic to base class (make `_process_job_async()` the abstract method)
- Move `cleanup()` method to base class
- Move `main()` function to a factory/launcher module
- Keep only service-specific `_process_job_async()` implementation in subclasses

---

## 2. HIGH: Inconsistent Worker Registration with Retry Logic

### Issue Summary
Worker registration has duplicated retry logic with **subtle inconsistencies** between implementations.

### Files Affected
- Notebook worker: `register_worker()` (lines 188-235)
- PlantUML worker: `register_worker()` (lines 189-217) - **NO retry logic!**
- DrawIO worker: `register_worker()` (lines 193-240)

### Code Comparison

**Notebook worker - HAS RETRY:**
```python
def register_worker(db_path: Path) -> int:
    # Retry logic with exponential backoff
    max_retries = 5
    retry_delay = 0.5
    for attempt in range(max_retries):
        try:
            conn = queue._get_conn()
            cursor = conn.execute(...)
            worker_id = cursor.lastrowid
            logger.info(f"Registered worker {worker_id}")
            return worker_id
        except sqlite3.OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Failed to register worker (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error(f"Failed to register worker after {max_retries} attempts: {e}")
                raise
```

**PlantUML worker - NO RETRY:**
```python
def register_worker(db_path: Path) -> int:
    worker_identifier = os.getenv('WORKER_ID') or os.getenv('HOSTNAME', 'unknown')
    queue = JobQueue(db_path)
    conn = queue._get_conn()  # <-- Will fail immediately on DB lock!
    cursor = conn.execute(...)
    worker_id = cursor.lastrowid
    logger.info(f"Registered worker {worker_id} (identifier: {worker_identifier})")
    return worker_id
```

**DrawIO worker - HAS RETRY (duplicated from notebook):**
```python
# Same retry logic as notebook worker (lines 209-240)
```

### Issue Details
1. **PlantUML worker lacks retry logic** - will fail immediately if DB is locked during startup
2. **Missing imports** - PlantUML worker missing `import sqlite3` and `import time` (but doesn't use them)
3. **Duplicated exponential backoff** - Same logic implemented in both notebook and drawio workers
4. **Inconsistent error handling** - Notebook catches `sqlite3.OperationalError`, but this is overly specific

### Recommended Fix
- Extract registration into a utility function in `clm.infrastructure.workers` module
- Create a reusable retry wrapper with configurable parameters
- Use for all three workers with consistent behavior

---

## 3. HIGH: Subprocess Error Handling and Retry Pattern Issues

### File
`/home/user/clm/src/clm/infrastructure/services/subprocess_tools.py`

### Issue 1: Overly Broad Exception Handling
```python
async def run_subprocess(cmd, correlation_id):
    while True:
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), CONVERSION_TIMEOUT * 2 ** (current_iteration - 1)
            )
            return process, stdout, stderr
        except Exception as e:  # <-- TOO BROAD!
            logger.error(f"{correlation_id}:Error while communicating with subprocess:{e}")
            await try_to_terminate_process(correlation_id, process)
            if current_iteration >= NUM_RETRIES:
                e.add_note(...)  # <-- This modifies system exception!
                raise
```

**Problems:**
- Catches all exceptions (KeyboardInterrupt, SystemExit, etc.)
- Retries on ALL exceptions including those that shouldn't be retried (e.g., invalid command)
- Modifies system exception with `add_note()` - defensive/unclear intent
- No distinction between retriable vs non-retriable errors

### Issue 2: Hardcoded Configuration
```python
CONVERSION_TIMEOUT = 60
NUM_RETRIES = 3

# These are used by all three services, but:
# - PlantUML needs ~30s, DrawIO needs ~45s, Notebook needs variable time
# - No per-service customization possible
```

### Issue 3: Timeout Calculation Logic
```python
CONVERSION_TIMEOUT * 2 ** (current_iteration - 1)  # Exponential backoff
# Iteration 1: 60s
# Iteration 2: 120s
# Iteration 3: 240s
# Total worst case: 420s = 7 minutes per job!
```

This is reasonable but not documented anywhere.

### Recommended Fix
- Catch specific exceptions (asyncio.TimeoutError, subprocess errors)
- Allow per-service timeout configuration via environment variables
- Document timeout behavior and retry strategy
- Use tenacity library (already a dependency in drawio-converter) for consistent retry logic

---

## 4. HIGH: Inconsistent Async/Await Patterns

### Issue Summary
Services use async/await inconsistently, with mixed blocking and non-blocking I/O.

### File Comparisons

#### Notebook Worker (Process-Sync, I/O-Sync)
```python
async def _process_job_async(self, job: Job):
    # Uses synchronous file I/O
    with open(input_path, 'r', encoding='utf-8') as f:
        notebook_text = f.read()  # <-- BLOCKING!
```

#### DrawIO Worker (Process-Async, I/O-Async)
```python
async def _process_job_async(self, job: Job):
    # Uses aiofiles for async I/O
    async with aiofiles.open(tmp_input, "w", encoding="utf-8") as f:
        await f.write(drawio_content)  # <-- NON-BLOCKING
```

#### PlantUML Worker (Process-Sync, I/O-Sync)
```python
async def _process_job_async(self, job: Job):
    with open(input_path, 'r', encoding='utf-8') as f:
        plantuml_content = f.read()  # <-- BLOCKING!
```

**Issue:** 
- DrawIO uses aiofiles (good!)
- PlantUML and Notebook use sync I/O in async functions (defeats async benefit)
- DrawIO also creates empty file then writes to it (wasteful):
  ```python
  async with aiofiles.open(tmp_output, "wb") as f:
      await f.write(b"")  # Create empty file
  # ... later ...
  async with aiofiles.open(tmp_output, "rb") as f:
      result_bytes = await f.read()  # Read it back
  ```

### Recommended Fix
- Use aiofiles consistently in all workers
- Use `loop.run_in_executor()` for subprocess operations (already done in notebook processor)
- Don't create empty files, just write directly

---

## 5. MEDIUM: Inconsistent Configuration and Environment Variables

### Issue 1: Inconsistent Defaults
```python
# Notebook worker
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Notebook processor (different default!)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# Both PlantUML and DrawIO
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
```

### Issue 2: Mixed getenv() and environ.get()
```python
# Inconsistent API usage
worker_identifier = os.getenv('WORKER_ID') or os.getenv('HOSTNAME', 'unknown')  # getenv
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))  # environ.get
```

Both work but mixing styles is unprofessional.

### Issue 3: Configuration at Module Load Time
```python
# These are evaluated once at import time, can't be changed per-request:
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
DB_PATH = Path(os.environ.get("DB_PATH", "/db/jobs.db"))
JINJA_TEMPLATES_PREFIX = os.environ.get("JINJA_TEMPLATES_PATH", "templates")  # typo: PREFIX vs PATH
DRAWIO_EXECUTABLE = os.environ.get("DRAWIO_EXECUTABLE", "drawio")
PLANTUML_JAR = os.environ.get("PLANTUML_JAR")  # Can raise FileNotFoundError at import time!
```

**Issue:** PLANTUML_JAR validation happens at module load:
```python
if _plantuml_jar_from_env:
    PLANTUML_JAR = _plantuml_jar_from_env
    if not Path(PLANTUML_JAR).exists():
        raise FileNotFoundError(...)  # <-- Fails at import time!
```

This is **defensive code masking issues** - fails with unclear error if env var is wrong.

### Issue 4: Hardcoded Configuration Values
```python
# PlantUML converter
cmd = [
    "java",
    "-DPLANTUML_LIMIT_SIZE=8192",  # <-- Hardcoded, not configurable
    "-jar", PLANTUML_JAR,
    "-tpng",
    "-Sdpi=200",  # <-- Hardcoded DPI
    "-o", str(input_file.parent),
    str(input_file),
]

# DrawIO converter  
cmd.extend(["--border", "20"])  # <-- Hardcoded border
if output_format == "png":
    cmd.extend(["--scale", "3"])  # <-- Hardcoded scale
```

### Recommended Fix
- Use environment variables with consistent API (choose either getenv or environ.get)
- Validate configuration at startup, not import time
- Move hardcoded values to constants at top of module with clear comments
- Support per-format configuration (different DPI for different purposes)

---

## 6. MEDIUM: Missing Empty Output Validation

### Issue Summary
Output validation is **inconsistent** across workers.

### Files Affected
- Notebook worker: `_process_job_async()` - **NO validation** ❌
- PlantUML worker: `_process_job_async()` (lines 142-143) - validates ✓
- DrawIO worker: `_process_job_async()` (lines 146-147) - validates ✓

### PlantUML/DrawIO Validation
```python
if len(result_bytes) == 0:
    raise ValueError("Conversion produced empty result")
```

### Notebook Missing Validation
```python
# Just writes whatever was produced without checking
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(result)  # <-- No validation of result contents
```

**Risk:** Silently produces empty or corrupted notebooks without error.

### Recommended Fix
- Standardize validation across all workers
- Validate file size (minimum bytes)
- Validate file format (header bytes for binary formats)
- Implement in base Worker class as validation hook

---

## 7. MEDIUM: Inconsistent Cache Metadata

### Issue Summary
Cache results store different metadata across workers - makes cache queries difficult.

### Metadata Stored
```python
# Notebook worker (lines 153-159)
{
    'format': payload_data.get('format', 'notebook'),
    'kind': payload_data.get('kind', 'participant'),
    'prog_lang': payload_data.get('prog_lang', 'python'),
    'language': payload_data.get('language', 'en')
}

# PlantUML worker (lines 156-159)
{
    'format': output_format,
    'size': len(result_bytes)
}

# DrawIO worker (lines 160-163)
{
    'format': output_format,
    'size': len(result_bytes)
}
```

**Issues:**
- Notebook stores format string, PlantUML/DrawIO store actual extension
- Only binary converters store size
- Notebook stores kind/prog_lang which might not match original job
- No consistent schema for querying/filtering

### Recommended Fix
- Define standard cache metadata schema
- Include: `format`, `size`, `content_hash`, `created_at`, `service_type`
- Implement in base Worker or cache module

---

## 8. MEDIUM: Defensive Code and Dead Code Paths

### Issue 1: Unused Imports
```python
# PlantUML worker (line 12)
from base64 import b64decode, b64encode
# These are imported but NEVER used anywhere in the file!
```

### Issue 2: Dead Code Path in Entrypoint
```bash
# drawio-converter/entrypoint.sh (lines 50-56)
if [ "${USE_SQLITE_QUEUE}" = "true" ]; then
    echo "Running in SQLite worker mode"
    exec python -m drawio_converter.drawio_worker
else
    echo "Running in RabbitMQ mode"  
    exec python -m drawio_converter.drawio_converter
fi
```

**Issue:** RabbitMQ mode doesn't exist! The fallback references non-existent code path. This suggests incomplete migration from RabbitMQ to SQLite.

### Issue 3: Defensive Exception Modification
```python
# subprocess_tools.py (line 40-42)
e.add_note(
    f"{correlation_id}:Error while communicating with subprocess:"
    f"iteration {current_iteration}:{e}"
)
```

This modifies system exceptions, making stack traces unclear. It's defensive code that doesn't follow standard Python practices.

### Issue 4: Optional Imports Not Used
```python
# Notebook worker imports but doesn't use:
from typing import Optional  # Never used in class definition!
```

### Recommended Fix
- Remove unused imports (configure ruff/flake8 to catch these)
- Remove dead code paths (RabbitMQ fallback)
- Don't modify system exceptions; instead wrap them
- Use proper exception chaining

---

## 9. MEDIUM: Inconsistent Error Message Formatting

### Issue Summary
Error messages have **inconsistent formatting** making logs hard to parse.

### Examples
```python
# Notebook worker
logger.error(f"Error processing notebook job {job.id}: {e}", exc_info=True)

# PlantUML worker  
logger.error(f"Error processing PlantUML job {job.id}: {e}", exc_info=True)

# DrawIO worker
logger.error(f"Error processing DrawIO job {job.id}: {e}", exc_info=True)

# subprocess_tools
logger.error(f"{correlation_id}:Error while communicating:{e}")

# notebook_processor
logger.error(f"{cid}:Could not process notebook: No contents.")

# plantuml_converter
logger.error(f"{correlation_id}:Error converting {input_file}: {stderr.decode()}")

# drawio_converter  
logger.error(f"{correlation_id}:Error converting {input_path}:{stderr.decode()}")
```

**Issues:**
- Some use colons `:` as separators, others use dashes `-` 
- Spacing inconsistent around colons
- Some include correlation_id at start, others don't
- Some use full paths, others use relative names
- Message capitalization varies

### Recommended Fix
- Define logging template/formatter
- Standardize on: `[WORKER_ID] [JOB_ID] MESSAGE: DETAILS`
- Use structured logging (JSON format) for better parsing

---

## 10. MEDIUM: Signal Handling and Cleanup Race Conditions

### Issue Summary
Signal handlers in base Worker class might not properly clean up if signal arrives during `process_job()`.

### File
`/home/user/clm/src/clm/infrastructure/workers/worker_base.py`

### Code Flow
```python
def _handle_shutdown(self, signum, frame):
    logger.info(f"Worker received shutdown signal")
    self.running = False  # <-- Sets flag

def run(self):
    while self.running:
        # ...
        self.process_job(job)  # <-- Long-running! Signal arrives here
        self._update_status('idle')  # <-- Might not execute
```

**Race Condition:**
If SIGTERM arrives during `process_job()`, the subprocess might not be properly terminated before worker exits.

### Recommended Fix
- Implement timeout enforcement in `process_job()` wrapper
- Add cleanup hook that processes can implement
- Use context managers for resource cleanup
- Consider using asyncio.CancelledError propagation

---

## 11. MEDIUM: Lack of Test Coverage

### Issue Summary
**ZERO test files** for worker services - makes refactoring risky.

### Metrics
```
Tests in CLM core: 221 total
Tests in worker services: 0 total
```

### What Should Be Tested
1. Event loop management (creation, reuse, cleanup)
2. Worker registration (success, retry logic, failure modes)
3. Job processing (success, failure, timeout)
4. Subprocess handling (timeout, retry, process termination)
5. File I/O (permissions, encoding, missing files)
6. Cache operations (correct metadata, error handling)
7. Signal handling (graceful shutdown during job)
8. Concurrency (multiple workers, database contention)

### Recommended Fix
- Create test suite in `/home/user/clm/tests/worker_services/`
- Use pytest fixtures for database, mock subprocess
- Test both success and failure paths
- Test timeout and retry behavior
- Test cleanup procedures

---

## 12. MEDIUM: Environment-Specific Configuration Issues

### Issue Summary
Hardcoded paths and configuration make it difficult to run services in different environments.

### Examples
```python
# DrawIO converter (line 54)
env = os.environ.copy()
env["DISPLAY"] = ":99"  # <-- Hardcoded! What if DISPLAY is different?

# Notebook processor
JINJA_TEMPLATES_PREFIX = os.environ.get("JINJA_TEMPLATES_PREFIX", "templates")
# But used as: loader=PackageLoader("nb", f"{JINJA_TEMPLATES_PREFIX}_{lang}")
# This couples configuration to package structure!

# PlantUML JAR path resolution
_default_jar_paths = [
    "/app/plantuml.jar",  # Docker path
    str(Path(__file__).parents[3] / "plantuml-1.2024.6.jar"),  # Relative path assumption
]
```

**Issues:**
- DISPLAY hardcoded to `:99`
- Jar path resolution relies on package structure (fragile)
- Template path resolution couples configuration to naming conventions

### Recommended Fix
- Make DISPLAY configurable with fallback
- Use absolute paths or well-defined base directories
- Document environment setup clearly

---

## Summary of Issues by Severity

| Severity | Count | Category | Impact |
|----------|-------|----------|---------|
| CRITICAL | 1 | Code Duplication | Maintenance nightmare, inconsistent bug fixes |
| HIGH | 3 | Retry Logic, Error Handling, Async Patterns | Reliability issues, resource leaks |
| MEDIUM | 8 | Configuration, Validation, Testing, etc. | Usability and observability |

### Immediate Actions (Priority Order)

1. **Extract worker base class methods** - Move event loop management, cleanup, main() to base
2. **Standardize worker registration** - Single retry-logic utility function
3. **Fix subprocess error handling** - Use specific exception catching
4. **Add comprehensive test suite** - Start with worker lifecycle tests
5. **Standardize async/await** - Use aiofiles in all workers
6. **Standardize logging** - Define format, use structured logging
7. **Remove dead code** - Remove RabbitMQ fallback paths
8. **Fix unused imports** - Configure linting rules

