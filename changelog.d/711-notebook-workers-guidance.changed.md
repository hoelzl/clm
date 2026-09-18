- **`clm info commands` now carries the notebook-worker guidance from the
  #711 cache-performance investigation.** The `--notebook-workers` row
  explains that notebook jobs — including the Recording/Speaker HTML renders
  that warm the executed-notebook cache — run serially per worker, that the
  default is one worker (`worker_management.default_worker_count`), and that
  on a large course this is the single biggest wall-clock lever (one measured
  rebuild: 12.5 min with 8 workers vs more than 29 min, unfinished, with 1).
  The investigation's remaining candidates — raising the default, and
  per-file dependency hashing — are recorded in `docs/claude/TODO.md`.
