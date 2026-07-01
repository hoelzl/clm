- **Removed the vestigial `[paths]` config section and its `CLM_PATHS__*`
  environment variables.** `CLM_PATHS__CACHE_DB_PATH` / `CLM_PATHS__JOBS_DB_PATH`
  / `CLM_PATHS__WORKSPACE_PATH` (and the `[paths]` block in `clm.toml` /
  `.clm/config.toml`) never actually relocated the databases a command opened —
  those paths come from the global `--cache-db-path` / `--jobs-db-path` /
  `--telemetry-db-path` options (and their `CLM_*_DB_PATH` env vars). The config
  section only surfaced in `clm config show`, misleadingly displaying hardcoded
  defaults rather than the effective paths. `clm config show` now reports the
  actual resolved database paths under a `[Databases]` heading. Old config files
  with a leftover `[paths]` section keep loading (the section is ignored).
