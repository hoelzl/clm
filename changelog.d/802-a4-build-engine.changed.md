- Extracted the build orchestration out of the `clm build` Click command into
  a new `clm.build` package (#802/A4): `run_build(BuildConfig)` is the
  programmatic equivalent of `clm build` — callable by MCP tools, the web
  studio, and tests without importing any CLI module. The reporter, output
  formatters, stray-file sweep, and git-dir mover moved from `clm.cli` to
  `clm.build`; the CLI command is now a thin adapter (parsing, `.env`
  loading, signals, logging setup, exit-code policy, watch runner).
  `clm.build` joins the import-linter layer contracts above `clm.workers`.
  CLI behavior is unchanged.
