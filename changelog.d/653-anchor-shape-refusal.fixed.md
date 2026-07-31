- `clm slides sync report` no longer reports a one-sided `slide` tag change as
  `duplicate_id`. Removing (or adding) `tags=["slide"]` on one half of a split
  deck makes the same id a slide start on one side and a continuation cell on
  the other; the parse used to refuse the whole deck with
  `duplicate_id: member key id:X resolves to 2 distinct members` — a message
  about parsing ambiguity whose `rename-id` hint renames *both* halves and so
  cannot fix it. The cause is now named by its own code,
  `anchor_shape_divergence`, which locates both halves, says which side carries
  the tag, and hints the edit that repairs it (#653).
- A parse refusal whose reasons are all codes `normalize` cannot repair no
  longer opens with "run `clm slides normalize --stamp-ids` first" — that
  command reports nothing to do for a duplicate id or a one-sided slide tag.
  The header names `normalize` only when at least one id-less reason is present.
