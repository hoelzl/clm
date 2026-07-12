# Handover: PR #636 Adversarial-Review Findings (worker liveness follow-ups)

**Status**: Not started — findings identified 2026-07-12, no fixes implemented yet.
This document is the source of truth for addressing the five findings from the
post-merge adversarial review of PR #636 (the issue #617 fix, merged as
`f96f8ab6` / commit `d5467df7`).

## 1. Feature Overview

PR #636 ("fix(build): recover in-flight jobs when a worker dies mid-build",
closes #617) wired up the previously-dormant `WorkerPoolManager` health
monitor in the build path so a worker that dies mid-job is marked `dead` and
its in-flight job is requeued, plus three hardening layers (`reset_hung_jobs`
clears `started_at`; job submission registers in `active_jobs` under
`asyncio.shield`; teardown orphans are folded into the build summary and force
a non-zero exit).

A post-merge adversarial review verified each layer against the merged code
and found that **activating the dormant monitor also activated a latent
timezone bug in its gating logic**, which makes the headline fix inert on
machines west of UTC and noisy east of it — plus four smaller issues. This
handover plans the remediation. The full review text was delivered in-session
on 2026-07-12; the findings are restated completely below, so this document is
self-sufficient.

Related: issue #617 (the original bug), PR #636 (the fix under review),
issues #597/#620 (the session-ownership rule the monitor scoping mirrors,
`jobs.session_id` added in schema v11 by #620/PR #634).

## 2. Design Decisions

Decisions already implied by the review; each phase has latitude on details
but these anchors should hold:

- **Compare timestamps in UTC, not local time.** `workers.last_heartbeat` is
  written by SQLite `CURRENT_TIMESTAMP`, which is UTC. Any staleness math must
  parse it as UTC and compare against `datetime.now(timezone.utc)`. Do NOT
  "fix" this by switching the writer to localtime — other readers
  (`_get_available_workers` at `src/clm/infrastructure/backends/sqlite_backend.py:1270,1311`
  uses SQL-side `datetime('now')`, which is UTC and therefore *correct* today)
  depend on UTC in the DB.
- **Don't gate liveness on a heartbeat busy workers never send** (Finding 2).
  `last_heartbeat` is only updated while idle-polling or on the busy→idle
  transition (`src/clm/infrastructure/workers/worker_base.py:661-663,790`).
  Notebook jobs routinely run minutes, so "stale heartbeat while busy" is the
  *normal* state, not evidence of trouble. Preferred direction (decide in
  Phase 2): run the cheap `is_worker_running` process check unconditionally on
  every monitor cycle for own-session workers (it is `process.poll()` for
  pool-started direct workers — cheap), keep heartbeat staleness only as the
  trigger for the *expensive* Docker stats/hung heuristic, and demote the
  "stale heartbeat" log line to DEBUG for `busy` workers. Alternative
  (rejected as bigger/riskier): add a background heartbeat thread to
  `worker_base` — touches every worker type, adds a thread per worker, and
  the DB write contention was the reason heartbeats were throttled in the
  first place.
- **Session-scope the teardown orphan sweep the same way the monitor was
  scoped.** PR #636 scoped `_monitor_health` to `session_id` citing the
  #597/#620 ownership rule but left `mark_orphaned_jobs_failed` unscoped.
  `jobs.session_id` exists (schema v11), so filter on it when the caller has a
  session. Keep an unscoped fallback for `session_id is None` callers (mirrors
  the monitor's two-branch pattern) so `clm status`-style maintenance still
  works.
- **Orphan reporting should say "orphaned", not "timed out".** The exit path
  reuses the per-stage-timeout message and points at a summary rendered
  *before* the orphans were appended. Add a dedicated message; do not try to
  re-render the whole summary (finish_build has already run — re-rendering
  risks duplicate output and was implicitly rejected by PR #636's own design).

Trade-off accepted in PR #636 and NOT being reverted: orphans discovered at
teardown only affect the exit code and an echoed message, not the pretty
summary table. That is acceptable; Phase 4 only fixes the wording/visibility.

## 3. Phase Breakdown

### Phase 1 [TODO] — Fix the timezone bug in `_is_heartbeat_stale` (Finding 1, HIGH) — **ACTIVE PHASE**

- **What**: `_is_heartbeat_stale`
  (`src/clm/infrastructure/workers/pool_manager.py:946-963`) parses the UTC DB
  timestamp with `datetime.fromisoformat` (naive) and compares against local
  `datetime.now()`. West of UTC → age is negative → never stale → the #617
  liveness fix silently does nothing. East of UTC (the maintainer's CEST
  machine) → everything always looks ≥2h stale → gate permanently open (fix
  works by accident, with warning-log spam and Docker stats churn).
- **Fix**: treat the parsed timestamp as UTC
  (`heartbeat_time.replace(tzinfo=timezone.utc)` after `fromisoformat`, or
  parse then compare against `datetime.now(timezone.utc)`); handle a timestamp
  that already carries an offset defensively.
- **Also fix the tz-dependent test**:
  `tests/infrastructure/workers/test_pool_manager.py::test_monitor_health_reaps_only_own_session_dead_workers`
  seeds `last_heartbeat` with SQLite `datetime('now', '-120 seconds')` (UTC).
  It passes on UTC CI and east-of-UTC machines but **fails west of UTC**
  today. After the Phase 1 fix it becomes correct as-is (120s > 30s threshold
  in true UTC terms) — verify by temporarily faking a west-of-UTC comparison
  in a unit test of `_is_heartbeat_stale` itself (pass fixed timestamps; do
  not depend on the host TZ).
- **Add direct unit tests** for `_is_heartbeat_stale`: fresh UTC timestamp →
  not stale; 2-minute-old UTC timestamp → stale; malformed → stale. These must
  be host-TZ-independent.
- **Acceptance**: unit tests pass; existing monitor test passes; grep for
  other local-vs-UTC comparisons against DB timestamps in
  `pool_manager.py`/`discovery.py` (`discovery.py:183` compares against
  `datetime.now(timezone.utc)` and force-tags the parsed value with
  `timezone.utc` at `discovery.py:135` — that is the correct pattern to copy).

### Phase 2 [TODO] — Stop gating liveness on heartbeats busy workers never send (Finding 2, MEDIUM)

- **What**: once Phase 1 makes staleness *accurate*, every busy worker on a
  >30s job legitimately trips the stale gate each 10s cycle:
  warning-per-worker-per-cycle log spam, and in Docker mode a stats pull every
  5th cycle feeding the pre-existing "CPU < 1% and busy → mark `hung`"
  heuristic — a container waiting on I/O (or an LLM call) can be marked
  `hung`, which removes it from `_get_available_workers` counts
  (`sqlite_backend.py:1233,1270`).
- **Fix direction** (see Design Decisions): in `_monitor_health`
  (`pool_manager.py:826-944`) run `is_worker_running` unconditionally for
  own-session workers each cycle; keep the heartbeat-staleness condition only
  around the Docker stats/hung branch, and require staleness *plus* CPU<1%
  *plus* `busy` (as today) there; demote the "stale heartbeat" warning to
  DEBUG when `status == 'busy'` (a stale *idle* worker still warrants a
  WARNING — idle workers heartbeat every ~2s, so staleness there is real
  signal).
- **Acceptance**: a busy direct worker on a 60s job produces zero WARNING
  lines and is never marked dead/hung (test with a fake executor reporting
  alive); a dead process is still reaped within one cycle; the Docker hung
  heuristic still fires for a genuinely stale+idle-CPU busy container.

### Phase 3 [TODO] — Session-scope `mark_orphaned_jobs_failed` (Finding 3, MEDIUM)

- **What**: `JobQueue.mark_orphaned_jobs_failed`
  (`src/clm/infrastructure/database/job_queue.py:415-` , SELECT at :451-459)
  reaps **every** non-terminal started job in a shared jobs DB. Concurrent
  builds A and B: A finishing first (a) marks B's genuinely in-flight jobs
  `failed` with the orphan message — breaking B (pre-existing bug), and (b)
  folds B's jobs into **A's** summary and forces A to exit non-zero for jobs
  that were never its own (new noise added by PR #636's reporting layer).
- **Fix**: add an optional `session_id` parameter; when provided, add
  `AND session_id = ?` to the orphan SELECT (and keep the UPDATE keyed by the
  selected ids, unchanged). `WorkerLifecycleManager.stop_managed_workers`
  (`src/clm/infrastructure/workers/lifecycle_manager.py:~292`) passes its own
  `self.session_id`. Preserve the unscoped behavior when `session_id is None`.
- **Tests**: extend `tests/infrastructure/database/test_job_queue.py` — seed
  in-flight jobs for sessions A and B, call with session A, assert only A's
  job is failed/returned and B's row is untouched. Extend
  `tests/infrastructure/workers/test_lifecycle_manager.py::test_stop_returns_orphans_for_the_build_to_surface`
  with a foreign-session in-flight row that must NOT appear in the returned
  orphans.
- **Gotcha**: check `_seed_inflight_job` in `test_lifecycle_manager.py` — it
  must stamp a session_id matching the manager's for the existing tests to
  keep passing; the manager's session_id comes from `mock_config`/ctor, so
  trace how `WorkerLifecycleManager.session_id` is populated first.

### Phase 4 [TODO] — Honest orphan exit message (Finding 4, LOW)

- **What**: the exit block at `src/clm/cli/commands/build.py:2662-2669` prints
  "one or more worker jobs **timed out** … See the error summary **above**"
  for orphans too — neither clause is true for them (orphans are appended
  *after* finish_build rendered the summary, so nothing is above; and they
  didn't time out). The orphan `BuildError.message`/`actionable_guidance`
  written by `_record_teardown_orphans` (`build.py:1667-1710`) are never
  displayed anywhere; the only human-readable trace is a log-file-only
  `logger.warning`.
- **Fix**: distinguish the two cases at exit time. Simplest robust approach:
  in the exit block, partition `summary.errors` for `category ==
  "orphaned_job"`; if any exist, `click.echo` a dedicated message with the
  count and the orphaned input files (data is already on the BuildError
  objects). Keep `timed_out = True` as the exit-forcing mechanism (do NOT
  introduce a new summary flag unless it stays trivially small — the exit
  policy ordering at :2650-2680 is issue-#90/#143-sensitive; read those
  comments first).
- **Tests**: extend `tests/cli/test_build_abort_summary.py` — after
  `_record_teardown_orphans`, whatever helper renders the exit message must
  mention "orphaned" and the file names, not "timed out" (shape depends on
  how you factor the message; a small pure helper like
  `_format_exit_failure(summary) -> str` makes this testable without a CLI
  run).

### Phase 5 [TODO] — Minor loose ends (Finding 5, LOW)

1. **Monitor join guarantee overstated**: `stop_pools`
   (`pool_manager.py:1044-1056`) joins the monitor with `timeout=5` while the
   loop sleeps `check_interval=10` — the join frequently times out and the
   daemon thread lingers up to one interval. Either fix the docstring/comment
   in `lifecycle_manager.py:231-239` ("stopped/joined by stop_pools()" — soften
   to best-effort), or make the sleep interruptible (e.g. `threading.Event`
   `self._stop_event.wait(check_interval)` set in `stop_pools`, then
   `join(timeout=check_interval + 1)`). The Event approach is small and makes
   the docs true — prefer it.
2. **Reused-worker liveness gap**: when all workers are reused (`auto_stop`
   disabled / shareable workers), `pool_manager` is never created
   (`lifecycle_manager.py:197-200`) so no monitor runs; shareable workers from
   another session are excluded by the session filter anyway. Decision: accept
   and document (comment near the reuse early-return + a note in issue #617's
   close-out), or file a separate issue. Do NOT try to monitor foreign-session
   workers — that violates the ownership rule.
3. **Shield exception noise**: in
   `src/clm/infrastructure/backends/sqlite_backend.py` (`_execute_operation_impl`,
   the `await asyncio.shield(_submit_and_track())` at ~:280 of the diff /
   search for `_submit_and_track`), if the caller is cancelled and the inner
   task then raises, asyncio logs "exception was never retrieved" at teardown.
   Wrap: create the task explicitly (`task = asyncio.ensure_future(...)`), add
   a done-callback that retrieves and debug-logs the exception when the task
   was abandoned, then `await asyncio.shield(task)`.

### Phase 6 [TODO] — Ship

- One PR per phase is overkill; suggested packaging: **PR A** = Phases 1+2
  (monitor correctness — one coherent story), **PR B** = Phase 3 (orphan sweep
  scoping), **PR C** = Phases 4+5 (reporting + polish). Each PR: changelog
  fragment in `changelog.d/` (type `fixed`), branch prefix `claude/`,
  fast suite green, push + PR without asking (per AGENTS.md). No `clm info`
  topics are affected (no CLI/spec surface changes) unless Phase 4 changes
  user-visible CLI output semantics — it changes an error message only, which
  needs no info-topic update.
- Consider filing GitHub issues for Findings 1–3 first and referencing them
  from the PRs, so the fixes are traceable from #617/#636.

## 4. Current Status

- **Completed**: adversarial review of merged PR #636 (2026-07-12), all five
  findings verified directly against the code at `f96f8ab6` (no fixes
  written). The review confirmed what *does* hold up: `reset_hung_jobs`
  clearing, shield semantics, missing-executor skip, and `stop_managed_workers`
  returning `[]` on all early paths — none of those need changes.
- **In progress**: nothing.
- **Blockers / open questions**:
  - Phase 2 design choice (unconditional process check vs. background
    heartbeat thread) — recommendation is the former; confirm with maintainer
    only if they push back in PR review.
  - Phase 5.2 (reused-worker gap): accept-and-document vs. new issue —
    default to accept-and-document.
- **Tests**: repo suite green at `f96f8ab6` (PR #636's CI passed). NOTE: the
  new monitor test is latently host-TZ-dependent (see Phase 1) — it passes
  here and on CI but would fail west of UTC; that is itself part of Finding 1.
- **Worktree note**: review was done in worktree `ancient-painting-charm`,
  whose branch `worktree-ancient-painting-charm` was reset to `origin/master`
  (`f96f8ab6`). Start fix work by branching off it:
  `git switch -c claude/pr636-review-findings` (never switch a worktree to
  literal `master`).

## 5. Next Steps

**Start Phase 1.** Concretely:

1. `git switch -c claude/pr636-review-findings-phase1` (or per-phase naming).
2. Fix `_is_heartbeat_stale` (`pool_manager.py:946-963`) to compare in UTC —
   copy the pattern at `discovery.py:135,183`
   (`datetime.fromisoformat(...).replace(tzinfo=timezone.utc)` vs
   `datetime.now(timezone.utc)`).
3. Add host-TZ-independent unit tests for `_is_heartbeat_stale` in
   `tests/infrastructure/workers/test_pool_manager.py`.
4. Re-run `test_monitor_health_reaps_only_own_session_dead_workers` and the
   monitor start/stop tests; then continue into Phase 2 in the same PR.
5. Gotchas: do not change the DB write side (UTC in DB is load-bearing for
   `_get_available_workers`); `datetime.fromisoformat` accepts SQLite's
   space-separated format on Python ≥3.11 (project floor is 3.12 — fine);
   parse errors must still return `True` (stale) as today.

## 6. Key Files & Architecture

Files the fixes will touch (none modified yet):

- `src/clm/infrastructure/workers/pool_manager.py` — `_monitor_health`
  (:826-944, the monitor loop: session-scoped SELECT, stale gate, process
  check, Docker hung heuristic), `_is_heartbeat_stale` (:946-963, **the tz
  bug**), `start_monitoring` (:810), `stop_pools` (:1044, join timeout 5s vs
  10s sleep).
- `src/clm/infrastructure/workers/worker_base.py` — heartbeat writer
  (`_update_heartbeat` :265-286, UTC via SQL `CURRENT_TIMESTAMP`; called only
  when idle :661-663 and on busy→idle :790). Phase 2 should NOT need to touch
  this if the unconditional-process-check direction is taken.
- `src/clm/infrastructure/database/job_queue.py` —
  `mark_orphaned_jobs_failed` (:415-, SELECT :451-459; Phase 3 adds session
  filter), `reset_hung_jobs` (:673, fixed by #636 — leave alone).
- `src/clm/infrastructure/workers/lifecycle_manager.py` —
  `start_managed_workers` (monitor start + its comment :231-239),
  `stop_managed_workers` (:250-321, calls the sweep at ~:292 and returns
  orphans; Phase 3 passes `self.session_id`).
- `src/clm/cli/commands/build.py` — `_record_teardown_orphans` (:1667-1710),
  orphan fold in `main_build` finally (~:2020-2031), exit policy
  (:2650-2680 — ordering is #90/#143-sensitive; Phase 4 edits the
  `summary.timed_out` branch message).
- `src/clm/infrastructure/backends/sqlite_backend.py` — shielded
  `_submit_and_track` (search the name; Phase 5.3), `_cleanup_dead_worker_jobs`
  (:663, the requeue the monitor feeds — reference only),
  `_get_available_workers` (:1224-, the `hung`-excluding healthy-worker count
  that makes false `hung` marks harmful — reference only).
- `src/clm/infrastructure/workers/discovery.py` — :135,183: the *correct*
  UTC-comparison pattern to copy in Phase 1.
- Tests: `tests/infrastructure/workers/test_pool_manager.py` (monitor tests +
  new `_is_heartbeat_stale` units), `tests/infrastructure/database/test_job_queue.py`
  (Phase 3), `tests/infrastructure/workers/test_lifecycle_manager.py`
  (Phase 3), `tests/cli/test_build_abort_summary.py` (Phase 4).

Connection map: `main_build` → `WorkerLifecycleManager.start_managed_workers`
→ `WorkerPoolManager.start_pools` + `start_monitoring` (monitor thread marks
own-session dead workers) → the backend completion loop's
`_cleanup_dead_worker_jobs` requeues jobs of `dead` workers → at teardown
`stop_managed_workers` → `stop_pools` then `mark_orphaned_jobs_failed` →
orphans returned to `main_build` → `_record_teardown_orphans` → exit policy.

Convention established by #636 that MUST be continued: any monitor/sweep that
mutates worker or job rows must be scoped to the build's own `session_id`
(the #597/#620 ownership rule); a missing executor or unverifiable worker is
*skipped*, never reaped.

## 7. Testing Approach

- Unit-level, in the existing test files listed above; the monitor tests use
  the real SQLite schema via the `db_path` fixture plus `MagicMock` executors
  and a real `_monitor_health` thread (see
  `test_monitor_health_reaps_only_own_session_dead_workers` for the pattern:
  seed worker rows, run the thread with a 0.05s interval, poll the DB with a
  deadline, always stop via `manager.running = False` + join in `finally`).
- All new time-related tests must be **host-TZ-independent**: pass explicit
  timestamps into `_is_heartbeat_stale`, or seed DB rows via SQLite
  `datetime('now', '-Xs')` (UTC) and compare in UTC.
- Run: `pytest tests/infrastructure/workers/ tests/infrastructure/database/
  tests/cli/test_build_abort_summary.py` for the affected slice; full fast
  suite (`pytest`, ~72s) runs automatically on push via the pre-push hook.
  From this worktree, remember the memory note: run with `PYTHONPATH=src` if
  unrelated ImportErrors appear (main-venv drift).
- Still needing tests once written: everything in Phases 1–4 (each phase's
  acceptance criteria double as its test list). Phase 5 items 1 and 3 warrant
  a small test each (Event-based prompt monitor shutdown; abandoned-shield
  exception retrieved); 5.2 is documentation-only.

## 8. Session Notes

- The PR is **already merged**; nothing here is a revert. Frame all PRs as
  follow-ups to #617/#636.
- Why the bug shipped: the tz bug is *invisible* on UTC CI and *masked* on the
  maintainer's CEST machine (always-stale means the gate is permanently open
  and the accurate process check makes the final call — so #617's fix
  genuinely works there). It only bites west of UTC, where the monitor becomes
  a no-op. This is worth stating in the Phase 1 PR description.
- The heartbeat-only-when-idle behavior (Finding 2) also means the
  pre-existing `_get_available_workers` "heartbeat within 30s" filter
  undercounts busy workers on long jobs — pre-existing, out of scope, but if
  worker-availability weirdness shows up during Phase 2 testing, that is why.
- `mark_orphaned_jobs_failed`'s cross-session reaping (Finding 3a) predates
  #636; what #636 added is the *reporting* of foreign orphans into the wrong
  build's summary (3b). The phase fixes both with one filter.
