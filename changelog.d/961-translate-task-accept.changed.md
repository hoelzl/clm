- **`clm slides translate` becomes an agent toolkit (#961).** New verbs `task`
  (frame the whole-deck cold start as a JSON task document — instructions,
  per-cell inputs with the shared prose/code/title prompts, glossary,
  answer schema, and `source_fingerprint` / `companion_fingerprint`
  freshness tokens) and `accept` (validate shape + freshness + coverage and
  write the twin and voiceover companion through the ordinary bootstrap
  engine: EN-authority shared ids, ledger record). Neither calls a model or
  needs an API key. Bare `translate SOURCE` is now the read-only `report`
  verb: twin absent it reports the task counts and points at `task` (exit 1);
  twin present it runs the read-only sync report as before. The in-process
  OpenRouter bootstrap moved behind `translate autopilot` (same options,
  key-gated). See `clm info sync-agents` → "Cold-starting a twin" and
  `clm info migration`.
