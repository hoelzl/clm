- **`clm slides coverage` becomes an agent toolkit (#963).** New verbs
  `report` (bare `coverage PATH` — frames the judgment read-only: one item
  per (slide, lang) pair with its bullets, voiceover, and
  `slide_hash`/`voiceover_hash` freshness tokens; cached verdicts surface;
  no-voiceover pairs are findings; the judge's system prompt is embedded
  verbatim; `--dump` stays) and `accept` (validator `coverage-verdicts`:
  shape + per-pair freshness + coverage of exactly the pending pairs +
  verbatim bullet texts, then banked into the same `CoverageCache` rows
  the embedded judge wrote — the cache is the trust store). Neither calls
  a model, needs a daemon, or imports the Ollama client. The in-process
  Ollama judge moved behind `coverage autopilot` (cache-only fallback
  preserved). Directory sweeps frame every deck in one document.
- **`clm slides assign-ids --llm-suggest` removed; `accept` lands agent
  titles (#963).** The Ollama title suggester flag (with `--llm-model` /
  `--ollama-url` / `--llm-timeout` / `--cache-dir`) is gone without an
  alias. The unchanged `--report-refusals --context --json` worklist is
  the framing; new `assign-ids accept PATH --answers` takes
  `{file, line, title, body}` rows (body echoed verbatim as the freshness
  token), slugs titles through the engine's own slugifier, resolves
  collisions against both halves of a split pair, keeps pair consistency
  (divergent de/en id sets are rejected), and stamps atomically. Bare
  `assign-ids PATH …` still mints (now the `run` verb); no verb imports
  the Ollama client.
- **`clm slides coverage-report` renamed to `clm slides language-coverage`
  (#963).** The course-wide DE/EN completeness sweep joins the
  `language-*` family and clears the near-collision with `coverage`'s new
  `report` verb. No alias; options unchanged. See `clm info sync-agents`
  → "Judging voiceover coverage" and `clm info migration`.
