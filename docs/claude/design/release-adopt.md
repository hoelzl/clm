# Release Adopt: migrating push-published courses to release channels

**Status**: Implemented 2026-11-16 (`clm release adopt`, issue #1140); design accepted 2026-11-16 (D1–D7 as recommended, D6 extended)
**Issue**: #1140
**Branch**: `claude/design-1140-release-adopt`

## Problem

Some courses publish by pushing an output target's git repos directly. The
motivating case is CppCourses `cpp-for-programmers`: the `shared` target
(kinds `code-along` + `completed`) is built into
`output/shared/cpp-for-programmers-{de,en}`, and each of those directories is
a git working tree whose origin is `shared/cpp-for-programmers-{de,en}` on
GitLab. Students clone these repos and work **in place** in the Code-Along
files. So every rebuild that changes an already-taught deck becomes a merge
conflict for them. That includes a slide edit, a renumbering, and a clm
output-format change.

Per-topic freezing (`clm release`) is the fix. But `release sync` assumes it
is the first writer into the destination: for a topic in the ledger that
isn't frozen yet, it **copies from the current build**. Pointed at a repo
that already holds published content, the first sync would replace what
students have with today's build, which is exactly the change the migration
exists to avoid.

## Facts measured on the motivating course (2026-11-16)

- The `de` repo holds 2,438 manifest files: 114 topics with 1,576 files, plus
  **862 skeleton files** (`topic_id: null`). The skeleton is almost entirely
  the `code/` tree, copied by the spec's **global** `<dir-groups>`:
  `code/external` (623 files),
  `code/completed` (171) and `code/starter_kits` (61), plus 7 config files.
- The published repo is the build output directory itself. Right now the
  working tree (the current build) differs from published `HEAD` in 52
  Code-Along files. **"The destination" and "the current build" are
  therefore different things, even before any migration.**
- Paths include section numbers (`07 M5 Fehlerbehandlung/04 Mehr zu
  Ausnahmen.cpp`), and recent commits renumber decks. So a topic's paths in
  the current build can differ from its paths in the published commit.
- With `lang="de"`, the language-restricted manifest paths are already
  re-rooted at the language directory (`Folien/…`, `code/…`), matching the
  repo layout. `source-target="shared"`, a target that mixes code-along and
  completed kinds, is a supported channel source; `clm info releases` uses it
  in the shared-destination example. That answers the issue's open question.
- `topic_digest` is **written but never verified** anywhere
  (`clm.release.sync.apply_sync` is the only writer; there are no readers in
  release code). The recordings drift check computes the same digest from
  the provenance manifest, not from a frozen manifest. So an adopted record
  only has to use the same format: a sorted sha256 over file content hashes
  (`topic_digest_from_files`, `hash_file`).

## Two hard constraints the issue does not mention

1. **The channel's `path` cannot be the output directory.** `clm build` owns
   its output tree, and only `.git/` survives a build sweep. A channel working
   tree inside the output target would be rewritten by every build. The
   channel needs its own clone of the published remote, for example
   `cohorts/cpp-for-programmers-de`, cloned from
   `shared/cpp-for-programmers-de`. Students keep their remotes because the
   *remote* is unchanged; only the local working tree moves.
2. **The old push path must be cut off.** After migration,
   `clm git push` on the `shared` target would push the unfrozen build over
   the frozen channel repo. The output target must get a different remote,
   or no remote at all. This must be part of the documented procedure, and
   preferably detected (see D6).

## Proposal: `clm release adopt`

```
clm release adopt SPEC --channel NAME (TOPIC_ID... | --section SEL... | --all-present)
                  [--dry-run] [--json] [--deliver-missing]
```

For each selected topic, against the **channel's destination working tree**
(the clone, i.e. the published state):

1. Look up the topic's files in the current build's provenance manifest,
   restricted and re-rooted for the channel's `lang`.
2. Classify each file: `same` (destination bytes equal the build's
   `content_hash`), `differs` (present, but the bytes differ) or `missing`
   (not in the destination).
3. Plan the topic:
   - `adopt`: no file is missing. Freeze it with `source_commit` = the
     destination repo's `HEAD` commit, prefixed so it can't be confused with
     a source commit (e.g. `published:<sha>`); `copied_at` = now; and
     `topic_digest` computed from the **destination's** bytes. The topic is
     recorded as students have it, not as the build has it.
     `differs-from-build: true` is set when any file differs.
   - `refuse-missing`: some files are absent. By default the topic is
     neither added nor frozen (D3).
4. Append the adopted topics to the channel ledger, in the same way as
   `release add`.
5. Set `skeleton_frozen = true` **without copying anything**. Skeleton files
   that are already present stay as they are. A skeleton file the
   destination lacks is reported, and delivered only by
   `--refreeze-skeleton` / evergreen (D4).
6. Write `.clm-released.<stream>.json`. In the destination, never copy,
   delete or modify any other file. Adopt is a bookkeeping operation: it
   changes the frozen manifest, the ledger in the course repo, and (D6a) the
   output tree's git remote configuration, never content.

`--dry-run --json` gives one row per topic: `action` (`adopt` /
`refuse-missing` / `skip-frozen`) and the counts of `same`, `differs` and
`missing`. The `differs` rows double as the issue's "what would a rebuild
change for students" report.

### Recommended migration procedure (documented, not automated)

1. Make the published state and the build agree, if possible: build, and
   push the `shared` target one last time the old way. Adopt then reports
   `differs: 0`, and every adopted digest equals the build digest. Adopt also
   works without this step; the `differs` rows simply tell you what
   students won't receive.
2. Add `<release-channels source-target="shared">` with one channel per
   language. Each channel's `path` is a fresh clone location, and the
   `repo="…"` attribute (#322) points at the existing remote.
3. `clm git init SPEC --channel …` clones the existing remote, or clone it
   by hand.
4. Cut the old push path (constraint 2): drop or change the `shared`
   target's `<remote-path>` in the spec. `adopt` removes the output tree's
   `origin` remote itself (D6a).
5. `clm release adopt SPEC --channel … --all-present --dry-run`, review,
   then run it without `--dry-run`.
6. `clm git commit/push --channel …` publishes only the new
   `.clm-released.<stream>.json`, so students pull exactly one new file.

From then on the normal flow applies: `release add/section` + `sync` for
new topics, and `--refreeze` for deliberate corrections.

## Decisions (accepted by the owner, 2026-11-16)

| # | Question | Recommendation | Why |
|---|---|---|---|
| D1 | A separate command, or a `release sync --adopt` flag? | **A separate `release adopt` command** | Different invariant: sync copies, adopt never touches content. A flag on sync invites mixing the two in one run |
| D2 | When a topic's destination files differ from the build | **Adopt anyway; the digest comes from the destination; report `differs`** | Freezing what students *have* is the point. Refusing would force exactly the overwrite we're avoiding |
| D3 | When files are missing in the destination (renumbered or new deck) | **Refuse the topic by default. `--deliver-missing` copies only the missing files (never overwrites) and then freezes** | Missing files usually mean a renumber: the student has the old name and the build has the new one. Delivering silently would leave both copies in the repo. The owner should see it first |
| D4 | The 862-file `code/` skeleton | **Freeze it (presence-as-frozen). Don't make `code/**` evergreen.** Changes reach students via `--refreeze-skeleton 'code/…'` when you choose to send them | Students edit `code/starter_kits` in place; evergreen would overwrite their work. But see the open point below |
| D5 | Which topics: everything present, or an explicit list | **Require an explicit choice: topic ids, `--section`, or `--all-present`. No default** | Adopting a topic freezes it for good, so a typo or an empty argument must not adopt 114 topics |
| D6 | Guard against the old push path | **Two layers. (a) `adopt` removes the `origin` remote from any output-target working tree whose origin equals the channel's origin. It prints the removed URL and the `git remote add` command that restores it; `--keep-output-remote` opts out, and `--dry-run` only reports. (b) `clm git push` and `clm git init` in output-target mode refuse to push to, or re-add, a remote that is a release channel's origin; an override flag allows it** | Owner: a push of the output directory to that remote is far more likely a mistake than intended, even a manual one. Removing the remote alone doesn't last: `clm git init` re-adds `origin` from the target's `<remote-path>` (`_init_handle_existing_repo`), hence layer (b). The procedure also tells the author to drop or change the target's `<remote-path>` |
| D7 | `source_commit` for adopted records | **`published:<dest HEAD sha>`** | It's honest: we don't know the source commit, and the field is informational only (never read) |

### The `code/` skeleton (D4 background)

The `code/` tree is topic-less in the manifest because global `<dir-groups>`
copy it. `code/completed` holds the completed solutions for **every** topic,
and `code/starter_kits` the starters. That's fine for this course, because
the `shared` target already ships completed code. But under freezing, new
exercises added to `code/completed` or `code/starter_kits` after adoption
reach students only if the file is **missing** in their repo. Presence-as-
frozen delivers missing skeleton files only on a skeleton copy, and the
skeleton is frozen after adopt. So in practice they reach students only via
`--refreeze-skeleton`.

clm already supports topic ownership for copied directories: a `<dir-group>`
nested inside a `<topic>` records that topic in the manifest and keeps the
output placement (#208). Using it for about 170 subdirs would scatter the
`code/` layout over the spec, though.

Decided: ship adopt with D4 as written. **#1288** adds an optional
`<subdir topic="…">` owner to global dir-groups. The owner noted that
ownership must stay optional, because top-level material copied into the
course (`code/external`, the build files) belongs to no topic.

## Tests (when implemented)

- `same` / `differs` / `missing` classification against a destination fixture,
  with `lang` re-rooting.
- Adopt writes only the frozen manifest and the ledger. A whole-tree hash of
  the destination is identical before and after.
- The digest equals `topic_digest_from_files` over the destination's
  hashes, and equals the build digest when `differs == 0`.
- A later `release sync` reports every adopted topic `skip-frozen` and
  copies nothing, including the skeleton.
- `--deliver-missing` never overwrites an existing file.
- D5: no selector → usage error.
- D6a: adopt removes a matching output-tree `origin` remote and prints the restore
  command; `--keep-output-remote` and `--dry-run` leave it alone; a different
  origin is never touched.
- D6b: an output-target `git push` / `git init` to a channel's origin is refused;
  the override flag works.
- `clm info releases` and `clm info commands` get the command and the
  migration procedure (the info-topics maintenance rule).

## Implementation notes (2026-11-16)

Shipped as `clm release adopt` (`src/clm/release/adopt.py`, CLI in
`src/clm/cli/commands/release.py`; the D6b guard is `ChannelRemoteGuard` in
`src/clm/cli/commands/git.py`). Every claim below is executed by
`tests/release/test_adopt.py`. Where the code went past or beside the text
above:

- **Two extra topic actions.** `skip-unbuilt` (an explicitly selected topic
  with no files in the build manifest: nothing to compare) and `skip-failed`
  (the topic is in the manifest's `failed_topics`, #295). Neither is frozen.
- **`differs_from_build` lives in the frozen record**, written only when
  true, so ordinary sync records keep their shape
  (`TestAdopt::test_records_digest_commit_and_skeleton`).
- **D6b covers `clm git sync` too**, since it pushes, and the output targets
  of `--all`. A channel's own working tree is never refused, nor an output
  target whose path *is* a channel path. `init` refuses only when it would
  add or clone the remote (`TestChannelRemoteGuard`). Refusals exit 1.
- **Remote comparison** normalizes https / ssh / scp-like spellings to
  `host/path` and resolves local paths (`normalize_remote_url`,
  `TestNormalizeRemoteUrl`).
- **`--all-present`** selects topics with at least one built file in the
  destination, in spec order: a partly present topic shows up as
  `refuse-missing`, an entirely absent one is left for `add` + `sync`.
- **Multi-channel selection** (`--channel` globs, `--all-channels`) works as
  for `sync`.
- **The shared-destination overlap preflight** runs only with
  `--deliver-missing`, the only mode that writes topic files.
- **A run that adopts nothing** (and finds nothing frozen already) writes
  nothing: no frozen manifest, no `skeleton_frozen`, no remote removal. A
  premature `skeleton_frozen` would starve a later `add` + `sync` of the
  skeleton (`TestReviewFollowUps::test_nothing_adopted_records_nothing`).
- The ledger gets every selected topic that is frozen for the stream, so a
  re-run repairs an interrupted freeze-then-ledger write.
- D6a and D6b also match `remote.origin.pushurl`, the URL a push really uses.
- A destination with uncommitted changes gets a warning: the digest covers
  the working tree while `source_commit` names HEAD.
