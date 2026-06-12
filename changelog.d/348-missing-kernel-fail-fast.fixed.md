Builds on machines without the required Jupyter kernel no longer stall
into long timeout cascades (#348). A `NoSuchKernel` failure is now
classified as `missing_kernel` — a permanent condition for the lifetime of
the build — so the notebook worker fails the job on the first attempt
instead of burning six kernel-startup/backoff cycles, and the error
carries an actionable hint (install the kernelspec, or build with
`--no-html`). Separately, a pre-registered worker whose subprocess dies
before activation (e.g. an import crash from missing `clm[all-workers]`
extras) is marked dead after the first activation-wait timeout, so
subsequent job submissions fail fast with a pointer to the worker log
instead of silently repeating a 30-second wait per job.
