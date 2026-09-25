- `clm slides rename PATH NEW_STEM` (#991) renames a split deck's file stem
  atomically: both halves and their separated voiceover companions move
  together (`git mv` inside a work tree), the deck's section in the per-topic
  sync ledger is re-keyed as a pure rename (the deck stays warm — `sync
  report` is clean afterwards), the build cache's path-keyed rows follow the
  files (the `course renumber` migration), and `clm validate` runs on the
  result. Refuses before touching anything on a name collision, a companion
  present in both layouts, a ledger section already under the new stem, or a
  non-bare stem. `--single`, `--no-cache-migrate`, `--no-validate`,
  `--dry-run`, `--json`. Exit `1` when the post-rename validation reports
  errors; `2` when refused.
