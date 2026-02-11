# Integration Test Failure Analysis

## Summary

After setting up the environment and running integration tests, I identified 10 failing tests out of 41 total integration tests (19 passed, 12 skipped).

## Root Causes

### 1. Missing Worker Service Packages (RESOLVED)

**Problem**: PlantUML and DrawIO converter worker services were not installed.

**Error Messages**:
```
/usr/local/bin/python: No module named plantuml_converter
/usr/local/bin/python: No module named drawio_converter
```

**Impact**:
- Workers failed to start and register in the database
- All tests requiring PlantUML or DrawIO processing failed

**Resolution**: ✅ **FIXED**
- Installed `plantuml-converter` service: `pip install -e services/plantuml-converter/`
- Installed `drawio-converter` service: `pip install -e services/drawio-converter/`

### 2. Config Loader Doesn't Support Direct Value Setting

**Problem**: The `load_worker_config()` function only supports CLI flag-style overrides, not direct value setting.

**Current Behavior**:
```python
# config_loader.py only checks:
if cli_overrides.get("no_auto_start"):     # CLI flag style
    config.auto_start = False
if cli_overrides.get("fresh_workers"):      # CLI flag style
    config.reuse_workers = False
```

**What Tests Use**:
```python
# Tests use direct value style:
cli_overrides = {
    "auto_start": False,        # Direct value, not "no_auto_start"
    "auto_stop": True,          # Direct value, not "no_auto_stop"
    "reuse_workers": False,     # Direct value, not "fresh_workers"
}
```

**Affected Tests**:
- `test_auto_start_behavior` - Expects `should_start_workers()` to return False
- Multiple e2e tests using `auto_start`, `auto_stop`, `reuse_workers` directly

**Proposed Fix**:
Enhance `config_loader.py` to support both CLI flag style and direct value style:

```python
# Support both "no_auto_start" (CLI flag) and "auto_start" (direct value)
if cli_overrides.get("no_auto_start"):
    config.auto_start = False
    logger.info("CLI override: auto_start = False")
elif "auto_start" in cli_overrides:
    config.auto_start = cli_overrides["auto_start"]
    logger.info(f"CLI override: auto_start = {config.auto_start}")

# Support both "no_auto_stop" (CLI flag) and "auto_stop" (direct value)
if cli_overrides.get("no_auto_stop"):
    config.auto_stop = False
    logger.info("CLI override: auto_stop = False")
elif "auto_stop" in cli_overrides:
    config.auto_stop = cli_overrides["auto_stop"]
    logger.info(f"CLI override: auto_stop = {config.auto_stop}")

# Support both "fresh_workers" (CLI flag) and "reuse_workers" (direct value)
if cli_overrides.get("fresh_workers"):
    config.reuse_workers = False
    logger.info("CLI override: reuse_workers = False")
elif "reuse_workers" in cli_overrides:
    config.reuse_workers = cli_overrides["reuse_workers"]
    logger.info(f"CLI override: reuse_workers = {config.reuse_workers}")
```

### 3. Config Loader Doesn't Support Both Naming Conventions

**Problem**: Tests use both `notebook_count` and `notebook_workers` naming, but config_loader only supports `_workers` suffix.

**Current Behavior**:
```python
# config_loader.py line 68:
cli_key = f"{worker_type}_workers"  # Only checks notebook_workers, plantuml_workers, drawio_workers
```

**What Tests Use**:
```python
# Some tests use _count suffix:
"notebook_count": 2,
"plantuml_count": 1,
"drawio_count": 1,

# Other tests use _workers suffix:
"notebook_workers": 2,
"plantuml_workers": 1,
"drawio_workers": 1,
```

**Proposed Fix**:
Support both naming conventions in config_loader:

```python
# Apply per-type overrides
for worker_type in ["notebook", "plantuml", "drawio"]:
    # Try both _workers and _count suffixes
    cli_workers_key = f"{worker_type}_workers"
    cli_count_key = f"{worker_type}_count"

    if cli_overrides.get(cli_workers_key) is not None:
        type_config = getattr(config, worker_type)
        type_config.count = cli_overrides[cli_workers_key]
        logger.info(f"CLI override: {worker_type}.count = {type_config.count}")
    elif cli_overrides.get(cli_count_key) is not None:
        type_config = getattr(config, worker_type)
        type_config.count = cli_overrides[cli_count_key]
        logger.info(f"CLI override: {worker_type}.count = {type_config.count}")
```

### 4. UnboundLocalError in test_e2e_managed_workers_reuse_across_builds

**Problem**: Variable `lifecycle_manager2` is referenced in `finally` block but may not be defined if exception occurs before line 248.

**Location**: `tests/e2e/test_e2e_lifecycle.py:272`

**Code**:
```python
try:
    # ... code ...
    lifecycle_manager2 = WorkerLifecycleManager(...)  # Line 248
    started_workers2 = lifecycle_manager2.start_managed_workers()
    # ... code ...
finally:
    # Stop workers after all builds
    lifecycle_manager2.stop_managed_workers(started_workers2)  # Line 272 - UnboundLocalError!
```

**Proposed Fix**:
Initialize variables before `try` block or check existence in `finally`:

```python
lifecycle_manager2 = None
started_workers2 = None

try:
    # ... code ...
    lifecycle_manager2 = WorkerLifecycleManager(...)
    started_workers2 = lifecycle_manager2.start_managed_workers()
    # ... code ...
finally:
    # Stop workers after all builds
    if lifecycle_manager2 is not None and started_workers2 is not None:
        lifecycle_manager2.stop_managed_workers(started_workers2)
```

### 5. SQLite Transaction Error

**Problem**: Nested transaction error in worker job polling.

**Error Message**:
```
sqlite3.OperationalError: cannot start a transaction within a transaction
```

**Location**: `src/clm/infrastructure/database/job_queue.py:200`

**Context**: Appears in test teardown, suggests a transaction handling issue when workers are polling for jobs.

**Proposed Investigation**:
1. Check if `job_queue.py` is properly managing transaction context
2. Ensure `BEGIN IMMEDIATE` is not called within an existing transaction
3. Review connection isolation level settings
4. Check if multiple workers are sharing the same connection (should use separate connections)

**Potential Fix**:
Ensure proper transaction isolation in `get_next_job()`:

```python
def get_next_job(self, worker_type: str, worker_id: int):
    conn = self.get_connection()
    # Don't start a new transaction if one is already active
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    # ... rest of code ...
```

Or use context manager for transactions:
```python
def get_next_job(self, worker_type: str, worker_id: int):
    conn = self.get_connection()
    with conn:  # Automatically handles transaction
        # ... query code ...
```

### 6. Worker Count Assertions

**Problem**: Tests expect specific worker counts but get different counts because PlantUML/DrawIO workers fail to start.

**Example**:
```
test_e2e_managed_workers_auto_lifecycle:
  Expected: 4 workers (2 notebook + 1 plantuml + 1 drawio)
  Actual: 2 workers (only notebook workers started)
```

**Root Cause**: This is a **consequence** of issues #1 and #2:
1. Worker services not installed (now fixed)
2. Config loader not properly applying overrides

**Expected Resolution**: Once config_loader is fixed and services are installed, these tests should pass.

## Test Failure Summary

| Test | Root Cause | Status |
|------|------------|--------|
| `test_course_1_notebooks_native_workers` | Missing worker services | ✅ Should be fixed |
| `test_course_dir_groups_copy_e2e` | Missing worker services | ✅ Should be fixed |
| `test_course_4_single_plantuml_e2e` | Missing worker services | ✅ Should be fixed |
| `test_e2e_managed_workers_auto_lifecycle` | Config loader + missing services | 🔧 Needs config fix |
| `test_e2e_managed_workers_reuse_across_builds` | UnboundLocalError | 🔧 Needs code fix |
| `test_e2e_persistent_workers_workflow` | Missing worker services | ✅ Should be fixed |
| `test_e2e_worker_health_monitoring_during_build` | Missing worker services | ✅ Should be fixed |
| `test_start_managed_workers_reuse` | Config loader | 🔧 Needs config fix |
| `test_start_managed_workers_fresh` | Config loader | 🔧 Needs config fix |
| `test_auto_start_behavior` | Config loader | 🔧 Needs config fix |

## Skipped Tests

12 tests were skipped:
- 3 Docker-related tests (Docker daemon not available or marked as docker tests)
- 5 Direct integration tests (requires full worker setup, possibly intentionally skipped)
- 1 DrawIO test (DrawIO executable not available)
- 3 Other integration tests

## Recommended Actions

### Immediate (High Priority)

1. **Fix config_loader.py** to support:
   - Direct value setting (`auto_start: False` in addition to `no_auto_start: True`)
   - Both naming conventions (`notebook_count` and `notebook_workers`)

2. **Fix UnboundLocalError** in `test_e2e_managed_workers_reuse_across_builds`:
   - Initialize variables before `try` block
   - Add null checks in `finally` block

3. **Add docker package to requirements** ✅ DONE
   - Added to `pyproject.toml`

4. **Install worker services** ✅ DONE
   - Installed plantuml-converter
   - Installed drawio-converter

### Medium Priority

5. **Investigate SQLite transaction error**:
   - Review transaction handling in `job_queue.py`
   - Consider connection pooling or per-worker connections
   - Add proper transaction context management

6. **Re-run integration tests** after fixes to verify:
   - All worker-related failures are resolved
   - Config loader properly handles all override forms
   - No UnboundLocalError occurs

### Low Priority

7. **Install external tools** (for full test coverage):
   - PlantUML JAR file (for PlantUML worker)
   - DrawIO executable (for DrawIO worker in direct mode)
   - These are only needed for running the full suite with external converters

8. **Standardize test configuration naming**:
   - Choose one convention (`_workers` vs `_count`)
   - Update all tests to use the chosen convention
   - Document the preferred convention in test guidelines

## Expected Outcome

After implementing fixes #1-4:
- **19 passing tests** (currently passing, should remain passing)
- **10 failing tests** → **10 passing tests** (after fixes)
- **12 skipped tests** (will remain skipped without Docker/external tools)

**Total: 29/29 non-skipped tests passing (100%)**

The 12 skipped tests are expected and appropriate:
- Docker tests require Docker daemon (can be run with `--workers=docker` when Docker is available)
- External tool tests require PlantUML/DrawIO installation (can be run when tools are available)
