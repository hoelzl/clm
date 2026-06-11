- Underscore-prefixed directories under `slides/` (e.g. `_archive/`, `_drafts/`)
  are now invisible to module/topic discovery, to the recursive deck walks behind
  the `clm slides` batch tools and `clm slides sync`, and to `clm course orphans`
  — previously an archived module under `slides/_archive/` participated in topic
  resolution and could silently shadow a live topic ID via first-occurrence-wins,
  shipping retired decks in its place. A spec binding `module="_archive"` now
  fails validation with `unknown_module`. The legacy `_cassettes/` sidecar inside
  a topic is unaffected (#318).
