- The voiceover transcribe CLI tests no longer leak a
  `.clm/voiceover-cache/transcripts/` directory into the working directory
  `pytest` was invoked from — they now isolate the transcript cache under
  `tmp_path` via `--cache-root` (#235).
