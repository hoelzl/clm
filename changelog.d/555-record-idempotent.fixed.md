- `clm slides sync record` no longer rewrites `confirmed_commit` on members
  whose recorded state is unchanged, and no longer rewrites a ledger file whose
  content is byte-identical — a repo-wide record sweep over clean pairs now
  leaves `git status` clean instead of dirtying every committed
  `<topic>/.clm/sync-ledger.json` (previously ~500-file churn). The same
  preservation applies to `apply`'s per-item ledger updates. The record `--json`
  envelope gains a per-pair `ledger_changed` boolean and a top-level `unchanged`
  count ([#555](https://github.com/hoelzl/clm/issues/555)).
