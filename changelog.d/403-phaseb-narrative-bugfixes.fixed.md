- **`clm slides sync`: fixed two silent narrative-handling bugs in the Phase B
  voiceover edit-detection (Issue #403).** (1) Removing (or editing) several voiceovers
  under one slide in a single sync could mis-target the second and later cells: the
  apply step recomputed each narrative's occurrence ordinal over the *already-mutated*
  deck, so after deleting the first the survivors renumbered and the next operation hit
  the wrong cell — leaving one narrative behind plus an error that held the watermark, so
  the stale state recurred every run. Apply now resolves narrative targets from a
  pre-mutation snapshot and edits/deletes by object identity. (2) A voiceover sitting
  under the macro-generated title slide whose nearest predecessor is a non-slide content
  cell (e.g. a leading intro cell carrying a `slide_id`) failed to pair against its
  baseline — its owning slide was recovered as "none" on the baseline side but "title" on
  the current side — so a one-sided edit to it was silently dropped. The baseline
  owning-slide recovery now mirrors the live computation for the title group.
