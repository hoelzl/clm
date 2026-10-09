- Spec-mode `clm validate` now warns (`image_ref_generated_dir`) when a deck
  references an image by its build-owned source path `img-generated/<name>`.
  That directory never exists in the output (its files land in the output's
  `img/`), so such a link was broken in every output, and validate stayed
  silent because it only recognised `img/` references. The finding tells the
  author to reference `img/<name>` instead. (#1058)
