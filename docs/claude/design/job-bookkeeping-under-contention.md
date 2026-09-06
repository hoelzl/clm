# Job Bookkeeping Under Contention (issue #917)

**Status**: implemented (PR for #917). **Scope**: `SqliteBackend`,
`DatabaseManager`, `worker_base`, the new `worker_liveness` and `busy_retry`
modules.

## Problem

On a loaded Windows host, multi-worker builds of two unchanged, validating
course specs produced four symptoms in one afternoon (clm 1.27.0):

1. Hundreds of `Job #N completed but not found in tracked jobs` warnings,
   build otherwise fine (8 workers).
2. `RuntimeError: No workers available to process 'notebook' jobs` in a late
   stage of a build whose pool was healthy; identical retry succeeded
   (4 workers).
3. `sqlite3.OperationalError: database is locked` on
   `INSERT INTO processed_files`, followed by a frozen progress counter
   (16 workers).
4. mitmproxy startup timeout (separate subsystem, already has a knob).

All three SQLite symptoms are races in the bookkeeping, not in the content.
This note records the root cause of each and the invariant that now
prevents it. The original analysis and the regression tests live in
`tests/infrastructure/backends/test_sqlite_backend_bookkeeping_races.py`.

## Root causes and invariants

### 1. Two registrations, two event-loop turns

`_execute_operation_impl` registered a submitted job in `active_jobs` inside
the shielded submit task (the #617 fix) and in the `ProgressTracker` only
after the caller's `await asyncio.shield(...)` resumed. Between those two
loop turns the completion poll loop can run; a job a fast worker has already
completed is retired and reported to a tracker that never saw it. Besides
the warning, the tracker's `completed` count and the progress bar stay short
for the rest of the stage.

**Invariant**: a job is never visible to the poll loop before the tracker
knows it. `_register_submitted_job` performs both registrations, tracker
first, in one synchronous step inside the shielded task. No `await` may sit
between them. Pinned structurally by
`test_job_is_tracked_before_it_becomes_visible_to_the_poll_loop` and
behaviourally (with `poll_interval=0`, which forces the interleaving) by
`test_instant_completion_is_counted_by_progress_tracker`.

### 2. Two liveness rules

The pool's health monitor treats a worker row's *status* as the liveness
authority: it polls the worker process each cycle and marks a vanished one
`dead`; a stale `workers.last_heartbeat` on a `busy` row is documented as
normal, because workers refresh that column only while idle-polling and
between jobs. The submission gate `_get_available_workers` required a
heartbeat under 30 s. So once every worker of a type was mid-job for longer
than that (routine for executed notebooks, and worse when heartbeat UPDATEs
queue behind a contended jobs-DB lock), a submission counted zero live
workers, found nothing `created` to wait for, and raised. The message also
claimed a "10 second" window that existed nowhere in the code.

**Invariant**: one liveness rule, in `clm.infrastructure.database.
worker_liveness`, mirroring the claim rule of `JobQueue.get_next_job`
(#620):

| worker row | counts as available when |
|---|---|
| owned by this build session | status is `idle`/`busy` (heartbeat age irrelevant: this process's monitor marks it `dead`) |
| unowned (legacy / external) | status `idle`/`busy` **and** a heartbeat within 120 s on either channel (`workers.last_heartbeat` or the per-cell `worker_heartbeats` beacon) |
| owned by another session | never (it will never claim our jobs) |

Execution mode is matched as before. The 120 s grace is the same constant
the pool manager's stale-row cleanup uses, so "alive" means one thing.
Pre-registered (`created`) workers are still waited for and, after the
activation timeout, still marked dead as startup casualties (#348).

Why not also fail fast from the completion loop when a pool dies mid-stage?
Considered and deliberately left out: a pool that dies after all
submissions are in already fell through to the stall detector (#851) before
this change, and a pool that dies during submission is still caught by the
gate as soon as the monitor marks the rows `dead`. No regression, no new
mechanism.

### 3. Loop-thread cache writes, and a lossy writer

Under 16 workers the cache DB (`processed_files`, `processing_issues`,
`executed_notebooks`) is written by every worker plus the build. The
completion loop ran `clear_issues`, `store_warning` and `store_error`
inline on the event loop — each a write that can wait the full 30 s
`busy_timeout` — and the progress bar only advances from that loop. The
background result-cache writer committed one transaction per row and, on
`database is locked`, logged and *dropped* the row: the cache entry was
gone, so the next build re-executed that notebook. Worker-side, a job's
terminal `completed` UPDATE failing on the same contention fell into the
processing-failure handler, whose `failed` UPDATE failed too, leaving the
job in `processing` on a live worker until the stall detector fired.

**Invariants**:

- No blocking database write runs on the event loop in the completion
  path. All cache-DB writes go through `_enqueue_cache_write` (FIFO, one
  writer thread, so "clear, then store" ordering per job is preserved);
  the dead-worker sweep (`BEGIN IMMEDIATE` on the jobs DB) runs on the
  submit thread.
- The writer commits queued writes in batches under one lock acquisition
  (`DatabaseManager.batch`: per-method commits become no-ops inside an
  explicit `BEGIN IMMEDIATE … COMMIT`) and retries a batch that hits a
  transient lock error (`busy_retry.retry_on_busy`, rollback + backoff)
  before dropping it — loudly.
- A worker's terminal status write is wrapped in the same bounded retry
  (`_write_terminal_status`).

`retry_on_busy` retries only `sqlite3.OperationalError`s whose message is a
lock/busy condition; everything else propagates from the first attempt.

## Hardening from the adversarial review

The first cut of the above was reviewed adversarially (10 findings, all
acted on). The resulting rules are part of the design, not afterthoughts:

- **The writer thread must never die.** `_run_cache_write_batch` lets
  nothing escape: lock errors are retried then dropped loudly, any other
  database error is logged and dropped. `DatabaseManager.batch` rolls back
  when `COMMIT` itself raises (SQLite leaves the transaction open in that
  case) and clears a stale open transaction before `BEGIN`, so one failed
  commit cannot poison every later batch on the connection.
- **Two-phase result writes.** `_ResultCacheWrite.prepare()` (jobs-DB
  lookup, output-file read, `Result` construction) runs *before* the batch
  transaction opens; a prepare failure drops that one item. No file I/O and
  no other database's lock error ever happens while the cache-DB write lock
  is held or rolls back a batch of unrelated rows.
- **A bookkeeping failure is not a processing failure.** The worker's
  `completed` write sits in the `else` of the processing `try`: when it
  fails even after retries, the job stays `processing` for the host's stall
  handling instead of being recorded (and cached, and replayed) as a failed
  build of a file that built fine.
- **Retries stay inside the heartbeat grace.** The default schedule is four
  attempts (each already bounded by `busy_timeout`), and the worker refreshes
  its liveness heartbeat between attempts (`on_retry`), so a starved write
  never makes another build's stale-row cleanup delete the worker's row.
- **Retry lives where SQLite is touched.** `JobQueue.add_job` and
  `check_cache` retry internally (a single INSERT / a rolled-back
  `BEGIN IMMEDIATE` probe are safe to repeat), so a starved submission no
  longer tears down the stage `TaskGroup`; in Docker/API mode the host-side
  route that performs the terminal status write retries, because the worker
  only ever sees an HTTP 500.
- **The sweep has its own thread.** The dead-worker sweep runs on a dedicated
  single-thread maintenance executor, not the submit thread, so a submission
  backlog cannot park the awaiting poll loop.
- **Status-authoritative needs a watchdog for idle rows.** The health monitor
  now promotes an *idle* owned worker with no heartbeat on either channel for
  the whole grace period to `hung` (idle workers heartbeat every ~2 s; busy
  rows stay untouched), so an alive-but-wedged process cannot count as
  available forever.
- **Name the likely cause.** When the gate finds no claimable worker but
  live workers of *other* sessions exist (persistent workers left by an
  earlier build), the error says so instead of pointing at worker logs.

## What was deliberately not changed

- `busy_timeout` (30 s local / 60 s network) and WAL settings in
  `journal_mode.py`: the problem was not the wait but what happened after
  it, and where it happened.
- The "not found in tracked jobs" log level: with the root cause gone it
  is a genuine anomaly again and stays a WARNING.
- The build summary's `0 warnings` line counts content warnings only; the
  runtime warnings from symptom 1 no longer occur. Renaming that counter is
  a reporting decision outside this fix.
- The mitmproxy startup timeout (symptom 4) already names its knob
  (`CLM_MITM_STARTUP_TIMEOUT`); raising the default is a separate call.
