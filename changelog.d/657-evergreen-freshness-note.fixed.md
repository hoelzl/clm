- `clm info releases` and `clm release sync --help` now state that evergreen
  freshness is fixed at **build** time (#657): regenerate the sources of
  evergreen files (exported outlines, schedules — often a spec-declared
  task) before `clm build`, or the sync truthfully reports `up-to-date` for
  the stale content the build baked in and ships it to the cohort.
