- **Recordings dashboard: Restore now updates the committed recordings ledger**
  (#1196). Restoring an older take used to change only the files and the local
  state file, so the ledger (and `clm recordings report`) still described the
  replaced take. The restored take now becomes the part's ledger entry, the
  replaced one moves to `superseded`, and a deck `ack` given after the replaced
  take was recorded is dropped, the same rule as `clm recordings invalidate`.
