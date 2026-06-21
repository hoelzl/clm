- **`clm build` no longer repeats identical errors and warnings.** A source
  slide is processed once per output target and language, so the same finding
  (e.g. a dropped voiceover narration) used to be printed many times as the
  build progressed. Each unique error/warning is now shown once in the live
  stream, and the final summary collapses duplicates into a single entry with
  a `(N times)` suffix recording how often it occurred. JSON output gains an
  `occurrence_count` field per error/warning.
