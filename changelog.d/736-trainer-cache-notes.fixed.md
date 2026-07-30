- `clm build`: trainer HTML no longer loses speaker notes on an
  execution-cache hit (#736). The cache-reuse path filtered every
  non-partial kind through a hard-coded notes/voiceover drop; it now
  projects the cached notebook through the consuming output kind's own
  delete set, so trainer keeps its notes exactly as a cache-miss build
  does (completed output is unchanged; the #734 starters stay dropped in
  every non-partial view).
