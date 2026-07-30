- `clm build`: the `partial` output kind now recognizes **both** workshop
  opener forms (#732). The build carried a tag-only duplicate of the range
  detector, so a workshop opened only by the sanctioned `workshop-…`
  slide_id form passed `clm validate` but its partial build detected no
  range — the starter cell was deleted and the full solution shipped
  unblanked. One canonical detector (`clm.slides.workshop_scope`) now backs
  the validator, `clm export`, and the build. The validator's
  orphan-`end-workshop` warning uses the same opener predicate, so a
  slide_id-opened workshop's closer no longer warns "the tag has no
  effect".
