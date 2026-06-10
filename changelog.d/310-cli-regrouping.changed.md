- **Breaking: command-tree regrouping (#310).** The single-command groups
  `topic`, `spec`, and `authoring` were merged into the domain groups
  `course` and `slides`, and the remaining stray top-level commands moved
  into their natural groups — a clean break with no deprecation aliases:
  `clm targets`/`clm sync-includes`/`clm spec decks|orphans`/`clm topic
  resolve` → `clm course targets|sync-includes|decks|orphans|resolve-topic`;
  `clm authoring rules` → `clm slides rules`; `clm polish` →
  `clm slides polish`; `clm delete-database` → `clm db delete`;
  `clm export calendar` → `clm calendar generate` (the whole cohort-calendar
  lifecycle now lives in one group: `generate` → `check` → `status` →
  `push`); `clm voiceover port-voiceover` → `clm voiceover port`. The
  synonym pairs `slides translate`/`bootstrap` and `export
  summary`/`summarize` still work but are listed once in `--help`. The top
  level shrinks from 31 to 26 entries. See `clm info migration` for the full
  table.
