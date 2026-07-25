- **Repaired the two mixed-mode Docker worker tests unskipped by the T1 fix.**
  `test_mixed_worker_modes` hard-coded the image tag `drawio-converter:latest`,
  which CLM has not published since the images gained their `clm-` prefix — the
  Docker client tried to *pull* it and the test died on `ImageNotFound`. It now
  resolves the tag from the CI-built and published candidates the way the e2e
  lifecycle test does, and skips with a stated reason when no image is present
  instead of quietly degrading to a direct-only run.
  `test_stale_worker_cleanup_mixed_mode` inserted its "stale" worker rows
  without a `last_heartbeat`, so the column defaulted to the current timestamp
  and the direct worker was correctly judged *healthy* and kept; the rows now
  carry a genuinely old heartbeat.
