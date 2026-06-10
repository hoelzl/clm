# Per-Topic Solution Release

Release course solutions to student cohorts **one topic at a time** — and keep
what each cohort already received **frozen** so later edits to the course never
rewrite history for a group that has already moved on. Multiple cohorts can each
progress on their own schedule from the same course source.

This is CLM's answer to the "delayed solutions" problem: students should get the
worked-out solution for a topic only *after* its workshop has been discussed, and
once they have it, it should not silently change underneath them.

> Reference docs: `clm info commands` (the `clm release` and `clm git` groups)
> and the [`<release-channels>` element](spec-file-reference.md#release-channels-per-cohort-solution-release)
> in the spec-file reference.

## How it works

The workflow has four moving parts:

| Piece | What it is | Where it lives |
|---|---|---|
| **Channel** | One student cohort = one git repository | declared in `<release-channels>` in the spec |
| **Ledger** | Plain-text list of released topic ids (one per line) | a file in your **course source** repo (e.g. `release/jan.txt`) |
| **Provenance manifest** | Maps each built output file → its owning topic | `.clm-manifest.json`, written by `clm build` (on by default) |
| **Frozen manifest** | The per-cohort freeze record (what was promoted, and the bytes' hash) | `.clm-released.json`, committed inside the cohort repo |

The **ledger** is the volatile state — it grows every week as you release more
topics — and it deliberately lives *outside* the spec so the spec stays
diff-clean. The **provenance manifest** is what makes per-topic promotion
possible at all: a built output path (e.g. `output/.../03 Functions.html`) does
not by itself tell CLM which source topic produced it, so `clm build` records
that mapping in `.clm-manifest.json`.

## 1. Configure channels in the spec

Add a `<release-channels>` block to your course spec. It names each cohort and
points at the `completed`-kind output target that is the **frozen source** —
the built tree topics are promoted out of.

```xml
<output-targets>
    <!-- ... your students / solutions targets ... -->
    <output-target name="solutions">
        <path>./dist/solutions</path>
        <kinds><kind>completed</kind></kinds>
    </output-target>
</output-targets>

<release-channels source-target="solutions">
    <remote-path>cohorts</remote-path>
    <channel name="jan" path="./solutions/jan" ledger="release/jan.txt"/>
    <channel name="may" path="./solutions/may" ledger="release/may.txt"/>
</release-channels>
```

See the [spec-file reference](spec-file-reference.md#release-channels-per-cohort-solution-release)
for the full attribute table and remote-URL derivation.

## 2. Build with the provenance manifest

Build the frozen source as usual. The manifest is written by default:

```bash
clm build course.xml        # writes .clm-manifest.json under each output root
```

(The manifest is suppressed for `--snapshot`, `--verify-against`,
`--only-sections`, and errored builds — so build the *whole* solutions target
when you intend to release from it.)

## 3. Create each cohort repo (once)

```bash
clm git init course.xml --channel jan      # create the 'jan' cohort repository
```

`clm git init` is idempotent — re-run it after creating the remote to wire up
`origin`.

## 4. Release topics as their workshops wrap

Append topic ids to the cohort's ledger with `clm release add` (validated
against the spec, so a typo'd topic id is caught immediately):

```bash
clm release add course.xml functions lists --channel jan
```

Then promote the released-but-not-yet-frozen topics into the cohort repo and
push, in one step:

```bash
clm release sync course.xml --channel jan --push -m "Release functions, lists"
```

`release sync` copies only the released topics' bytes (located via the
provenance manifest) into the cohort working tree, records them in
`.clm-released.json`, and — with `--push` — commits and pushes the cohort repo
using `clm git`'s machinery. A topic that is already frozen is **never**
re-copied, so re-running `sync` is safe and only ever adds new releases.

Preview first if you like:

```bash
clm release sync course.xml --channel jan --dry-run    # print the plan, copy nothing
```

## Releasing a whole week at once

A "week" is a course section. `clm release week` expands the selected
section(s) to their topic ids and appends them all to the ledger — a
section-scoped `release add`:

```bash
clm release week course.xml "name:Week 1" --channel jan
clm release week course.xml idx:3 --channel jan        # the 3rd section
```

`SELECTORS` use the same grammar as `build --only-sections` (`id:` / `idx:` /
`name:` prefixes, or a bare 1-based index or name substring). Section indices
are **disabled-inclusive** — an `enabled="false"` section still consumes its
index, and a selected-but-disabled section is reported and skipped rather than
silently shifting which topics get released.

## Checking what's released

```bash
clm release status course.xml --channel jan
```

This shows released vs pending topics and, when the cohort repo is resolvable,
which released topics are **frozen** vs still **awaiting sync**.

## Re-freezing a topic (e.g. a bug fix)

A frozen topic is intentionally never re-propagated. If you must push a
correction to a topic a cohort already received, opt in explicitly:

```bash
clm release sync course.xml --channel jan --refreeze functions --push -m "Fix functions solution"
clm release sync course.xml --channel jan --refreeze-all       # re-freeze everything (rare)
```

## Evergreen files (e.g. a NEWS file)

Global (skeleton) files — those not owned by any topic, such as shared data or
dir-group content — are copied once on the first sync and then frozen with the
rest of the skeleton. Some of them are *meant* to change over a cohort's
lifetime: a NEWS file, announcements, a schedule. Declare those as
**evergreen** and every sync re-copies them whenever the built content differs
from the cohort's copy:

```xml
<release-channels source-target="solutions">
    <evergreen>NEWS.md</evergreen>
    <channel name="jan" path="./solutions/jan" ledger="release/jan.txt">
        <evergreen>jan-schedule.md</evergreen>   <!-- additive per cohort -->
    </channel>
</release-channels>
```

```bash
# Edit NEWS.md in the course source, rebuild, sync — the cohort's copy follows.
clm build course.xml
clm release sync course.xml --channel jan --push -m "Update NEWS"
```

Patterns are `fnmatch` globs matched against the path as it appears in the
cohort repo (POSIX separators; the re-rooted path for `lang`-scoped channels);
the repeatable `--evergreen PATTERN` option adds patterns per invocation.
Evergreen is **skeleton-only**: a pattern matching a topic-owned file is
warned about and ignored — released topic content still changes only via
`--refreeze`. Syncing never deletes; removing an evergreen file from the
source just stops refreshing it.

## How `clm git --channel` differs from ordinary `clm git`

The regular `clm git` subcommands operate on your `<output-targets>` repos.
Add `--channel NAME` (or `--all-channels`) and the very same
`init`/`status`/`commit`/`push`/`sync`/`reset` subcommands instead operate on
the per-cohort repositories from `<release-channels>`:

```bash
clm git status course.xml --all-channels        # status of every cohort repo
clm git sync course.xml --channel jan -m "..."  # commit + push one cohort
```

`clm release sync` is what *populates* a cohort working tree; `clm git --channel`
is what *versions and distributes* it. (`release sync --push` simply chains the
two for convenience.) The private `.clm-manifest.json` is always kept out of the
distributed repos; the per-cohort `.clm-released.json` freeze record is committed
normally.

## See also

- `clm info commands` — full reference for the `clm release` and `clm git` groups
- [Spec-file reference → Release Channels](spec-file-reference.md#release-channels-per-cohort-solution-release)
- [`clm recordings drift`](recordings.md) — detect recordings that went stale
  after slide edits, using the same provenance manifest
