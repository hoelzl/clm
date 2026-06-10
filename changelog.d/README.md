# Changelog fragments

Pending changelog entries live here as one file per change, instead of being
inserted into `CHANGELOG.md`'s `[Unreleased]` section directly. Concurrent PRs
all inserting at the same lines of `CHANGELOG.md` was the dominant source of
merge conflicts; new files in this directory never conflict.

## Adding an entry

Create a file named

```
<pr-or-issue>-<short-slug>.<type>.md
```

where `<type>` is one of the Keep-a-Changelog section names in lowercase:
`added`, `changed`, `deprecated`, `removed`, `fixed`, `security`. Examples:

```
changelog.d/306-evergreen-release-files.added.md
changelog.d/302-manifest-git-pollution.fixed.md
```

The file content is the finished markdown entry exactly as it should appear in
`CHANGELOG.md` — usually a single `- **Bold summary.** …` bullet, written in
the same style as existing released sections. Multi-line bullets and multiple
bullets per file are fine.

Do **not** edit the `[Unreleased]` section of `CHANGELOG.md` in a PR.

## Releasing

At release time (Step 1 of `docs/developer-guide/releasing.md`):

```
python scripts/collect_changelog.py X.Y.Z
```

folds all fragments into a new `## [X.Y.Z] - DATE` section of `CHANGELOG.md`
(grouped by type, ordered by filename within each section) and deletes the
collected fragment files. Use `--dry-run` to preview. Entries that were added
to `[Unreleased]` by hand are folded in too, ahead of the fragments.

This `README.md` is ignored by the collector and keeps the directory present
in git when no fragments are pending.
