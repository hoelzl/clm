- **Sync v3: a new one-sided *id-keyed* cell is now framed `translate_new` /
  `copy_new_shared` in ledger mode, not a dead-end `verify_cold`.** Previously,
  adding a slide (with a fresh `slide_id`) or an id-keyed shared code cell to one
  language half of a ledgered deck reported the cell `verify_cold` — whose only
  answer, `confirm`, `apply` rejects for a one-sided member ("cannot confirm a
  one-sided member") — so the twin had to be hand-authored. The engine now
  routes a one-sided un-ledgered *id-keyed* member to `translate_new` (answer
  with the target-language body; the twin and shared `slide_id` are minted for
  you) or `copy_new_shared` (mechanical verbatim copy). Two-sided cold members
  still frame `verify_cold`. Un-id'd positional inserts stay `verify_cold`
  because ordinal aliasing makes mechanical mirroring unsafe — mint a `slide_id`
  to resolve. `clm info sync-agents` now documents the add-in-one-language flow
  and the decision-`body` format. (#566)
