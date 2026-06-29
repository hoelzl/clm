- **`clm slides sync autopilot` gains a non-interactive `--conflict` policy (#447).**
  The "German is the source of truth" reconcile without `--interactive`: `--conflict
  de-wins` (or `en-wins`) takes that side as authoritative and re-translates it over the
  losing half of **every** both-edited conflict in one pass — making a whole-week
  reconcile fully agent-drivable. Because that **overwrites the losing half**
  (irreversible — the discarded edits survive only in git), it is opt-in (`leave` stays
  the default) and a writing run requires `--yes`; `--dry-run` previews exactly what
  would be overwritten. `de-wins-safe` / `en-wins-safe` add an **escalate** tier: a
  model containment check defers any conflict whose losing half carries content the
  winner lacks (a meaningful independent edit), resolving only the rest. An equivalence
  gate skips already-in-sync conflicts (no needless overwrite/translation). id-less
  localized and remove-vs-edit conflicts are never auto-resolved (deferred + reported).
  The `--json` apply payload gains `conflicts_resolved` / `conflicts_escalated`.
  Autopilot-only (the agent toolkit's `apply` stays model-free — resolving a conflict
  re-translates). Also fixes a latent bug in the interactive `[d]e-wins` / `[e]n-wins`
  path: a narrative conflict's anchor identity is now preserved when recasting it to a
  directed edit (it previously failed to locate the cell).
