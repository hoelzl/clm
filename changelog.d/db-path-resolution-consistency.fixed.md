- **Build-end cache cleanup now follows `--cache-db-path`.** The end-of-build
  "prune stale executed-notebook hashes" step located the cache DB via a
  separate, CLI-disconnected config field (`get_config().paths.cache_db_path`)
  instead of the path the build actually opened. When `--cache-db-path` /
  `CLM_CACHE_DB_PATH` was overridden — or a build ran from a topic
  subdirectory — it pruned the wrong file (or created a stray `clm_cache.db`
  in the cwd) and never pruned the real cache. It now uses the backend's own
  cache DB path.
- **`clm status` / `clm monitor` honor `CLM_JOBS_DB_PATH`.** They previously
  auto-detected the jobs DB via the legacy `CLM_DB_PATH` only, so a build
  redirected with `CLM_JOBS_DB_PATH` (e.g. onto a RAM disk) left them opening a
  different, idle database. `CLM_JOBS_DB_PATH` now takes precedence, with
  `CLM_DB_PATH` kept as a fallback.
