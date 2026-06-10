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
| **Frozen manifest** | `.clm-released.json` — per-cohort freeze record inside the cohort repo; distributed to students |

## Spec configuration

```xml
<release-channels source-target="solutions" name="materials">
    <remote-path>cohorts</remote-path>
    <share-with group="trainers" access="maintainer" />

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
| `path` | `<channel>` | yes | Path to the cohort's git working tree |
| `ledger` | `<channel>` | yes | Path to the release ledger file |
| `lang` | `<channel>` | no | Restrict promotion to one language; re-roots files at the language directory |
| `<share-with group="…" access="…">` | block or channel | no | GitLab group sharing (applied by `clm release provision`) |

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

### Frozen manifest (`.clm-released.json`)

Written into the cohort repo by `clm release sync`. Committed and distributed.

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

### `clm release add`

Append topic ids to a channel's ledger (validates against spec).

```
clm release add SPEC TOPIC_ID... --channel NAME
clm release add SPEC TOPIC_ID... --ledger release/jan.txt
```

### `clm release week`

Release every topic in one or more course sections.

```
clm release week SPEC SELECTOR... --channel NAME
```

Selectors: bare index (`1`), `id:SECTION_ID`, `idx:N`, `name:SUBSTRING`.
Section indices are disabled-inclusive — enabling/disabling sections does not
renumber the sections that follow.

### `clm release status`

Show released vs pending topics and (with `--channel` or `--dest`) the frozen state.

```
clm release status SPEC --channel NAME
```

### `clm release sync`

**Core step.** Promote released-but-not-frozen topics into the cohort repo.

```
clm release sync SPEC --channel NAME [--dry-run] [--push] [-m MESSAGE]
clm release sync SPEC --channel NAME --refreeze TOPIC_ID... [--push]
clm release sync SPEC --channel NAME --refreeze-all [--push]
```

Sync actions per topic:

| Action | When |
|---|---|
| `copy` | Released, not yet frozen → copy files, freeze |
| `skip-frozen` | Already frozen → skip |
| `refreeze` | Frozen but in `--refreeze` set → re-copy, update freeze |
| `skip-failed` | Build errored for this topic → refuse until next clean build |

Skeleton (global files) is copied once on first sync and then frozen.
`--push` chains `clm git commit` + `clm git push` after promotion.

### `clm release provision`

Share channel repos with GitLab groups (requires `CLM_GITLAB_TOKEN`).

```
clm release provision SPEC [--channel NAME] [--dry-run]
```

## `clm git` commands

All `clm git` subcommands operate on output targets by default; add
`--channel NAME` or `--all-channels` to operate on cohort repos instead.
`.clm-manifest.json` is always excluded from staging; `.clm-released.json`
is always included.

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

## Key design properties

- **Cumulative ledger** — entries are never removed; minimal per-release git diff.
- **Immutable freezing** — frozen topics are never re-propagated without `--refreeze`.
- **Provenance-driven** — files are promoted via manifest lookup, not path inference.
- **Idempotent syncs** — re-running sync is safe; frozen topics are skipped.
- **Manifest exclusion** — `.clm-manifest.json` is never distributed; the frozen manifest `.clm-released.json` is.

See `clm info commands` for the full flag reference.
