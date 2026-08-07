- **One env name and one default for the jobs-DB path** (#802 A8). Direct
  workers now receive the jobs database as `CLM_JOBS_DB_PATH` — the same
  variable the host CLI resolves for `--jobs-db-path` — instead of a bare
  `DB_PATH`, and the worker-side container default `/db/jobs.db` is gone: a
  worker launched in SQLite mode without `CLM_JOBS_DB_PATH` refuses to start
  instead of silently creating and polling an empty queue. The notebook
  worker's cache path was renamed the same way (`CACHE_DB_PATH` →
  `CLM_CACHE_DB_PATH`). Only hand-launched `python -m clm.workers.*`
  invocations are affected; clm-managed workers get the value injected.
  `clm workers reap` still recognizes the legacy `DB_PATH` spelling on
  surviving workers launched by an older clm.
