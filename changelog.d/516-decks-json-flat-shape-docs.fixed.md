- **`clm course decks --json` help now says its output is a *flat* `topics[]`,
  not a section grouping (#516).** The old wording ("a per-topic `topics` array
  **keyed by `section`**") read as a `{section: [...]}` mapping, so agents wrote
  filters against a non-existent `sections[]` shape, got `[]`, and reported a
  silent no-output failure (exit 0, nothing printed) even though the command
  emitted valid JSON. The `--json` help, command docstring, and `clm info
  commands` entry now state the shape is a **flat** `topics` array whose
  `section` is a plain string field, list the real top-level keys (`spec`,
  `slides_dir`, `lang`, `deck_count`, `decks`, `topics`, `unresolved` — no
  `sections` key), and point at `clm export outline --format json` for the
  section-grouped (`sections[].topics[]`) shape. Docs only — no behavior change.
