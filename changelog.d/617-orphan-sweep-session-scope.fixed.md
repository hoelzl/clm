- The teardown orphan sweep (`mark_orphaned_jobs_failed`) is now scoped to the
  build's own session: a build finishing first in a shared jobs DB no longer
  marks a concurrent build's in-flight jobs as failed, nor folds them into its
  own summary and exit code (#617/#636 follow-up, Finding 3). Maintenance
  callers (`clm workers` reap) keep the unscoped sweep.
