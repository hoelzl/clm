- Fixed the worker health monitor's heartbeat staleness check comparing the
  UTC database timestamp against local time (follow-up to issue #617 /
  PR #636). West of UTC every heartbeat looked perpetually fresh, silently
  disabling the mid-build worker-liveness recovery; east of UTC everything
  always looked stale, spamming warnings. Staleness is now computed in UTC.
  The monitor also no longer gates its process-liveness check on heartbeat
  staleness: busy workers only heartbeat between jobs, so a stale heartbeat is
  the normal mid-job state — liveness is now checked unconditionally each
  cycle, stale-heartbeat log lines are DEBUG for busy workers (still WARNING
  for idle ones), and the Docker zero-CPU "hung" heuristic still requires a
  stale heartbeat before pulling container stats.
