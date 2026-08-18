- **`clm cassette scan` can gate a course repo (#883).** Two new options,
  `--write-baseline PATH` and `--baseline PATH`, record the findings a repo
  already has so only *new* ones fail the exit code. Without them nothing
  changes — a bare scan still fails on any finding. This exists because a repo
  whose findings are all known and benign could never turn the gate green:
  PythonCourses holds 294, every one a non-credential response cookie and none
  worth re-recording live teaching material to clear (#874), so the check could
  not be wired up at all — and an unsatisfiable gate gets switched off.
  A baseline entry is `(path relative to the scan root, location, key)`,
  deliberately **without** the interaction index (re-recording shifts every
  index, so an index-keyed baseline would fail the gate the first time someone
  did the right thing) and **without** the value (a finding never carries one,
  and `__cf_bm` rotates on every recording). That makes the key name-level:
  accepting `set-cookie` for a file accepts any `set-cookie` in it — a limit
  documented in `clm info commands` rather than implied away, and one the audit
  could not avoid in any case, since it only ever sees the header name. Entries
  matching nothing are reported as stale and never fail; an unreadable cassette
  is not baselineable and still fails, which is why `--write-baseline` exits
  non-zero when it meets one. `--json` gains `accepted_count`, `new_count`,
  `stale_count` and `stale_entries`, and each finding carries `accepted`;
  `finding_count` keeps its existing meaning.
