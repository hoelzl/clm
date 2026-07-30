- `clm git --dry-run` previews now resolve the real branch (#686): read-only
  git queries (`rev-parse`, `status`, `rev-list`, `remote get-url`, …)
  execute normally under dry-run, while mutating commands stay stubbed with
  the `[dry-run] Would run:` preview. Previously every query was stubbed to
  empty output, so `push --dry-run` previewed `push -u origin ''` and
  "Pushed to origin/" — the one question a dry run exists to answer ("which
  branch am I about to push?") was unanswerable. `fetch` stays stubbed
  (it rewrites remote-tracking refs), so ahead/behind previews may compare
  against slightly stale remote state — and `sync --dry-run` now exits 1
  when those refs show the remote ahead, accurately predicting the real
  run's abort. `init --dry-run`'s remote classification runs read-only and
  prompt-free (`GIT_TERMINAL_PROMPT=0`; an unreachable remote degrades to
  the local-only preview), its crash-recovery branch previews instead of
  attempting a real `.git` restore, and the `.gitignore` it used to write
  even under `--dry-run` is now only previewed. Commit/sync dry-run
  previews gate on the real working-tree status, so "working tree clean"
  no longer prints over a dirty tree.
