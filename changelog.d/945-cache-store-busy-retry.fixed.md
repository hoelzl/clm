- **Worker-side executed-notebook cache store retries lock contention (#945).**
  `ExecutedNotebookCache.store()` — the write every notebook worker makes to
  the shared cache DB after executing a deck — did a bare INSERT + commit with
  no busy-retry (the residue of #917/#918, which hardened only the host-side
  writes). With many parallel workers a starved writer failed with
  `sqlite3.OperationalError: database is locked`, which the build reported as
  a *user* error ("Check your notebook for errors") on a healthy notebook,
  persisted that bogus failure to the issue cache, and left the notebook
  uncached so the next build re-executed it. The store now runs under the
  same bounded `retry_on_busy` schedule as the host-side writers, rolling back
  between attempts and refreshing the worker's heartbeat so a worker stuck
  behind the lock is not swept as dead; if the lock outlasts the schedule the
  job still succeeds and logs a warning that the notebook was not cached
  (matching Docker mode, where the API-backed store was already best-effort).
  The Worker API's store and status routes run these retrying writes on a
  thread so a long retry no longer stalls other Docker workers' requests.
  Independently, the error categorizer now classifies a worker-side SQLite
  lock error as an `infrastructure` / `database_locked` error with contention
  guidance instead of the notebook-content default, so it is neither blamed
  on the file nor persisted to the error cache. A *cell* that itself raises
  "database is locked" (a notebook using sqlite3) still counts as the
  notebook's own error.
