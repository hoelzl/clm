- Speech recognition with faster-whisper no longer conditions each window on
  the previous window's text (`CLM_WHISPER_CONDITION_ON_PREVIOUS_TEXT` now
  defaults to `false`; set it to `1` for the old behaviour). On German and English
  lecture recordings the old default produced repetition loops, invented clauses
  and outro hallucinations, and the new one lowered the word error rate. Cached
  transcripts are kept: a video is decoded with the new default the next time it
  is transcribed, or now with `clm harvest --refresh-cache`. The cleanup step also
  flags the German subtitle-credit hallucination "ARD Text im Auftrag von Funk"
  (#1105).
