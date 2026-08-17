- Language-suffixed diagram sources no longer collide on one render target
  (#855). `embeddings.de.drawio` and `embeddings.en.drawio` both rendered to
  `img-generated/embeddings.png` — `with_suffix` swallowed the `.de`/`.en`
  segment as if it were an extension — so one render was silently lost per
  build (last writer won, race-dependent) and the suffixed names slides
  reference (`img/embeddings.de.png`) never existed in the output. Renders
  now keep the source's full stem (`embeddings.de.drawio` →
  `embeddings.de.png`); single-suffix sources are unchanged.
  `clm course migrate-generated-images` mirrors the new naming. Multi-dot
  diagrams re-render once after upgrading; see `clm info migration` for the
  stale-collapsed-render cleanup step.
