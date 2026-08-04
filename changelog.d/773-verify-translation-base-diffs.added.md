- **Sync report: base diffs on translation rows (wire schema 5, #773 phase 1).**
  `verify_translation` and `translate_edit` items now carry `base_ref` plus
  per-side `de_diff`/`en_diff` — unified hunks against the newest commit whose
  bytes match the ledger's recorded fingerprints — so the reader judges the
  hunks instead of re-diffing two full cells by eye (`verify_translation` was
  measured at 68% of all framed rows). Recovery is a capped, read-only git
  walk that degrades to the previous plain shape when the base is not
  committed; `--since REF` recovers against the named ref. A
  `verify_translation_batch` observation fires when three or more
  `verify_translation` rows all diverge from the same recovered base, and the
  text report renders the hunks inline. The report/decision `schema` is now
  `5`; schema-3/4 decision documents remain accepted, and no auto-resolution
  is added at any threshold — every row still takes its own explicit answer.
