- Fixed intermittent `worker died mid-job (orphaned at pool shutdown)` failures
  in an otherwise uninterrupted build (issue #617). When a worker process died
  or hung mid-job, nothing marked a *busy* worker dead, so the completion loop's
  dead-worker requeue never fired and the job lingered in `processing` until the
  teardown sweep stamped it an orphan — misattributed to an innocent slide file.
  The worker health monitor is now started for the build pool (scoped to the
  build's own session), so a worker whose process is gone is detected and its
  in-flight job is requeued for retry on another worker. In addition:
  `reset_hung_jobs` now clears `started_at` so a legitimately-requeued job is
  never mis-stamped as an orphan; job submission registers the job for the
  completion poll under `asyncio.shield`, so a cancelled submission can never
  leave a worker-claimable but untracked row; and any orphan still discovered at
  teardown is folded into the build summary (and forces a non-zero exit) instead
  of being silently banked in the jobs DB.
