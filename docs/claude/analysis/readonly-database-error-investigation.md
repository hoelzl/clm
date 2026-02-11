# Readonly Database Error Investigation

## Error Message

```
ERROR:clm.infrastructure.workers.worker_base:Worker X failed to update heartbeat: attempt to write a readonly database
```

**Frequency**: Thousands of occurrences across all worker processes
**Location**: Multiple methods in `worker_base.py` and `job_queue.py`

## Root Cause Analysis

### The Problem

After implementing autocommit mode (`isolation_level=None`) to fix the SQLite transaction error, we now have a different issue: **manual `conn.commit()` calls fail in autocommit mode**.

### Why This Happens

When SQLite is in autocommit mode (`isolation_level=None`):
- **Every statement is automatically committed immediately** after execution
- **No implicit transaction is created**
- **Calling `conn.commit()` manually when there's no active transaction causes an error**

### Code Pattern Causing the Error

All these methods follow the same problematic pattern:

```python
def _update_heartbeat(self):
    try:
        conn = self.job_queue._get_conn()  # Connection in autocommit mode
        conn.execute(
            """UPDATE workers SET last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?""",
            (self.worker_id,)
        )  # ← Statement executes and autocommits immediately
        conn.commit()  # ← ERROR: No transaction to commit!
        self._last_heartbeat = datetime.now()
    except Exception as e:
        logger.error(f"Worker {self.worker_id} failed to update heartbeat: {e}")
```

### Files Affected

#### 1. `src/clm/infrastructure/workers/worker_base.py`

**Methods with manual commit calls:**
- `_update_heartbeat()` (line 84)
- `_update_status()` (line 101)
- `_update_stats()` (line 140)
- `_log_event()` (line 188)

**Pattern:**
```python
conn = self.job_queue._get_conn()
conn.execute("...")
conn.commit()  # ← Problem
```

#### 2. `src/clm/infrastructure/database/job_queue.py`

**Methods with manual commit calls (sample):**
- `add_job()` (line 116)
- `update_job_status()` - likely has same issue
- `add_to_cache()` - likely has same issue
- `check_cache()` (line 156) - has commit on cache hit path

**Exception:**
- `get_next_job()` - correctly uses explicit `BEGIN IMMEDIATE` transaction (lines 206-238)

## Solutions

### Option 1: Remove Manual Commit Calls (RECOMMENDED)

In autocommit mode, simply remove all `conn.commit()` calls except where explicit transactions are used.

**For worker_base.py:**

```python
def _update_heartbeat(self):
    """Update worker heartbeat in database."""
    try:
        conn = self.job_queue._get_conn()
        conn.execute(
            """
            UPDATE workers
            SET last_heartbeat = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (self.worker_id,)
        )
        # No commit() needed - autocommit mode handles it
        self._last_heartbeat = datetime.now()
    except Exception as e:
        logger.error(f"Worker {self.worker_id} failed to update heartbeat: {e}")
```

**For job_queue.py:**

```python
def add_job(self, ...):
    conn = self._get_conn()
    cursor = conn.execute(
        """INSERT INTO jobs (...) VALUES (...)""",
        (...)
    )
    # No commit() needed - autocommit mode handles it
    job_id = cursor.lastrowid
    return job_id
```

**Exception - Keep explicit transactions:**

```python
def get_next_job(self, ...):
    conn = self._get_conn()

    # Defensive check (from previous fix)
    if conn.in_transaction:
        conn.rollback()

    # Explicit transaction for atomicity
    conn.execute("BEGIN IMMEDIATE")
    try:
        # ... atomic operations ...
        conn.commit()  # ← KEEP this - explicit transaction
        return job
    except Exception:
        conn.rollback()
        raise
```

### Option 2: Use Explicit Transactions Everywhere

Wrap every write operation in explicit BEGIN/COMMIT:

```python
def _update_heartbeat(self):
    try:
        conn = self.job_queue._get_conn()
        conn.execute("BEGIN")
        conn.execute("UPDATE workers SET ...", (...))
        conn.commit()
        self._last_heartbeat = datetime.now()
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        logger.error(f"Worker {self.worker_id} failed to update heartbeat: {e}")
```

**Downside**: More verbose, adds transaction overhead to simple operations.

### Option 3: Hybrid Approach

- Use autocommit for simple, single-statement operations (heartbeat, status updates)
- Use explicit transactions only for multi-statement operations requiring atomicity (get_next_job)

This is essentially Option 1, which is the cleanest.

## Recommended Fix Strategy

**Implement Option 1 (Remove Manual Commits)**:

1. **Remove `conn.commit()` from worker_base.py methods:**
   - `_update_heartbeat()`
   - `_update_status()`
   - `_update_stats()`
   - `_log_event()`

2. **Remove `conn.commit()` from job_queue.py methods:**
   - `add_job()`
   - `update_job_status()`
   - `add_to_cache()`
   - `check_cache()` (cache hit path)
   - Any other simple write operations

3. **Keep `conn.commit()` ONLY in methods with explicit transactions:**
   - `get_next_job()` - has `BEGIN IMMEDIATE`
   - Any other method that explicitly calls `BEGIN`

4. **Update check_cache() defensive rollback:**
   - The cache miss rollback (line 161) is still valid - ensures cleanup

## Testing Strategy

After implementing the fix:

1. **Run integration tests** to verify no readonly database errors
2. **Check worker heartbeats** are updating correctly
3. **Verify atomic operations** still work (get_next_job)
4. **Monitor logs** for any new errors

## Expected Outcome

- ✅ No more "attempt to write a readonly database" errors
- ✅ Workers update heartbeats successfully
- ✅ Atomic operations remain atomic
- ✅ Simpler, cleaner code (less manual transaction management)
- ✅ All integration tests pass (or at least no new failures)

## Implementation Notes

### Why This Works

With `isolation_level=None`:
- **Single statements**: Auto-committed immediately, no manual commit needed
- **Explicit transactions**: Must use BEGIN/COMMIT explicitly for atomicity
- **No implicit transactions**: Prevents the original "cannot start a transaction within a transaction" error

### Why the Original Fix Caused This

The original autocommit mode fix (Phase 8) correctly identified the problem (implicit transactions), but didn't update all the code to work with autocommit mode. The code was written assuming implicit transaction mode, where every operation needed a manual commit.

### Clean Separation

- **Autocommit mode**: For simple, independent operations (status updates, logging)
- **Explicit transactions**: For complex, multi-step operations requiring atomicity (get_next_job)

This gives us the best of both worlds: simplicity for simple ops, atomicity where needed.

---

**Date**: 2025-11-15
**Author**: Claude
**Related Documents**:
- `sqlite-transaction-error-investigation.md` - Original transaction error investigation
- `test-environment-setup-summary.md` - Overall work summary
