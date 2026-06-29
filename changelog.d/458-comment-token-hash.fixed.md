- **`//`-comment decks (C++/C#/Java/TS) now get reflow-insensitive markdown
  hashing.** #429 made a pure soft re-wrap of a markdown prose cell hash identically
  (so it is not mis-read as an edit), but it hard-coded the `#` comment token, so only
  Python/Rust decks benefited; a `//` deck's re-wrap still read as drift. The real
  source comment token is now threaded from `CellMetadata.comment_token` through
  `cell_content_hash` / `hash_cell` / `anchor_of` (Option A — the ~25 hash call sites
  are unchanged, so the threading is atomic and can't half-apply). `WATERMARK_HASH_VERSION`
  is bumped to 3 and the consistency ledger gains a per-entry `hash_version`, so `//`
  decks re-baseline once automatically (the existing stale-version self-heal); `#`-deck
  hashes are byte-identical and do not re-baseline. The Studio render path threads the
  token too, so a future `//`-deck would not false-trip the optimistic-concurrency
  guard. (#458, follow-up to #429)
