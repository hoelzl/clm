- **`clm slides sync` now reconciles separated voiceover companions (write-back).**
  Building on the read-mode support, `sync apply` / `autopilot` / `accept` now
  *write* a separated-voiceover pair: a narration added, edited, moved, or removed
  in one language's companion (`voiceover_*.de.py` / `.en.py`) is propagated —
  translated when needed — into the other language's companion, committing the ≤4
  files (both decks + both companions) in one atomic batch (issue #501). The deck
  stays voiceover-free on disk before and after, so a crash can never leave a
  half-inlined deck. A one-sided narration creates the missing companion pinned to
  the twin's layout (`voiceover/` subdir vs sibling); an emptied companion is
  deleted. An in-sync pair writes nothing, and the reconciled state is recorded in
  the watermark under a `separated` representation marker so later runs diff in the
  same representation (a legacy voiceover-free watermark auto-re-baselines on the
  first companion-aware run). Speaker `notes` stay inline in the deck (voiceover-only
  extract). A mixed / cross-language / orphaned-cell pair still refuses and writes
  nothing.
