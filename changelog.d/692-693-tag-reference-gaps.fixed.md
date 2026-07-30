- `clm info slide-format`: two documentation gaps agents kept falling into
  (#692, #693). The `alt` tag — which has its own normalize migration — now
  appears in the code-visibility table with the pairing invariants
  (`completed` always follows a `start` cell, `alt` never does; identical
  output visibility otherwise; a legacy `start`→`alt` pair is migrated by
  `tag_migration`, do not infer it from old corpora). And workshops are now
  documented as a **range**, in a dedicated "Workshop scope" subsection:
  both opener forms (`workshop` tag, `workshop-…` slide_id on a slide-start
  markdown cell), all three closers including the implicit
  run-to-end-of-deck default, a worked example, and what the `partial`
  output does inside the range — the per-cell reading of the old tag table
  produced provably wrong coverage numbers.
