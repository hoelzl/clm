- Sped up worker health monitoring on Windows: when a worker was not in the
  executor's local process table, `is_worker_running` fell back to a
  system-wide `psutil.process_iter(["pid", "environ"])` sweep that read every
  process's environment block (a PEB read per process — seconds per scan
  under load), once per worker per 10 s monitor cycle; profiling showed it
  consuming ~57% of all host-process samples during a build (#711). The scan
  now reads process names first and only pays the `environ()` call for
  Python interpreters (the only processes that can be clm workers), caches
  the WORKER_ID → PID map for 5 s so a monitor cycle scans at most once, and
  re-verifies liveness per call with `psutil.pid_exists`.
