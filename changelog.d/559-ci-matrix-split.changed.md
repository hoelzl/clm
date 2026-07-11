- CI now runs the unit/integration/e2e suites as a `python-version × suite`
  matrix (6 parallel jobs) instead of sequential steps, cutting PR wall clock
  from ~7.5 to ~5.5 minutes. The "Require CI green" ruleset's required checks
  were updated to the six new job names (#559).
