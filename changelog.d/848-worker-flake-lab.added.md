- **`scripts/worker_flake_lab.py`** — promotion of the flake-investigation
  harness used to root-cause the direct-worker boot thundering herd (§10 of
  `docs/claude/design/test-flakiness-root-causes.md`) out of session scratch.
  Three subcommands match the investigation's three steps: `boot` (cold-boot
  latency of one direct worker; ~1.4 s baseline), `herd -n N` (how boot
  latency scales with N simultaneous boots — the measurement that explains
  the rotating registration timeouts under `-n auto`), and `repro -n N`
  (flake rate of the worker-family test files, for before/after fix
  verification).
