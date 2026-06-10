- Finished job rows are now pruned at build end by default: completed jobs are
  kept 7 days and failed jobs 30 days (previously both were kept forever, which
  made the jobs database — and `clm monitor` startup — grow without bound).
  Job rows are diagnostic only; the results/execution caches live in separate
  tables, so this never causes re-execution. Set
  `CLM_RETENTION__COMPLETED_JOBS_RETENTION_DAYS` /
  `CLM_RETENTION__FAILED_JOBS_RETENTION_DAYS` to tune.
