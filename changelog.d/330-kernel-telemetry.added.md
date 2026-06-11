- **Kernel execution telemetry + `clm kernel-triage`** (#330): notebook
  executions that fail or pass only after a retry are now recorded
  persistently in a per-deck telemetry database (`clm_telemetry.db` next to
  the cache db; override with the global `--telemetry-db-path`), including
  attempt count, failure type (`cell_execution_error` / `dead_kernel` /
  `startup_timeout` / `cell_timeout`), failing cell index, and a
  deterministic-vs-flaky classification. Decks that passed only after a
  retry are surfaced as a "Flaky decks" list in the build summary (JSON:
  `flaky_files`). The new `clm kernel-triage SPEC` command re-executes all
  `evaluate="no"` workaround topics plus all recorded flaky decks against
  the current kernel (real `clm build` in a throwaway environment) and
  reports which workarounds can be lifted — run it after every
  xeus-cpp/CppInterOp image bump.
