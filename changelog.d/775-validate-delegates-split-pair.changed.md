- **`clm validate`**: the split-pair structural checks — shared-cell byte
  parity, cross-side tag parity, and `slide_id` set/order parity — are now
  computed by the **sync engine's** structural oracle, the same one
  `clm slides sync verify` and the sync write gate use. The two can no longer
  disagree about whether a pair is sound.

  The visible improvement is that **phantom findings are gone**. The replaced
  checks paired the two halves *positionally*, so a single one-sided insert
  offset every later cell and each offset pair was reported as a tag mismatch.
  On the reference corpus 25 tag warnings become 20 — one deck contributed 6, of
  which 5 were artefacts pointing at lines where nothing was wrong. The id-order
  check also stops flagging a legitimately one-sided mid-transition id as an
  order divergence.

  Messages are re-worded (the engine names both tag sets rather than the
  one-sided delta, and enumerates one finding per offending id instead of one
  listing the whole set). **Severities are unchanged** — `validate` keeps its own
  policy, so nothing that was a warning became a commit-blocking error.

  The companion `for_slide` parity check is deliberately not delegated: it
  compares *sets*, so it cannot produce a positional artefact, and delegating it
  would turn a one-sided companion from a warning into an error.
