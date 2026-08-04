- **The split-routing abort-gate test now exercises the production gate.**
  `TestBuildRefuses` re-implemented the Phase-6 abort logic inside its own
  body and asserted on its own `SystemExit`, so deleting the real gate in
  `build.py` kept the test green (found by the #777 adversarial review). The
  rewritten test scaffolds the actual broken trees (dual-format conflict and
  half pair — both categories, the second previously untested) and drives the
  real `process_course_with_backend`, with a poisoned `start_stage` as the
  tripwire proving the build aborts before any worker stage starts.
  Verified by mutation: removing the production gate now fails both cases.
  (#778)
