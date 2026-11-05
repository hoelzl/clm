- `clm harvest report`/`task` now clean the ASR transcript at read time
  (fresh and cached transcripts alike; the transcript cache is not
  invalidated): exact Whisper repetition loops are collapsed to one
  occurrence (3+ identical consecutive occurrences of a 2+ word phrase, or 2
  of a phrase of 8+ words, within or across segments), and possible hallucinations are flagged
  for the curating agent — `low_confidence`, `no_speech`,
  `high_compression` (from faster-whisper's per-segment `avg_logprob`,
  `no_speech_prob`, `compression_ratio`, now recorded in the transcript),
  `repeat_collapsed`, and `trailing_phrase` ("Thank you." ×9 at the end of a
  video). Flags appear in each item's `transcript.suspect_segments`, in
  `unmatched_speech`, in `summary.suspect_segments`, and in `harvest align
  report` segments; the curate instructions tell the agent how to treat
  them. `harvest autopilot` applies the same collapsing (the two pipelines
  share alignment cache entries). Cached alignments are recomputed once.
  (#1105)
