- **Evergreen release files — `<evergreen>` patterns and `clm release sync
  --evergreen`.** Skeleton (global) files matching an evergreen glob pattern
  are exempt from the release freeze: every sync re-copies a matching file
  whose built content differs from the cohort's copy — for files that are
  *meant* to change over a cohort's lifetime (a NEWS file, announcements),
  which previously froze with the rest of the skeleton after the first sync
  and could never be updated. Patterns are declared on `<release-channels>`
  (inherited by every channel; channel-level entries are additive) or passed
  per-invocation with the repeatable `--evergreen` option, and match
  destination-relative POSIX paths (re-rooted paths for `lang`-scoped
  channels). Evergreen is skeleton-only by design: patterns matching
  topic-owned files are warned about and ignored (topic content still changes
  only via `--refreeze`), and the comparison is stateless (destination hash
  vs. manifest hash) so the `.clm-released.json` format is unchanged and
  re-runs are idempotent.
