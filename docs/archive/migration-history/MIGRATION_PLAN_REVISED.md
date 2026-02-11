# CLM Architecture Migration: Revised Plan

**Date**: 2025-11-14
**Based on**: Comprehensive codebase analysis
**Status**: 60% Complete
**Current Branch**: `claude/analyze-redesign-status-01TD4PD52MuujskkVpKvQYMV`

## Overview

This document replaces the original MIGRATION_PLAN.md with an accurate view of:
1. ✅ **What's been completed** (Phases 1-3)
2. 🔄 **What's in progress** (Phase 4)
3. ❌ **What remains** (Phases 5-7)

For detailed analysis of current state, see [ARCHITECTURE_MIGRATION_STATUS.md](./ARCHITECTURE_MIGRATION_STATUS.md).

---

## Phase 1: SQLite Infrastructure ✅ COMPLETE

**Status**: 100% complete
**Test Coverage**: 47 passing tests
**Duration**: Completed over 2-3 weeks

### What Was Built

#### 1.1 Database Schema ✅
- **File**: `clm-common/src/clm_common/database/schema.py`
- **Tables**: `jobs`, `workers`, `results_cache`, `schema_version`
- **Features**:
  - DELETE journal mode (cross-platform compatibility)
  - 30-second busy timeout
  - Proper indexes for performance
  - WAL mode issues resolved (Docker volume mount compatibility)

#### 1.2 JobQueue Class ✅
- **File**: `clm-common/src/clm_common/database/job_queue.py`
- **Methods**:
  - `add_job()` - Submit jobs to queue
  - `get_next_job()` - Atomic polling with transaction
  - `update_job_status()` - Track job state
  - `check_cache()` / `add_to_cache()` - Result caching
- **Features**: Thread-safe, automatic connection management

#### 1.3 Worker Base Class ✅
- **File**: `clm-common/src/clm_common/workers/worker_base.py`
- **Features**:
  - Abstract `Worker` class with polling loop
  - Self-registration in database
  - Heartbeat mechanism
  - Graceful shutdown (SIGTERM/SIGINT)
  - Job processing with error handling
  - Statistics tracking

#### 1.4 Worker Pool Manager ✅
- **File**: `clm-common/src/clm_common/workers/pool_manager.py`
- **Features**:
  - Manages multiple worker pools
  - Health monitoring (heartbeat + CPU tracking)
  - Auto-restart of hung/dead workers
  - Stale worker cleanup
  - Docker network management

#### 1.5 Worker Executor Abstraction ✅
- **File**: `clm-common/src/clm_common/workers/worker_executor.py`
- **Classes**:
  - `WorkerExecutor` (abstract base)
  - `DockerWorkerExecutor` (runs workers in containers)
  - `DirectWorkerExecutor` (runs workers as subprocesses)
- **Benefits**: Flexible deployment, easier testing

### Test Results
- ✅ 13 tests for `worker_base.py`
- ✅ 15 tests for `pool_manager.py`
- ✅ 7 tests for direct worker integration
- ✅ 12 tests for database operations
- **Total**: 47 passing tests

### Key Fixes Applied
- ✅ Worker registration race condition fixed
- ✅ Windows Docker volume mounting fixed
- ✅ DELETE journal mode (no WAL) for cross-platform support
- ✅ Retry logic with exponential backoff

---

## Phase 2: Remove RabbitMQ from Workers ✅ COMPLETE

**Status**: 100% complete
**Duration**: 1-2 days

### What Was Done

#### 2.1 Notebook Processor ✅
- **File**: `services/notebook-processor/src/nb/__main__.py`
- **Change**: Removed dual-mode code, now directly calls `notebook_worker.main()`
- **Worker**: `services/notebook-processor/src/nb/notebook_worker.py` (SQLite-only polling)

#### 2.2 DrawIO Converter ✅
- **File**: `services/drawio-converter/src/drawio_converter/__main__.py`
- **Change**: Removed dual-mode code, now directly calls `drawio_worker.main()`
- **Worker**: `services/drawio-converter/src/drawio_converter/drawio_worker.py` (SQLite-only polling)

#### 2.3 PlantUML Converter ✅
- **File**: `services/plantuml-converter/src/plantuml_converter/__main__.py`
- **Change**: Removed dual-mode code, now directly calls `plantuml_worker.main()`
- **Worker**: `services/plantuml-converter/src/plantuml_converter/plantuml_worker.py` (SQLite-only polling)

### Result
- ✅ Workers no longer import FastStream/RabbitMQ code
- ✅ No more `AMQPConnectionError` on worker startup
- ✅ Workers poll SQLite queue exclusively
- ✅ All workers self-register and process jobs correctly

### Legacy Code Remaining
- ⚠️ Old RabbitMQ server implementations still exist in converter modules:
  - `notebook_server.py`
  - `drawio_converter.py` (RabbitMQ handlers)
  - `plantuml_converter.py` (RabbitMQ handlers)
- **Status**: Not used by workers, safe to delete (Phase 6 cleanup)

---

## Phase 3: Backend Integration ✅ COMPLETE

**Status**: 100% complete
**Test Coverage**: 15 passing unit tests
**Duration**: 1-2 days

### What Was Built

#### 3.1 SqliteBackend Class ✅
- **File**: `clm-faststream-backend/src/clm_faststream_backend/sqlite_backend.py`
- **Features**:
  - Inherits from `LocalOpsBackend`
  - Job submission via `execute_operation()`
  - Completion polling via `wait_for_completion()`
  - Integrates with both database cache and SQLite cache
  - Supports all job types (notebook, drawio, plantuml)
  - Configurable poll interval (default: 0.5s) and timeout (default: 300s)
  - Proper error handling and logging

#### 3.2 CLI Integration ✅
- **File**: `clm-cli/src/clm_cli/main.py` (lines 148-159, 284-288)
- **Feature**: Added `--use-sqlite` flag to `clm build` command
- **Behavior**:
  ```bash
  clm build course.yaml --use-sqlite  # Uses SqliteBackend
  clm build course.yaml               # Uses FastStreamBackend (CURRENT DEFAULT)
  ```

### Test Results
- ✅ 15 unit tests for SqliteBackend
- ✅ Backend initialization
- ✅ Job submission for all types
- ✅ Completion polling (success and failure)
- ✅ Timeout handling
- ✅ Cache integration (database + SQLite)
- ✅ Multiple concurrent operations
- ✅ Error scenarios

### E2E Tests
- ✅ E2E course conversion tests use SqliteBackend
- ✅ Tests pass with direct worker execution
- ⚠️ Not yet tested with default CLI (waiting for Phase 4)

---

## Phase 4: Make SQLite Default 🔄 READY TO START

**Status**: 0% complete
**Priority**: ⭐ **CRITICAL** - This is the key remaining work
**Estimated Time**: 30 minutes code + 2 hours testing

### Current Problem

The CLI still defaults to RabbitMQ:
```python
# In clm-cli/src/clm_cli/main.py
if use_sqlite:
    backend = SqliteBackend(...)  # NEW - requires --use-sqlite flag
else:
    backend = FastStreamBackend(...)  # LEGACY - current default ❌
```

Users must explicitly pass `--use-sqlite` to use the new backend, which is backward and blocks adoption.

### Required Changes

#### Step 4.1: Reverse Default Backend

**File**: `clm-cli/src/clm_cli/main.py`

**Change 1**: Update backend selection logic (lines 148-159)
```python
# BEFORE
if use_sqlite:
    backend = SqliteBackend(...)
else:
    backend = FastStreamBackend(...)

# AFTER
if use_rabbitmq:  # NEW FLAG: temporary backward compatibility
    logger.warning(
        "RabbitMQ backend is DEPRECATED and will be removed in a future version. "
        "Please migrate to SQLite backend (default)."
    )
    backend = FastStreamBackend(
        db_manager=db_manager,
        ignore_db=ignore_db
    )
else:
    backend = SqliteBackend(  # NEW DEFAULT
        db_path=db_path,
        workspace_path=output_dir,
        db_manager=db_manager,
        ignore_db=ignore_db
    )
```

**Change 2**: Update CLI option (lines 284-288)
```python
# REMOVE
@click.option(
    "--use-sqlite",
    is_flag=True,
    default=False,
    help="Use SQLite-based backend instead of RabbitMQ (Phase 3 migration).",
)

# ADD
@click.option(
    "--use-rabbitmq",
    is_flag=True,
    default=False,
    help="Use RabbitMQ-based backend (DEPRECATED). Default is SQLite.",
)
```

**Change 3**: Update function signature
```python
# BEFORE
def build(
    ctx, spec_file, data_dir, output_dir, watch,
    print_tracebacks, print_correlation_ids, log_level,
    ignore_db, force_db_init, keep_directory,
    use_sqlite,  # REMOVE
):

# AFTER
def build(
    ctx, spec_file, data_dir, output_dir, watch,
    print_tracebacks, print_correlation_ids, log_level,
    ignore_db, force_db_init, keep_directory,
    use_rabbitmq,  # ADD
):
```

#### Step 4.2: Update main() Function Call

**File**: `clm-cli/src/clm_cli/main.py` (around line 306)

```python
# BEFORE
asyncio.run(
    main(
        ctx, spec_file, data_dir, output_dir, watch,
        print_tracebacks, print_correlation_ids, log_level,
        db_path, ignore_db, force_db_init, keep_directory,
        use_sqlite,  # CHANGE THIS
    )
)

# AFTER
asyncio.run(
    main(
        ctx, spec_file, data_dir, output_dir, watch,
        print_tracebacks, print_correlation_ids, log_level,
        db_path, ignore_db, force_db_init, keep_directory,
        use_rabbitmq,  # TO THIS
    )
)
```

### Testing Checklist

After making changes:

- [ ] `clm build course.yaml` works without any flags (uses SqliteBackend)
- [ ] `clm build course.yaml --use-rabbitmq` still works (backward compatibility)
- [ ] Deprecation warning is shown when using `--use-rabbitmq`
- [ ] All output files are generated correctly
- [ ] Cache works (run same course twice, second should be instant)
- [ ] Watch mode works with new default
- [ ] All existing E2E tests pass
- [ ] Performance is acceptable (measure time)

### Expected User Experience

```bash
# New default behavior (SQLite, no RabbitMQ needed)
$ clm build examples/sample-course/course.yaml
INFO:clm_faststream_backend.sqlite_backend:Initialized SQLite backend
INFO:clm_faststream_backend.sqlite_backend:Waiting for 5 job(s) to complete...
INFO:clm_faststream_backend.sqlite_backend:All jobs completed successfully

# Backward compatibility (with deprecation warning)
$ clm build examples/sample-course/course.yaml --use-rabbitmq
WARNING:clm_cli.main:RabbitMQ backend is DEPRECATED and will be removed...
INFO:clm_faststream_backend.faststream_backend:Connected to RabbitMQ
...
```

### Success Criteria

- ✅ SqliteBackend is the default
- ✅ No breaking changes for existing workflows
- ✅ Clear deprecation path for RabbitMQ
- ✅ All tests pass

---

## Phase 5: Remove RabbitMQ Infrastructure ❌ NOT STARTED

**Status**: 0% complete
**Priority**: Medium
**Estimated Time**: 1-2 hours

### Current Situation

`docker-compose.yaml` still includes:
- `rabbitmq` service (ports 5672, 15672)
- `rabbitmq-exporter` service
- `loki` service (optional)
- `prometheus` service (optional)
- `grafana` service (optional)
- Worker services with `RABBITMQ_URL` environment variables

### Required Changes

#### Step 5.1: Create Backup

```bash
# Create backup of current docker-compose
cp docker-compose.yaml docker-compose.legacy.yaml
git add docker-compose.legacy.yaml
git commit -m "Backup legacy RabbitMQ docker-compose configuration"
```

#### Step 5.2: Simplify docker-compose.yaml

**Remove Services**:
- `rabbitmq`
- `rabbitmq-exporter`
- `loki` (optional: keep if desired for logging)
- `prometheus` (optional: keep if desired for metrics)
- `grafana` (optional: keep if desired for dashboards)

**Update Worker Services**:
```yaml
services:
  notebook-processor:
    build: ./services/notebook-processor
    image: clm-notebook-processor:0.2.2
    container_name: clm-notebook-worker
    volumes:
      - ./data:/workspace
      - ./clm_jobs.db:/db/jobs.db  # SQLite database
    environment:
      - DB_PATH=/db/jobs.db
      - LOG_LEVEL=INFO
    # REMOVE: depends_on: rabbitmq
    # REMOVE: RABBITMQ_URL environment variable
    restart: unless-stopped

  # Similar for drawio-converter and plantuml-converter
```

#### Step 5.3: Update Build Scripts

Check if `build-services.sh` / `build-services.ps1` reference RabbitMQ services and update if needed.

### Testing

- [ ] `docker-compose up -d` starts only worker services
- [ ] Workers connect to SQLite database correctly
- [ ] Workers process jobs via pool manager
- [ ] No RabbitMQ connection attempts in logs

### Notes

- Keep `docker-compose.legacy.yaml` for users who need RabbitMQ temporarily
- Update documentation to reference legacy file for backward compatibility

---

## Phase 6: Clean Up Legacy Code ❌ NOT STARTED

**Status**: 5% complete (worker entrypoints cleaned, but server code remains)
**Priority**: Medium
**Estimated Time**: 2-3 hours

### 6.1 Remove FastStream Dependencies

**Files to Update**:
- `services/plantuml-converter/pyproject.toml` - Remove `faststream[rabbit]~=0.5.19`
- `services/drawio-converter/pyproject.toml` - Remove `faststream[rabbit]~=0.5.19`

**Note**: `notebook-processor/pyproject.toml` already clean ✅

### 6.2 Delete Legacy RabbitMQ Server Code

**Files to Delete** (not used by SQLite workers):
1. `services/notebook-processor/src/nb/notebook_server.py` (80+ lines)
2. Legacy RabbitMQ handlers in:
   - `services/drawio-converter/src/drawio_converter/drawio_converter.py`
   - `services/plantuml-converter/src/plantuml_converter/plantuml_converter.py`

**Alternative**: Rename to `*_legacy.py` and add deprecation notice if concerned about deletion

### 6.3 Add Deprecation Warnings to FastStreamBackend

**File**: `clm-faststream-backend/src/clm_faststream_backend/faststream_backend.py`

Add warning on initialization:
```python
class FastStreamBackend(LocalOpsBackend):
    def __init__(self, ...):
        logger.warning(
            "FastStreamBackend (RabbitMQ) is DEPRECATED. "
            "It will be removed in a future version. "
            "Please use SqliteBackend (default)."
        )
        # existing initialization...
```

### 6.4 Remove Unused Imports

Search for and remove:
- Unused FastStream imports
- Unused RabbitMQ connection code
- Unused correlation ID tracking (specific to RabbitMQ)

**Command**:
```bash
# Find FastStream imports
grep -r "from faststream" --include="*.py" | grep -v "test_" | grep -v "legacy"

# Find RabbitMQ references
grep -r "rabbitmq\|RabbitMQ\|RABBITMQ" --include="*.py" | grep -v "test_" | grep -v "legacy"
```

### 6.5 Update Test Configuration

Remove RabbitMQ-specific test markers and fixtures if they exist:
- Check `conftest.py` for RabbitMQ fixtures
- Check for `@pytest.mark.broker` tests
- Update test documentation

### Success Criteria

- ✅ No FastStream dependencies in worker services
- ✅ Legacy RabbitMQ server code removed or clearly marked as deprecated
- ✅ Deprecation warnings in place for FastStreamBackend
- ✅ No unused imports remain
- ✅ All tests still pass

---

## Phase 7: Package Consolidation ❌ NOT STARTED

**Status**: 0% complete
**Priority**: Low (future work)
**Estimated Time**: 1-2 days

### Current Structure

```
clm/                        # Core course processing
clm-cli/                    # Command-line interface
clm-common/                 # Shared infrastructure
clm-faststream-backend/     # Backend implementations
```

### Target Structure

```
clm/
├── src/
│   └── clm/
│       ├── __init__.py
│       ├── cli/              # Merged from clm-cli
│       │   ├── __init__.py
│       │   ├── main.py
│       │   └── commands/
│       ├── core/             # Core course logic
│       │   ├── course.py
│       │   ├── course_spec.py
│       │   ├── section.py
│       │   ├── topic.py
│       │   └── course_files/
│       ├── database/         # Merged from clm-common
│       │   ├── __init__.py
│       │   ├── schema.py
│       │   ├── job_queue.py
│       │   └── db_operations.py
│       ├── workers/          # Merged from clm-common
│       │   ├── __init__.py
│       │   ├── worker_base.py
│       │   ├── pool_manager.py
│       │   └── worker_executor.py
│       ├── backends/         # Merged from clm-faststream-backend
│       │   ├── __init__.py
│       │   ├── backend.py
│       │   └── sqlite_backend.py
│       └── messaging/        # Merged from clm-common
│           ├── payloads.py
│           └── results.py
├── services/                 # Worker implementations (unchanged)
│   ├── notebook-processor/
│   ├── drawio-converter/
│   └── plantuml-converter/
└── tests/
```

### Migration Process

#### Step 7.1: Plan Import Updates

Create mapping of old imports to new imports:
```python
# OLD → NEW
clm_common.database → clm.database
clm_common.workers → clm.workers
clm_common.messaging → clm.messaging
clm_cli.main → clm.cli.main
clm_faststream_backend.sqlite_backend → clm.backends.sqlite_backend
```

#### Step 7.2: Merge clm-common

```bash
# Move files
mkdir -p clm/src/clm/database
mkdir -p clm/src/clm/workers
mkdir -p clm/src/clm/messaging

mv clm-common/src/clm_common/database/* clm/src/clm/database/
mv clm-common/src/clm_common/workers/* clm/src/clm/workers/
mv clm-common/src/clm_common/messaging/* clm/src/clm/messaging/

# Move tests
mkdir -p clm/tests/database
mkdir -p clm/tests/workers
mv clm-common/tests/database/* clm/tests/database/
mv clm-common/tests/workers/* clm/tests/workers/
```

#### Step 7.3: Merge clm-cli

```bash
# Move files
mkdir -p clm/src/clm/cli
mv clm-cli/src/clm_cli/* clm/src/clm/cli/

# Move tests
mkdir -p clm/tests/cli
mv clm-cli/tests/* clm/tests/cli/
```

#### Step 7.4: Merge clm-faststream-backend

```bash
# Move files
mkdir -p clm/src/clm/backends
mv clm-faststream-backend/src/clm_faststream_backend/sqlite_backend.py clm/src/clm/backends/
mv clm-faststream-backend/src/clm_faststream_backend/backend.py clm/src/clm/backends/

# Note: Keep faststream_backend.py separate or mark as deprecated

# Move tests
mkdir -p clm/tests/backends
mv clm-faststream-backend/tests/test_sqlite_backend.py clm/tests/backends/
```

#### Step 7.5: Update All Imports

**Automated approach** (recommended):
```bash
# Use a script to update imports
python scripts/update_imports.py
```

**Manual approach**:
```bash
# Find and replace (use carefully!)
find clm -type f -name "*.py" -exec sed -i 's/from clm_common/from clm/g' {} +
find clm -type f -name "*.py" -exec sed -i 's/import clm_common/import clm/g' {} +
find clm -type f -name "*.py" -exec sed -i 's/from clm_cli/from clm.cli/g' {} +
find clm -type f -name "*.py" -exec sed -i 's/from clm_faststream_backend/from clm.backends/g' {} +
```

#### Step 7.6: Update pyproject.toml

**Single consolidated package**:
```toml
[project]
name = "clm"
version = "0.3.0"  # Bump version
description = "Coding Academy Lecture Manager - Unified Package"
dependencies = [
    "click>=8.0.0",
    "pydantic~=2.8.2",
    "attrs>=21.0.0",
    "watchdog>=2.1.0",
    # ... other dependencies
]

[project.scripts]
clm = "clm.cli.main:cli"

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
    # ... test dependencies
]
```

#### Step 7.7: Thorough Testing

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] E2E tests pass
- [ ] CLI works correctly: `clm --help`, `clm build`, `clm watch`
- [ ] Workers start and process jobs
- [ ] Installation works: `pip install -e .`
- [ ] Package can be built: `python -m build`

### Benefits

- Single package simplifies development
- Clearer code organization
- Easier dependency management
- Simpler installation for users
- Better IDE support (single source tree)

### Risks

- Large refactoring, potential for import errors
- Must update all documentation
- Must update all deployment scripts
- Breaking change for any external code depending on old package names

### Recommendation

**Do this LAST**, after everything else is stable and working. Consider this a major version bump (0.2.x → 0.3.0 or 1.0.0).

---

## Phase 8: Enhanced Monitoring (Future) ❌ NOT STARTED

**Priority**: Low (nice to have)
**Estimated Time**: 1-2 days

### Proposed CLI Commands

#### 8.1 `clm status` Command

Show system status:
```bash
$ clm status

CLM System Status
=================

Workers:
  notebook: 2 running, 1 idle, 1 busy, 0 hung
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

#### 8.2 `clm workers` Command

Manage workers:
```bash
$ clm workers list

ID  Type      Status  Jobs  Uptime    Last Heartbeat
==  ========  ======  ====  ========  ==============
1   notebook  busy    45    2h 34m    2s ago
2   notebook  idle    38    2h 34m    1s ago
3   drawio    idle    102   2h 34m    0s ago
4   plantuml  idle    89    2h 34m    1s ago

$ clm workers restart 1
Restarting worker 1...
Worker 1 restarted successfully.
```

#### 8.3 `clm jobs` Command

Manage jobs:
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

#### 8.4 `clm cache` Command

Manage cache:
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

### Implementation Notes

- Use Click command groups
- Query SQLite database for status
- Add `rich` library for better terminal output (tables, colors)
- Consider adding `--json` flag for machine-readable output

---

## Testing Strategy

### Test Pyramid

```
                    E2E Tests (5)
                  /               \
            Integration Tests (20)
          /                         \
      Unit Tests (50+)
```

### Current Test Coverage

| Layer | Current | Target | Status |
|-------|---------|--------|--------|
| **Unit Tests** | 47 | 60+ | ⚠️ Add more for Phase 4+ |
| **Integration Tests** | 10 | 20+ | ⚠️ Need full CLI integration tests |
| **E2E Tests** | 3 | 5+ | ⚠️ Need more realistic course tests |

### Required Tests for Phase 4

1. **CLI Default Backend Test**
   - Test `clm build` without flags uses SqliteBackend
   - Test output is correct

2. **Backward Compatibility Test**
   - Test `clm build --use-rabbitmq` still works (if RabbitMQ available)
   - Test deprecation warning is shown

3. **Performance Comparison Test**
   - Compare processing time: RabbitMQ vs SQLite
   - Verify SQLite is competitive

4. **Cache Test**
   - Process course twice
   - Verify second run uses cache (near-instant)

5. **Watch Mode Test**
   - Start watch mode
   - Modify source file
   - Verify auto-reprocessing works

---

## Risk Management

### Identified Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Making SqliteBackend default breaks users** | Medium | High | Keep `--use-rabbitmq` flag, thorough testing |
| **Performance regression with SQLite** | Low | Medium | Benchmark before/after, optimize if needed |
| **Package consolidation introduces bugs** | High | High | Do last, after everything stable, extensive testing |
| **Docker volume mounting issues** | Low | Medium | Already resolved with DELETE journal mode |
| **Worker synchronization issues** | Low | High | Already handled with atomic transactions |

### Rollback Plan

If issues arise:

1. **Phase 4** (default backend change):
   - Revert CLI changes
   - Keep both backends available
   - Gather more user feedback

2. **Phase 5** (remove RabbitMQ infrastructure):
   - Use `docker-compose.legacy.yaml`
   - No code changes to rollback

3. **Phase 6** (cleanup):
   - Restore deleted files from git history
   - Re-add dependencies if needed

4. **Phase 7** (consolidation):
   - Extensive git history to rollback
   - May require new release

---

## Timeline and Priorities

### Critical Path (This Week)

1. **Phase 4.1**: Make SqliteBackend default (30 min)
2. **Phase 4.2**: Update documentation (2 hours)
3. **Phase 4.3**: Full E2E testing (2 hours)
4. **Phase 4.4**: Performance benchmarking (1 hour)

**Total**: ~6 hours work

### Short-Term (Next 2 Weeks)

5. **Phase 5**: Remove RabbitMQ from docker-compose (1 hour)
6. **Phase 6**: Clean up legacy code (3 hours)
7. **Testing**: Additional integration tests (4 hours)

**Total**: ~8 hours work

### Long-Term (Next Month)

8. **Phase 7**: Package consolidation (2 days)
9. **Phase 8**: Enhanced monitoring (2 days)
10. **Documentation**: Comprehensive docs update (1 day)

**Total**: ~5 days work

---

## Success Metrics

Track these metrics before and after migration:

| Metric | Before | Target | How to Measure |
|--------|--------|--------|----------------|
| **Default Backend** | RabbitMQ | SQLite | Check CLI code |
| **Docker Services** | 8 | 3 | Count in docker-compose.yaml |
| **Startup Time** | ~30s | <10s | Time docker-compose up |
| **Memory Usage** | ~1.5GB | <800MB | Docker stats |
| **Test Coverage** | 47 tests | 60+ tests | pytest --cov |
| **Package Count** | 4 | 1 | Count package directories |
| **Build Time** | ? | Baseline | Time to build Docker images |
| **Processing Time** | ? | <= Baseline | Time to process sample course |

---

## Recommendations

### Immediate Actions (Do Now)

1. **Phase 4.1**: Change CLI default to SqliteBackend
   - Highest priority
   - Enables all users to benefit from new architecture
   - Small code change, well-tested foundation

2. **Phase 4.2**: Update documentation
   - Critical for user communication
   - Prevents confusion about migration status

3. **Phase 4.3**: E2E testing with real course
   - Validate everything works end-to-end
   - Catch any integration issues

### Short-Term (Next Week)

4. **Phase 5**: Remove RabbitMQ from docker-compose
   - Simplifies deployment
   - Reduces confusion

5. **Phase 6.1**: Clean up dependencies
   - Remove FastStream from worker pyproject.toml files
   - Reduces build times

### Long-Term (When Stable)

6. **Phase 7**: Package consolidation
   - Major refactoring
   - Do after everything else is proven stable
   - Consider as part of version 1.0.0 release

7. **Phase 8**: Enhanced monitoring
   - Nice to have
   - Improves user experience
   - Can be done incrementally

---

## Conclusion

The CLM architecture migration is **60% complete** with solid foundations:

- ✅ **Phases 1-3**: SQLite infrastructure, workers, and backend fully implemented
- 🔄 **Phase 4**: Critical work remains - making SqliteBackend the default
- ❌ **Phases 5-7**: Cleanup and polish work

**The key blocker** to completing the migration is **making SqliteBackend the default in the CLI**. This is a small code change (~20 lines) but has high impact - it enables all users to benefit from the new architecture.

**Estimated time to functional completion** (Phases 4-5): **1 week**
**Estimated time to full polish** (Phases 6-7): **1 month**

The migration strategy has proven successful - avoiding dual-mode complexity by making workers SQLite-only was the right choice. The remaining work is primarily cleanup and making the new system the default user experience.

---

## Appendix: Quick Reference

### Key Commands

```bash
# Current (requires flag to use SQLite)
clm build course.yaml --use-sqlite

# After Phase 4 (SQLite is default)
clm build course.yaml

# Backward compatibility (temporary)
clm build course.yaml --use-rabbitmq

# Start worker pool
python -m clm_common.workers.pool_manager

# Check database status
sqlite3 clm_jobs.db "SELECT * FROM jobs;"
sqlite3 clm_jobs.db "SELECT * FROM workers;"
```

### Key Files to Modify in Phase 4

1. `clm-cli/src/clm_cli/main.py` (lines 148-159, 284-288, 306)
   - Reverse backend default
   - Change flag from `--use-sqlite` to `--use-rabbitmq`
   - Add deprecation warning

### Test Commands

```bash
# Run all unit tests
pytest

# Run integration tests
pytest -m integration

# Run E2E tests
pytest -m e2e

# Run with coverage
pytest --cov=clm --cov=clm_common --cov=clm_cli --cov=clm_faststream_backend

# Run specific test file
pytest clm-faststream-backend/tests/test_sqlite_backend.py -v
```

---

**Document Version**: 1.0
**Last Updated**: 2025-11-14
**Status**: CURRENT
**See Also**: [ARCHITECTURE_MIGRATION_STATUS.md](./ARCHITECTURE_MIGRATION_STATUS.md)
