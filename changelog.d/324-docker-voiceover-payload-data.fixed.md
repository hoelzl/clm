- Docker source-mount workers no longer discard the host's voiceover-merged
  notebook payload by re-reading the raw slide file from the mount (#324).
  The payload's `data` is now the canonical input in both Direct and Docker
  modes, so (1) companion narration reaches docker-built output and (2) the
  worker-side `execution_cache_hash` agrees with the host's again, restoring
  Stage-4 cache replay for voiceover decks built with docker workers.
