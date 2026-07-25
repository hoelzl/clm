- **The Docker job's promotion readiness now tracks itself.** Whether the layer
  cache and retry loops actually removed the job's infra flake is an empirical
  question needing ~20 runs of evidence — the kind of follow-up that gets
  forgotten. `scripts/docker_job_stability.py`, run nightly, keeps one
  `ci-health` issue up to date with the job's recent outcomes on `master` and
  the consecutive-success streak, and comments only when the promotion criterion
  is first met. Editing an issue body sends no notification, so the daily
  refresh is a live dashboard rather than noise.
