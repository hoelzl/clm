# CLM {version} — Migration Guide

This guide covers breaking changes across major CLM versions.

## Removed / renamed configuration variables ({version})

The database paths are configured **only** through the global CLI options and
their `CLM_*_DB_PATH` environment variables (resolved once in `clm.cli.main`).
A parallel `[paths]` config section / `CLM_PATHS__*` family used to shadow them
but never actually relocated the database a command opened — it has been
removed. The `USE_SQLITE_QUEUE` flag is also gone: SQLite is the only job queue,
so the switch had no effect. These are **hard cuts** — the old names no longer
work.

| Removed variable | Replacement | Notes |
|---|---|---|
| `CLM_PATHS__CACHE_DB_PATH` | `CLM_CACHE_DB_PATH` (or `--cache-db-path`) | Same meaning; the new form actually takes effect. |
| `CLM_PATHS__JOBS_DB_PATH` | `CLM_JOBS_DB_PATH` (or `--jobs-db-path`) | Now honored by `clm build`, `status`, and `monitor` alike. |
| `CLM_PATHS__WORKSPACE_PATH` | *(none)* | Was vestigial; the worker workspace is derived from the output dir. |
| `[paths]` section in `clm.toml` / `.clm/config.toml` | CLI options / `CLM_*_DB_PATH` | An old `[paths]` block still loads (it is ignored). |
| `USE_SQLITE_QUEUE` | *(none)* | SQLite is the only queue; the flag had no consumer. |

`CLM_DB_PATH` (the legacy jobs-DB auto-detect used by `clm status` / `monitor`)
still works but is superseded by `CLM_JOBS_DB_PATH`, which now takes precedence.

Run `clm config show` (or `clm config show --json`) to see the **effective**
database paths and configuration for the current invocation.

## `//`-language decks re-baseline the sync watermark once (issue #458, {version})

`clm slides sync`'s reflow-insensitive markdown hashing (#429) now threads the
deck's real source comment token, so `//`-comment decks (C++/C#/Java/TS) get the
same benefit Python/Rust decks already had: a pure soft re-wrap of a markdown
prose cell no longer reads as an edit. Because that changes every `//`-deck
markdown hash, `WATERMARK_HASH_VERSION` is bumped to `3`.

**One-time migration — no action required.** The watermark's existing
stale-version self-heal does the work: on the first `clm slides sync apply` (or
`autopilot`) after upgrading, a `//` deck's stale-version watermark is treated as
absent, the pair cold-starts off git `HEAD`, and the new hashes are recorded — no
manual `clm slides sync baseline clear` and no false "everything edited" drift.
The committed **consistency ledger** (`--ledger`) self-heals the same way:
a `//`-deck entry whose hashes the new engine computes differently re-checks and
re-records on the next `baseline bless --ledger` / `apply --ledger`. **`#`-comment
decks (Python/Rust) are unaffected** — their hashes are byte-identical (`"#"` was
the prior default), so neither the watermark nor the ledger re-baselines for them.

## Cohort calendar event UIDs are now globally unique (issue #436, {version})

`clm calendar generate -f ics` and `clm calendar push` give every event a
**stable UID** so re-exporting / re-pushing updates events in place instead of
duplicating them. That UID was seeded from the bare **slide-file stem**, which is
unique only *within* a topic — so two distinct decks sharing a stem (a common
pattern: many topics name their lead deck `slides_010_*`) produced the **same**
UID and collided. One event was silently dropped from the `.ics` feed and from a
pushed Google calendar (`duplicate event UID … keeping the later assignment`).

The UID is now seeded from each deck's globally-unique **`module/topic/stem`**
identity, eliminating the entire collision class.

**One-time migration — no action required.** Because every **video / merged**
event's UID changes (date-keyed review/exam *inserts* keep theirs and are left
in place), the **first `clm calendar push` after upgrading re-creates those
events once**: it deletes the old-UID events and inserts the new ones in a single
sync. Students subscribed to the shared Google calendar (read-only "See all event
details", with no event guests) and `.ics` subscribers see a one-time refresh of
the entries — **no notification emails are sent** (the events have no attendees)
and no manual step is needed. Preview the churn first:

```bash
clm calendar push course.xml --channel <name> --dry-run
```

The `.ics` feed re-keys the same way on its next export. After this single
re-creation the calendar is stable again. (There is no opt-out / versioned scheme:
the old stem-only UID was buggy, so keeping it alive was deliberately rejected in
favour of one clean re-key.)

## `clm slides sync` is now a verb group; the bare command reads (epic #440, {version})

`clm slides sync` was a single leaf command that **wrote to the working tree by
default** and, when an API key was present, invoked embedded models on its main
path (the edit judge, the new-slide translator, the cold-pair verifier, the
`--llm-recover` recoverer). It is now an **agent toolkit**: a verb group whose
engine **never calls a model**, and whose **bare form reads**. The embedded
models survive only behind one explicit `autopilot` verb.

This is a **breaking change** for any script, skill, CI step, or repo guideline
that ran `clm slides sync …`. The agent workflow is `clm info sync-agents`; the
per-verb reference is `clm info commands`.

What changed, concretely:

- **`clm slides sync DECK` no longer writes.** Bare `sync DECK` is now an alias
  for `clm slides sync report DECK` — it prints the tiered report and mutates
  nothing. Every write is an explicit verb.
- **The default no longer calls models.** The toolkit verbs
  (`report` / `verify` / `apply` / `task` / `accept`) need **no API key**. The
  engine classifies deterministically, `apply` writes the mechanical tier, and a
  tier-2/3 item is *framed* as a `task` for a model **you** run, then validated
  by `accept`. The old write-everything-with-models behavior is `autopilot` —
  the only verb that loads `.env` and needs `$OPENROUTER_API_KEY`.

Old → new:

| Old | New |
|-----|-----|
| `clm slides sync DECK` (writes, may call models) | `clm slides sync apply DECK` (mechanical tier, no model) + `task`/`accept` for the rest, **or** `clm slides sync autopilot DECK` (one-shot with models) |
| `clm slides sync DECK --dry-run [--json]` | `clm slides sync report DECK [--json]` |
| `clm slides sync DECK --explain` | `clm slides sync report DECK --explain` |
| `clm slides sync DECK --verify` | `clm slides sync verify DECK` |
| `clm slides sync DECK --rebaseline` | `clm slides sync baseline bless DECK` |
| `clm slides sync DECK --llm-recover` / `--interactive` / `--provider` / `--llm-model` / `--translation-model` / `--glossary-*` / `--verify-cold-pairs` | the same flags on `clm slides sync autopilot DECK` |
| `clm slides watermark {list,clear,prune}` | `clm slides sync baseline {show,clear,prune}` (the legacy `watermark` group still works as an alias) |

How to migrate a course repo (see `clm info sync-agents` §"Revising your
repository guidelines"):

- **A CI / drift check** → `clm slides sync report DECK --json` (read-only) or
  `clm slides sync verify DECK` (a deterministic structural gate). **Never run
  `autopilot` in CI** — it is the only verb that calls models.
- **An automated mechanical reconcile** → `clm slides sync apply DECK`.
- **A human one-shot** (you want the old behavior) → `clm slides sync autopilot
  DECK`.
- **Skills / scripts that exported `$OPENROUTER_API_KEY` for sync** → only
  `autopilot` needs it now; document the `task` → model → `accept` handoff where
  a skill previously relied on the engine translating.
- **`--baseline` / `--baseline-from` / `--cache-dir`** still exist on the verbs
  that take a baseline (`report` / `apply` / `task` / `accept`), unchanged in
  meaning. (`verify` takes neither — it is a pure structural check.)

## Default output structure is now shared/trainer/speaker (issues #380/#381/#383, {version})

A course spec with **no** `<output-targets>` previously built a single
`public`/`speaker` toplevel containing *all* kinds — including `partial` in the
public tree — and `clm git` managed only `public` (silently skipping speaker
unless `<include-speaker>` was set). That default has been replaced with three
access-control-by-path tiers:

| Tier | Path | `<remote-path>` | Kinds |
|------|------|-----------------|-------|
| `shared` | `output/shared` | `shared` | `code-along`, `completed` |
| `trainer` | `output/trainer` | `trainer` | `code-along`, `completed`, `trainer` |
| `speaker` | `output/speaker` | `speaker` | `recording` |

What changed, concretely:

- **No more `partial` by default** (#380). `partial` ships only when an explicit
  `<output-targets>` block opts into it. Participant material (`shared`) is
  `code-along` + `completed` only.
- **`output/public/` is gone**; participant output now lives under
  `output/shared/`. Recording material stays under `output/speaker/`; full
  trainer material is under `output/trainer/`.
- **`clm build` and `clm git`/`clm zip` now agree** (#381). All three tiers are
  built *and* managed. `clm git` lists the speaker tier too (no longer silently
  skipped); its remote is still gated by `<include-speaker>` (local-only when
  unset), so recording material is not pushed by default.
- **Group-path remotes work without an env var** (#383). Each tier carries its
  own `<remote-path>`, so remote URLs derive as
  `{repository-base}/{tier}/{project-slug}-{lang}` out of the box.

How to migrate a course repo:

- If you relied on the old `public/`+all-kinds layout (e.g. published `partial`
  to students, or pushed everything to one repo), add an explicit
  `<output-targets>` block that reproduces it. See `clm info spec-files`
  ("Default output structure") for the shape.
- Otherwise no action is needed — re-running `clm build` writes the new tiers;
  prune the stale `output/public/` tree.

### `<github><de>`/`<en>` per-language URLs now warn (issue #382)

The pre-1.x `clx` form `<github><de>URL</de><en>URL</en></github>` was being
**silently ignored**, leaving every output repo local-only. `clm` now logs a
warning when `<github>` contains unrecognized children such as `<de>`/`<en>`.
Replace it with `<project-slug>` + `<repository-base>` (and optionally
`<remote-path>`/`<remote-template>`). Relatedly, `<repository-base>` is now
required only when the active remote template actually references
`{repository_base}` — a self-contained `<remote-template>` no longer needs a
placeholder base.

## Underscore-prefixed dirs under `slides/` are no longer discovered (issue #318, after 1.11)

Directories whose name starts with `_` (e.g. `slides/_archive/`,
`slides/_drafts/`, or an `_old_…` dir inside a module) are now invisible to
module/topic discovery, to the recursive deck walks behind the `clm slides`
batch tools, and to `clm course orphans`. Previously an archived module under
`slides/_archive/` participated in topic resolution and — because unbound
resolution is first-occurrence-wins and `_archive` sorts before `module_*` —
could silently *shadow* a live topic ID, shipping retired decks in its place.

Consequences for course repos:

- Parking retired content under `slides/_archive/` is now safe; moving the
  archive out of `slides/` is no longer necessary.
- A spec that binds `module="_archive"` (or any underscore-prefixed name) now
  fails validation with `unknown_module` — archived content cannot be built.
  Rename the directory (drop the leading underscore) if you genuinely need to
  build from it.
- `--exclude _archive` on `clm slides normalize` / `assign-ids` /
  `slug-report` / `coverage-report` is now redundant for underscore-named
  dirs (but still works, and is still needed for non-underscore names).
- The legacy `_cassettes/` sidecar inside a topic is unaffected: it is not a
  module/topic directory and stays in the course file map.

## Execution cache keys now cover dependencies (issue #321, after 1.11)

`clm build` previously keyed its notebook execution caches on the deck text
alone, so editing a sibling file the deck depends on (a C++ header it
`#include`s, a Jinja `{% include %}` target, a CSV it reads) silently
replayed stale execution output with a fresh timestamp, and only
`--ignore-cache` recovered. The cache keys now also cover every non-image
topic sibling, a fingerprint of CLM's bundled Jinja templates, the worker
execution environment (`direct` or the configured Docker image reference),
and the per-topic `evaluate=` / `skip-errors=` attributes.

Consequences for course repos:

- **The first build after upgrading re-executes every deck once** (the key
  schema changed). Subsequent builds cache normally.
- Editing any non-image file in a topic directory re-executes the decks in
  that topic — you no longer need `--ignore-cache` after changing a shared
  header or data file.
- Changing the configured Docker worker image (or switching direct↔docker)
  invalidates too. The key uses the image *reference*, not a content digest:
  a re-pulled `:latest` tag does not invalidate — pin worker images to
  versioned tags or `@sha256:` digests for exact invalidation.
- Editing the HTTP-replay cassette still does **not** invalidate (deliberate;
  record-capable modes rewrite cassettes after execution). Use
  `--ignore-cache` after a manual cassette edit.
- `clm build --output-mode verbose` now prints `↻ Replayed from cache` for
  every file served from a cache instead of executed.

## Command-tree regrouping (issue #310, after 1.11)

The single-command groups `topic`, `spec`, and `authoring` were merged into
the domain groups `course` and `slides`, and the remaining stray top-level
commands moved into their natural groups. This is a **clean break** — the old
names are gone, with no deprecation aliases:

| Removed | Use instead |
|---|---|
| `clm targets SPEC` | `clm course targets SPEC` |
| `clm sync-includes SPEC` | `clm course sync-includes SPEC` |
| `clm spec decks …` | `clm course decks …` |
| `clm spec orphans …` | `clm course orphans …` |
| `clm topic resolve …` | `clm course resolve-topic …` |
| `clm authoring rules …` | `clm slides rules …` |
| `clm polish …` | `clm slides polish …` |
| `clm delete-database …` | `clm db delete …` |
| `clm export calendar …` | `clm calendar generate …` |
| `clm voiceover port-voiceover …` | `clm voiceover port …` |

`clm course gate` is unchanged. The whole cohort-calendar lifecycle now lives
in one group: `clm calendar generate` → `check` → `status` → `push`.

The synonym pairs `slides translate`/`slides bootstrap` and `export
summary`/`export summarize` both still work, but `--help` now lists only the
canonical name (`translate`, `summary`).

**Update course repos:** scripts, Makefiles, CI steps, agent prompts, and
`<tasks>` blocks in spec files that call a removed name fail with *"No such
command"* after upgrading — replace them with the new invocations above.

## Course-document commands moved under `clm export`

The three commands that turn a course spec into a human-readable document are
now subcommands of a new `clm export` group, and the **flat top-level forms were
removed** (no deprecation alias):

| Removed | Use instead |
|---|---|
| `clm outline …` | `clm export outline …` |
| `clm schedule …` | `clm export schedule …` |
| `clm summarize …` | `clm export summary …` (or `clm export summarize …`) |

`summarize` was also renamed to the noun `summary` for consistency with
`outline`/`schedule`; `clm export summarize` is kept as an alias. Update any
scripts, Makefiles, or CI steps that call the old names.

The three commands also gained a **consistent option vocabulary**:

- `--include-optional` (now on all three) — include modules marked
  `optional="true"` on a `<section>`/`<subsection>`. **Off by default**, so an
  outline/summary that previously listed optional sections now hides them unless
  you pass the flag. (The MCP `course_outline` tool is unchanged — it still
  shows optional content.)
- `--include-disabled` (now on all three) — include `enabled="false"`
  sections/subsections. It takes an **optional value**: a bare
  `--include-disabled` (or `=marked`) tags them `(disabled)` (disabled whole
  sections listed after the enabled ones in `outline`/`summary`), while
  `--include-disabled=merge` folds them into the normal course flow, in
  declared order, with no marker. Give the value with `=` and keep `SPEC_FILE`
  first, since a bare flag placed immediately before the spec path would be
  read as its value.
- `clm export schedule` gained `-d/--output-dir`; `-L/--language` is the
  canonical spelling everywhere (`schedule` keeps `--lang` as an alias).

## The vcrpy HTTP-replay transport was removed ({version})

CLM {version} **removes the legacy in-process `vcrpy` replay transport**
entirely (issue #355). mitmproxy had been the default since 1.10;
`CLM_HTTP_REPLAY_TRANSPORT=vcrpy` was the temporary escape hatch and is gone:

- `CLM_HTTP_REPLAY_TRANSPORT=vcrpy` now makes `clm build` **fail immediately**
  with a usage error. Remove the variable from CI configs, shell profiles, and
  course Makefiles.
- Cassettes recorded under the vcrpy transport (pre-1.10, or any course that
  kept building with the opt-out) do **not** strict-replay through the proxy.
  Re-record them once and commit the result:

  ```bash
  clm build course.xml --http-replay=refresh
  git add <topic>/**/*.http-cassette.yaml
  ```

- Cassettes already recorded under mitmproxy (the default since 1.10) are
  unaffected — the on-disk format is unchanged.
- The `vcrpy` *package* is gone from CLM's dependencies entirely: the
  cassette format (vcrpy v1 YAML — unchanged on disk) is now implemented by
  CLM itself and needs only PyYAML. An isolated `mitmdump` tool environment
  now needs `uv tool install mitmproxy --with pyyaml` (an env installed with
  the old `--with vcrpy` keeps working — vcrpy depended on PyYAML).

## Breaking changes in CLM 1.10

CLM 1.10 carries **two intentional breaking changes** around HTTP replay —
review these before upgrading a course repo's pin.

### 1. mitmproxy is now the default HTTP-replay transport

The HTTP-replay engine that records and replays a topic's network traffic
(`http-replay="yes"`) now runs as an **out-of-process mitmproxy proxy** instead
of the in-process `vcrpy` patcher. The proxy correctly matches **repeated and
concurrent identical requests** that vcrpy's consume-once model mishandled — a
LangChain chain invoked many times with the same body, or a `RunnableParallel`
fan-out — which previously made such decks impossible to strict-replay.

**Cassettes are not byte-compatible between the two transports.** An existing
vcrpy cassette must be **re-recorded under mitmproxy** before strict
`--http-replay=replay` (the CI default) passes. Re-record locally with the
permissive default and commit the result:

```bash
clm build course.xml --http-replay=refresh   # re-record from scratch, review the diff
git add <topic>/**/*.http-cassette.yaml
```

The on-disk format is still vcrpy's YAML layout (the mitmproxy addon
serializes to it). In 1.10–1.12, `CLM_HTTP_REPLAY_TRANSPORT=vcrpy` opted back
into the old in-process transport during the transition; **that opt-out was
removed in {version}** (see the section above) — re-recording is now the only
path. Starting the proxy is gated on the course actually containing an
`http-replay` topic, so a replay-free build never spawns `mitmdump` and pays
no cost. See `docs/user-guide/http-replay.md`.

**Client-library coverage (as of {version}):** under the mitmproxy transport
the kernel tags traffic from **httpx**, **requests**, and **aiohttp** so the
shared proxy routes it to the topic's cassette. (CLM releases between the
transport switch and {version} tagged only httpx — `requests`-based decks
recorded into a non-committed catch-all and could not strict-replay; upgrade
and re-record those topics with `--http-replay=refresh`.) Other HTTP stacks
(`urllib.request`, raw `urllib3`/`http.client`, subprocesses) are still
proxied but untagged: they hit the per-build catch-all cassette, and the build
log carries a `CLM-HTTP-REPLAY-UNTAGGED` warning. Use a covered client library
in such decks.

### 2. Python 3.11 support dropped

`requires-python` is now `>=3.12` — mitmproxy, the new default replay transport,
requires Python 3.12+. Recreate any 3.11 virtualenv on 3.12, 3.13, or 3.14
before upgrading; `pip install` refuses the package on 3.11 with a
`requires-python` mismatch. Course repos that build in Docker get the bumped
`python:3.12-slim` worker images automatically.

## Header-line-less title convention for C#/C++/Java/TypeScript ({version})

CLM {version} makes the `//`-comment languages (C#, C++, Java, TypeScript) use
the same deck-title convention Python already used: the title is a **standalone**
`// {{ header("DE", "EN") }}` j2 call with **no** authored `// %%` wrapper cell.
The `header` macro now emits its own `%% [markdown] lang="de"` boundary, and new
`header_de` / `header_en` sibling macros are available for split decks.

**Why:** one title convention across all languages unblocks the multi-language
authoring tooling (split decks, `voiceover extract/inline`, `assign-ids`,
`normalize`, `sync`). It also fixes a latent bug: a `//`-family deck whose title
used a *neutral* wrapper (`// %% [markdown] tags=["slide"]`) put German title
content in a language-neutral cell, so it leaked into the **English** build (the
EN slides showed two titles). After migrating, each language has exactly one.

**Old shape (no longer correct):**

```
// %% [markdown] lang="de" tags=["slide"]
// j2 from 'macros.j2' import header
// {{ header("Titel", "Title") }}
```

**New shape:**

```
// j2 from 'macros.j2' import header
// {{ header("Titel", "Title") }}
```

**How to migrate a course (do this in lockstep with bumping the course's CLM
pin — a reformatted deck requires CLM {version}):**

1. Commit the course repo clean.
2. `python scripts/reformat_header_convention.py <slides-dir> --apply` (the
   script lives in the CLM repo; dry-run without `--apply` first). It removes the
   wrapper line, drops now-unnecessary `<!-- clang-format off/on -->` comments
   around the title, and skips genuine outliers for manual review.
3. `python scripts/verify_header_reformat.py <lang>` — asserts exactly one title
   slide per language across the corpus.
4. Rebuild; for `lang="de"`-wrapped decks the output is byte-identical, for
   neutral-wrapped decks the (previously doubled) English title is corrected.

Python (`#`) decks are unchanged.

## Day-of-week scheduling: `<subsection>` + `clm schedule` (issue #261, {version} — additive)

CLM {version} adds an optional `<subsection>` layer inside a `<section>`'s
`<topics>` to express day-of-week scheduling for certification listings
(`<section>` = week, `<subsection>` = day), plus a new `clm schedule` command
that exports the weekday deck listing in Markdown or CSV. See
`clm info spec-files` for the `<subsection>` grammar and `clm info commands`
for `clm schedule`.

**Nothing changes for existing specs.** The feature is entirely opt-in: a spec
that declares no `<subsection>` parses and builds exactly as before. `clm build`
flattens subsections away, so a spec with subsections builds **byte-identically**
to the same spec with the wrappers removed — there is no migration step. To
adopt it, wrap a section's `<topic>`s in `<subsection weekday="mon">…` groups
and run `clm schedule course.xml`.

## Per-topic solution release (issue #208, {version} — additive)

CLM {version} adds **per-topic solution release**: hand a student cohort a
topic's full solution only after its workshop has been discussed, one topic at a
time, with multiple cohorts each on their own schedule, and solutions **frozen**
so later course edits never change what a cohort already received. The feature is
entirely opt-in — a course that declares no `<release-channels>` block behaves
exactly as before.

One behavior **does** change by default: `clm build` now writes a
`.clm-manifest.json` provenance index into each output root (it maps every output
file to its owning topic, which the release workflow needs). This file is private
and is automatically excluded from every repo `clm git` touches, so it never
reaches students. If you don't want it, pass `--no-provenance-manifest`. It is
always suppressed under `--snapshot` / `--verify-against` (it embeds a build
timestamp and commit, so it never enters a reproducibility baseline).

### How to adopt

1. Add a `<release-channels>` block to the course spec, naming the
   `completed`-kind output target that is the frozen source and one
   `<channel>` per cohort (see `clm info spec-files` → `<release-channels>`).
2. Build the source as usual: `clm build course.xml` (the manifest is written
   automatically).
3. For each cohort repo, once: `clm git init course.xml --channel <name>`.
4. As each topic's workshop wraps up:
   - `clm release add course.xml <topic-id> --channel <name>` — record the
     release in the cohort's ledger.
   - `clm release sync course.xml --channel <name> --push` — promote and freeze
     that topic into the cohort repo and push it.

The ledger (one topic id per line) is the source-repo-side record of what was
released; the per-cohort frozen manifest is the freeze record that ships in
the cohort repo. A frozen topic is never re-propagated unless you pass
`--refreeze`. Full command reference: `clm info commands` → `clm release` and
`clm git`.

When a cohort spans several channels (e.g. `materials`/`solutions` streams in
two languages), `add`/`week`/`status`/`sync` take a glob or `--all-channels` so
step 4 stays one command instead of one per channel (CLM {version}+):
`clm release sync course.xml --all-channels --push`. List the addresses with
`clm release channels course.xml`.

## Per-stream frozen manifests / shared destination repos (issue #325, {version} — auto-migrating)

Channels of **different** release streams may now share one destination `path`,
releasing materials and solutions into a single repository students pull from
(see `clm info releases` → "Shared destination"). To make that possible the
frozen manifest is now **per stream**: a channel in a named stream writes
`.clm-released.<stream>.json` instead of `.clm-released.json` (a single
*unnamed* `<release-channels>` block keeps the legacy name).

**No manual migration.** On a channel's next `clm release sync`, an existing
legacy `.clm-released.json` whose `channel` field matches is adopted, rewritten
under the per-stream name, and the legacy file is removed — freeze state is
preserved (the sync output reports the migration). Commit the rename with the
usual `--push` / `clm git sync`. A legacy file recording a *different* channel
is left untouched (it belongs to the stream that has not synced yet).

To merge an existing materials + solutions repo pair for a running cohort,
point the solutions channels' `path` at the existing materials working trees —
students keep their remotes; the standalone solutions repos simply stop
receiving syncs.

CLM {version} lets a topic keep its authoring **sidecars** — voiceover
companions (`voiceover_*.py`) and HTTP-replay cassettes (`*.http-cassette.yaml`)
— in per-type subdirectories so the topic directory holds only the editable
`slides_*.py` sources and the output companions (`img/`, `drawio/`):

```
topic_070_rag_introduction/
├── .clm/cassettes/ ← *.http-cassette.yaml
├── voiceover/      ← voiceover_*.py
├── drawio/  img/
└── slides_010_*.de.py  slides_010_*.en.py
```

**Nothing changes unless you opt in** — the flat layout (sidecars next to the
slides) and the legacy top-level `cassettes/` keep working, and every layout is
auto-detected by directory presence everywhere (build, `extract`/`inline`/`sync`,
`split`/`unify`, `validate`).

> **CLM {version} consolidates cassettes under `.clm/cassettes/`.** HTTP-replay
> cassettes are a committed build *input*, not author-edited content, so they now
> live in the build-internal `.clm/` tree (issue #453) instead of a top-level
> `cassettes/`. The top-level `cassettes/` and `_cassettes/` are still **read**,
> so existing repos keep replaying with no change; `clm slides tidy` migrates them
> (`git mv cassettes/ → .clm/cassettes/`). `.clm/cassettes/` (and the per-slide
> sync ledger `.clm/sync-ledger.json`, issue #448) must stay **committed** — only
> the regenerable `.clm/voiceover-cache|backfill|traces/` scratch and the transient
> `*.http-cassette.yaml.staging-*` markers under `.clm/cassettes/` are gitignored.
> `voiceover/` stays a top-level folder (the author edits its narration).

*Adopt it:*

```bash
# Preview, then move a topic / section / whole course into the foldered layout
clm slides tidy slides/module_550/topic_070 --dry-run
clm slides tidy slides --layout subdir

# Flatten back if you prefer
clm slides tidy slides --layout sibling
```

`tidy` uses `git mv` for tracked files, deletes transient `*.staging-*` cassette
markers, and **consolidates the legacy top-level `cassettes/` / `_cassettes/`
directories into `.clm/cassettes/`** (both names are still read as a fallback).

**Since CLM {version} the foldered layout is the default** for a *new* sidecar:
a build records a topic's first cassette into `.clm/cassettes/`, and `clm
voiceover extract` / `sync` create a new companion in `voiceover/` — **unless**
that deck already has a sibling (or legacy top-level) sidecar, which is kept in
place so a deck is never split across layouts. This default is write-time only
and never changes build output (every layout is still read). Precedence for a
new sidecar: explicit `--layout` flag → an existing per-type directory → a course
default → **subdir** (the new fallback). Opt a course back into the flat layout
with `sibling`:

```toml
[tool.clm]
sidecar-layout = "sibling"   # or per-shell with CLM_SIDECAR_LAYOUT=sibling
```

For cassettes the course override is the `<sidecar-layout>` element in the
course spec (see `clm info spec-files`); the env var / pyproject key drive the
voiceover authoring tools too.

## `clm voiceover extract` no longer extracts speaker notes by default ({version} — backward-compatible)

`clm voiceover extract` now moves **only `voiceover`-tagged cells** into the
`voiceover_*` companion; `notes` (speaker-notes) cells **stay inline** in the
deck. Previously both were extracted, so a file named "voiceover" also held
speaker notes — confusing for authors and downstream agents. Speaker notes are
short and belong with the slide they annotate; left inline they still reach the
**trainer** and **recording** outputs (the build filters by tag regardless of a
cell's location).

**Nothing breaks.** Existing companions that already contain notes keep working
unchanged — the build merge always reads both `voiceover` and `notes` back. Only
*new* extractions differ.

- To extract notes as before, pass `--include-notes` (or set it per call).
- To pull notes **out** of existing companions and back into the deck, use the
  migration helper `clm voiceover inline-notes PATH` (see `clm info commands`).

## Bootstrap a second language: `clm slides translate` ({version} — additive)

New command **`clm slides translate SOURCE`** (alias `clm slides bootstrap`)
generates the missing-language split half of a single-language deck as a full
translation. This is the cold start `clm slides sync` deliberately refuses to
perform — sync only fills per-cell gaps inside an *already-existing* pair, and a
missing twin is a usage error there.

**Nothing changes for existing course repos** — this is a new command, no
behavior change to `build`, `sync`, `split`, or `unify`. Adopt it when starting a
new deck in one language:

```bash
# 1. You wrote only slides_x.de.py. Create the English half.
clm slides translate slides/topic/slides_x.de.py

# 2. From here on, keep the two halves in step with the usual tool.
clm slides sync slides/topic/slides_x.de.py

# 3. (Optional) merge to a single bilingual file for editing.
clm slides unify slides/topic/slides_x.de.py
```

What it does (see `clm info commands` → *`clm slides translate`* for the full
reference):

- Code is translated **iff** a cell carries a `lang` tag — the existing
  no-`lang`-is-shared model, no new marker. Shared (no-`lang`) cells, including
  idiomatic code, are copied byte-for-byte into both halves.
- The voiceover companion (`voiceover_*`) is translated in lockstep, preserving
  `for_slide` / `vo_anchor`.
- EN-authority shared `slide_id`s are minted onto both halves and the sync
  watermark is recorded, so the **next** `translate` or `sync` is a clean no-op —
  re-running never doubles the deck (a present twin degrades to incremental sync;
  `--force` re-bootstraps).
- Needs `$OPENROUTER_API_KEY` (or `$OPENAI_API_KEY`); reads a project `.env`. With
  no key the bootstrap exits `1` and writes nothing. `--dry-run` previews with no
  key and no LLM.

A generated deck is valid for all the split-pair tooling immediately — it passes
`clm validate slides <dir> --fail-on warning` (slide_id set/order parity,
shared-cell byte parity, pairing adjacency, companion `for_slide` parity).

## Cell-spacing checks + `cell_spacing` normalize ({version} — additive)

`clm validate` (the `format` group) now surfaces two `warning`s, and
`clm slides normalize` gains a `cell_spacing` operation that fixes them:

- **A cell must be separated from the previous cell by a blank line** — except a
  j2 cell, since the title-header block (`# j2 … import header` immediately
  followed by `# {{ header(…) }}`) is tight-coupled by design.
- **A markdown cell body must start with a blank comment line (`#`)** — so
  content that opens with a bullet or heading renders correctly.

**Non-breaking but newly visible.** Both are `warning`s, so the default
error-gate and existing CI are unaffected — but `clm validate --fail-on warning`
(the pre-commit gate) will now flag pre-existing slides that lack a blank lead or
run cells together. Fix a whole course in one pass:

```bash
clm slides normalize slides/                       # fixes spacing with the other ops
clm slides normalize slides/ --operations cell_spacing   # spacing only
```

`cell_spacing` runs by default (part of `all`), is idempotent, and preserves the
split/unify round-trip. No code or spec changes are required in course repos.

## Preamble-code wrapping: `preamble_code` (issue #253, {version} — additive)

A deck may carry **preamble code** — an executable line between the
`# {{ header(…) }}` macro call and the first `# %%` cell, e.g.:

```python
# j2 from 'macros.j2' import header
# {{ header("Regeln für Typen", "Rules for Types") }}
from typing import Iterable          # <- preamble code, no cell marker
# %% [markdown] lang="de" ...
```

Because `# {{ ` is itself a cell boundary, that code becomes the **body** of the
header cell, and jupytext folds it into the **title markdown** at build time. In
the bilingual `header(de, en)` macro the code rides the EN title (so it is
silently dropped from a DE build); in a split `.de.py` half it rides the DE title
(so it is kept). The two builds therefore **diverge on the DE side** — the
bilingual→split conversion is not render-neutral. The split *source* still
round-trips byte-identically; the divergence is build-side only.

CLM {version} addresses this three ways:

- **`clm validate`** (the `format` group) emits a `warning` pointing at the code.
- **`clm slides split`** prints a `warning:` (to stderr; non-fatal) and never
  rewrites the source.
- **`clm slides normalize`** gains a `preamble_code` operation that moves the
  code into its own `# %%` code cell — a shared, language-neutral cell that every
  build includes identically and `split` copies verbatim to both halves. After
  the fix the import is finally executed **as code** (not rendered as markdown
  text), and the bilingual and split builds are byte-identical.

```bash
clm slides normalize slides/                          # fixes it with the other ops
clm slides normalize slides/ --operations preamble_code   # this fix only
```

**Non-breaking.** The validate finding is a `warning` (the default error-gate and
CI are unaffected). `preamble_code` runs first among the normalize ops, is
default-on (part of `all`), idempotent, and a strict no-op on a conforming deck.
**Note:** top-of-file code *before* the `# j2` import line (a true file preamble,
e.g. a leading `import os`) lands in the split's shared preamble and is already
render-neutral — it is **not** flagged.

## Breaking changes in CLM 1.8

CLM 1.8 retires the Phase 0 deprecation period. It carries **intentional
breaking changes** — review these before upgrading a course repo's pin.

### 1. Flat top-level CLI aliases removed

The flat command names, deprecated since CLM 1.6, no longer exist. Use the
verb-grouped invocations:

| Removed | Use instead |
|---------|-------------|
| `clm normalize-slides` | `clm slides normalize` |
| `clm language-view` | `clm slides language-view` |
| `clm suggest-sync` | `clm slides suggest-sync` |
| `clm search-slides` | `clm slides search` |
| `clm resolve-topic` | `clm course resolve-topic` |
| `clm authoring-rules` | `clm slides rules` |
| `clm validate-slides` | `clm validate` |
| `clm validate-spec` | `clm validate` |
| `clm extract-voiceover` | `clm voiceover extract` |
| `clm inline-voiceover` | `clm voiceover inline` |

Scripts, hooks, and agent prompts that call a flat name now fail with
Click's `No such command`. Update them to the group-qualified form.

### 2. `clm build --keep-directory` removed

The flag was a no-op alias (keeping the output tree has been the default
since the git-friendly output-writes rollout). Drop it from any build
invocation. To opt into the legacy wipe-and-restore flow, use `--clean`.

### 3. Validator: missing `slide_id` and DE/EN non-adjacency are now errors

Two `clm validate` slide findings escalated from `warning` to `error`:

- A `slide`/`subslide` cell **missing a `slide_id`**. Fix with
  `clm slides assign-ids <dir>` (or `clm slides sync` for a split deck).
- A **DE/EN content/voiceover pair that is not adjacent** (an intervening
  language-tagged cell wedged between the two halves). Fix with
  `clm slides normalize`.

A course repo must clear these before its build/validate passes succeed
under 1.8. The errors fail the pre-commit gate and the PostToolUse hook.

### 4. MCP tool names aligned to the verb-group scheme

The MCP server's tool names were renamed to mirror the CLI verb groups
(group-first, no aliases). Update `.mcp.json`, CLAUDE.md / AGENTS.md tool
tables, and agent prompts:

| Old MCP tool | New MCP tool |
|--------------|--------------|
| `resolve_topic` | `topic_resolve` |
| `search_slides` | `slides_search` |
| `normalize_slides` | `slides_normalize` |
| `get_language_view` | `slides_language_view` |
| `suggest_sync` | `slides_suggest_sync` |
| `extract_voiceover` | `voiceover_extract` |
| `inline_voiceover` | `voiceover_inline` |
| `course_authoring_rules` | `authoring_rules` |
| `validate_spec` + `validate_slides` | `validate` (single tool; dispatches on input type) |

`course_outline` and the `voiceover_*` tool family are unchanged (already
group-first / no verb group).

## Slide format redesign: stable `slide_id`s (additive — no break)

CLM {version} ships **Phase 2** of the slide-format-redesign: the
`clm slides assign-ids` command that generates stable, EN-derived,
kebab-case ASCII `slide_id` values for slide and subslide cells.

Adoption is opt-in — nothing happens until you run the command. The
recommended workflow on a course repo:

```bash
# Preview what would change
clm slides assign-ids slides/ --report-only

# Apply for the headed-slide majority
clm slides assign-ids slides/

# Decide what to do with refusals (extractable headingless slides)
clm slides assign-ids slides/ --report-only --accept-content-derived
clm slides assign-ids slides/ --report-only --llm-suggest   # if Ollama is running
```

Pin a slide's id with the **preserve marker**:

```python
# %% [markdown] lang="de" tags=["slide"] slide_id="!intro"
```

The `!` is source-level only — referenced everywhere as the bare form
`intro`. `--force` and any future regeneration tool will leave
preserved ids alone.

Voiceover and notes cells inherit the id of the preceding slide
(1:N — multiple narrative cells per slide). The title slide (j2
`header()` macro) anchors `slide_id="title"` automatically. See
`clm info commands` → `clm slides assign-ids` for the full flag
matrix.

## Fully automatable bilingual→split id completion ({version} — additive, issue #251)

Converting a bilingual course module to the split layout is a clean command
sequence (`assign-ids → split → extract → tidy`) — except `assign-ids` used to
**hard-refuse** content-less code subslides: a code cell whose first statement is
a bare expression with no heading and no nameable construct
(`(1 + 1j) * (1 + 1j)`, `letters[0:3]`, `a == b`). The only non-manual escape
was the LLM, so a human had to hand-author `slide_id="…"` on **both** split
halves mid-conversion.

CLM {version} adds the opt-in **`--accept-code-derived`** flag. It slugs each
such cell's first real code line (`letters[0:3]` → `letters-0-3`), is pair-safe
(the same id on `.de` / `.en`), stable, and idempotent. The scanner is
comment-token-aware, so non-Python decks (`.cs`/`.cpp`/`.java`/`.ts`) are
completed too. Run it alongside `--accept-content-derived` to mint a whole
module with no human in the loop:

```bash
clm slides assign-ids slides/module_110_basics/ \
    --accept-content-derived --accept-code-derived
```

It is a **separate** flag from `--accept-content-derived` by design: the
content-derived minting funnels (`clm course gate`, `clm slides sync`,
`clm slides translate`) keep their current behavior and do not start emitting
opaque code-line slugs. Genuinely empty / pure-punctuation / magic-only cells
still refuse — `--report-refusals` then lists only the cells that truly need a
human.

## Voiceover extract/inline: data-loss hardening ({version})

CLM {version} closes two ways `clm voiceover extract` / `inline` could lose
authored narration. Both are safe-by-default behavior changes; scripts that
relied on the old behavior may need a flag or an exit-code check.

- **`inline` no longer destroys the companion when a cell is unmatched.**
  Previously, if a voiceover's owning `slide_id` had been renamed (so its
  `for_slide` no longer matched), inline stranded that cell at the end of the
  slide file (stripped of `for_slide`/`vo_anchor`) and **deleted the companion
  anyway**, exit 0. Now inline places only the cells it can match, **keeps the
  companion** rewritten to the unmatched remainder (anchors intact), and
  **exits non-zero**. Recover by fixing the `slide_id`(s) and re-running
  inline. *Migration:* a script that treated inline as "always consumes the
  companion" must handle the non-zero exit / surviving companion; check
  `companion_retained` / `unmatched_cells` in `--json`.

- **`extract` refuses to overwrite an existing companion without `--force`.**
  The companion is rebuilt from the slide's current voiceover cells, so a blind
  re-extract discarded anything living only in the companion. Extract now
  raises (writing nothing) unless `--force` is passed. *Migration:* add
  `--force` to any pipeline that intentionally rebuilds the companion.

These are also surfaced by the new fast-suite `tests/slides/test_edit_dynamics.py`
cross-command harness (`scripts/edit_dynamics_harness.py`).

## `clm voiceover extract` auto-pairs on a split half ({version})

CLM {version} makes `clm voiceover extract` produce **both** companions of a
split deck in one op. When `FILE` is a split half (`<deck>.de.py` /
`<deck>.en.py`) whose twin exists on disk, extract now mints EN-authority
`slide_id`s across both halves and extracts both, so the two companions'
`for_slide` sets agree by construction (closing the per-language footgun where
extracting each half by hand could mint divergent slugs).

*Migration (behavior change):* a bare `clm voiceover extract <deck>.de.py` that
used to write only `voiceover_<deck>.de.py` now also writes
`voiceover_<deck>.en.py`, and the EN-authority pre-mint may stamp `slide_id`s on
**both** slide halves (so `git diff` shows the `.en` half too). To keep the old
single-half behavior, pass `--single`. The `--json` output for a paired extract
is a new shape — `{"paired": true, "companions": [<de>, <en>], …}` — so a
consumer that reads top-level `cells_extracted` should branch on the `paired`
key (a single-file/bilingual extract still emits the flat object). A pair that
is not structurally alignable makes extract **refuse** (reconcile with
`clm slides sync` first). Bilingual decks (no `.de`/`.en` twin) are unchanged.
The MCP `extract_voiceover` tool gains matching `both` / `single` parameters.

## `slides split` / `unify` carry the voiceover companion ({version})

Previously `clm slides split` only split the deck — a sibling voiceover
companion (`slides_<name>.py` → `voiceover_<name>.py`) was left behind,
orphaned: the build then found no companion next to either
`slides_<name>.de.py` or `slides_<name>.en.py`, silently dropping the
narration. CLM {version} splits the companion in lockstep into
`voiceover_<name>.de.py` / `voiceover_<name>.en.py` (routing each cell by its
`lang`, preserving `for_slide` / `vo_anchor`), and `clm slides unify`
recombines them into `voiceover_<name>.py`. The companion round trip is
byte-identical, the same trust property the deck split already guarantees;
it relies on the #162 `de_id == en_id` invariant so each narration cell's
owning slide exists in its language's half.

*Migration:* none required — the behavior is additive and only fires when a
companion is present. The `split`/`unify` `--force` flag now also covers the
companion targets, and the refusal is atomic (no file is written if any deck
*or* companion target exists without `--force`). `--json` output gains
`source_companion` / `de_companion` / `en_companion` (split) and
`target_companion` / `companion_overwrote` (unify).

## Cross-file `slide_id` parity detective for split decks ({version}, issue #162)

`slide_id` is the cross-language join key for a split deck: voiceover
`for_slide` resolution, `clm slides unify` (which requires `de_id == en_id`),
and `extract`/`inline` all assume the `.de.py` and `.en.py` halves agree on the
**set and order** of slide ids. A born-split deck, a per-file
`clm slides assign-ids` run on one half, or a hand-edited id silently diverges
them.

CLM {version} adds a `clm validate` **`pairing`** check (warning) that flags a
divergent `slide_id` set or order between a split pair. It runs on a
directory/course validate **and** on a single-file validate when the twin
exists on disk — so a pre-commit hook (`clm validate slides/ --fail-on warning`,
or per-file in a PostToolUse hook) catches a divergence before it ships.

The same join key governs **separated voiceover**: each narration cell's
`for_slide` is the `slide_id` of the slide it covers. CLM {version} extends the
detective with a companion **`for_slide` parity** check (the both-language
voiceover compatibility check) — it flags a split deck whose
`voiceover_<name>.de.py` / `voiceover_<name>.en.py` companions narrate
different sets of slides (`for_slide` sets differ), or where only one language
has a companion at all. Without it, a half-finished EN companion silently ships
the EN slides with missing narration at build time. It is wired the same way
as the `slide_id` parity check (directory/course, plus single-file when the
twin exists on disk) and compares the bare (`!`-stripped) `for_slide` *set* —
one language may legitimately split a slide's narration across a different
number of cells.

Both detectives are `warning`-severity, so by default `clm validate` reports
them but still exits 0. CLM {version} adds `clm validate --fail-on {error,warning}`
to make them gate-able: `clm validate slides/ --fail-on warning` exits non-zero
on any warning (the parity detectives included), which is what a pre-commit hook
needs. `--fail-on` governs the exit code with `--json` too; without it, behavior
is unchanged (human output fails on errors, JSON exits 0).

Since CLM {version} `clm slides assign-ids` keeps the two halves consistent
automatically. A **directory / course run** mints **EN-authority** ids across a
`.de.py` / `.en.py` pair at once (the #162 *generative*): the slug derives from
the EN heading and the same id is stamped on both, deterministic regardless of
file order. A **single-file run** is **twin-aware** (the #162 *defensive*): an
id-less slide adopts the sibling's `slide_id` when the twin exists on disk with
a matching slide count; when both halves are id-less the first-assigned half's
slug wins (parity still holds — use the directory run or `clm slides sync` for
EN-authority). A pair that is not byte-faithfully unifiable (divergent shared
cells) falls back to the per-file path, and `clm validate`'s #162 detective
flags any residual divergence.

`clm voiceover extract` shares this defensive: since CLM {version} the
`slide_id`s it auto-generates before extraction are twin-aware on a split half,
so extracting the `.de` and `.en` halves separately keeps them in parity and
the two companions narrate the same slides. (No migration required — extract on
bilingual decks is unchanged.) For deterministic EN-authority ids, run
`clm slides assign-ids <dir>` on the pair before extracting.

## `clm build` fails on dropped companion voiceover ({version})

Separated voiceover lives in `voiceover_*.py` companions; the build merges each
narration cell back next to the slide named by its `for_slide`. If a `for_slide`
matches no `slide_id` in the slide (typically a renamed `slide_id`), that
narration is **dropped** from the output. Previously the build only logged a
warning and exited 0 — the loss was silent.

Since CLM {version} each dropped narration is reported as a `voiceover`-category
**error** in the build summary, governed by the **same `--fail-on-error`
policy** as cell-execution errors: it fails the build under `--fail-on-error`
(default-on in `--http-replay=replay` / CI) and is surfaced-but-non-fatal
otherwise.

*Migration:* a CI build (replay mode) that currently ships a deck with an
unmatched companion `for_slide` will now **fail** instead of silently dropping
the narration. Fix the `for_slide` / `slide_id` mismatch (`clm voiceover inline`
then re-extract, or `clm slides sync`), or pass `--no-fail-on-error` /
`CLM_FAIL_ON_ERROR=0` to tolerate it. Running `clm validate slides/ --fail-on
warning` in a pre-commit hook catches the underlying `slide_id` / `for_slide`
divergence before it ever reaches a build.

## Command surface: split-safety hardening ({version})

`clm slides sync` is the one operation that keeps both halves of a split deck
consistent. CLM {version} hardens the surface around it so the everyday path is
the safe one — no command was removed and every tool stays fully invocable.

- **`clm slides sync` pairing guard.** Before any read or write, sync now checks
  that the two paths are the two halves of **one** deck (one `.de`, one `.en`,
  same name — the routing prefix is not required). A **swapped** order is
  auto-corrected with a note; the **same file** twice, **two same-language**
  halves, **two different decks**, or a path that is **not a split half** (a
  bilingual or untagged file) are rejected with a usage error (exit 2)
  before any LLM call. This closes the #162 footgun where a mismatched pair could
  silently produce a divergent or no-op sync. *Migration:* none for well-formed
  invocations; a script that relied on passing a mismatched pair will now get a
  clear usage error instead of a surprising write.
- **Cold-start pairs are reconciled or refused — never doubled (#216).** When a
  split pair has changes that would have to flow in *both* directions with no
  shared ids to pair the halves, `clm slides sync` now decides at plan time
  instead of adding every cell on both sides (which previously errored at exit 2
  for an all-id-less pair, or silently **doubled** both decks for an id-carrying
  one). A **freshly-split parallel pair** (all cells id-less) is **paired and
  minted one shared `slide_id` per slide** — but only after a cheap, cached LLM
  **correspondence check** confirms the two halves actually translate each other
  (default-on when an OpenRouter key is configured; turn it off with
  `--no-verify-cold-pairs`). A **half-id'd pair** — one half fully id'd, the
  other fully id-less (e.g. ids were assigned on only one half) — is **paired
  and the id-less half adopts the id'd half's *existing* slide_ids** (no fresh
  minting, no translation; the same correspondence check gates it). Without a
  provider, or if the check returns "no" or cannot run, the pair is **refused**
  rather than guessed. A pair that still cannot be paired unambiguously — both
  halves id'd with **mismatched** ids, or **mixed authority** (different halves
  id'd on different slides) — is **refused**. A refusal emits **`refuse`** items,
  writes nothing, holds the watermark, and `--dry-run` shows exactly what a
  writing run does (`N refuse`, exit 1 — "changes pending"); a confirmed
  bootstrap shows `1 mint`/`1 adopt` instead (also exit 1 until applied). This
  bootstrap works **whether or not the pair is committed to git** (#225): a
  committed un-bootstrapped pair has a git-HEAD baseline that carries no ids, so
  it is treated as a cold start the same as a never-committed one — it mints/adopts
  rather than reading the id-less side as "missing every slide" (which would have
  doubled the deck). A committed pair that **shares some ids but gives one slide a
  different `slide_id` on each half** (the same content id'd divergently) is, with a
  provider available, **reconciled** (#228): the same correspondence check confirms the
  twin, and `sync` **rewrites the divergent id** so both halves share one (EN-authority).
  Leftover suspects with no confirmed twin use a direction-guarded hybrid — a
  single-direction leftover is cross-added (a genuinely-distinct one-sided slide),
  both-direction leftovers defer. Without a provider, or if the check returns "no" or
  cannot run, such a pair is **refused** rather than cross-added in both directions
  (#226) — `sync` cannot tell a divergent-id twin from a genuinely one-sided slide by id
  alone, so it declines instead of risking a duplicate. A reconcile shows `N reconcile`
  in `--dry-run` (exit 1 until applied). *Migration:* for a refused pair, sync one
  direction at a time (author one half, sync, then the other), reconcile the divergent
  ids so both halves share one id, or run `clm slides assign-ids <dir>`; then re-run sync.
- **`clm slides assign-ids` is now plumbing (hidden).** Per-file id minting on a
  *single* split half can mint a divergent slug — the #1 silent #162 break. It is
  hidden from `clm slides --help` but stays invocable by name for agents/scripts
  and one-off fixes. *Migration:* for everyday authoring, let the funnels mint ids
  — `clm slides sync` mints a shared id across both halves as it reconciles them,
  and `clm slides normalize` runs the same minting pass. To mint ids across a
  whole tree safely, `clm slides assign-ids <dir>` still works (EN-authority pair
  minting); prefer it over running the command on one half.
- **`clm slides suggest-sync` is now plumbing (hidden).** The old read-only
  single-FILE *bilingual* suggester is hidden from `clm slides --help` (still
  invocable, and still the `suggest_sync` MCP tool). It coexisted confusingly with
  the split-pair `sync`. *Migration:* for split-format decks use `clm slides sync`;
  `suggest-sync` remains for the pre-split bilingual layout and agent/MCP use.
- **`clm slides sync` accepts a single path.** `EN_PATH` is now optional: pass one
  half (`clm slides sync slides_x.de.py`) and the twin is derived from disk, or
  pass the bilingual deck stem (`slides_x.py`, when it still exists) to derive both
  halves. A missing twin is a clear usage error (exit 2); sync never invents a
  translated half. *Migration:* purely additive — the two-path form is unchanged,
  so existing invocations and scripts keep working.
- **`clm slides sync` accepts a directory (batch mode).** Pass a directory and
  every `.de`/`.en` deck pair under the tree is synced in one pass (prefix-agnostic
  enumeration, voiceover companions ignored). A half with no twin under the tree is
  **skipped with a warning**; the sweep **continues past a failing pair** and the
  exit code is the **worst** over all pairs (`0` < `1` < `2`). A **writing**
  directory run requires **`--yes`** (or an interactive confirm) since it writes to
  every pair at once; `--dry-run` / `--explain` directory runs are unprompted.
  `--interactive` stays single-pair only, and a second path with a directory is a
  usage error. `--json` over a directory returns an envelope
  `{ "mode", "root", "exit_code", "pairs": [ … ] }` (each `pairs` entry is one
  single-pair object). *Migration:* purely additive — passing a single file or a
  pair is unchanged; only a directory argument (previously rejected) now triggers
  the sweep.

## Slide format redesign: `clm validate` enforces `slide_id`

CLM {version} also ships **Phase 3** of the slide-format-redesign:
`clm validate` now inspects `slide_id` metadata and reports findings
under the existing `pairing` check group. The findings run in both
full (`clm validate slides/`) and quick (`clm validate slides/ --quick`)
modes, so the PostToolUse hook surfaces them at edit time.

### Severities and rollout

| Finding | Severity in {version} | Notes |
|---------|----------------------|-------|
| `slide`/`subslide` cell missing `slide_id` | `warning` through 1.7, **`error` since 1.8** | Escalated in CLM 1.8 (the release that retires the Phase 0 deprecation aliases). See "Breaking changes in CLM 1.8" above. |
| duplicate `slide_id` across slide groups | `error` | Group-aware: paired DE/EN cells sharing the EN-derived slug are not a duplicate. Bare-form comparison so `!intro` and `intro` collide. |
| voiceover/notes `slide_id` ≠ preceding `slide`/`subslide` anchor | `error` | Walk-back skips j2, code, shared (lang-less), and cross-language narrative cells. The j2 `header()` macro anchors `slide_id="title"` for narrative cells that follow it. |
| paired DE/EN slides carry mismatched bare `slide_id`s | `warning` | Fix with `clm slides assign-ids --force`. |
| `slide_id` value is not a valid kebab-case ASCII slug (≤30 chars) | `warning` | The leading `!` preserve marker is permitted and does not count toward the length cap. |

The two-release deprecation window (warning through 1.7, error from 1.8)
gave course repositories time to sweep `clm slides assign-ids` across
their decks without the hook spamming warnings for unmigrated files.

### How to migrate

```bash
# 1. Add ids to the headed-slide majority (zero-risk default).
clm slides assign-ids slides/

# 2. Review what the validator reports against the now-half-migrated
#    course; for the warning entries, decide whether to opt into
#    content-derived ids or hand-author them.
clm validate slides/ --quick

# 3. For extractable headingless slides, either:
clm slides assign-ids slides/ --accept-content-derived
# or, with Ollama running:
clm slides assign-ids slides/ --llm-suggest --accept-content-derived

# 4. For hard refusals (no extractable content), hand-author slide_id="..."
#    on the cell directly. Use the preserve marker `!` if you want the id
#    to survive future regeneration: slide_id="!intro".

# 5. Re-validate. As of CLM 1.8, missing slide_id is an error too —
#    along with duplicates, narrative adjacency mismatch, and invalid slug.
clm validate slides/
```

The `error`-severity findings already fail validation in {version} —
they cover content bugs (typo-introduced duplicates, stale voiceover
copies referencing the wrong slide) that should be fixed regardless
of the migration timeline.

## Slide format redesign: `clm slides coverage` (warning-level, opt-in)

CLM {version} also ships **Phase 4** of the slide-format-redesign:
`clm slides coverage` asks a local LLM (Ollama, default `qwen3:30b`)
whether each slide's bullets are covered by the voiceover that
follows it. Findings are emitted at `warning` severity — same
option-B rollout the Phase 3 missing-slide_id rule uses, so the
severity can promote to `error` in a future minor once the
false-positive rate against real ML AZAV decks is known.

The command is fully opt-in: it is not folded into `clm validate`
because LLM latency (~5-15s per pair on a cold model) would make
the hook too slow to run on every edit. Run it as part of a
deliberate sweep, on a pre-commit gate once the cache is populated,
or as a CI step.

### Cache, recommended layout

Verdicts are stored in `clm-llm.sqlite` in the LLM cache directory
resolved from `--cache-dir` → `$CLM_CACHE_DIR` →
`tool.clm.cache_dir` → `<project-root>/.clm-cache/`. For AZAV-scale courses
where the cache is worth sharing across machines, point
`tool.clm.cache_dir` in the course repo's `pyproject.toml` at a
sibling git repo (e.g. `../PythonCoursesClmLlmCache`) and commit
the database there. Trainers who don't work on AZAV-scale courses
can leave the setting unset and regenerate locally on demand — the
cache is fully regenerable.

Verdicts cache as `(slide_hash, voiceover_hash, prompt_version,
lang)` so:

- Re-runs over an unchanged deck make zero LLM calls.
- Editing one bullet's wording invalidates only that one pair's
  cache entry.
- Paired DE/EN slides cache as two independent rows (the LLM judges
  per-language).
- Bumping `prompt_version` invalidates every cached verdict at once
  via `CoverageCache.invalidate_prompt_version`.

### How to sweep a course

```bash
# 1. First pass — populates the cache and surfaces gaps.
clm slides coverage slides/

# 2. Fix the gaps. Re-run; only edited pairs re-check.
clm slides coverage slides/

# 3. Inspect cached verdicts for a deck if a gap looks wrong.
clm slides coverage --dump | less

# 4. Once stable, commit the SQLite cache so reviewers/CI don't re-spend.
git add -- "$CLM_CACHE_DIR/clm-llm.sqlite"
```

The PostToolUse hook on PythonCourses surfaces findings as
warnings only and never blocks writes. Use pre-commit or
`clm slides coverage slides/` as the gating sweep.

## `voiceover` coverage check is now opt-in (issue #176)

Course-authoring policy changed (2026-05-31): **voiceover is optional**
for every deck. Decks may intentionally ship without voiceover, and
narration is added only on explicit request.

Accordingly, the `voiceover` review check (which reports a gap for every
slide / nontrivial code cell that lacks a voiceover cell) is no longer
part of any default, "all", or "review" bundle. It runs **only** when you
name it explicitly.

What changed:

- The library functions `validate_file` / `validate_directory` /
  `validate_course` no longer include `voiceover` in their `checks=None`
  default. The default bundle is now `format`, `pairing`, `tags`,
  `code_quality`, `completeness`.
- The **MCP `validate_slides`** tool — which calls those functions with
  `checks=None` — therefore no longer emits `voiceover_gaps` by default.
  This was the main source of the false-positive flood on voiceover-less
  decks.

What did **not** change:

- The CLI `clm validate <slides>` default was already deterministic-only
  (`format`, `pairing`, `tags`) and never ran `voiceover`.
- The PostToolUse quick path (`clm validate --quick`) never ran it.

To run voiceover coverage on a deck that *is* meant to be fully narrated,
ask for it explicitly:

```bash
clm validate slides/topic/slides_intro.py --checks voiceover
```

…or via MCP: `validate_slides(path, checks=["voiceover"])`.

## `clm slides sync` is now a single-language authoring command (writes by default)

CLM {version} reworks `clm slides sync` into the single-language
authoring workflow (issue #166): edit **one** half of a split deck, run
the command, and it brings the other half into sync in a single pass —
propagating edits, translating and inserting brand-new slides, dropping
removed slides, mirroring reorders, and minting a shared `slide_id` onto
both decks.

### Breaking changes

| Before | After |
|--------|-------|
| **Default was dry-run** (printed diffs, wrote nothing). | **Default writes to the working tree.** A bare `clm slides sync de en` applies the agreed changes. Nothing is committed — review with `git diff`. Pass `--dry-run` for the old preview-only behavior. |
| `--source-lang de\|en` selected the edited side (or was inferred from `sync_snapshots` drift / git timestamps). | **Removed.** Direction is decided **per cell** by diffing each deck against the structural watermark, so a single pass can carry edits in both directions. A cell edited on both decks is isolated as a `conflict` instead of being guessed. Passing `--source-lang` is now a usage error. |
| `--apply --trivial` auto-applied EOL-/whitespace-only diffs. | **Removed.** The new default already applies; there is no trivial-only mode. Use `--interactive` to gate proposals one by one. Passing `--apply` or `--trivial` is now a usage error. |
| Per-cell `sync_snapshots` rows recorded the last accepted `(de_hash, en_hash)` for direction inference. | The engine now records an ordered, per-language **`sync_watermarks`** baseline (the whole deck, with cell order and id-less cells), written only on a successful apply. `sync_snapshots` is no longer written or read by `sync`. |

### What stays the same

- The pair arguments (`DE_PATH EN_PATH`), `--interactive`, `--json`,
  `--llm-model`, `--ollama-url`, `--llm-timeout`, `--cache-dir`, and
  `--no-cache` are unchanged in spelling (but see What's new — the
  `--llm-model` *default* now depends on the new `--provider`).
  `--interactive` still prompts per proposal (now
  `[a]pply / [s]kip / [q]uit`, plus `[d]e-wins / [e]n-wins` on a
  conflict) before a single atomic apply.
- Exit codes keep their buckets: `0` clean, `1` something left for
  review (a skipped proposal / unresolved conflict), `2` a structural
  error (classifier error, missing target cell, or the edit LLM down).

### What's new

- `--provider [openrouter|local]` (default `openrouter`) selects the
  edit-reconciliation judge backend, and `$CLM_SYNC_PROVIDER` sets a
  persistent default. The edit judge previously was always the local
  Ollama model; it now defaults to **Claude Sonnet via OpenRouter** (much
  faster), which needs `$OPENROUTER_API_KEY` (or `$OPENAI_API_KEY`). Pass
  `--provider local` for the offline Ollama judge. The judge model is
  still `--llm-model`, but its default now depends on `--provider`
  (`anthropic/claude-sonnet-4-6` for openrouter, `qwen3:30b` for local).
  See Issue #167.
- `--translation-model TEXT` (default `anthropic/claude-sonnet-4-6`)
  picks the OpenRouter model that translates brand-new slides on the add
  path. It needs `$OPENROUTER_API_KEY` (or `$OPENAI_API_KEY`); without a
  key, add proposals defer (everything else still applies).
- **Code cells and auxiliary markdown now sync too** (previously only
  `slide` / `subslide` / `voiceover` / `notes` markdown was propagated). A
  **language-neutral** code cell (no `lang=`) is copied **verbatim** across
  both halves; a **localized** code cell (`lang=`) and `alt` / untagged
  markdown are twinned and translated; a new slide brings its code along, and
  code moved between slide groups follows. If you used to hand-edit code on
  both halves to keep them in step, sync now does it for you — new code cells
  are **not** minted a `slide_id` (they stay id-less, kept consistent
  structurally).
- **The project `.env` is loaded automatically.** Sync walks up from each
  deck and loads the first `.env` it finds (without overriding already-set
  variables), so an `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY` kept in `.env`
  (not exported) is now found — previously every brand-new-slide translation
  silently deferred. Pass `--no-env-file` to skip it.
- A transient judge / translation failure now **retries with backoff** instead
  of dropping the cell, and the `--llm-timeout` default is provider-aware
  (120s for `openrouter`, 300s for `local`).

### Content-anchor sync (Issue #190 — additive, no break)

CLM {version} tracks cell identity in the watermark by a **content anchor**
(`hand slide_id > construct slug > content hash`, never written into the file),
which fixes the sync limitations that used to lose code edits or churn
translations. These are behavior *improvements* — no change is required in a
course repo — but agents driving `clm slides sync` should know:

- **A code-only edit to a language-neutral code cell now propagates.** Editing
  *only* a neutral (`# %%`, no `lang=`) code cell on one half — with no narrative
  or id change — used to be **silently dropped**; it is now copied verbatim to the
  twin. If a past sync left the two halves' code out of step, the next sync repairs
  it.
- **Unchanged localized code is no longer re-translated** when its slide group is
  rebuilt for a sibling's sake — it is spliced verbatim by its anchor, so re-runs
  are churn-free and spend no LLM.
- **A drifted `slide_id` is migrated back deterministically.** Split an id'd code
  cell (e.g. add an `import` above a `def`, leaving the id on the import) and the
  next sync moves the id onto the cell whose construct it names, minting a fresh
  slug on the orphan — symmetric across both decks (`de_id == en_id` preserved),
  no LLM.
- **A neutral cell edited differently on both decks auto-heals with a warning.**
  Set `CLM_SYNC__SHARED_DIVERGENCE=error` (see `clm info` / the configuration
  guide) to surface it as an error and write nothing instead.
- **New flags:** `--explain` (a read-only content-anchor diff — see why a cell did
  or did not sync), and the opt-in `--llm-recover` / `--recovery-model` (default
  off) for *genuinely ambiguous* id realignment (a function renamed while a cell
  was split). Without `--llm-recover`, an ambiguous region is left untouched and
  re-surfaces next run.
- **Watermark schema:** the `sync_watermarks` table gains a nullable `construct`
  column, migrated automatically on first use; pre-#190 caches upgrade in place
  (existing rows backfill to `NULL`). No action needed.

### How to migrate

```bash
# Before: bare invocation was a safe dry-run.
clm slides sync intro.de.py intro.en.py            # now WRITES — review with git diff

# Keep the old preview behavior explicitly.
clm slides sync intro.de.py intro.en.py --dry-run

# Drop --source-lang entirely — direction is per-cell now.
clm slides sync intro.de.py intro.en.py            # (was: --source-lang de)

# Replace --apply --trivial with the default apply, or gate with --interactive.
clm slides sync intro.de.py intro.en.py            # (was: --apply --trivial)
clm slides sync intro.de.py intro.en.py --interactive
```

Because the default now mutates files, wire `clm slides sync` into a
clean working tree (commit or stash first) so `git diff` shows exactly
what the sync proposed. The watermark advances only on a successful,
non-deferred apply, so a conflict or a skipped proposal re-surfaces on
the next run rather than being silently baselined.

## CLI restructure: verb-grouped subcommands

CLM {version} reorganises the top-level command surface. Several flat
commands moved under new groups for a smaller, more scannable layout:

| Old (still works, deprecated)        | New canonical                |
|--------------------------------------|------------------------------|
| `clm normalize-slides`               | `clm slides normalize`       |
| `clm language-view`                  | `clm slides language-view`   |
| `clm suggest-sync`                   | `clm slides suggest-sync`    |
| `clm search-slides`                  | `clm slides search`          |
| `clm resolve-topic`                  | `clm course resolve-topic`   |
| `clm authoring-rules`                | `clm slides rules`           |
| `clm extract-voiceover`              | `clm voiceover extract`      |
| `clm inline-voiceover`               | `clm voiceover inline`       |
| `clm validate-slides PATH`           | `clm validate PATH`          |
| `clm validate-spec SPEC`             | `clm validate SPEC`          |

### Deprecation timeline

- **{version}**: Old names still work and emit a one-line
  deprecation notice on stderr naming the new invocation. The
  notice does not affect exit codes or stdout, so scripts that
  pipe `--json` output through `jq` continue to work; only
  interactive users see the migration hint.
- **1.8 (planned)**: Old names removed.

### `clm validate` consolidates the two validators

`clm validate <path>` replaces both `validate-slides` and
`validate-spec`. It inspects the argument:

- `.xml` file → spec validation (formerly `validate-spec`)
- `.py` file or directory → slide validation (formerly `validate-slides`)

For ambiguous cases (an `.xml` you want fed to the slide validator,
or vice versa), pass `--kind=slides` or `--kind=spec` explicitly.

All flags from both old commands are still available; the new
command refuses combinations that don't apply (`--quick` with
`--kind=spec`, `--include-disabled` with `--kind=slides`).

### How to migrate

For scripts and CI:

- **Greppable rename**: each old name maps 1:1 to a new path. A
  global find-and-replace of `clm normalize-slides` →
  `clm slides normalize` (and similar for the other names) is
  safe.
- **PostToolUse hooks** referencing the old names will print the
  deprecation notice each edit. Update the hook command to the
  new path to silence it.
- **Skill files** (`.claude/commands/*.md` in consuming repos)
  should be updated to the new paths; the deprecation will be a
  noticeable lint signal if you forget any.

For interactive use, no action is needed — the old names keep
working until 1.8, and the deprecation notice tells you the new
path each time you invoke an old one.

## `clm build` no longer wipes the output tree by default

CLM {version} changes how `clm build` manages each output root. The
previous flow — move every nested `.git/` aside, `shutil.rmtree` the
whole tree, then regenerate from scratch — invalidated git's stat-cache
on every build and turned sub-second `git status` calls into multi-minute
re-hashes on large courses.

The new default does the opposite:

- **No wipe.** The existing output tree is left in place across builds.
- **Hash-aware writes.** Each write site checks whether the destination
  already holds byte-identical content; if so, the write is skipped so
  mtime/inode are preserved and git's stat-cache stays valid.
- **Post-build sweep.** Once all stages complete, anything under a
  build-owned root that the build did not write is deleted. This is what
  removes orphans from renamed/removed sections.

### What changed

| Flag | Before | After |
|------|--------|-------|
| (default) | wipe + restore `.git/` + rebuild | no wipe; hash-aware writes + sweep |
| `--keep-directory` | opt out of the wipe | **removed in CLM 1.8** (was a no-op alias) |
| `--incremental` | implies `--keep-directory`; skip cached writes | skip cached writes; implies `--no-sweep` |
| `--clean` | n/a (new) | opt into the legacy wipe-and-restore flow |
| `--no-sweep` | n/a (new) | opt out of the post-build sweep |

### How to migrate

Most users need no action — the default is faster and produces the same
output for unchanged content. A few scripts may need an explicit flag:

- **You depend on the output tree being wiped at the start of every
  build.** Pass `--clean`. It runs the legacy flow (move `.git/` aside,
  `shutil.rmtree` each root, regenerate). Nested `.git/` directories
  are preserved across the wipe, same as before.
- **You scripted `--keep-directory`.** The flag was **removed in CLM 1.8**
  (it had been a no-op alias since the output tree stopped being wiped by
  default). Remove it from the invocation; pass `--clean` if you actually
  want the legacy wipe-and-restore flow.
- **You scripted `--incremental` to avoid the wipe.** Drop `--incremental`
  unless you also want the disk-write skipping it adds on top of the new
  default. `--incremental` now implies `--no-sweep` as well.

### Governing principle for output trees

The new flow assumes **everything under a build-owned output root is
exclusively CLM's**. Authors do not hand-place auxiliary files there —
the sweep removes `.gitignore`, `README.md`, editor caches, and
similar untracked files at the root of an output tree. If a course
genuinely needs an auxiliary file in its output, the right answer is
to teach CLM to generate it, not to special-case the sweep.

Nested `.git/` directories are spared (so the output tree can be its
own git repo) and any subtree containing a `.git/` is treated as opaque
(so a nested repo's files are left alone).

## Topic ID via `id=` attribute (preferred form when topics have children)

CLM {version} adds an `id=` attribute on `<topic>` as an alternative to the
existing text-content form. The legacy form continues to work unchanged for
plain topics:

```xml
<topic>introduction</topic>          <!-- still works -->
<topic id="introduction"/>           <!-- equivalent -->
```

### What changed

When a `<topic>` carries `<include>` or any other child elements, the
attribute form is now **required**:

```xml
<!-- BEFORE: only the text-before-children shape was safe -->
<topic>
    gradio_intro
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
</topic>

<!-- AFTER: use id= attribute (clearer, order-independent) -->
<topic id="gradio_intro">
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
</topic>
```

CLM now hard-errors when a `<topic>` has child elements but no resolvable
ID. This closes a footgun: XML parsers assign text appearing *after* a
child element to that child's tail rather than to the parent, so an author
who writes the ID after a child would silently end up with an empty topic
ID. The error message points at the wrinkle so authors know what to fix.

Specifying the ID twice (both `id=` attribute *and* text content) is also a
hard error. Pick one form per topic.

### How to migrate

For topics with `<include>` (or any child elements) that currently use the
text-before-children form:

```xml
<!-- Before -->
<topic>
    gradio_intro
    <include source="..." as="..."/>
</topic>

<!-- After -->
<topic id="gradio_intro">
    <include source="..." as="..."/>
</topic>
```

Childless topics need no changes — `<topic>introduction</topic>` keeps
working. Migration is opt-in for those.

Verification: `clm validate course.xml` parses cleanly. Any `<topic>`
with children and a stale post-child ID will surface as a clear
`CourseSpecError` pointing at the section.

## Splitting the `speaker` output kind into `trainer` + `recording`

CLM {version} renamed the single `speaker` output kind into two named
kinds, since they serve genuinely different audiences:

- `trainer` — for trainers teaching the course. Keeps speaker `notes`
  cells but strips `voiceover` cells (those are only meaningful when the
  deck is read aloud for video recording). This is the variant most
  trainers want.
- `recording` — for the trainer recording the course on video. Keeps
  both `notes` and `voiceover` cells. The voiceover cells contain the
  polished narration read on camera.

### What still works

`<kind>speaker</kind>` continues to parse and is treated as
`recording` for one release. Spec parsing logs a deprecation warning
and rewrites the kind internally, so downstream consumers
(`clm build`, `clm validate`, the MCP tools, etc.) only ever see
the canonical kinds. The `--speaker-only` CLI flag also still works
and now selects both `trainer` and `recording`, since both share the
private (`speaker/`) toplevel output directory.

### What changed

- Output paths now always include a kind subdir. Previously a `speaker`
  build wrote to `output/speaker/<course>/Slides/Html/<topic>.html`
  (no kind subdir). The new `recording` and `trainer` kinds write to
  `output/speaker/<course>/Slides/Html/Recording/<topic>.html` and
  `output/speaker/<course>/Slides/Html/Trainer/<topic>.html`
  respectively. The deprecated `speaker` kind alias produces the same
  layout as `recording` (i.e., it now also has a kind subdir).
- The HTML cache producer is now `recording`, not `speaker`. Trainer,
  Completed, and Partial HTML all reuse Recording's cached executed
  notebook by filtering the appropriate cell subset.

### How to migrate course specs

Replace `<kind>speaker</kind>` with whichever new kind matches that
target's intent:

```xml
<!-- Before -->
<output-target name="instructor">
    <path>./output/instructor</path>
    <kinds><kind>speaker</kind></kinds>
</output-target>

<!-- After: live-teaching deck (most trainers) -->
<output-target name="trainer">
    <path>./output/trainer</path>
    <kinds><kind>trainer</kind></kinds>
</output-target>

<!-- After: video-recording deck -->
<output-target name="recording">
    <path>./output/recording</path>
    <kinds><kind>recording</kind></kinds>
</output-target>
```

If a single target should produce both decks (e.g., one repository
holding both for a recording trainer), list both kinds:

```xml
<output-target name="instructor">
    <path>./output/instructor</path>
    <kinds>
        <kind>trainer</kind>
        <kind>recording</kind>
    </kinds>
</output-target>
```

Verification:

- `clm validate course.xml` — the spec parses cleanly without
  any `speaker` deprecation warnings.
- `clm build course.xml --speaker-only` — produces both `Trainer/` and
  `Recording/` subdirs under the private (`speaker/`) toplevel.

## Migrating from `-build.xml` subset specs to `enabled="false"`

CLM {version} introduced the `enabled` attribute on `<section>` elements and
the `clm build --only-sections` flag. Together they replace the common
pattern of carrying a second "buildable subset" spec file alongside the
full roadmap spec.

Before, courses with not-yet-implemented topics typically looked like
this:

```text
course-specs/
├── machine-learning-azav.xml        # full roadmap; wraps unfinished
│                                    # sections in <!-- XML comments -->
└── machine-learning-azav-build.xml  # same spec with those sections
                                      # removed so clm build succeeds
```

Three-step migration:

1. **Add `enabled="false"` to not-yet-ready sections** in the full
   roadmap spec. A disabled section may omit `<topics>` entirely or
   reference topic IDs that do not exist — it is not built or validated.

   ```xml
   <section id="w17" enabled="false">
       <name>
           <de>Woche 17: Fortgeschrittene Themen</de>
           <en>Week 17: Advanced Topics</en>
       </name>
       <topics>
           <topic>not_yet_implemented_topic</topic>
       </topics>
   </section>
   ```

2. **Delete the `-build.xml` subset file.** One source of truth from
   now on.

3. **Update any scripts or automation** that reference the `-build.xml`
   path to use the full spec instead.

Verification:

- `clm build course.xml` — builds the full roadmap minus disabled
  sections.
- `clm build course.xml --only-sections w03` — dev-time iteration on a
  single section (see `clm info commands`).
- `clm outline course.xml --include-disabled` — lists the disabled
  sections with a `(disabled)` marker so you can see the full roadmap.
- `clm validate course.xml --include-disabled` — validates disabled
  sections' topics with a `(disabled)` suffix on each finding so you can
  track which topics still need to be created.

See also: `clm info spec-files` for the `enabled` / `id` attribute
reference and `clm info commands` for the `--only-sections` selector
syntax.

## v1.2.0 to v1.2.1: Voiceover sync argument order change

### Breaking Change

`clm voiceover sync` now accepts **multiple video files**. To support this,
the argument order was flipped:

```bash
# Before (v1.2.0)
clm voiceover sync VIDEO SLIDES --lang de

# After (v1.2.1)
clm voiceover sync SLIDES VIDEO... --lang de
```

`SLIDES` is now the first positional argument, followed by one or more
`VIDEO` paths. Single-video invocations work the same way — just swap the
argument order.

### New default: merge mode

`clm voiceover sync` now **merges** transcript content into existing
voiceover cells by default instead of overwriting them. Use `--overwrite`
to restore the old destructive behavior. Note that `--mode verbatim`
without `--overwrite` is now an error.

---

## v0.3.x to v0.4.0: Unified Package Architecture

### Summary

CLM v0.4.0 consolidated all worker code into a single `clm` package with
optional extras, replacing separate worker packages.

### Breaking Changes

#### Worker packages are no longer separate

Before (v0.3.x):

```bash
pip install -e .
pip install -e ./services/notebook-processor
pip install -e ./services/plantuml-converter
pip install -e ./services/drawio-converter
```

After (v0.4.0+):

```bash
pip install -e ".[all]"           # Everything
pip install -e ".[all-workers]"   # All workers only
pip install -e ".[notebook]"      # Specific worker
```

#### Module paths changed

Before (v0.3.x):

```python
import nb
import plantuml_converter
import drawio_converter
```

After (v0.4.0+):

```python
from clm.workers import notebook
from clm.workers import plantuml
from clm.workers import drawio
```

Command-line entry points:

```bash
# Before
python -m nb
python -m plantuml_converter
python -m drawio_converter

# After
python -m clm.workers.notebook
python -m clm.workers.plantuml
python -m clm.workers.drawio
```

#### Docker images updated

```dockerfile
# Before
COPY ./clm-common ./clm-common
COPY ${SERVICE_PATH} ./service
RUN pip install ./clm-common && pip install ./service
CMD ["python", "-m", "nb"]

# After
COPY . ./clm
RUN pip install ./clm[notebook]
CMD ["python", "-m", "clm.workers.notebook"]
```

### Installation extras (v0.4.0+)

| Extra | Description |
|-------|-------------|
| `[notebook]` | Jupyter notebook processing |
| `[plantuml]` | PlantUML diagram conversion |
| `[drawio]` | Draw.io diagram conversion |
| `[all-workers]` | All workers |
| `[ml]` | ML packages (PyTorch, FastAI, etc.) |
| `[summarize]` | LLM-powered summaries and polish (openai) |
| `[voiceover]` | Video-to-speaker-notes pipeline |
| `[recordings]` | Video recording management and audio processing |
| `[slides]` | Slide authoring tools with fuzzy search |
| `[mcp]` | MCP server for AI-assisted slide authoring |
| `[dev]` | Development tools (pytest, mypy, ruff) |
| `[tui]` | TUI monitoring |
| `[web]` | Web dashboard |
| `[all]` | Everything |

---

## v0.2.x to v0.3.0: Consolidated Package

### Summary

CLM v0.3.0 merged four separate packages (`clm`, `clm-common`,
`clm-faststream-backend`, `clm-cli`) into a single `clm` package.

### Breaking Changes

#### Import paths changed

```python
# Core imports — add .core
from clm import Course          # -> from clm.core import Course
from clm.course_files import    # -> from clm.core.course_files import
from clm.operations import      # -> from clm.core.operations import
from clm.utils import           # -> from clm.core.utils import

# Infrastructure — replace clm_common
from clm_common import           # -> from clm.infrastructure import
from clm_common.backend import   # -> from clm.infrastructure.backend import
from clm_common.database import  # -> from clm.infrastructure.database import
from clm_common.messaging import # -> from clm.infrastructure.messaging import
from clm_common.workers import   # -> from clm.infrastructure.workers import

# Backends — replace clm_faststream_backend
from clm_faststream_backend import SqliteBackend
# -> from clm.infrastructure.backends import SqliteBackend
# Note: FastStreamBackend (RabbitMQ) was removed entirely

# CLI — replace clm_cli
from clm_cli.main import cli    # -> from clm.cli.main import cli
```

#### Convenience imports still work

```python
from clm import Course, Section, Topic, CourseFile, CourseSpec  # OK
```

### Uninstalling old packages

```bash
pip uninstall -y clm clm-cli clm-common clm-faststream-backend
pip install -e .
```
