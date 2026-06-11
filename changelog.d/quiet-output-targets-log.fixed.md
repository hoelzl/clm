- **Quieter CLI startup.** The `Output targets: [...]` log line emitted by
  `Course.from_spec` / `Course.process_all` was demoted from INFO to DEBUG, so
  commands that don't reconfigure logging (e.g. `clm export …`, `clm calendar`)
  no longer print it to the console. `clm build` now shows the target names as
  a dimmed startup message instead.
