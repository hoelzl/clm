- **`clm slides sync` now detects *edits* to per-slide voiceovers, and no longer
  doubles them when the two halves disagree on a `slide_id`.** Building on the
  positional placement from PR #405, an id-less narrative cell (voiceover / notes) is
  now given a stable identity — the n-th narrative of its role under its owning slide —
  recorded in an additive `anchor` column on the sync watermark. This lets sync (a)
  propagate a one-sided **edit** to a voiceover (the previous engine could only *add*
  id-less narratives, never reconcile them), (b) recognize an already-paired voiceover
  as in-sync instead of re-adding it every run, and (c) pair an id-less voiceover on one
  half with its id-carrying twin on the other under the same slide — the report-#10
  "destructive doubling" where a default (writing) sync would insert ~11 duplicate
  German voiceovers into a deck that already had them. As a safety net, a *mass* of
  narrative adds whose slide already carries a same-role narrative on the other half is
  refused loudly (the halves are structurally mis-aligned) rather than written
  (Issue #403).
