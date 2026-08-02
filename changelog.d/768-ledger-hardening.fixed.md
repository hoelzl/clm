- **Sync trust store**: concurrent runs on sibling decks of the same topic no
  longer silently revert each other. A topic ledger is one file holding
  independent per-deck sections, and every verb read the whole file and wrote it
  back, so the second of two parallel `sync apply` runs wrote its *pre-run* copy
  of the first's deck over the first's work — with no error on either side, and
  the reverted members reporting cold on the next run. Saves now merge: sections
  another run changed while this one was working are preserved, and only the
  sections this run actually modified are overwritten. Concurrent runs on the
  **same** deck still cannot be ordered without a lock, so the later writer wins
  and logs a warning — parallelize by deck, not within one.
- **Sync trust store**: the normal `report` → `apply` → `record` loop no longer
  rewrites every touched member on every pass. Re-recording compared the
  `provenance` stamp, and the verbs alternate between `record` and `apply`, so
  members whose content had not changed churned anyway — 883-line ledger diffs
  for 60 changed cells, enough noise to make the store unreviewable. Switching
  between the automatic provenances (`record`, `apply`, `structural`) is no
  longer a change. An explicit `--provenance agent` or `semantic:<model>` still
  records fresh, and is no longer demoted by a later automatic pass.
- **Docs**: `confirmed_commit` is now described as what it has always been — the
  repo `HEAD` when the entry was last written with a real change. It does *not*
  contain the recorded state (`record` runs before you commit), a no-op
  re-record leaves it alone, and nothing reads it for a verdict. The previous
  wording ("the commit at which this state was last actually established")
  described behaviour the code never had.
