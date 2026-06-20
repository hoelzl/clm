- **Mobile Deck Studio P2 — structural editing.** The phone authoring surface
  (`clm serve --spec`) gained **insert**, **delete**, and **move/reorder** of
  cells, with automatic `slide_id` minting (or inheriting an anchor slide's id
  for companion `notes`/`voiceover` cells). All structural writes route through
  the same byte-exact serializer as cell edits, so untouched cells never shift,
  and stay guarded by optimistic concurrency (`409` on a stale `deck_version`).
  The UI adds a reorder mode (up/down chevrons) plus per-cell insert/delete
  controls. (#395)
