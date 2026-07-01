- **Unified duplicate environment-variable spellings (hard cut).** Settings that
  had two env names now have one canonical form (old names no longer work; see
  `clm info migration` for the full table):
  - `CLM_DB_PATH` → `CLM_JOBS_DB_PATH` (the legacy jobs-DB auto-detect for
    `clm status` / `monitor`).
  - `CLM_E2E_PROGRESS_INTERVAL` / `CLM_E2E_LONG_JOB_THRESHOLD` /
    `CLM_E2E_SHOW_WORKER_DETAILS` → `CLM_PROGRESS__UPDATE_INTERVAL` /
    `CLM_PROGRESS__LONG_JOB_THRESHOLD` / `CLM_PROGRESS__SHOW_WORKER_DETAILS`
    (also settable as `[progress]` in `clm.toml`). These drive **real build**
    progress output (not just E2E tests), so they moved out of the misleading
    `logging.testing.e2e_*` home into a top-level `[progress]` section. They now
    flow through the config system with the defaults the build had always used
    (5 s / 30 s / show-details), so build output is unchanged.

  The worker-count cap keeps its friendly short env var — **`CLM_MAX_WORKERS`**
  is the canonical spelling (the env form of `[worker_management] max_workers_cap`)
  and is now documented as such. Phase 5 of the config/CLI/env unification
  (`docs/proposals/config-cli-precedence-unification.md`).
