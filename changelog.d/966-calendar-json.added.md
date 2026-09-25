- `clm calendar check`, `status` and `push` take `--json` (#966): `check`
  emits its findings as rows with a stable `rule` code, the TOML key /
  adjustment / projected segment they `anchor` to, and the projected `dates`;
  `status` emits the same facts the text prints (`state`, `current`,
  `reference`, `upcoming`, `drift_days`) plus `plan`, the whole projected
  calendar; `push --dry-run --json` emits the insert/update/delete plan
  before anything touches Google Calendar (and `push --json` the plan that
  was applied). stdout carries one JSON document, diagnostics stay on
  stderr, exit codes are unchanged. Part of #970.
