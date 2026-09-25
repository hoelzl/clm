# CLM {version} — Solution Release Reference

The release system lets a trainer publish course solutions to student cohorts
**one topic at a time**. Each cohort progresses at its own pace; once a topic
is released to a cohort it is **frozen** — later edits to the course source
never rewrite what students already received.

## Concepts

| Term | What it is |
|---|---|
| **Channel** | One student cohort's git repository, declared in the spec |
| **Ledger** | Plain-text list of released topic ids for that cohort (`release/<name>.txt`) |
| **Provenance manifest** | `.clm-manifest.json` — maps every built output file to its source topic; written by `clm build`; never distributed |
| **Frozen manifest** | `.clm-released.<stream>.json` (legacy `.clm-released.json` for a single unnamed stream) — per-cohort, per-stream freeze record inside the cohort repo; distributed to students |

## Spec configuration

```xml
<release-channels source-target="solutions" name="materials">
    <remote-path>cohorts</remote-path>
    <share-with group="trainers" access="maintainer" />
    <evergreen>NEWS.md</evergreen>

    <channel name="jan" path="./cohorts/jan" ledger="release/jan.txt">
        <share-with group="cohort-jan" access="developer" />
    </channel>

    <channel name="may" path="./cohorts/may" ledger="release/may.txt" lang="de" />
</release-channels>
```

| Attribute | Where | Required | Description |
|---|---|---|---|
| `source-target` | `<release-channels>` | yes | Name of the `<output-target>` to promote from (typically a `completed`-kind target) |
| `name` | `<release-channels>` | when multiple blocks | Stream name; channels are addressed as `stream/channel` (e.g. `materials/jan`) |
| `name` | `<channel>` | yes | Cohort identifier used on the CLI |
| `path` | `<channel>` | yes | Path to the cohort's git working tree. Unique within a stream; channels of *different* streams may share a path to release into one repository (see below) |
| `ledger` | `<channel>` | yes | Path to the release ledger file |
| `lang` | `<channel>` | no | Restrict promotion to one language; re-roots files at the language directory |
| `<share-with group="…" access="…">` | block or channel | no | GitLab group sharing (applied by `clm release provision`) |
| `<evergreen>` | block or channel | no | Glob pattern of skeleton files exempt from the freeze — re-copied on every sync when the built content changed (e.g. a NEWS file). Block patterns are inherited; channel patterns are additive |

The derived remote URL is:
`{repository-base}/{remote-path}/{project-slug}-{channel}-{stream}[-{lang}]`

## File formats

### Ledger (`release/jan.txt`)

Plain text, one topic id per line. Comments (`#`) and blank lines ignored.
Cumulative — entries are never removed. Edit by hand or via `clm release add/week`.

```
# release/jan.txt
introduction
variables
control_flow
```

### Provenance manifest (`.clm-manifest.json`)

Written by `clm build` into each output target root. Maps output files to topics.
**Private — never committed or distributed.** `clm git commit/sync` excludes it
automatically.

```json
{
  "version": 1,
  "spec": "course.xml",
  "target": "solutions",
  "source_commit": "abc1234def",
  "partial": false,
  "failed_topics": [],
  "files": [
    {"path": "Sec_01/01 Introduction.ipynb", "topic_id": "introduction", ...},
    {"path": "shared/data.csv", "topic_id": null, ...}
  ]
}
```

`topic_id: null` entries are skeleton/global files not owned by any topic.
`failed_topics` lists topics whose build errored; they are refused by sync until
the next successful build.

### Frozen manifest (`.clm-released.<stream>.json`)

Written into the cohort repo by `clm release sync`. Committed and distributed.
The file is **per release stream**: a named stream writes
`.clm-released.<stream>.json` (e.g. `.clm-released.materials.json`); a single
unnamed `<release-channels>` block keeps the legacy `.clm-released.json` name.
A pre-existing legacy file whose `channel` field matches is adopted and
renamed on the channel's next sync — no manual migration.

```json
{
  "version": 1,
  "channel": "materials/jan",
  "skeleton_frozen": true,
  "frozen": {
    "introduction": {"source_commit": "abc123", "copied_at": "2026-03-10T10:00:00Z", "topic_digest": "sha256:…"},
    "variables":    {"source_commit": "abc123", "copied_at": "2026-03-17T10:00:00Z", "topic_digest": "sha256:…"}
  }
}
```

Once a `topic_id` appears in `frozen`, subsequent syncs skip it — students keep
exactly what they were given. Only `--refreeze` overrides this.

## `clm release` commands

### Addressing one or many channels

`add`, `week`, `status`, and `sync` all take the same channel selection. A
single `--channel NAME` (an exact `STREAM/CHANNEL` or a unique bare name) keeps
the original behavior. To hit several channels in one invocation (CLM
{version}+, issue #390):

- **Glob:** `--channel 'materials/*'` or `--channel '*/2026-04-*'` — matched
  with `fnmatch` against each channel's `ADDRESS`.
- **Repeat:** `--channel materials/2026-04-de --channel solutions/2026-04-de`.
- **`--all-channels`:** every channel in every `<release-channels>` block.

Matched channels are de-duplicated and processed in spec order. Explicit
`--ledger`/`--source`/`--dest` address a single channel and cannot be combined
with multi-channel selection.

### `clm release channels`

List the declared channels — the canonical source of the addresses the
selectors above match.

```
clm release channels SPEC          # ADDRESS / LANG / SOURCE / LEDGER / DEST table
clm release channels SPEC --json   # same rows as JSON, for scripting
```

### `clm release add`

Append topic ids to one or more channel ledgers (validates against spec).

```
clm release add SPEC TOPIC_ID... --channel NAME
clm release add SPEC TOPIC_ID... --channel 'materials/*'   # glob → every match
clm release add SPEC TOPIC_ID... --all-channels            # every channel
clm release add SPEC TOPIC_ID... --ledger release/jan.txt
```

### `clm release week` / `clm release section`

Release every topic in one or more course sections.

```
clm release week SPEC SELECTOR... --channel NAME
clm release section SPEC SELECTOR... --channel NAME   # same command, structural name
```

`section` is an alias of `week` with identical behaviour (since {version},
#1009): a section is a thematic block, and "week" is what `clm export
schedule` and the cohort calendar make of it by convention (`clm info
calendar`, "Sections, weeks and teaching days"). Both spellings are kept —
course-repo runbooks written against `week` keep working.

Selectors: bare index (`1`), `id:SECTION_ID`, `idx:N`, `name:SUBSTRING`.
Section indices are disabled-inclusive — enabling/disabling sections does not
renumber the sections that follow.

### `clm release status`

Show released vs pending topics and (with `--channel` or `--dest`) the frozen state.

```
clm release status SPEC --channel NAME
clm release status SPEC --all-channels --json      # one row per channel, for scripting
```

`--json` (CLM {version}+, issue #967) emits an array — one row per channel
in spec order (a single row with `"channel": ""` in explicit `--ledger`
mode):

```json
[
  {
    "channel": "materials/2026-04", "stream": "materials",
    "ledger": "/course/release/materials-2026-04.txt",
    "dest": "/course/release/materials/2026-04",
    "topics_total": 12,
    "released": ["intro", "functions"], "pending": ["lists", "…"],
    "frozen": ["intro"], "awaiting_sync": ["functions"],
    "skeleton_frozen": true,
    "frozen_manifest": "/course/release/materials/2026-04/.clm-released.materials.json"
  }
]
```

`frozen`, `awaiting_sync`, `skeleton_frozen` and `frozen_manifest` are
`null` when no destination is known (explicit `--ledger` without `--dest`).

### `clm release sync`

**Core step.** Promote released-but-not-frozen topics into the cohort repo.

```
clm release sync SPEC --channel NAME [--dry-run] [--push] [-m MESSAGE]
clm release sync SPEC --channel NAME --refreeze TOPIC_ID... [--push]
clm release sync SPEC --channel NAME --refreeze-all [--push]
clm release sync SPEC --channel NAME --evergreen PATTERN [--push]
clm release sync SPEC --channel NAME --refreeze-skeleton PATTERN [--push]
clm release sync SPEC --all-channels --push          # promote + push every channel
clm release sync SPEC --channel NAME --dry-run --json # the promotion plan as rows
```

**`--dry-run --json`** (CLM {version}+, issue #967) emits the plan as one
array with a document per channel, so an agent can decide what to release
and predict what a sync will do:

```json
[
  {
    "channel": "materials/2026-04", "stream": "materials",
    "source": "/course/output/shared", "dest": "/course/release/materials/2026-04",
    "language": "de", "dry_run": true, "partial_manifest": false,
    "skeleton": {"action": "frozen", "file_count": 7, "present_count": 0},
    "rows": [
      {"kind": "topic", "action": "copy", "topic_id": "functions", "file_count": 4},
      {"kind": "topic", "action": "skip-frozen", "topic_id": "intro", "file_count": 3},
      {"kind": "topic", "action": "skip-failed", "topic_id": "lists", "file_count": 0},
      {"kind": "skeleton", "action": "refresh", "path": "NEWS.md", "label": "evergreen"}
    ],
    "evergreen_up_to_date": 1,
    "push": {"requested": true, "message": "Release to materials/2026-04: 1 new, 1 evergreen"}
  }
]
```

A first sync's `skeleton` carries `"action": "copy"` and the `files` it
would freeze. `push.message` is the commit message `--push` would use (your
`-m` text, else the generated summary). `--json` without `--dry-run` runs
the sync and adds `result` (`files_copied`, `copied_topics`,
`refrozen_topics`, `skipped_topics`, `failed_topics`, `skeleton_copied`,
`refreshed_files`); combined with `--push` it needs `--dry-run`, because
the commit/push output is not JSON. Notes and warnings go to stderr.

**First delivery to a fresh cohort** (issue #868): the destination directory
is created by `clm release sync`, not by `clm build`, and `clm git init
--channel` creates it (empty) when it does not exist yet (CLM {version}+). So
either order works:

```
clm git init SPEC --channel NAME            # repo + remote; creates the empty destination
clm release sync SPEC --channel NAME --push # first promotion, committed and pushed
```

or `release sync` first (no `--push`), then `git init --channel`, then
`git push --channel`. A remote project's `default_branch` may still need
reconciling after the first push (issue #955).

Sync actions per topic:

| Action | When |
|---|---|
| `copy` | Released, not yet frozen → copy files, freeze |
| `skip-frozen` | Already frozen → skip |
| `refreeze` | Frozen but in `--refreeze` set → re-copy, update freeze |
| `skip-failed` | Build errored for this topic → refuse until next clean build |

Skeleton (global files) is copied once on first sync and then frozen —
**except evergreen files**: skeleton files matching the channel's
`<evergreen>` patterns (or `--evergreen` options) plan `refresh` whenever the
built content differs from the cohort's copy, and `up-to-date` otherwise.
Evergreen is skeleton-only; patterns matching topic-owned files are warned
about and ignored (topic content changes only via `--refreeze`). The
comparison is stateless (destination hash vs. manifest hash), so nothing is
recorded in the frozen manifest and re-runs are idempotent.
`--push` chains `clm git commit` + `clm git push` after promotion.

**The skeleton freeze is a one-shot decision (issue #869).** The first sync
prints the skeleton files it freezes, with a reminder that only `<evergreen>`
matches stay updatable, so the decision is visible while it is cheap. Three
things follow from the comparison being stateless:

- **A frozen skeleton file has an escape hatch**: `--refreeze-skeleton
  PATTERN` (CLM {version}+) re-copies matching skeleton files from the current
  build — the topic `--refreeze` for the onboarding surface (a setup doc with a
  stale clone URL, a README). Plan lines are labelled `refreeze-skeleton`,
  the result line `Skeleton refreeze: re-copied …`; nothing is recorded, so
  the file is frozen again afterwards. A pattern matching no skeleton file is
  reported.
- **Adding an `<evergreen>` pattern after the first sync works**: the check
  is destination hash vs manifest hash, independent of `skeleton_frozen`.
- **A skeleton file absent from the destination counts as differing**, so a
  later `<evergreen>` match or `--refreeze-skeleton` delivers a file the
  cohort never received. "Ship a placeholder now, replace it when ready" is
  therefore a safe pattern.

**Evergreen freshness is fixed at build time**: the sync compares the
cohort's copy against the *built* content, so regenerate the sources of
evergreen files (e.g. exported outlines or schedules, often produced by a
spec-declared task via `clm export outline` / `clm calendar generate`)
**before** `clm build` — otherwise the sync truthfully reports `up-to-date`
for the stale content the build baked in, and a `refresh` you expect never
appears.

### Shared destination: several streams, one cohort repo

Channels of **different** streams may point at the same `path` — both streams
then release into a single repository students pull from (e.g. materials at
session start, solutions after the exercise), each on its own ledger and
timeline. Sharing is detected from the spec; no extra configuration:

```xml
<release-channels name="materials" source-target="shared">
    <channel name="2026-04-de" lang="de" path="cohorts/2026-04/combined-de"
             ledger="release/materials-2026-04-de.txt"/>
</release-channels>
<release-channels name="solutions" source-target="solutions">
    <channel name="2026-04-de" lang="de" path="cohorts/2026-04/combined-de"
             ledger="release/solutions-2026-04-de.txt"/>
</release-channels>
```

How the pieces interact:

- **Per-stream freeze records.** Each stream keeps its own
  `.clm-released.<stream>.json`, so materials releasing a topic never freezes
  it for solutions (and `--refreeze` stays scoped to one stream's files).
- **No conflicting outputs.** The streams' notebook outputs must be disjoint
  (e.g. code-along/partial kinds vs completed kinds — they land in different
  subtrees). A topic's *static* files (project scaffolding, data) are built
  verbatim into every target and may be claimed by both streams as long as
  they are **byte-identical** — whichever stream releases the owning topic
  first delivers them. `clm release sync` cross-checks the source manifests
  of all streams sharing the destination and refuses to promote when a
  shared topic-owned path has *differing* content (usually: the targets were
  built from different source states — rebuild both). A sharer that is not
  built yet is skipped with a note.
- **Skeleton: presence-as-frozen.** A skeleton file already present in the
  destination is kept, never overwritten — the second stream's first sync
  copies only the skeleton files the first one didn't deliver. Evergreen
  files still refresh by content. Because the two streams may sync from
  different builds, **sync both streams after the same `clm build`** to keep
  evergreen content from ping-ponging.
- **Same `lang` required.** Channels sharing a path must declare the same
  `lang` (spec validation error otherwise).
- **One repo to `clm git`.** `--all-channels` visits the shared working tree
  once (displayed as `materials/… (+ solutions/…)`); `clm release provision`
  applies each group share once per repository. **Caveat:** `clm git reset
  --channel` hard-resets the whole repo — it discards the *other* stream's
  uncommitted promotions too; re-run that stream's sync afterward.
- **What you give up.** With separate repos the materials repo *cannot*
  contain a solution. Shared, the ledger is the only gate: a wrong
  `release add` or premature sync publishes solutions into the repo students
  already pull (and into its git history). Double-check `--dry-run` output
  on the solutions stream.

To migrate a running cohort, point the solutions channels' `path` at the
existing materials working trees: students keep their remotes, the materials
stream adopts the legacy `.clm-released.json` on its next sync, and the
standalone solutions repos simply stop receiving syncs.

### `clm release provision`

Share channel repos with GitLab groups (requires `CLM_GITLAB_TOKEN` or
`GITLAB_TOKEN` with `api` scope — exported, or in the project's `.env`, which
is loaded first as `clm build` does; issue #870).

```
clm release provision SPEC [--channel NAME] [--dry-run]
```

`--dry-run` previews the shares and ends with a `credentials:` line — which
token variable is set, or `MISSING — the real run will fail` — so the preview
validates the whole setup, not just channel/group resolution. Its exit code
stays 0 either way.

## `clm git` commands

All `clm git` subcommands operate on output targets by default; add
`--channel NAME` or `--all-channels` to operate on cohort repos instead.
`.clm-manifest.json` is always excluded from staging; the frozen manifests
(`.clm-released*.json`) are always included. Channels sharing a destination
are visited once per repo, not once per channel.

| Command | What it does |
|---|---|
| `clm git init SPEC [--channel NAME]` | Initialize git repo; clone from remote if it exists |
| `clm git status SPEC [--channel NAME]` | Branch, remote URL, ahead/behind, changed files |
| `clm git commit SPEC -m MSG [--channel NAME]` | Stage + commit (excludes manifest) |
| `clm git push SPEC [--channel NAME]` | Push to configured remote |
| `clm git sync SPEC -m MSG [--channel NAME]` | Commit + push in one step |
| `clm git reset SPEC [--channel NAME]` | Hard-reset to remote tracking branch (discards local changes) |

`clm git sync --amend` implies `--force-with-lease` (required for amended commits).

## Standard workflow

```bash
# 1. Add the week's topics to the ledger
clm release week course.xml 1 --channel jan

# 2. Preview what will be promoted
clm release sync course.xml --channel jan --dry-run

# 3. Promote and push to the cohort repo
clm release sync course.xml --channel jan --push -m "Release Week 1"

# 4. Check status
clm release status course.xml --channel jan
```

### Re-releasing a corrected topic

```bash
# Fix the source and rebuild
clm build course.xml

# Re-freeze the corrected topic for jan (already received it)
clm release sync course.xml --channel jan --refreeze functions --push \
  -m "Fix functions solution"

# Cohorts that haven't received it yet get the fix automatically on next sync
```

### Recovering when the remote is ahead

```bash
clm git reset course.xml --channel jan   # discard local changes
clm build course.xml                      # rebuild (fast via cache)
clm release sync course.xml --channel jan --push -m "Resync"
```

### Multiple cohorts, independent schedules

```bash
clm release add course.xml functions --channel jan
clm release sync course.xml --channel jan --push -m "jan: release functions"

clm release add course.xml introduction variables --channel may
clm release sync course.xml --channel may --push -m "may: release week 1"
```

### Updating a NEWS file across releases

```bash
# Spec: <evergreen>NEWS.md</evergreen> inside <release-channels>.
# Edit the source NEWS file, rebuild, and sync — every cohort's copy follows.
clm build course.xml
clm release sync course.xml --channel jan --push -m "Update NEWS"
```

## Key design properties

- **Cumulative ledger** — entries are never removed; minimal per-release git diff.
- **Immutable freezing** — frozen topics are never re-propagated without `--refreeze`;
  the only exception is **evergreen** skeleton files (declared via `<evergreen>`),
  which follow the latest build by design.
- **Provenance-driven** — files are promoted via manifest lookup, not path inference.
- **Idempotent syncs** — re-running sync is safe; frozen topics are skipped.
- **Manifest exclusion** — `.clm-manifest.json` is never distributed; the per-stream frozen manifests (`.clm-released.<stream>.json`) are.
- **Stream-scoped freezing** — streams sharing one destination repo freeze independently; presence-as-frozen protects the shared skeleton.

See `clm info commands` for the full flag reference.
