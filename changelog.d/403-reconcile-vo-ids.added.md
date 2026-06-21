- **`clm slides reconcile-vo-ids`** — a new command that symmetrizes the voiceover /
  notes `slide_id` convention across the two halves of a split deck (Issue #403 fix #3).
  When a deck drifts into an asymmetric state — one half's paired voiceovers id-less, the
  other's id'd — this makes them agree, either stripping the id'd side (`--to id-less`,
  the default) or copying the id'd side's *existing* id onto the id-less side
  (`--to ids`). It is the safe alternative to `assign-ids` on a split half: it pairs the
  halves' voiceovers by the same occurrence-under-slide identity `clm slides sync` uses
  and never derives an id from per-file content, so the two halves can never diverge (the
  #162 hazard `assign-ids` carries). Accepts a single half, both halves, or a directory;
  supports `--dry-run` / `--json`.
