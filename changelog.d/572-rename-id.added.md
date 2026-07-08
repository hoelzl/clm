- **`clm slides rename-id DECK OLD NEW`** — rename a `slide_id` across both
  halves of a split deck **and** the committed sync ledger, atomically. Renaming
  a `slide_id` by hand dropped the member's v3 ledger baseline to *cold* (the
  engine keys trust by `id:<slide_id>` and only recovers `pos: → id:`
  migrations), so a cell renamed **and** edited in one go reported `verify_cold`
  — whose only answer, `confirm`, banks the existing, now-stale twin. The new
  command rewrites the id (and every `for_slide` owner reference) on both halves
  and *migrates* the ledger baseline key (carrying the recorded fingerprints,
  never re-hashing): a pure rename then reports clean, and a rename done
  alongside an edit reports `translate_edit` against the carried baseline, so the
  stale twin can never be confirmed unnoticed. Refuses a rename that would create
  a duplicate id. See `clm info commands` / `clm info sync-agents`. (#572)
