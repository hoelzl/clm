- `clm query affected-specs` — map changed file paths (arguments and/or
  `--stdin`) to the course specs whose builds they can influence, using the
  same topic/include/dir-group resolution as the build (single-file topics
  also claim the sibling files their content references). Fails open
  (`"all": true`) for build-relevant paths no spec claims, so a CI matrix
  built from the `--json` output never silently skips a course; clearly
  build-irrelevant paths (`.github/`, top-level docs) and content invisible
  to every build (unreferenced topics, `_archive` dirs) affect nothing.
  First member of the new **`clm query`** group for read-only,
  scripting-oriented introspection commands (issue #350).
