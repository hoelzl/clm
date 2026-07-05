- Fixed `--workers=docker` builds silently executing notebook jobs on host
  (Direct-mode) workers started by a concurrent build sharing the same jobs
  database, which failed C++ decks with `NoSuchKernel: xcpp20` (the kernel
  only exists in the Docker image). Three coordinated changes: worker reuse
  is now execution-mode-aware (a Docker-mode build never counts another
  build's Direct workers as sufficient), jobs are tagged with the execution
  mode they require (jobs-DB schema v10) so only matching-mode workers claim
  them, and the worker-availability check counts only matching-mode workers.
- A missing Jupyter kernelspec (`NoSuchKernel`) is now reported as a
  **configuration** error instead of a user error. Besides the correct label
  and actionable guidance, this keeps the failure out of the persistent
  error cache, so fixing the environment (e.g. rebuilding the Docker image)
  is no longer masked by a replayed stale error.
