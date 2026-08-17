- Replayed (cached) failures are no longer presented identically to fresh
  execution failures (#860). Errors replayed from stored results now carry a
  `cached` label in every output mode (`✗ [User Error, cached]`, quiet-mode
  `ERROR (cached):`), plus a provenance line naming the remedy (rebuild with
  `--ignore-cache`, inspect with `clm cache explain`); the summary splits the
  count (`11 errors (0 from this run's execution, 11 replayed from cache)`),
  and the JSON report exposes per-error/per-warning `from_cache` plus
  `error_count_from_execution` / `error_count_from_cache`. When the same
  finding is both replayed and freshly reproduced in one build, fresh
  evidence wins — "cached" always means "not executed in this build".
