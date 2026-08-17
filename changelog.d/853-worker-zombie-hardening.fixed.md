- Hardened the worker/job-queue plumbing against leaked "zombie" workers
  (#853). Worker modules (`python -m clm.workers.*`) now parse their command
  line: `--help` prints the environment-variable contract and exits instead of
  silently starting a real job-claiming worker, and any other argument is
  rejected. A worker whose `workers` row has been deleted (pool stop or
  stale-row cleanup) is no longer handed jobs with the session-ownership
  filter silently dropped — it gets nothing, notices the missing row on its
  next heartbeat/status write, and shuts itself down. `cleanup_stale_workers`
  no longer deletes the row of a live worker it merely cannot see: rows whose
  heartbeat (either the idle-loop channel or the per-cell
  `worker_heartbeats` channel) is fresh are kept.
