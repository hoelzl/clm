- `clm git status --json` (#969): one JSON array on stdout with one element
  per visited repository — `kind` (`target` / `channel`), `name`, `language`,
  `path`, `exists`, `initialized`, `branch`, `remote`, `ahead` / `behind`,
  `dirty`, `untracked` and the `git status --porcelain` `changes` — across
  `--target` / `--channel` / `--all-channels` / `--all`, shared destinations
  collapsed as in the text output. The `--dry-run` header and its
  `[dry-run] Would run:` stubs go to stderr so stdout stays one document;
  exit codes are unchanged. Part of #970.
