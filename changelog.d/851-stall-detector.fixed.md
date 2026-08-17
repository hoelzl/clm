- **Large healthy builds no longer abort at a hardcoded 20-minute completion
  cap** (#851). Waiting for a stage's worker jobs is now governed by a
  progress-aware stall detector: the build aborts only when *no* job has
  completed for `worker_management.job_stall_timeout` seconds (default 1200;
  every completion resets the clock), instead of when the whole batch exceeds
  a flat 1200 s deadline — which deterministically killed any cold build
  whose stage held more than 20 minutes of queued work. An absolute cap is
  still available as `worker_management.max_wait_for_completion`, but it is
  opt-in and unlimited by default; when set, the build warns early if the
  observed completion rate cannot drain the batch in time.
- On a stall/cap abort, jobs that were still queued are now reported honestly
  as "never started" instead of "the worker appears to be stuck processing
  this file" with per-file `jupyter execute` debugging advice — previously a
  single-worker capacity abort blamed hundreds of innocent files
  individually (#851). Only jobs a worker actually claimed keep the
  stuck-worker framing (#143).
- The exit-time failure message now leads with the abort cause (jobs gave up
  / breakdown of claimed vs never-started) instead of relabeling the
  collateral in-flight job "worker died mid-job (orphaned at pool shutdown)
  … See issue #617"; the #617 message is reserved for orphans that are not
  explained by the build's own abort (#851).
- `clm build` warns up front when a course with more than 50 files runs on a
  single notebook worker (the default), since notebook jobs then execute
  serially (#851).
