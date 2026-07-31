- Sync report items carry **`resolution`** (`mechanical` / `decision` /
  `manual`). An empty `answers` list meant two opposite things — "nothing to
  answer, `apply` executes it" on a mechanical row and "blocked, repair the
  files yourself" on a framed one — and `clm info sync-agents` documented only
  the first, so its own example filter script misclassified every blocked item.
  Branch on `resolution`.
- Report items also carry **`de_body`/`en_body`**: the same cell bytes as
  `de`/`en` but without the `# %%` delimiter line, which is exactly what a
  `body` answer must contain. Report output is now valid decision input; agents
  no longer have to rediscover "strip line 1".
- A cold member present on **one half only** no longer advertises `confirm`.
  The executor always refused it (confirm asserts that both halves agree), and
  for a positional member the rejection then blocked its whole `(group, kind)`
  pool with no visible cause. The item now comes back with no answers,
  `resolution: manual`, and a `detail` naming the repair — give the cell a
  `slide_id` so its twin can be framed, or delete it.
