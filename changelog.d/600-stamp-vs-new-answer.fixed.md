- **Sync: replacing an un-id'd positional cell with new `slide_id`-keyed cells
  no longer dead-ends the decision loop** (#600). The two "suspected stamp"
  shapes — a new id'd cell appearing while a positional base cell of the same
  pool is unaccounted for, and the vanished positional cell itself — are now
  framed as a new `stamp_vs_new` action carrying a `treat_as_new` answer
  (previously `ambiguous_alignment` with an empty `answers` vocabulary, which
  no decision document could resolve). Answering `treat_as_new` grows the
  twin for the new id'd cell and mirrors the vanished positional cell's
  removal in one `apply --decisions` pass; the stamped-and-edited reading is
  still reconciled manually, and the genuinely ambiguous
  `ambiguous_alignment` shapes (rival id stamps, both-sides-added pools)
  remain answerless by design.
