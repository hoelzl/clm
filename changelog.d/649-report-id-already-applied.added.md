- **Sync wire schema 4.** `report` / `apply` / `record` payloads now announce
  `"schema": 4`, and every report pair payload carries the deck's identity and
  a freshness token:
  - `report_id` — a hash of the bundle bytes plus this deck's ledger section.
    Echo it at the top level of the decision document
    (`{"schema": 4, "report_id": "…", "decisions": [...]}`) and `apply` will
    refuse the **whole** document — exit 2, nothing written — when the deck or
    its ledger moved on since that report. Documents without the token are
    still accepted, with a warning naming the field; that grace ends in a
    future release (#649).
  - `deck_key` and `ledger` — the deck's trust identity, so it is visible that
    a deck half and its `voiceover_*` companion are one deck with one ledger
    section. Pointing `sync` at a companion now says on stderr that it is
    reconciling the deck.
- `apply` distinguishes **`already_applied`** from `rejected`: an answer whose
  member frames nothing in the current report is redundant, not wrong — the
  state it asks for already holds — and no longer blocks exit 0. Only a handle
  naming no member of the deck is a stale-handle rejection. This is the
  verdict half of #649, where apply reported "rejected" for decisions whose
  writes had demonstrably landed.
- `report` and `apply` embed `exit_code` in their `--json` envelopes, decision
  parse/freshness refusals emit a JSON envelope instead of an empty stdout, and
  the rejection block is printed to stderr **before** the payload so a merged
  stream still ends in valid JSON.
