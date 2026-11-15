- The faster-whisper decoder settings that guard against repetition loops and
  silence hallucinations are configurable: `CLM_WHISPER_CONDITION_ON_PREVIOUS_TEXT`,
  `CLM_WHISPER_NO_SPEECH_THRESHOLD` and `CLM_WHISPER_COMPRESSION_RATIO_THRESHOLD`
  (used by `clm harvest`, `clm voiceover` and the MCP transcribe tool). The defaults
  are faster-whisper's own, so nothing changes unless a variable is set; a non-default
  value is part of the transcript and alignment cache keys (#1105).
