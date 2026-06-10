- `clm slides assign-ids` no longer mints collision-suffixed slide ids that
  exceed the 30-character slug cap (and that `clm slides validate` then
  rejected) — the base slug is trimmed at a word boundary before the `-N`
  dedup suffix is appended (#233).
