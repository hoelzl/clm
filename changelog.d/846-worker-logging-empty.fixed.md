- Fixed direct and Docker worker logs being completely empty: an import-time
  `logging.basicConfig(WARNING)` in `clm.core.utils` made each worker's own
  INFO-level setup a silent no-op, so worker subprocesses logged nothing to
  their executor-side log files. Worker entry points now configure logging in
  `main()` and the library tree is import-pure; worker log files carry boot,
  registration, and job-processing lines again.
