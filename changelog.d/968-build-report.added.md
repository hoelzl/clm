- `clm build --report FILE` (#968) writes the JSON build envelope — the
  document `--output-mode json` prints (`status`, `errors[]` with
  `actionable_guidance`, `warnings[]`, `stages`, `flaky_files`,
  `rebuild_reasons`, `output_conflicts`, the cache/execution error split,
  `log_directory`) — to `FILE` when the build ends, whatever the output
  mode, so an agent can read a build a human ran with live progress. The
  file adds `provenance_manifests` (the `.clm-manifest.json` paths written),
  `spec_file` and `report_written_at`, and is also written for a spec parse
  / validation failure (`status: "error"` / `"validation_failed"`), a
  timeout, an abort, and an exception that escapes before any summary
  (`status: "aborted"`). The envelope now carries `timed_out` and reports
  `status: "timed_out"` for a stall/cap abort or teardown orphans instead
  of `"success"`. stdout is unchanged; exit codes are unchanged. Part of
  #970.
