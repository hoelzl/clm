- Carved the sync-free identity/snapshot layer (`clm.slides.doc_identity`:
  `content_fingerprint`, `pair_signature`, `baseline_from_deck`,
  `iter_with_groups`, `member_group_token`) and the sync-free write surface
  (`clm.slides.doc_write`: `DeckEmitter`, `write_changed_files`,
  `new_companion_path`) out of the v3 sync differ/executor, so non-sync
  consumers of the bilingual deck model (the upcoming `clm harvest` toolkit,
  epic #546 Phase 1) can import identity and write files without pulling in
  `sync_diff` or the ledger. No behavior change; `sync_diff` re-exports the
  moved names.
