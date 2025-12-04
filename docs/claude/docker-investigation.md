# Docker Integration Investigation

## Summary

Investigation into why Docker worker tests fail on Windows. The lite notebook image builds and works correctly, but tests cannot verify worker registration due to SQLite WAL mode issues with Docker volume mounts on Windows.

## Completed Work

### 1. Docker Image Build (Success)
- Built `clx-notebook-processor:lite-test` image successfully (5.97GB)
- All Jupyter kernels work: Python, C++, C#, F#, Java, TypeScript
- Worker module imports correctly
- Container starts and runs correctly

### 2. Bug Fixes Applied

#### Double-slash path bug in worker_executor.py
**File:** `src/clx/infrastructure/workers/worker_executor.py:165-170`

**Problem:** The code used double-slashes (`//db/filename`) for container paths on Windows to work around MSYS/Git Bash path conversion. However:
- Python docker SDK doesn't use a shell, it communicates directly with Docker daemon
- On Linux containers, `//db/` is NOT equivalent to `/db/` - it's treated as a network path
- This caused containers to look for database at `//db/test.db` which doesn't exist

**Fix:** Changed to always use single slash paths:
```python
# Before
db_path_in_container = f"//db/{db_filename}" if sys.platform == "win32" else f"/db/{db_filename}"

# After
db_path_in_container = f"/db/{db_filename}"
```

#### Test fixture temp directory
**File:** `tests/infrastructure/workers/test_lifecycle_integration.py:47-82`

**Problem:** The `db_path` fixture used `tempfile.NamedTemporaryFile` which creates files directly in the system temp directory. When Docker mounts the parent directory (entire temp dir), it can cause issues.

**Fix:** Changed to create a dedicated temp directory for each test's database:
```python
temp_dir = Path(tempfile.mkdtemp(prefix="clx-test-db-"))
path = temp_dir / "test.db"
```

### 3. Updated Test Image References
Updated Docker tests to use locally built image:
- `tests/infrastructure/workers/test_lifecycle_integration.py` - Updated to `clx-notebook-processor:lite-test`
- `tests/e2e/test_e2e_lifecycle.py` - Updated to `clx-notebook-processor:lite-test`

## Blocking Issue: SQLite WAL Mode on Windows Docker

### The Problem

SQLite WAL mode requires shared memory-mapped file access for readers to see uncommitted writes. On Docker Desktop for Windows, when a database is on a bind-mounted volume:

1. Container writes to database -> Data goes to WAL file (`test.db-wal`)
2. Host tries to read database -> Cannot see WAL writes because:
   - The `-shm` (shared memory) file doesn't work across Docker volume mounts on Windows
   - Even after `PRAGMA wal_checkpoint(TRUNCATE)`, host still sees stale data

### Evidence

Test sequence:
1. Host initializes database (empty workers table)
2. Container starts, worker registers successfully
3. From inside container: `SELECT * FROM workers` shows 1 row
4. From host: `SELECT * FROM workers` shows 0 rows
5. Container runs `PRAGMA wal_checkpoint(TRUNCATE)`
6. From host: Still shows 0 rows

Even copying files out with `docker cp` shows the file system isn't properly syncing.

### Previous Context (from user)

> WAL mode was a problem earlier because it needs shared memory which we don't have with docker on windows. But when we tried to disable WAL mode the whole system broke, and attempts to fix it led nowhere, so please don't try to disable it.

## Options to Consider

### Option 1: Skip Docker tests on Windows
Mark Docker tests to skip on Windows platform. Docker mode would only be tested in CI on Linux.

```python
@pytest.mark.skipif(sys.platform == "win32", reason="Docker tests require Linux due to WAL mode issues")
```

### Option 2: Use Linux VMs/WSL2 for Docker testing
If Docker Desktop is using WSL2 backend, the tests might work from within WSL2.

### Option 3: Alternative database location
Instead of bind-mounting the database, the container could use an internal database and communicate via different mechanism (e.g., named pipe, network).

### Option 4: Direct execution mode testing only
Keep Docker tests but run them only in direct execution mode for unit testing, rely on CI for actual Docker testing.

## Current Test Status

| Test | Status | Notes |
|------|--------|-------|
| Direct mode integration | Working | Uses subprocess workers |
| Docker mode integration | Failing | WAL sync issue |
| E2E direct mode | Needs verification | |
| E2E Docker mode | Failing | WAL sync issue |

## Files Modified

1. `src/clx/infrastructure/workers/worker_executor.py` - Fixed double-slash path
2. `tests/infrastructure/workers/test_lifecycle_integration.py` - Updated fixture and image
3. `tests/e2e/test_e2e_lifecycle.py` - Updated image reference

## Next Steps

1. Decide on approach for Docker testing on Windows (skip vs alternative)
2. If skipping: Add platform-specific skip markers to Docker tests
3. Ensure Docker tests work in CI (Linux environment)
4. Run full test suite to verify no regressions from fixes

## Docker Image Versioning Proposal

Also discussed but not implemented:

### Recommended: Unified versioning
- Keep all images at same version as main package (currently 0.5.0)
- Tags: `0.5.0`, `0.5.0-lite`, `0.5.0-full`, `latest`, `lite`, `full`
- Rationale: Workers are tightly coupled to CLX core

### CI/CD Strategy
- PR checks: Build lite images only (faster)
- Main branch: Build both lite and full
- Release: Push versioned tags to Docker Hub
