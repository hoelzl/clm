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
  could not avoid in any case, since it only ever sees the header name.
  Entries matching nothing are **stale**, split by cause because the reasons
  mean different things: *cleared* (scanned and parsed, finding gone — that
  deck was re-recorded, which is exactly what the audit asks for) never fails;
  *unreadable* (the file is there but will not parse) and *missing* (the file
  was not scanned at all — a sparse checkout, content that did not
  materialize, moved decks, or the wrong scan root) both do.
  Entries are relative to the scan root, so without that a gate pointed at the
  wrong tree would find nothing, accept nothing and pass over a repo it never
  looked at. The report is always printed *before* such a refusal, so a run
  with both missing entries and a genuinely new finding still shows the
  finding. An unreadable cassette is not baselineable and still fails, which
  is why `--write-baseline` exits non-zero when it meets one. `--json` gains
  `accepted_count`, `new_count`, `stale_count`, `stale_cleared_count`,
  `stale_unreadable_count`, `stale_missing_count` and `stale_entries`;
  `finding_count` keeps its existing meaning, and every finding now carries an
  `accepted` field (always `false` without a baseline).
