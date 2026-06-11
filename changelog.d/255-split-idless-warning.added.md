- `clm slides split` now warns (without failing or rewriting the source) when
  the bilingual source contains `slide`/`subslide` cells missing a `slide_id`,
  pointing at `clm slides assign-ids`. Lightweight substitute for the deferred
  `split --assign-ids` proposal (#255): the documented
  `assign-ids --accept-content-derived --accept-code-derived` → `split`
  pipeline already guarantees id-complete halves; the warning catches the
  forgotten-first-step case at split time instead of one `validate` later.
