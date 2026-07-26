- **`clm slides sync record` no longer blesses a divergence hidden in a voiceover
  companion.** The ledger write gate ran the structural checks on the two deck
  halves while `sync verify` ran them on the companion-inlined projection, so the
  two verbs disagreed about the same pair: `verify` failed on a byte-diverged
  *shared* narration cell while `record` banked it as verified — and a banked
  "verified" divergence is what later lets a mirror or a propagation overwrite
  content that only ever existed on one side. `record` and `apply`'s post-write
  ledger save now gate on the same projection `verify` reads, so a one-sided
  id'd narrative member, a diverged shared companion cell, a duplicated companion
  id, or a pair CLM cannot project at all (mixed / cross-language layout, orphaned
  `for_slide`) refuses the write instead of being trusted.
  `--allow-diverged-companion` is the documented override on both verbs: it drops
  **only** the violations the companion projection introduced — a corruption in
  the deck halves themselves still refuses — and logs each one at WARNING.
  Measured before shipping: of 1063 split pairs across PythonCourses, CppCourses
  and CSharpCourses, zero start failing (229 project differently and all stay
  clean). `clm harvest`'s write path deliberately keeps the deck-halves-only gate
  — a one-sided narrative member is its sanctioned pending state, and the two
  entry points now sit side by side in `clm.slides.sync_verify` so the difference
  is a documented choice rather than a per-call-site accident.
