- **Worker-registration timeouts now say what each worker did (#847).**
  `WorkerPoolManager.describe_workers()` reports, per worker the pool started,
  its database row (status, last heartbeat), whether its process or container
  is still alive, and the tail of its log; `DirectWorkerExecutor` gained
  `get_container_logs()` (the per-worker log file tail, for parity with the
  Docker executor) so the report works in both modes. The direct-worker
  integration tests' registration wait attaches that report to its
  `TimeoutError`, and the lifecycle tests' healthy-worker wait accepts a
  describer — a "expected 2 active workers, got 0" under xdist load can now
  be told apart from a worker that died on import, whose traceback lives only
  in that log. This was the remaining item of the rotating-xdist-flake issue;
  its timing shapes were fixed in PRs #848 and #925.
