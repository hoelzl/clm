- **`clm course decks --json`, `clm export outline --format json`, and the
  `course_outline` MCP tool now document that their JSON *is* the
  section → source-`.py`-deck mapping.** The capability was always present (a
  per-topic array carrying each topic's `section` / `resolved_module` /
  `slide_files`, or sections whose topics carry `slides: [{file, title}]`), but
  the help text and tool descriptions only said "List the deck files" / "Output
  as JSON" / "Generate a structured JSON outline", so agents reconstructed the
  mapping by parsing the spec XML or grepping `slides/`. The command help,
  docstrings, `clm info commands`, and the two MCP `course_outline` docstrings
  now state the mapping explicitly and cross-reference each other. Docs only —
  no behavior change.
