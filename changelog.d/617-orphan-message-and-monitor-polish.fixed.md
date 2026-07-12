- The build's exit message now distinguishes teardown-orphaned jobs from
  per-stage timeouts: instead of the misleading "one or more worker jobs
  timed out … see the error summary above", orphaned jobs get a dedicated
  message naming the affected input files (#617/#636 follow-up, Finding 4).
- `stop_pools()` now interrupts the health monitor's between-cycle wait via a
  stop event, so the monitor thread is actually joined at teardown instead of
  lingering up to one check interval (#617/#636 follow-up, Finding 5.1).
- A job submission abandoned by a cancelled caller no longer triggers
  asyncio's "exception was never retrieved" warning at teardown; the shielded
  task's exception is retrieved and debug-logged (#617/#636 follow-up,
  Finding 5.3).
