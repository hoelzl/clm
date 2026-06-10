# Evergreen Release Files

**Status**: Implemented
**Branch**: `claude/release-evergreen-files`

## Problem

`clm release sync` is add-only by design: once content reaches a cohort it is
frozen and never modified again. The freeze operates at two boundaries
(`src/clm/release/sync.py`):

1. **Topics** — a topic id recorded in `.clm-released.json` is skipped by later
   syncs (`skip-frozen`); `--refreeze` is the explicit override.
2. **Skeleton** — all global files (`topic_id: null` in the provenance
   manifest: dir-groups, shared data) are copied once on the first sync, then
   `skeleton_frozen = true` forever. **No override exists.**

Some global files are *meant* to change over the lifetime of a cohort — a NEWS
file, announcements, a schedule. Today they freeze with the rest of the
skeleton after the first sync and can never be updated.

## Design: evergreen files

The complement of "frozen": a set of glob patterns naming **skeleton files that
are never frozen** — every sync re-promotes a matching file when the built
content differs from what is in the cohort repo.

### Spec syntax

Declared in `<release-channels>`, inherited by every channel (same parse-time
inheritance as `<share-with>`); channel-level entries are **additive** (union,
not override — patterns have no identity key):

```xml
<release-channels source-target="solutions" name="materials">
    <evergreen>NEWS.md</evergreen>
    <evergreen>announcements/*</evergreen>
    <channel name="jan" path="./cohorts/jan" ledger="release/jan.txt">
        <evergreen>jan-schedule.md</evergreen>
    </channel>
</release-channels>
```

CLI escape hatch on `clm release sync` for explicit-paths mode (which has no
spec block): `--evergreen PATTERN` (repeatable, additive with the channel's
patterns).

The name deliberately avoids `refresh` — one typo away from the existing
`--refreeze`, which does something different. "Evergreen" is the natural
antonym of the system's freeze metaphor.

### Semantics

- **Skeleton-only.** Patterns match only manifest entries with
  `topic_id: null`. A pattern that matches a *topic-owned* file is reported
  (warning pointing at `--refreeze`) and ignored. This keeps every existing
  invariant intact: `topic_digest` stays truthful for drift detection,
  `--refreeze` remains the only way topic content changes, and an evergreen
  pattern can never leak files of an unreleased topic into a cohort.
- **Matching**: `fnmatch.fnmatchcase` against the manifest-relative POSIX path
  — the path *as it appears in the cohort repo*. For `lang`-scoped channels
  that is the path after `restrict_manifest_to_language` re-roots it, so
  patterns are written dest-relative. `*` crosses `/` (fnmatch semantics), so
  `NEWS*.md` matches at the root and `*/NEWS.md` matches at any depth.
- **Copy-on-change, stateless**: an evergreen file plans `refresh` when the
  destination file is missing or its sha256 differs from the manifest's
  `content_hash`, else `up-to-date`. The destination *is* the state — no
  change to the `.clm-released.json` format, no version bump, idempotent
  re-runs, and `--push` commits nothing when nothing changed. This is sound
  because promotion copies bytes verbatim: after a copy, dest hash ==
  manifest hash.
- **First sync**: the skeleton copy already delivers evergreen files; the
  evergreen pass only operates once the skeleton is frozen
  (`plan.copy_skeleton == False`). A skeleton file *added* to the source after
  the skeleton froze still reaches cohorts if it matches an evergreen pattern
  (dest missing → refresh); non-evergreen late additions stay frozen out, as
  before.
- **Failed builds**: a partial manifest already excludes failed topics' files;
  skeleton entries have no topic, so an intact skeleton still refreshes during
  a partial build.
- **VCS guard**: evergreen reuses the same `.git`/`.svn`/`.hg` refusal as all
  promotion copies (issue #302), and the scan additionally never plans a VCS
  path.
- **No deletions**: removing an evergreen file from the source stops
  refreshing it but does not delete it from cohorts — sync never deletes.

### Rejected alternatives

- **Recording refreshes in `.clm-released.json`** — a second source of truth
  plus a format version bump; hashing a handful of small matched dest files
  per sync is free.
- **Always-copy instead of copy-on-change** — loses the "refreshed N files"
  signal and makes every `--push` produce mtime-churn.
- **Allowing evergreen to match topic files** — breaks the `topic_digest`
  drift contract and opens the unreleased-topic leak. If a use case emerges
  it is an explicit v2 with digest-exclusion semantics, not a default.

## Implementation map

| Layer | Change |
|---|---|
| `core/course_spec.py` | `evergreen` on `ReleaseChannelSpec`/`ReleaseChannelsSpec`, `<evergreen>` parsing with block→channel inheritance, validation (no empty/backslash patterns) |
| `core/provenance_manifest.py` | `hash_file` made public (shared by the evergreen scan) |
| `release/sync.py` | `scan_evergreen(manifest, patterns, dest_root)` → `EvergreenScan` (plans + ignored topic-owned matches); `SyncPlan.evergreen`; `apply_sync` refresh pass (skipped while `copy_skeleton`); `SyncResult.refreshed_files` |
| `cli/commands/release.py` | `--evergreen` on `sync`, channel pattern resolution, plan display (`refresh`/up-to-date), result + push-message reporting, topic-owned-match warning |
| Info topics | `releases.md`, `spec-files.md`, `commands.md` |

Tests: `tests/core/test_release_channels_spec.py` (parsing/validation),
`tests/release/test_sync.py` (scan + apply semantics),
`tests/release/test_release_cli.py` (end-to-end refresh across syncs,
explicit-mode flag, lang re-rooting, warnings, dry-run).
