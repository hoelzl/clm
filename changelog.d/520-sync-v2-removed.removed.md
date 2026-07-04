- **BREAKING** — the sync v2 engine was deleted (#520 Phase 4): the
  `task` / `accept` / `autopilot` / `diagnose` / `shadow` verbs, the
  `sync baseline` subgroup and `clm slides watermark`, the watermark/baseline
  flags (`--baseline`, `--baseline-from`, `--use-watermark`, `--cache-dir`,
  `--ledger`, `--explain`, `apply --yes/--auto-heal`), the v2 core modules
  (`sync_plan` / `sync_apply` / `sync_code` and friends), the embedded
  OpenRouter/Ollama sync-judge clients with their prompt/caches
  (`SyncCache`, `SyncAlignmentCache`, `SyncCorrespondenceCache`,
  `SyncSnapshotCache`, `SyncWatermarkCache`), and the legacy v1 sections of
  `.clm/sync-ledger.json` (dropped on the next ledger write). Replacements:
  `report --json` + `apply --decisions` for the task/accept round-trip,
  `record` for bless/accept-record, the agent loop for autopilot. The stale
  `.clm-cache/clm-llm.sqlite` watermark tables are dead data and can be
  deleted.
