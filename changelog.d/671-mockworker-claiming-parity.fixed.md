- **`MockWorker` now claims jobs through the real `JobQueue.get_next_job`.** Its
  hand-rolled `UPDATE … RETURNING` had drifted from the production claim path in
  six ways — no `execution_mode` filter (PR #564's cross-mode job-theft guard),
  no session-ownership filter (issue #620), no `attempts < max_attempts` guard,
  no `attempts` increment, no `started_at` stamp and no `priority` ordering —
  and wrote the container-id *string* into the integer `jobs.worker_id` column.
  Terminal status now goes through `update_job_status` for the same reason. A
  new `test_mock_worker_claiming_parity.py` pins each recovered property so the
  fixture cannot drift again.
