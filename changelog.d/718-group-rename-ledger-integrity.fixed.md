- `clm slides sync`: a both-halves slide-id rename recorded through the sync
  loop (`record_group_rename`) no longer leaves dangling `id:<old>`
  references in the committed ledger — `rename_group_scopes` now rewrites
  owner references and the `id:` handles inside member-order lists too, and
  every ledger save sweeps deck sections for dangling references (#718).
  Ledgers already damaged by the old path heal on their next save.
