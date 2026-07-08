- **Incremental rebuilds no longer delete cached recording/speaker HTML.** A
  SQLite job-cache hit (`jobcache_hit`) served the output from disk without
  recording it in the output-write registry, so the end-of-build stray-file
  sweep treated the valid cached file as an orphan and removed it. This bit
  recording/speaker HTML for unchanged topics on incremental Docker-backend
  rebuilds (notebooks and `shared`/`trainer` HTML were unaffected). The
  job-cache-hit path now registers its on-disk output, matching the
  database-cache and executed-job paths. (#577)
