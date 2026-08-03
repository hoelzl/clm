- **`clm validate`**: the split pair's **tag-set parity** check is now computed
  by the sync engine's oracle — the same one `clm slides sync verify` runs —
  instead of `validate`'s own pairing code.

  The visible improvement is that **phantom findings are gone**. The old check
  paired the two halves *positionally*, so a single one-sided insert offset every
  later cell and each offset pair was reported as a tag mismatch, pointing at
  lines where nothing was wrong. The engine pairs id'd cells by
  `(slide_id, role)` and only falls back to positional matching *within* one
  slide, so an offset cannot cascade. On the reference corpus 25 tag warnings
  become 20 — one deck contributed 6, of which 5 were artefacts.

  The message now names both tag sets rather than the one-sided delta. Severity
  is unchanged, and the finding still points at the offending DE cell.

  The other three pair checks are unchanged: they compare *sets*, or a
  length-guarded shared-cell stream, so they cannot produce this artefact.
