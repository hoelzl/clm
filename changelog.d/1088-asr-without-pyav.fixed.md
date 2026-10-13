- Fixed: ASR (`clm harvest transcribe`, `cached_transcribe` and every other
  faster-whisper path) crashed with `TypeError: open() got an unexpected keyword
  argument 'metadata_errors'` when PyAV 19 was installed. Global
  `uv tool install`s pick up PyAV 19 because they ignore clm's lock file. clm
  now decodes the audio itself: it reads the 16 kHz mono WAV that
  `extract_audio` produced, or falls back to ffmpeg for any other input. It
  hands faster-whisper the samples, so faster-whisper's PyAV decoder is never
  called. The decoded samples are bit-identical to faster-whisper's own (#1088).
