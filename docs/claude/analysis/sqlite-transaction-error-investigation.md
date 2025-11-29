# SQLite Transaction Error Investigation

## Error Message

```
sqlite3.OperationalError: cannot start a transaction within a transaction
```

**Location**: `src/clx/infrastructure/database/job_queue.py:200` in `get_next_job()`

## Root Cause Analysis

### Problem Overview

The error occurs when `get_next_job()` tries to execute `BEGIN IMMEDIATE` while a transaction is already active on the database connection.

### Technical Details

#### Default SQLite Behavior in Python

By default, `sqlite3.connect()` creates connections with:
- `isolation_level = ""` (empty string, NOT None)
- This enables **implicit transaction management**
- Every SQL statement (including SELECT) automatically starts a transaction if one isn't active
- Transactions must be explicitly committed or rolled back

#### Connection Setup in job_queue.py

```python
# Lines 72-78: Connection creation
self._local.conn = sqlite3.connect(
    str(self.db_path),
    check_same_thread=False,
    timeout=30.0
)
self._local.conn.row_factory = sqlite3.Row
# NOTE: isolation_level is NOT set, defaults to ""
```

**Result**: Every SELECT/INSERT/UPDATE/DELETE starts an implicit transaction that remains active until explicitly committed or rolled back.

### Methods Leaving Transactions Open

I've identified 5 methods that execute queries without proper transaction cleanup:

#### 1. `check_cache()` (Line 125)

```python
def check_cache(self, output_file: str, content_hash: str) -> Optional[Dict[str, Any]]:
    conn = self._get_conn()
    cursor = conn.execute("""SELECT result_metadata FROM results_cache...""")  # Starts transaction
    row = cursor.fetchone()

    if row:
        conn.execute("""UPDATE results_cache...""")
        conn.commit()  # ✅ Commits if cache hit
        return json.loads(row[0])

    return None  # ❌ NO COMMIT/ROLLBACK on cache miss - transaction left open!
```

**Issue**: When cache misses (returns None), the implicit transaction from the SELECT is never closed.

#### 2. `get_job()` (Line 322)

```python
def get_job(self, job_id: int) -> Optional[Job]:
    conn = self._get_conn()
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))  # Starts transaction
    row = cursor.fetchone()

    if not row:
        return None  # ❌ NO COMMIT/ROLLBACK

    return Job(...)  # ❌ NO COMMIT/ROLLBACK
```

**Issue**: NEVER commits or rolls back - every call leaves a transaction open.

#### 3. `get_job_stats()` (Line 356)

```python
def get_job_stats(self) -> Dict[str, Any]:
    conn = self._get_conn()
    stats = {}
    for status in ['pending', 'processing', 'completed', 'failed']:
        cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (status,))  # Starts transaction
        stats[status] = cursor.fetchone()[0]

    return stats  # ❌ NO COMMIT/ROLLBACK
```

**Issue**: Executes multiple SELECTs without closing the transaction.

#### 4. `get_queue_statistics()` (Line 374)

```python
def get_queue_statistics(self) -> Dict[str, Any]:
    conn = self._get_conn()
    stats = self.get_job_stats()  # May leave transaction open

    cursor = conn.execute("""SELECT job_type, COUNT(*) as count FROM jobs GROUP BY job_type""")  # May start or continue transaction
    stats['by_type'] = {row[0]: row[1] for row in cursor.fetchall()}

    cursor = conn.execute("""SELECT id, job_type, ...""")
    stats['processing_jobs'] = [...]

    return stats  # ❌ NO COMMIT/ROLLBACK
```

**Issue**: Multiple SELECTs without transaction cleanup.

#### 5. `get_jobs_by_status()` (Line 417)

```python
def get_jobs_by_status(self, status: str, limit: int = 100) -> List[Job]:
    conn = self._get_conn()
    cursor = conn.execute("""SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?""", ...)  # Starts transaction

    jobs = []
    for row in cursor.fetchall():
        jobs.append(Job(...))

    return jobs  # ❌ NO COMMIT/ROLLBACK
```

**Issue**: SELECT without transaction cleanup.

### The Failure Scenario

1. Worker thread calls one of the read methods above (e.g., `check_cache()` with cache miss)
2. Read method executes SELECT, starting an implicit transaction
3. Read method returns WITHOUT committing or rolling back
4. **Transaction remains active on the thread-local connection**
5. Worker thread calls `get_next_job()` on the SAME connection
6. `get_next_job()` tries to execute `BEGIN IMMEDIATE` (line 200)
7. **ERROR**: "cannot start a transaction within a transaction"

### Why get_next_job() Uses Explicit BEGIN

```python
# Line 200
conn.execute("BEGIN IMMEDIATE")
```

The `BEGIN IMMEDIATE` is intentional for atomicity:
- Gets a lock immediately (not deferred)
- Prevents race conditions when multiple workers compete for the same job
- Atomically SELECTs and UPDATEs the job

But it assumes NO transaction is already active!

## Solutions

### Solution 1: Enable Autocommit Mode (RECOMMENDED)

Set `isolation_level = None` to enable autocommit mode:

```python
def _get_conn(self) -> sqlite3.Connection:
    if not hasattr(self._local, 'conn'):
        self._local.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None  # ← AUTOCOMMIT MODE
        )
        self._local.conn.row_factory = sqlite3.Row
    return self._local.conn
```

**Effect**:
- No implicit transactions
- Statements execute and commit immediately
- Explicit transactions require `BEGIN`/`COMMIT`/`ROLLBACK`
- Prevents transaction leaks

**Verification needed**:
- Ensure all write operations are properly wrapped in transactions
- Check that `get_next_job()` transaction logic still works

### Solution 2: Defensive Check Before BEGIN (SAFETY NET)

Check if transaction is active before starting a new one:

```python
def get_next_job(self, job_type: str, worker_id: Optional[int] = None) -> Optional[Job]:
    conn = self._get_conn()

    # Defensive: rollback any lingering transaction
    if conn.in_transaction:
        logger.warning("Found active transaction before get_next_job(), rolling back")
        conn.rollback()

    # Use transaction to atomically get and update job
    conn.execute("BEGIN IMMEDIATE")
    try:
        # ... rest of method
```

**Effect**:
- Prevents the error by cleaning up leaked transactions
- Logs warnings to identify bugs
- Works as a safety net even if other fixes are in place

### Solution 3: Add Rollback to Read Methods

Add explicit transaction cleanup to all read methods:

```python
def get_job(self, job_id: int) -> Optional[Job]:
    conn = self._get_conn()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

        if not row:
            return None

        return Job(...)
    finally:
        # Ensure transaction is closed (rollback is safe for reads)
        if conn.in_transaction:
            conn.rollback()
```

**Effect**:
- Explicit cleanup
- Prevents transaction leaks
- Verbose but foolproof

## Recommended Fix Strategy

**Multi-layered approach for robustness:**

1. **Enable autocommit mode** (Solution 1)
   - Prevents implicit transactions entirely
   - Clean, predictable behavior

2. **Add defensive check** (Solution 2)
   - Safety net in `get_next_job()`
   - Catches any remaining edge cases
   - Helps identify bugs through warnings

3. **Add rollback to critical read paths** (Solution 3 - selective)
   - Add to frequently-called reads like `check_cache()`
   - Belt-and-suspenders approach

## Testing Strategy

After implementing fixes:

1. **Run integration tests** to verify no regressions
2. **Check for warning logs** from defensive rollback
3. **Verify transaction behavior**:
   - Read operations don't leave transactions open
   - Write operations are properly committed
   - Atomic operations in `get_next_job()` still work correctly

## Implementation Priority

1. **High**: Enable autocommit mode (Solution 1)
2. **High**: Add defensive check (Solution 2)
3. **Medium**: Add rollback to `check_cache()` cache-miss path
4. **Low**: Add rollback to other read methods (as needed)

## Expected Outcome

After implementation:
- ✅ No more "cannot start a transaction within a transaction" errors
- ✅ All integration tests pass
- ✅ Worker threads can safely call read methods followed by `get_next_job()`
- ✅ Transaction management is explicit and predictable
