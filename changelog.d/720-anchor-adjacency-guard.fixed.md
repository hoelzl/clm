- `clm slides sync apply`: minting a slide's missing twin now refuses when
  the computed insert position would separate the slide from its group's
  existing cells on the other half (#720) — under a divergent group order the
  mirrored-predecessor placement previously wrote the twin at the end of the
  file, structurally corrupting the pair (#652). The refusal fails only that
  item, with a reorder-first instruction; all other items still land.
