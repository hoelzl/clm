- **`clm build` now honors the configured log level.** `[logging] log_level`
  in `clm.toml` and the `CLM_LOGGING__LOG_LEVEL` environment variable previously
  had no effect on a build — `--log-level` hard-defaulted to `INFO`, so the
  config value was always overridden. `--log-level` now defaults to "unset" and
  the effective level resolves as `--log-level` > `CLM_LOGGING__LOG_LEVEL` >
  `[logging] log_level` > `INFO`, applied to both host and worker logging. Phase
  3 of the config/CLI/env unification
  (`docs/proposals/config-cli-precedence-unification.md`).
