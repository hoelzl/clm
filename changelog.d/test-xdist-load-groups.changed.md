- **Further reduced pytest-xdist contention in the test suite** (follow-up to the
  flakiness hardening). The single `serial` xdist load group is now split by
  resource class — `@pytest.mark.serial("subproc")` / `("workerpool")` /
  `("port")` — so the subprocess-spawning and worker-pool families run on
  different workers concurrently instead of stacking one-at-a-time on a single
  worker (M-1; guarded by `tests/test_serial_xdist_groups.py`). The
  outline-command tests now write their transient per-test specs into a
  dedicated, gitignored, copytree-excluded `_volatile_specs/` directory rather
  than the committed `course-specs/` tree, removing a Windows scandir/unlink
  race against the e2e data-copy fixture (M-2). The e2e hardlink copy degrades
  to a per-file byte copy on failure instead of aborting and crashing a retry on
  a partial directory (M-4). Test-infrastructure only.
