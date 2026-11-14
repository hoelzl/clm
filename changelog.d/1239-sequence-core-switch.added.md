- **`CLM_SYNC_CORE=sequence` (preview)** runs `clm slides sync report` and
  `apply` on the sequence core, a replacement for the engine's structural half
  (design note `docs/claude/design/sync-sequence-core.md`, phase C). It aligns
  each half to the cell sequence the ledger records instead of pairing cells by
  position, and renders both halves from one merged result. Its report is a
  schema-6 envelope with new handles (`seq:<n>`, `new:<lang>:<file>:<n>`) and
  one new question, `identify`. `apply` settles the whole deck or writes
  nothing. Off by default; `record` and `verify` are unchanged. Details:
  `clm info sync-agents`.
- The ledger's recorded cell sequence now also carries, per localized cell, the
  fingerprint of its text, its trailing blank lines and its `for_slide` /
  `vo_anchor` values (`texts`), which the sequence core's text layer compares.
  The default engine ignores them; a section gains them the next time its
  sequence is recorded.
