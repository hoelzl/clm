- **`[external_tools]` config-file settings now take effect.** `plantuml_jar`
  and `drawio_executable` set in `clm.toml` / `.clm/config.toml` were previously
  ignored — only the raw `PLANTUML_JAR` / `DRAWIO_EXECUTABLE` env vars reached
  the converters. The host now resolves each through the config system
  (env var > config file) and injects the effective value into **Direct**
  workers, so a config-file path is honored (the env var still wins when both
  are set). Docker workers continue to use the tools baked into the worker
  image. This is Phase 2 of the config/CLI/env unification
  (`docs/proposals/config-cli-precedence-unification.md`).
