- `clm release status --json` and `clm release sync --json` (#967): `status`
  emits one row per channel (released / pending / frozen / awaiting-sync
  topic ids, ledger, destination, frozen-manifest path, stream); `sync
  --dry-run --json` emits the promotion plan as rows (`copy` / `refreeze` /
  `skip-frozen` / `skip-failed` topics with file counts, evergreen and
  `--refreeze-skeleton` refreshes, the skeleton decision, and the `--push`
  commit-message preview); a real `sync --json` adds the result. Multi-channel
  runs emit one array element per channel in spec order; notes and warnings
  go to stderr; exit codes are unchanged. Part of #970.
