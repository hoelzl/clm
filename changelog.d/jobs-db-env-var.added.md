- **`CLM_JOBS_DB_PATH` / `CLM_CACHE_DB_PATH` / `CLM_TELEMETRY_DB_PATH` env vars.**
  The global `--jobs-db-path` / `--cache-db-path` / `--telemetry-db-path` options
  now also read these environment variables, so the databases can be relocated
  once instead of per-invocation. An explicit env path is honored verbatim (not
  re-anchored to the project root). The jobs DB is ephemeral — pointing
  `CLM_JOBS_DB_PATH` at a RAM disk (e.g. `Z:\clm_jobs.db`, Direct worker mode)
  spares the SSD without affecting the persistent cache or telemetry databases.
  (Note: the pre-existing `CLM_PATHS__*` variables only feed `clm config`
  display; these new `CLM_*_DB_PATH` forms are what a build/status/monitor run
  actually opens.)
