- **`CLM_MAX_WORKER_STARTUP_CONCURRENCY` removed** (hard cut, A7 of #802). It
  duplicated the existing `[worker_management] startup_parallel` config field
  with a divergent default (10 vs the documented 5). Use the config field or
  `CLM_WORKER_MANAGEMENT__STARTUP_PARALLEL`; see the removed → replacement
  table in `clm info migration`.
