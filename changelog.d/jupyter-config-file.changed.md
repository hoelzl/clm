- **`[jupyter]` config-file settings now take effect.** `jinja_line_statement_prefix`,
  `jinja_templates_path`, and `log_cell_processing` set in `clm.toml` (or via
  `JINJA_LINE_STATEMENT_PREFIX` / `JINJA_TEMPLATES_PATH` / `LOG_CELL_PROCESSING`)
  were previously ignored — the notebook worker read only the raw env vars, and
  the host never injected the config-folded value. The host now injects the
  resolved settings (env > config file > default, via `ClmConfig.jupyter`) into
  both Direct and Docker notebook workers. `log_cell_processing` is normalized to
  the exact `True`/`False` the worker compares against (a prior `LOG_CELL_PROCESSING=true`
  lowercase env value silently did nothing). Phase 4 of the config/CLI/env
  unification (`docs/proposals/config-cli-precedence-unification.md`).
