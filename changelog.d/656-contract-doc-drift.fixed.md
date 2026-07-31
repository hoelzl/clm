- `clm info sync-agents` no longer states two things the engine does not do:
  `--dry-run` was described as validating "everything", but it stops before the
  write and therefore never runs the structural verify gate — a clean dry run
  is not a promise that the pass will record; and `pos: → id:` was called the
  only key migration, which stopped being true when `clm slides rename-id`
  gained `id: → id:` (including the cascade of a renamed group anchor into its
  members' positional keys and order scopes).
- The refusal for an unanswerable item no longer says "has no decision
  vocabulary in Phase 3" — internal jargon with no remedy in it. It now names
  the state (`resolution: manual`) and the way out: read the item's detail,
  repair the files, re-report.
