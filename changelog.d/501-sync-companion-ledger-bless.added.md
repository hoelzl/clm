- **`clm slides sync baseline bless` and the consistency ledger are now
  companion-aware.** Blessing a separated-voiceover pair records its watermark over
  the companion-inlined projection with the `separated` marker (so the bless is not
  demoted on the next run), and the consistency-ledger recorders (`accept --record`,
  `bless`, and the semantic-judge pass) fingerprint the same projection — so a
  narration confirmed in-sync is stored and later suppressed like any other cell
  instead of being invisible to the ledger (issue #501). A drift *after* a
  confirmation still surfaces (the recorded hashes no longer match). The `clm
  validate` companion-parity suggestion now points authors at `clm slides sync`,
  which reconciles the divergence.
