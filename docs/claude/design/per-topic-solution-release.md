# Design: Per-Topic Solution Release (Frozen Promotion + Provenance)

**Status**: Draft
**Created**: 2026-06-03
**Authors**: Matthias Hölzl (design direction), Claude (AI assistant)
**Requirements**: [per-topic-solution-release.md](../requirements/per-topic-solution-release.md)
**Predecessor (shipped)**: [delayed-solution-release.md](./delayed-solution-release.md)

---

## 1. Design Thesis

Three roadmap items — per-topic delayed solutions, multi-cohort iterations, and build provenance —
collapse onto **one new primitive plus one new engine**:

1. **A build provenance manifest** (`.clm-manifest.json`) mapping every output file to
   `{section_id, topic_id, source_commit, source_dirty, content_hash}`.
2. **A stateless promotion/sync engine** (`clm release`) that, per cohort, copies *released but
   not-yet-frozen* topics from a frozen source tree into a destination repo and records the freeze.

Everything else is reuse:

| Reused | From |
|---|---|
| `partial` / `completed` cell rendering | `OutputSpec` subclasses (no change) |
| Source git commit + dirty capture | `clm.recordings.git_info.get_git_info()` (already exists) |
| Extensible per-output metadata sink | `JobQueue.add_to_cache(..., result_metadata)` |
| Symmetric payload round-trip (no dropped fields) | `Payload.from_job_payload()` → `model_validate` |
| Per-repo git ops + remote derivation + dry-run | `git_ops.py`, `GitHubSpec.derive_remote_url()` |
| Non-destructive, hash-aware output writes | shipped default build flow |

**Crucially, the feature sits *beside* the build, not inside it.** The only build-internal changes
are: capture the source commit, stamp `(section_id, topic_id)` onto notebook jobs, record
dir-group ownership, and run one post-build pass to emit the manifest. The `partial`/`completed`
machinery, the spec's per-topic structure, and the worker execution path are untouched.

---

## 2. Architecture

```
COURSE SOURCE REPO                          SOLUTIONS REPOSITORY (promotion dest; NOT a build target)
  course.xml            (stable structure)    jan/                  ← cohort subfolder
  <release-channels>    (structural decl)       .clm-released.json  ← frozen manifest (facts+provenance)
  release/jan.yaml      (volatile intent)        <copied topic files, frozen>
  release/mar.yaml                             mar/
  release/may.yaml                               .clm-released.json
                                                 <copied topic files, frozen>
        │
        │ clm build  (normal; CLM owns + sweeps the frozen source)
        ▼
  FROZEN SOURCE = a `completed`-kind <output-target>
    De|En/Course/slides/{Notebooks,Python,Html}/Completed/<Section>/<files>
    .clm-manifest.json   ← output_path → {section_id, topic_id, source_commit, source_dirty, hash}
        │
        │ clm release sync --channel jan    (copy released∧¬frozen topics, BY MANIFEST; freeze)
        ▼
  jan/  (then: clm git push --channel jan,  or  clm release sync --channel jan --push)
```

The frozen source is an ordinary, always-current `completed` output target that the author rebuilds
freely. The freeze boundary is **not** a pin on the source; it is each cohort's `.clm-released.json`.
The source may advance; a frozen topic in `jan/` never changes unless explicitly re-frozen.

---

## 3. Components

### 3.1 Build Provenance Manifest

**Artifact**: `.clm-manifest.json`, one per output root (written next to the existing CLM-owned
sidecars).

**Schema** (v1):

```json
{
  "version": 1,
  "source_commit": "abc123def…",
  "source_dirty": false,
  "built_at": "2026-06-03T10:00:00Z",
  "spec": "machine-learning-azav.xml",
  "files": [
    {
      "path": "<output-root-relative path>",
      "section_id": "w03",
      "topic_id": "functions",
      "kind": "completed",
      "format": "notebook",
      "language": "en",
      "content_hash": "sha256:…"
    }
  ],
  "dir_groups": [
    { "name": "data", "scope": "global", "section_id": null, "topic_id": null,
      "paths": ["…"] },
    { "name": "examples", "scope": "topic", "section_id": "w03", "topic_id": "functions",
      "paths": ["…"] }
  ]
}
```

**Why a manifest is mandatory (not path inference):** the owning **topic is not recoverable from the
output path**. Output files are grouped only by `sanitize(section.name)`; topics within a section
share that folder, notebooks carry a number prefix but assets do not, and dir-groups are written at
the *course* root rather than under their section. Section is path-recoverable; topic is not. The
manifest is therefore the join key the sync engine needs — and it carries `source_commit` for free,
which *is* provenance item (1).

**Where it is produced.** Two parts:

1. **Per-file ownership + provenance is stamped at the write chokepoint.** Each notebook job already
   funnels through `NotebookWorker._process_job_async → job_queue.add_to_cache(output_file,
   content_hash, result_metadata)` where `result_metadata` is an open dict currently holding
   `{format, kind, prog_lang, language}`. We extend it with `source_commit`, `source_dirty`,
   `section_id`, `topic_id`. (See 3.1.1 for the payload plumbing and the Issue #17 rule.)

2. **A post-build pass assembles the manifest.** After all stages, all targets, and the output sweep
   complete (in `build.py`, after `_maybe_run_sweep(...)` / `build_reporter.finish_build()`), a new
   `_write_provenance_manifests(course, root_dirs, source_commit, registry)` walks the build's
   write registry / `results_cache` and emits one `.clm-manifest.json` per output root. It runs only
   in one-shot mode (skipped under `--watch`); it is compatible with `--clean`, `--incremental`, and
   `--no-sweep` (the manifest simply reflects the final on-disk tree).

**Source commit capture.** Add `source_commit: str | None` (and `source_dirty: bool`) to
`BuildConfig`, populated once at the start of `main_build` via
`clm.recordings.git_info.get_git_info(course_root)`. Thread it into payloads and into the post-build
pass. If `course_root` is not a git repo, record `null` and proceed.

#### 3.1.1 Payload plumbing — `section_id` / `topic_id` (Issue #17 rule)

`NotebookPayload` (`src/clm/infrastructure/messaging/notebook_classes.py`) gains:

```python
section_id: str = ""
topic_id: str = ""
```

Set at submission in `ProcessNotebookOperation.payload()`
(`src/clm/core/operations/process_notebook.py`) from the in-scope file:

```python
section_id=self.input_file.topic.section.id or "",
topic_id=self.input_file.topic.id,
```

**Do not** hand-list these in any worker-side constructor. The worker reconstructs via
`NotebookPayload.from_job_payload(payload_data, …)`, which calls `model_validate` over the *whole*
dict (`src/clm/infrastructure/messaging/base_classes.py`). This symmetry is exactly the guarantee
that closed Issue #17 (a hand-listed constructor silently dropped `cross_references`). New fields
ride through automatically *because* of `model_validate`; a hand-listed path would silently drop
them.

#### 3.1.2 Dir-group ownership

Topic-scoped `<dir-group>`s are parsed as children of their `<topic>` (so the owner is known at
parse time) but are currently written to the course root with no recorded owner. We record
`(scope, section_id, topic_id)` for each dir-group in the manifest's `dir_groups` array. **We do
not restructure output paths** — that would change student-visible layout. Sync copies dir-groups
by manifest ownership regardless of where they sit in the tree. Global `<dir-groups>` get
`scope: "global"`.

### 3.2 Ledger (release intent — volatile, in source repo)

`release/<channel>.yaml`, e.g. `release/jan.yaml`:

```yaml
# Cumulative set of released topic ids for the January cohort.
released:
  - introduction
  - variables_and_types
  - functions
# Optional, reserved for a future scheduled-sync layer (not used in the first cut):
# schedule:
#   decorators: 2026-07-01
```

- **Cumulative**, not a per-week delta — this is what makes sync sweep-safe and diffs clean.
- Lives in the **course source repo**, never in `course.xml`.
- `clm release add` / `clm release week` edit it after validating ids against the spec's topics.

### 3.3 Frozen Manifest (release fact — in destination)

`<solutions-repo>/<channel>/.clm-released.json`:

```json
{
  "version": 1,
  "channel": "jan",
  "frozen": {
    "introduction":        { "source_commit": "aaa…", "copied_at": "2026-01-08T…", "files": {"…": "sha256:…"} },
    "variables_and_types": { "source_commit": "aaa…", "copied_at": "2026-01-15T…", "files": {"…": "sha256:…"} },
    "functions":           { "source_commit": "bbb…", "copied_at": "2026-01-22T…", "files": {"…": "sha256:…"} }
  },
  "skeleton_frozen": true
}
```

This file *is* the freeze boundary. Its git history in the solutions repo is the release audit log.

### 3.4 Sync Engine

`clm release sync --channel C` reconciles intent (ledger) into fact (destination), driven by the
**frozen source's provenance manifest**:

```
load ledger(C).released, frozen_manifest(C), source_manifest(.clm-manifest.json)

if not frozen_manifest.skeleton_frozen:
    copy all global dir-groups + each released section's section-scoped assets   # channel init
    mark skeleton_frozen = true

for topic in ledger(C).released:
    if topic in frozen_manifest.frozen and topic not in refreeze_set:
        skip                                   # frozen — students keep what they saw
    else:
        files = source_manifest.files_for(topic) + dir_groups.for_topic(topic)
        ensure section-scoped assets for topic.section present (R6.2)
        copy files  source → C/                # by manifest, never by glob; never rebuild
        frozen_manifest.frozen[topic] = {source_commit, now, hashes}

write frozen_manifest(C)
if --push: delegate to clm git (commit + push channel C)   # 3.6
```

Properties: idempotent (R5.2), `--dry-run` prints the copy/freeze/skip plan, `--refreeze <t>` /
`--refreeze-all` move topics into `refreeze_set`.

> **Note on the source.** The author "updates the output repo" by **re-running `clm build`** (the
> output tree is CLM-owned and swept; hand-edits are not tolerated). Sync reads the source's current
> working tree and *records* its commit; it does not pin or re-fetch a ref. The source may advance
> freely — the freeze lives entirely in the destination manifest.

### 3.5 `clm release` CLI group

New module `src/clm/cli/commands/release.py`, group registered like the existing ones
(`cli.add_command(release_group)` in `src/clm/cli/main.py`, next to `git_group`; group defined as
`@click.group("release")` in the `_groups.py` style):

| Subcommand | Signature (sketch) | Purpose |
|---|---|---|
| `add` | `release add SPEC --channel C TOPIC_ID…` | append ids to ledger (validated) |
| `week` | `release week SPEC --channel C SELECTOR` | resolve module/section selector → ids, append |
| `status` | `release status SPEC [--channel C]` | released vs pending; frozen vs pending-copy |
| `sync` | `release sync SPEC --channel C [--push] [--refreeze T… | --refreeze-all] [--dry-run]` | promote + freeze (+ push) |

`week` reuses the existing section/topic selector engine (`id:` / `idx:` / `name:`, the same one
`--only-sections` uses) to expand a week into its topic ids.

### 3.6 `clm git` Extension for Solution Channels

Solution channels are **not** `<output-targets>`, so `find_output_repos()` does not see them. We
make them first-class for git without disturbing the output-target path:

1. **Declare channels structurally in the spec** (3.7) so they are discoverable through the same
   spec-reading path, and their remotes derive through `GitHubSpec.derive_remote_url()` (reused).
2. **Generalize `OutputRepo`** (`src/clm/cli/commands/git_ops.py`) with a `source: str = "output"`
   field (`"output"` | `"channel"`; default keeps existing behavior).
3. **Add `find_release_channel_repos(spec_file, channel_filter)`** mirroring `find_output_repos`:
   enumerate `<release-channels>` → resolve each channel's destination path → derive its remote via
   `GitHubSpec.derive_remote_url(channel_name, language="", remote_path=…)` → yield `OutputRepo(...,
   source="channel")`.
4. **Add a `--channel` filter** to each `clm git` subcommand. When given, the command operates on
   channel repos; the per-repo loop, `run_git`, dry-run, and `has_remote()` are all already generic
   over a list of `OutputRepo` and need no change.
5. `clm release sync --push` calls this same machinery (commit + push the channel repo) — **no
   second git implementation.**

This touches only `git_ops.py` (one new discovery function, one struct field, one option per
command). `GitHubSpec.derive_remote_url()` and `OutputTargetSpec` remote handling are reused as-is.

### 3.7 Spec `<release-channels>` (structural, stable)

```xml
<release-channels source-target="solutions-source">
  <repository path="./solutions">
    <!-- optional remote derivation knobs, reusing GitHubSpec rules -->
    <remote-path>solution-cohorts</remote-path>
    <channel name="cohort-jan" subdir="jan" ledger="release/jan.yaml"/>
    <channel name="cohort-mar" subdir="mar" ledger="release/mar.yaml"/>
    <channel name="cohort-may" subdir="may" ledger="release/may.yaml"/>
  </repository>
</release-channels>
```

- `source-target` names the `completed`-kind `<output-target>` that is the frozen source.
- `<repository>` is one git repo (the promotion destination); it may hold several cohort
  subfolders — exactly the author's "single output repository, different solution folders, each its
  own ledger" model. Multiple `<repository>` elements are allowed for per-cohort repos.
- `subdir` is the cohort folder inside the repo; `ledger` points at the volatile schedule in source.
- This block is **structural and stable** — a handful of entries fixed for the course's life. It is
  categorically unlike the per-topic volatile annotations the author refuses (those are the ledger).
- Parsed by a new `ReleaseChannelsSpec` alongside `OutputTargetSpec` in `course_spec.py`; absent →
  feature dormant, all behavior unchanged.

> The frozen source assumes a `completed` target with no speaker notes. A course that only builds
> `trainer` (notes included) should add a `completed` output target for the channels to draw from.

### 3.8 Recording → Slide-Version Provenance (separable consumer)

Recordings already capture the source commit: `RecordingPart`/`TakeRecord`
(`src/clm/recordings/state.py`) carry `git_commit` / `git_dirty` via `get_git_info()`. Extend with:

```python
slide_content_hash: str | None = None   # hash of the visible slide notebook at record time
section_id: str | None = None
topic_id: str | None = None
```

Captured when a take/part is assigned. "What changed since this video was recorded" then equals a
diff between the recording's stored `slide_content_hash`/`source_commit` and the **current**
provenance manifest entry for that `(section_id, topic_id)` slide. This item is independent of the
release engine but consumes the same provenance primitive.

---

## 4. Data Flow

```
BUILD:    clm build → (jobs stamped section/topic/source_commit) → outputs written
                    → post-build pass → .clm-manifest.json per root

INTENT:   clm release add SPEC --channel jan functions     → release/jan.yaml gains "functions"

PROMOTE:  clm release sync SPEC --channel jan [--push]
            → read jan.yaml + jan/.clm-released.json + source .clm-manifest.json
            → copy released∧¬frozen topics' files (by manifest) source → solutions/jan/
            → record freeze; (optional) clm git commit+push channel jan

PROVENANCE: any output file → .clm-manifest.json → source_commit
            any recording   → state.json slide_content_hash → diff vs current manifest
```

---

## 5. Workflow Impact (explicit)

| Surface | Impact |
|---|---|
| **`clm build`** | New post-build manifest pass; new `BuildConfig.source_commit`; notebook jobs stamped with `section_id`/`topic_id`; dir-group ownership recorded. No change to existing output bytes, kinds, or the execution path. |
| **`clm git`** | New `--channel` filter + `find_release_channel_repos()`; `OutputRepo` gains a `source` tag. Default (output-target) behavior unchanged. This is the author-flagged extension that lets solution directories be pushed. |
| **`clm release`** | New command group (ledger editing, status, sync/promote, push delegation). |
| **Course-author workflow** | Author keeps rebuilding the frozen source freely; released topics are frozen per cohort and never regress. Weekly motion is `clm release add … ` then `clm release sync --channel … --push`. |
| **Recordings** | Optional new provenance fields linking a take to its slide version (reuses existing `git_commit` capture). |
| **Course spec** | Optional structural `<release-channels>`; **no per-topic churn** (that's the ledger). |
| **Solutions repo** | A `clm release`-curated promotion destination — **never** a build target, **never** swept by `clm build`. |
| **Docs / info topics (project rule)** | `commands.md` (`clm release`, `clm git --channel`), `spec-files.md` (`<release-channels>`), `migration.md` (`.clm-manifest.json` + provenance), plus `docs/user-guide` how-to. |

---

## 6. File Changes Summary (verified attach points)

| File | Change |
|---|---|
| `src/clm/infrastructure/messaging/notebook_classes.py` | Add `section_id`, `topic_id` fields to `NotebookPayload` |
| `src/clm/core/operations/process_notebook.py` | Set `section_id`/`topic_id` in `payload()` from `input_file.topic` |
| `src/clm/workers/notebook/notebook_worker.py` / `infrastructure/database/job_queue.py` | Thread `source_commit`/`source_dirty`/`section_id`/`topic_id` into `add_to_cache(result_metadata)` (no hand-listed payload reconstruction — keep `from_job_payload`/`model_validate`) |
| `src/clm/cli/commands/build.py` | `BuildConfig.source_commit/source_dirty`; capture via `get_git_info` at `main_build` start; new `_write_provenance_manifests(...)` post-sweep pass (skip under `--watch`) |
| `src/clm/core/provenance_manifest.py` *(new)* | `.clm-manifest.json` schema, writer, and `files_for(topic)` / `dir_groups.for_topic()` reader |
| `src/clm/core/course_spec.py` | `ReleaseChannelsSpec` + `<release-channels>` parsing; reuse `GitHubSpec.derive_remote_url()` |
| `src/clm/cli/commands/git_ops.py` | `OutputRepo.source` field; `find_release_channel_repos()`; `--channel` option on each subcommand |
| `src/clm/release/` *(new pkg)* | ledger I/O + validation; frozen-manifest I/O; sync/promote engine |
| `src/clm/cli/commands/release.py` *(new)* | `clm release` group (`add`/`week`/`status`/`sync`) |
| `src/clm/cli/main.py` | `cli.add_command(release_group)` |
| `src/clm/recordings/state.py` | Optional `slide_content_hash`/`section_id`/`topic_id` on `RecordingPart`/`TakeRecord` |
| `src/clm/cli/info_topics/{commands,spec-files,migration}.md` | New-feature docs (project maintenance rule) |
| `tests/...` | manifest emission; ledger/freeze/sync rules; refreeze; multi-channel divergence; `clm git --channel`; recording provenance |

---

## 7. Implementation Sequencing

One coherent feature; the order is dependency-driven, not gated milestones (the author intends to
drive straight through). Each step is independently testable and leaves the tree shippable.

1. **Provenance foundation.** Source-commit capture + payload `section_id`/`topic_id` (Issue #17
   rule) + dir-group ownership + the post-build `.clm-manifest.json` pass. *Standalone value:* answers
   "which commit produced this file." This is also where R6.3's dir-group ownership lands, so the
   engine has it when it arrives.
2. **Release engine.** Ledger I/O + frozen-manifest + the sync/promote algorithm reading the
   manifest, plus the `clm release` group (`add`/`week`/`status`/`sync`, no `--push` yet). Operates
   on a plain destination folder.
3. **Git integration.** `<release-channels>` spec parsing + `find_release_channel_repos()` +
   `clm git --channel` + `clm release sync --push` delegation. This is the author-requested
   `clm git` extension.
4. **Multi-cohort.** Largely emergent — it is "more than one channel over one source"; mostly tests
   proving per-cohort freeze and manifest-diff divergence (R8).
5. **Recording provenance.** Independent consumer (R10): recordings-side fields + a "diff since
   recorded" helper.

---

## 8. Testing Strategy

- **Manifest unit**: every written output file appears with correct `(section_id, topic_id,
  content_hash)`; `source_commit` recorded; dir-group ownership (global vs topic) correct.
- **Issue #17 guard**: a test asserting `section_id`/`topic_id` survive the job round-trip to the
  worker (the field-drop regression sentinel).
- **Sync rules**: released∧¬frozen → copied+frozen; frozen → skipped even after source changes;
  not-released → absent; `--refreeze` re-copies; idempotent re-run; `--dry-run` plan matches.
- **Assets/dir-groups**: section assets appear with the section's first released topic; topic
  dir-groups travel with their topic; global skeleton released at channel init and frozen.
- **No rebuild at release**: sync performs zero kernel executions (assert no jobs enqueued).
- **Multi-cohort**: two channels reach the same topic at different source commits → their frozen
  manifests record different `source_commit`s; diff identifies the divergence.
- **`clm git --channel`**: discovery, remote derivation, dry-run, push path.
- **Recording provenance**: stored slide hash enables a correct "changed since recording" diff.

---

## 9. Open Design Decisions (defaults chosen)

| # | Decision | Default |
|---|---|---|
| D1 | Topic ownership recovery | **Manifest-driven** (no output-path restructuring) |
| D2 | Frozen-manifest location | **Destination channel folder** (self-describing; history = audit log) |
| D3 | Global dir-groups | Released at **channel init**, then frozen |
| D4 | Channel destination layout | **One repo, per-cohort subfolders** (per-repo also expressible) |
| D5 | Scheduled reveal | **Deferred**; dated ledger reserved, manual `sync` ships first |
| D6 | Frozen source kind | A **`completed`** output target (add one if only `trainer` exists) |

---

## 10. References

1. Requirements: [per-topic-solution-release.md](../requirements/per-topic-solution-release.md)
2. Predecessor (shipped): [delayed-solution-release.md](./delayed-solution-release.md)
3. Git capture helper: `src/clm/recordings/git_info.py`
4. Payload round-trip guarantee (Issue #17): `src/clm/infrastructure/messaging/base_classes.py`
5. Git machinery: `src/clm/cli/commands/git_ops.py`, `GitHubSpec.derive_remote_url()` in
   `src/clm/core/course_spec.py`
