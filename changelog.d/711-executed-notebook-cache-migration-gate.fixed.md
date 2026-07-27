- Sped up cached builds: `ExecutedNotebookCache` ran its legacy-payload
  migration (a full-scan `DELETE` + commit) on *every* connection open — and
  the recording/speaker replay gate opens a fresh connection per HTML op,
  several hundred times per build (measured: 15% of host time in the cached
  phase of a large-course build, #711). The migration now runs once per
  database file, gated on `PRAGMA user_version`; steady-state opens cost only
  the `CREATE IF NOT EXISTS` schema checks.
