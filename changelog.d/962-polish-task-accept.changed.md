- **`clm slides polish` becomes an agent toolkit (#962).** New verbs `task`
  (frame the notes cleanup as a JSON task document — the level prompt as
  instructions, one row per notes-carrying slide with its `id:`/`pos:`
  handle and the slide's content in both language sides for context,
  answer schema, and `source_fingerprint` / `twin_fingerprint` freshness
  tokens) and `accept` (validate shape + freshness + coverage and write the
  polished `tags=["notes"]` cells through the ordinary narrative writer,
  atomically — byte-identical to the in-process path; the sync ledger stays
  untouched so the next `slides sync report` frames the twin's update).
  Neither calls a model, needs an API key, or needs the `[summarize]`
  extra. Bare `polish SLIDES --lang` is now the read-only `report` verb:
  it counts the notes a task would frame and points at `task` (exit 1).
  The in-process LLM cleanup moved behind `polish autopilot` (same options,
  key-gated on `$OPENAI_API_KEY`; the `verbatim` level stays key-free). See
  `clm info sync-agents` → "Polishing speaker notes" and
  `clm info migration`.
