- `clm git --dry-run` previews now resolve the real branch (#686): read-only
  git queries (`rev-parse`, `status`, `rev-list`, `remote get-url`, …)
  execute normally under dry-run, while mutating commands stay stubbed with
  the `[dry-run] Would run:` preview. Previously every query was stubbed to
  empty output, so `push --dry-run` previewed `push -u origin ''` and
  "Pushed to origin/" — the one question a dry run exists to answer ("which
  branch am I about to push?") was unanswerable. `fetch` stays stubbed
  (it rewrites remote-tracking refs), so ahead/behind previews may compare
  against slightly stale remote state.
