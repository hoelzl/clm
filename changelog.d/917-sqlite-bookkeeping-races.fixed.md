- **Multi-worker builds no longer lose jobs, abort on a busy pool, or drop
  cache writes under load** (#917). Three independent races in the SQLite
  job/worker bookkeeping were fixed at their root:
  - A submitted job is now registered with the progress tracker and the
    completion loop in one step (tracker first), so a job that a fast worker
    finishes before the event loop resumes is counted instead of producing
    `Job #N completed but not found in tracked jobs` and a progress bar that
    stayed short for the rest of the stage.
  - Worker availability uses one liveness rule shared with the pool's health
    monitor (`clm.infrastructure.database.worker_liveness`): a worker this
    build session owns counts while its status is idle/busy, regardless of
    heartbeat age — busy workers never heartbeat mid-job, so the old
    "heartbeat under 30 s" gate raised `No workers available` and killed a
    healthy build as soon as every worker was mid-job for more than 30 s.
    Unowned workers still need a fresh heartbeat (either channel, 120 s
    grace); another session's workers never count. The error message now
    describes the actual condition.
  - Every cache-DB write in the completion path (`clear_issues`,
    `store_warning`, `store_error`, the result blob) runs on the background
    writer thread in FIFO order, batched into one transaction per drain and
    retried with backoff on `database is locked` instead of being dropped —
    a dropped row silently re-executed that notebook on the next build. The
    dead-worker sweep also runs off the event loop. Workers retry a job's
    terminal `completed`/`failed` status write the same way (also on the
    host side for Docker/API-mode workers), as do `add_job` and the job-cache
    probe, so lock contention can no longer strand a finished job in
    `processing` until the stall detector fires or abort a build from a
    single starved submission. A failed `completed` write is never
    re-recorded as a failed build of the file, and the health monitor marks
    an idle worker that has been silent on both heartbeat channels for the
    whole grace period as `hung`.
