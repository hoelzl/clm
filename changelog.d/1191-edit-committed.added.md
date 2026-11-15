- `clm slides sync report --json`: a `translate_edit` or `verify_translation`
  row whose base was recovered (`base_ref`) now also carries `edit_committed`:
  `true` when the edit is already in a commit, `false` when it exists only in
  the working tree. It is judged against the bundle as committed at `HEAD`, so
  an unrelated later commit does not count. The field is absent whenever
  `base_ref` is (#1191).
