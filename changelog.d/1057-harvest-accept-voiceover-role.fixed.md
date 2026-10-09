- `clm harvest accept` now always writes a new narration cell (`"member": null`)
  as a `voiceover` cell. Before, the deck's majority narrative role was used and
  inline `notes` cells counted, so on a deck without voiceover the harvested
  narration landed as inline trainer `notes` that never reached the recording.
  The layout now follows the deck's voiceover cells only (default: companion).
  On a slide that ends in a reference solution (an `answer` / `alt` cell, or a
  `start`/`completed` pair), the default placement is before the solution:
  inline cells are inserted above it and companion cells carry a matching
  `vo_anchor`. `accept` (including `--dry-run`) now reports each new member's
  role, layout, and per-side `vo_anchor`. (#1057)
