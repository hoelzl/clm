- **`clm slides sync` no longer collapses or drops voiceovers when a slide owns
  more than one.** A narrative cell (voiceover / notes) added by sync is now kept
  id-less and anchored immediately after its real predecessor content cell — a code
  cell, a code subslide, or the heading — reusing the `vo_anchor` positional-anchor
  algorithm (Issue #403). Previously every narrative under a slide was stamped with
  that slide's `slide_id`, so two voiceovers after two different code cells collided
  on `(slide_id, voiceover)` → `unresolved duplicate slide_id …/voiceover`, which
  (being an apply error) rolled back the **whole deck** and shipped none of the
  run's good translations. A leading voiceover greeting before the first slide
  (e.g. a title-slide narration) is also placed instead of erroring with "narrative
  with no preceding slide — deferred" (Issue #7 of the AI-dev DE/EN sync report).
  This retires the field workarounds of hand-stamping `slide_id`s on voiceover cells
  and temporarily deleting leading greetings.
