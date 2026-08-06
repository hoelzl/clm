- **Internal re-layering (Phase 8 step S5 of the A1/A3 plan, refs #802)**: the
  slide-text model descended into a new `clm.core.slide_text` package —
  `slide_parser` (from `clm.notebooks`), `raw_cells`, `anchor_primitives`,
  `pairing` (from `clm.slides`), and the payload-time voiceover merge
  (`voiceover_merge`, extracted from `clm.slides.voiceover_tools` with its
  placement helpers made public). The authoring tools and the sync engine
  import the shared model from core. **This empties the layer-violation
  ratchet: the documented four-layer architecture now exists in the import
  graph.** Import paths only; no CLI behavior change.
