- Test suite: pytest's live-log handler no longer destroys Click `CliRunner`
  output capture. Emitting a log record inside `CliRunner.invoke()` used to
  rebind `sys.stdout`/`sys.stderr` to pytest's capture objects for the rest of
  the invocation (the handler suspends *and resumes* global capture around every
  record), so every CLI write after that record went missing from
  `result.output`. That failed six tests across the 2026-07-31 and 2026-08-08
  nightlies and was latent in all ~90 `CliRunner` modules. Live logging is now
  also skipped on xdist workers, where execnet swallows it anyway. Root-cause
  analysis: `docs/claude/design/test-flakiness-root-causes.md` (#821).
- `setup_logging()` retires only the root-logger handlers it installed itself,
  instead of clearing and closing every handler on the root logger. The old
  behaviour tore down (and closed) handlers belonging to an embedding
  application — including pytest's, which is what made the capture bug above
  look like a random 1-in-5 flake rather than a reproducible failure.
- E2E worker-lifecycle tests join the `serial("workerpool")` group, honour
  `CLM_E2E_TIMEOUT` instead of a hardcoded 120-second budget, and start 2
  notebook workers rather than 8 — removing the oversubscription that produced
  `JobsPendingTimeoutError` on a 4-core CI runner.
