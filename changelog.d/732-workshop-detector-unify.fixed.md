- `clm build`: the `partial` output kind now recognizes **both** workshop
  opener forms (#732). The build carried a tag-only duplicate of the range
  detector, so a workshop opened only by the sanctioned `workshop-…`
  slide_id form passed `clm validate` but its partial build detected no
  range — the full solution shipped unblanked. One canonical detector
  (`clm.slides.workshop_scope`) now backs the validator, `clm export`, and
  the build — including the cached-HTML partial path: the cached executed
  notebook now retains `slide_id`/`for_slide` metadata (stripped at the
  export boundaries instead of before the cache write), and the notebook
  cache schema version was bumped, so the first build after upgrading
  re-executes once instead of replaying stale slide_id-less artifacts. The
  validator's orphan-`end-workshop` warning uses the same opener predicate,
  so a slide_id-opened workshop's closer no longer warns "the tag has no
  effect".
