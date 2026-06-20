- **Mobile Deck Studio (P0/P1).** `clm serve --spec course.xml` now serves a
  phone-friendly authoring surface at `/studio/` alongside the jobs Monitor:
  browse the spec-resolved deck tree (with recents and a "not in spec" bucket),
  full-text search deck titles and cell text, open a deck, and edit cell
  bodies/tags. Writes go through CLM's byte-exact write-back engine and are
  guarded by **optimistic concurrency** — each carries the deck and cell
  versions the phone last saw, so a concurrent desktop edit (e.g. VS Code)
  returns HTTP 409 "changed elsewhere" instead of silently clobbering; a
  filesystem watcher additionally pushes a "changed on disk — reload" signal
  over the WebSocket. Startup prints the Studio URL plus a scannable QR code
  carrying a persistent bearer token (`--rotate-token` cycles it). Cells are
  addressed by stable `(slide_id, role)` identity, never by index. Structural
  ops, the bilingual language lock + sync-to-other-language, and the installable
  offline PWA are planned later phases. Design of record:
  `docs/claude/design/mobile-deck-studio.md`.
