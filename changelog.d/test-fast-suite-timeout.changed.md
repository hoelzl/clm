- **Tightened the fast-suite per-test timeout from 600s to 120s** (matching CI's
  `--timeout=120`), so a contention *hang* on the pre-push gate fails promptly
  instead of stalling ~10 minutes (which forced a manual Ctrl-C + re-run). The
  slowest legitimate fast test measures ~5.5s (16-worker dev box) / ~11s
  (64-worker contention), so 120s is a >10x backstop, not a performance gate.
  `tests/conftest.py` bumps the heavier suites' per-test timeout at collection
  time to match CI — `integration` → 240s, `e2e`/`slow`/`docker` → 600s — so
  running them locally (a non-default `-m` selection) never false-kills against
  the fast default; an explicit `@pytest.mark.timeout` still wins.
  Test-infrastructure only.
