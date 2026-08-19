- **`clm build` no longer prints its log to the terminal.** Every run dumped
  third-party `DEBUG` records on the way past — `docker.utils.config`,
  `docker.auth`, `urllib3.connectionpool` — and `--log-level=warning` did not
  stop them, because that flag sets the level of the `clm` logger and a
  `docker` record is not filtered there. Two things combined to cause it: the
  CLI's start-up `logging.basicConfig(level=INFO)` installed a console handler
  with **no level of its own** (so it emitted whatever the *logger* allowed),
  and `setup_logging` then opened the root logger to `DEBUG` so its file
  handler could capture everything — while never retiring that first handler.
  The console now shows **warnings and errors only**, which is what
  `--verbose-logging` has always advertised ("by default logs go to file
  only"); the full stream at `--log-level` goes to the rotating `clm.log`
  (10 MB × 3 backups) in the platform log directory, or under `CLM_LOG_DIR`.
  Pass `--verbose-logging` to echo it to the console as well. A *stricter*
  `--log-level` still applies to the console, so `--log-level=ERROR` hides
  warnings; a more permissive one does not, which is what `--verbose-logging`
  is for. Commands other than `build`, which do not configure a log file, now
  print warnings and errors instead of everything at `INFO`.
