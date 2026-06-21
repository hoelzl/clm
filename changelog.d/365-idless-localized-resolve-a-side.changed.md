- **`clm slides sync` now *resolves* an id-less-localized drift when a side clearly
  wins, instead of always deferring it (Issue #365, increment 2).** Building on the
  positional pairing from increment 1: when the two halves' id-less localized cells
  pair positionally and a paired cell drifted on **exactly one** side since the last
  sync, sync translates that winning edit onto its positional twin (located by
  per-language position, since the cell has no `slide_id`) rather than leaving a
  deferred conflict — so a pass in which every drifted id-less cell has a clear winner
  reconciles cleanly and advances the watermark. A cell genuinely edited on **both**
  sides still defers (resolution by side of a true both-sided edit is out of scope),
  except that a both-edited **markdown** cell the judge finds already equivalent is
  downgraded to *in-sync* (no write, no defer) so a false conflict no longer holds the
  watermark. A mixed pass — some cells resolved, some still deferred — flushes the
  resolved edits but holds the whole watermark until the remaining conflicts are
  resolved.
