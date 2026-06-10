- **Changelog entries are now fragment files in `changelog.d/`.** PRs no
  longer edit the `[Unreleased]` section of `CHANGELOG.md` (concurrent PRs
  inserting at the same lines made changelog merge conflicts near-universal);
  each PR instead adds `changelog.d/<pr-or-issue>-<slug>.<type>.md` with the
  finished markdown bullet. At release time the new
  `scripts/collect_changelog.py` folds all fragments (plus any stray
  hand-written `[Unreleased]` entries) into a `## [X.Y.Z]` section, grouped
  Added/Changed/Deprecated/Removed/Fixed/Security, and deletes them.
  Conventions in `changelog.d/README.md`; release procedure updated in
  `docs/developer-guide/releasing.md`.
