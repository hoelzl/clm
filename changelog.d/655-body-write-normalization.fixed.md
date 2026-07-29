- `clm slides sync apply`: decision bodies are normalized to the engine's
  canonical cell shape at the write boundary (#655) — a markdown body
  missing its leading blank comment line (`#`) gains it (a bare blank first
  line is promoted), so `clm validate` no longer warns on the engine's own
  output right after a clean apply, and the out-of-band fix plus
  zero-content `keep_twin` round it forced disappears. Code cells and
  single-line j2 macro cells are untouched. The `choice`/`body`
  mutual-exclusivity rejection now explains that a `body` alone already
  selects the body answer, and the decision-body contract (delimiter
  exclusion, comment prefixes, auto-inserted blank lead, exclusivity) is
  documented in `clm info sync-agents`.
