- `clm harvest task` / `accept` / `verify` (epic #546 Phase 3) — the
  judgment half of the agent-first harvest toolkit. `task` frames one
  slide's curation or translation as a JSON document (caller instructions,
  baseline + transcript inputs incl. `revisited_segments`, bullet-list
  `answer_schema`, per-side `baseline_fingerprint` + `video_fingerprint`
  freshness tokens). `accept` validates the answer (schema, freshness,
  single-cell guards, v3 re-parse gate) and writes the id-keyed member edit
  atomically through the v3 model, creating the voiceover member (minted
  `slide_id`, `for_slide` owner, deck-convention layout) when absent;
  `--record` banks it into the sync ledger with provenance
  `harvest:<video-fingerprint>` under one-sided-trust semantics, so a
  one-language write surfaces in the next `clm slides sync report` as
  `translate_new`/`translate_edit` — never as `in_sync` (the stale twin is
  never blessed) and never as corruption. `verify` runs the v3 lens gate +
  the shared structural gate, listing one-sided narrative members as
  `pending_twins` instead of failing them.
