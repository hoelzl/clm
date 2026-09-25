- `clm slides rename-id` now treats a deck's separated voiceover companions
  (`voiceover/voiceover_*.{de,en}.*` or the sibling layout) as part of the
  deck (#990): it rewrites `for_slide="OLD"` and `vo_anchor="id:OLD#n"` in
  both companions, can rename an id that lives only in a companion cell, and
  refuses a `NEW` that collides with a companion id. The recorded ledger
  fingerprints are carried across the rewritten reference bytes for cells
  sitting on their baseline, so a pure rename stays clean on the next `sync
  report` instead of orphaning the narration (`validate` errors, a
  `broken_owner` framing whose only answer was `remove`). The JSON report
  gains `vo_anchor_hits` and a per-side `companions` block; the
  `broken_owner` detail now names the rename path before `remove`.
