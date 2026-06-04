# Requirements: Per-Topic Solution Release (Frozen Promotion + Provenance)

**Status**: Draft
**Created**: 2026-06-03
**Authors**: Matthias Hölzl (course author / design direction), Claude (AI assistant)
**Supersedes-in-spirit**: [delayed-solution-release.md](./delayed-solution-release.md) — that
document specified the **multi-output-target** feature (the `<output-targets>` element and
per-target `clm git` repositories), which has since **shipped**. It explicitly scoped *out*
per-topic granularity, date-based release, and git deployment. Git deployment has since landed;
this document picks up the two deferred capabilities — **per-topic** release and a **schedule
that lives outside the spec** — and adds a provenance layer that several roadmap items share.

---

## Executive Summary

Students receive `partial` notebooks during a course: the worked code *before* each workshop is
shown, but the workshop's exercise cells are blanked so students solve them themselves. **After a
workshop is discussed, that topic's full solution should become available** — and only that
topic's, not solutions to exercises the cohort has not yet reached.

CLM today can express "students get code-along, solutions go elsewhere" only at **whole-course
target granularity**, and only by re-running builds. The course author wants:

1. **Per-topic** release (release `functions` this week without exposing `decorators` next week).
2. **No churn in the course spec** — the stable structure file must not gain volatile per-topic
   "is this released yet" annotations.
3. **Frozen solutions** — once a cohort has been given a topic's solution, later edits to the
   course (bug-fixes for the *next* cohort, ongoing development) must **not** retroactively change
   what the current cohort already received.
4. **Multiple concurrent cohorts** of the same course, each on its own release schedule (e.g. an
   iteration starting in January, another in March, another in May), all drawing from one body of
   material "modulo unchanged history of published material".
5. As a by-product, **provenance**: knowing the source git commit each output file was derived
   from, and the slide version that was on screen when a given video was recorded.

The design (see [design/per-topic-solution-release.md](../design/per-topic-solution-release.md))
achieves all of this with **one new build artifact** (a provenance manifest) and **one new
orchestration layer** (`clm release`), reusing the existing `partial`/`completed` output kinds,
the existing per-target `clm git` machinery, and a git-commit-capture helper that already exists
in the recordings code.

---

## Background: What Already Ships

| Capability | Mechanism |
|---|---|
| Multiple output directories, each a kind/format/language slice | `<output-targets>` → `OutputTarget.should_generate()` |
| Each target = its own directory **and its own git repo + remote** | `find_output_repos()`, `GitHubSpec.derive_remote_url()` |
| Push one target without touching others | `clm git push <spec> --target NAME` |
| Section-scoped builds | `clm build --only-sections id:w06` |
| Non-destructive, diff-friendly output writes (default) | hash-aware writes; `--clean` opts into wiping |
| `partial` = full code before a workshop, blanked inside it | `PartialOutput`; workshops delimited by `workshop`/`end-workshop` tags or `slide_id` `workshop-*` |
| `completed` = all code filled in | `CompletedOutput` |

The genuinely missing capability is **per-topic selection of revealed content, decoupled from the
stable spec, frozen at the moment of release**.

---

## Glossary

| Term | Meaning |
|---|---|
| **Frozen source** | A `completed`-kind output target (clean solutions, no speaker notes) built normally by `clm build`. The authoritative, always-current solutions tree. CLM owns and sweeps it. |
| **Solutions repository** | A git repository that is a **promotion destination**, *not* a `clm build` output target. CLM never builds into it; only `clm release` writes to it. **One repository per cohort** (1:1 with a release channel), each with its own remote — mirroring how each output target already maps to its own repo. |
| **Release channel (cohort)** | A `{ ledger + destination repository + frozen manifest }` triple. One iteration of a course on its own schedule. Each channel is its **own** git repository (its own remote, history, and push timing). |
| **Ledger** | The **volatile** per-channel file listing which topics have been released (a cumulative set of `Topic.id`s, optionally with dates). Lives in the **course source repo**, *not* the spec. |
| **Frozen manifest** | The per-channel record, in the destination, of what has actually been copied and from which source commit. The **freeze boundary**: a topic recorded here is never re-propagated unless forced. |
| **Provenance manifest** | `.clm-manifest.json` emitted into each build output root, mapping every output file to `{section_id, topic_id, source_commit, source_dirty, content_hash}`. The keystone primitive. |

---

## Requirements

### R1 — Build Provenance Manifest (keystone)

**Priority**: High

R1.1 Each `clm build` **MUST** be able to emit a `.clm-manifest.json` at each output root that
maps every written output file to its origin:

```json
{
  "version": 1,
  "source_commit": "abc123…",
  "source_dirty": false,
  "built_at": "2026-06-03T10:00:00Z",
  "files": [
    {
      "path": "En/Course/slides/Notebooks/Completed/Functions/03 Functions.ipynb",
      "section_id": "w03",
      "topic_id": "functions",
      "content_hash": "sha256:…"
    }
  ]
}
```

R1.2 The owning **`topic_id` MUST be recorded explicitly**, because it is **not recoverable from
the output path** (topics within a section share one section folder; non-notebook assets carry no
topic marker). `section_id` MAY be redundant with the path but **MUST** still be recorded for
convenience and asset grouping.

R1.3 The manifest **MUST** cover all output file kinds that can belong to a topic: notebooks, code
files, rendered HTML, topic-specific assets (images), and topic-scoped dir-group includes.

R1.4 Emitting the manifest **MUST** be opt-in-able and default-sensible; it **MUST NOT** change
any existing output bytes (it is an additional sidecar file, and like other CLM-owned sidecars it
is the build's to manage).

R1.5 The build provenance manifest is a **private, build-internal artifact** that lives in the
frozen-source output root and is **never shipped to students**. `clm release sync` reads it to
decide what to copy, but **MUST NOT** copy it (or any other `.clm-*` build sidecar) into a channel
destination. For an ~80-topic, 2-language course this manifest is on the order of ~200 KB
(≈600 output-file entries); it is not size-constrained because it never leaves the private tree.

### R2 — Source Commit Awareness at Build Time

**Priority**: High

R2.1 The build **MUST** capture the source course repository's git HEAD commit and dirty status at
build start, and thread it to the manifest. It **SHOULD** reuse the existing
`clm.recordings.git_info.get_git_info()` helper.

R2.2 If the source is not a git repository, the build **MUST** still succeed, recording
`source_commit: null`.

### R3 — Release Ledger (volatile, outside the spec)

**Priority**: High

R3.1 Each release channel **MUST** have a ledger file recording the **cumulative set of released
`Topic.id`s**. The ledger **MUST** live in the course source repository, **not** in the course
spec XML.

R3.2 The ledger **MUST** be the single source of truth for *release intent*. Adding a topic id to
it expresses "this topic's solution may now be propagated to this channel".

R3.3 `clm release` **MUST** provide commands to edit the ledger by topic id and by higher-level
grouping (e.g. release an entire module/section/week's worth of topics at once), validating ids
against the spec.

R3.4 The ledger format **SHOULD** permit an optional per-topic `release_after:` date, reserved for
a future scheduled-sync layer (see R10, out of scope for the first cut but not precluded).

### R4 — Frozen Manifest & Freeze-on-Copy

**Priority**: High

R4.1 Each channel **MUST** maintain a frozen manifest in its destination recording, **per released
topic** (not per file), `{ source_commit, copied_at, topic_digest }`, where `topic_digest` is a
single rolled-up hash over the topic's files for tamper/drift detection. This keeps the
student-shipped artifact small (~15 KB fully released for an 80-topic course); per-file hashes, if
needed, live only in the private build manifest (see R1.5).

R4.2 Once a topic is recorded in the frozen manifest, a subsequent sync **MUST NOT** re-propagate
it, even if the frozen source has since changed. *Students keep exactly what they were given in the
lecture.*

R4.3 The course author **MUST** be free to keep rebuilding/updating the frozen source at any time
(e.g. preparing the next revision); such changes **MUST NOT** leak into already-frozen channel
content.

R4.4 There **MUST** be an explicit override to re-propagate (re-freeze) a single topic or an entire
channel, for genuine bug-fixes to already-released material (e.g. `--refreeze <topic>` /
`--refreeze-all`).

### R5 — `clm release` Command Group

**Priority**: High

R5.1 A new `clm release` command group **MUST** provide at minimum:
- `clm release add <spec> --channel C <topic-id…>` — append topic ids to channel C's ledger.
- `clm release week <spec> --channel C <selector>` — resolve a module/section selector to topic
  ids and append them.
- `clm release status <spec> [--channel C]` — show released vs pending topics, and frozen vs
  not-yet-copied state, per channel.
- `clm release sync <spec> --channel C [--push] [--refreeze … | --refreeze-all] [--dry-run]` —
  reconcile intent (ledger) into the destination (copy newly-released, frozen topics; skip frozen;
  record freeze records), optionally committing and pushing.

R5.2 `clm release sync` **MUST** be idempotent: running it twice with no ledger change is a no-op.

R5.3 `clm release sync` **MUST** support `--dry-run`, listing what would be copied/frozen/skipped.

### R6 — Promotion Semantics (what moves when a topic releases)

**Priority**: High

R6.1 When a topic is promoted, **all** of that topic's output files (every configured language and
format) **MUST** be copied from the frozen source to the channel destination, located by the
**provenance manifest**, not by path-globbing.

R6.2 **Section-scoped assets** (images and other non-notebook files that land in a section folder)
**MUST** be released when the **first topic of that section** is released into the channel, so the
section folder is never half-populated for released topics.

R6.3 **Topic-scoped dir-group includes MUST** be released together with their owning topic. This
requires the build to record the owning `(section_id, topic_id)` of each topic-scoped dir-group in
the provenance manifest (currently not tracked).

R6.4 **Global `<dir-groups>`** (course-wide skeleton content meant to be present from the start)
**SHOULD** be released at channel initialization and then frozen like everything else (so later
churn does not retroactively alter a cohort).

R6.5 Promotion **MUST NOT** rebuild or re-execute anything. The bytes copied are exactly the bytes
in the frozen source at copy time (this is the "promote frozen artifacts" decision; it also guards
against the project's known build non-determinism).

### R7 — Sync / Freeze Rules (precise)

**Priority**: High

For each topic id in a channel's ledger, `clm release sync` **MUST** apply:

| Topic state | Action |
|---|---|
| released **and** already in frozen manifest | **skip** (frozen — keep what students have) |
| released **and** not yet frozen | **copy** from source → cohort repo; **record** freeze `{source_commit, copied_at, topic_digest}` |
| not released | **skip** |
| `--refreeze <t>` / `--refreeze-all` on a frozen topic | **re-copy** and **update** the freeze record |

### R8 — Multiple Concurrent Cohorts (release channels)

**Priority**: Medium-High

R8.1 The system **MUST** support **N channels over a single frozen source**, each with its own
ledger, **destination repository**, and frozen manifest.

R8.2 Because freezing is per-`(channel, topic)`, each cohort **MUST** freeze the version of the
material current when *that cohort* reached the topic — yielding "same material modulo unchanged
published history" automatically.

R8.3 It **MUST** be possible to determine where two cohorts' published material diverged by
comparing their frozen manifests (`source_commit` per topic).

R8.4 Each channel **MUST** be its **own** git repository (one repository per cohort), 1:1 with a
git remote — mirroring how each output target already maps to its own repo. This gives each cohort
independent history, access control, and push timing, and lets `clm git --channel NAME` push a
single cohort without coupling to any other.

### R9 — `clm git` Extension for Solution Channels

**Priority**: High (explicit author request)

R9.1 The `clm git` commands (`init`/`status`/`commit`/`push`/`sync`/`reset`) **MUST** be able to
operate on solution channel repositories, which are **not** `<output-targets>` and therefore are
**not** discovered by `find_output_repos()` today.

R9.2 Channel repositories **MUST** be discoverable from the spec (a structural `<release-channels>`
declaration — see R11) and their remotes derivable through the existing
`GitHubSpec.derive_remote_url()` path, so the integration reuses dry-run, remote-derivation, and
per-repo iteration rather than re-implementing them.

R9.3 The git commands **MUST** offer a filter to select channel repos (e.g. `--channel NAME`) and
**MUST NOT** change default behavior for existing output-target repos when no channel is requested.

R9.4 `clm release sync --push` **MUST** delegate to this same machinery (no second git
implementation).

### R10 — Recording → Slide-Version Provenance (roadmap by-product)

**Priority**: Medium (separable, shares the primitive)

R10.1 The recordings workflow **SHOULD** record, for each recorded part/take, the **slide version**
that was on screen: at minimum the source `git_commit` (already captured) plus the **content hash**
of the specific slide notebook and its `(section_id, topic_id)`.

R10.2 Given a recording's stored slide version, it **MUST** be possible to compute "what changed in
this slide since the video was recorded" by comparing against the current provenance manifest's
`source_commit`/`content_hash` for that slide.

### R11 — Spec Additions (structural, stable)

**Priority**: High

R11.1 The spec **MAY** gain a `<release-channels>` element declaring solution repositories and
their cohort channels. This is **structural and stable** (a handful of entries fixed for the
course's life), categorically different from the **volatile per-topic** annotations the author
refuses — those live in the ledger, never in the spec.

R11.2 If `<release-channels>` is absent, all current behavior **MUST** be unchanged and
`clm release` simply has nothing to operate on.

### R12 — Backward Compatibility & Non-Goals

R12.1 Builds and specs without any of the new elements **MUST** behave exactly as today.

R12.2 The provenance manifest **MUST** be additive (a new sidecar file); it must not alter existing
output bytes or break existing consumers/diffs.

---

## Non-Functional Requirements

- **NFR1 — No release-time execution.** Promotion copies bytes; it never runs a kernel. (Determinism
  + guards against known build non-determinism.)
- **NFR2 — Idempotency.** Re-running sync with an unchanged ledger is a no-op.
- **NFR3 — Solutions repo is not swept.** Channel destinations are `clm release`-curated, never a
  `clm build` target; the build sweep must never touch them.
- **NFR4 — Info-topic currency (project rule).** `clm info commands` / `spec-files` / `migration`
  **MUST** be updated in lockstep: new `clm release` group + `clm git --channel`, the
  `<release-channels>` element, and the `.clm-manifest.json` artifact.
- **NFR5 — Clean diffs.** Because output writes are already hash-aware and promotion is cumulative,
  each release **SHOULD** produce a git diff containing exactly the newly-revealed topics.

---

## Out of Scope (first cut)

1. **Automatic time-based reveal inside the build** (the original doc's exclusion still holds). A
   dated ledger + an explicit/scheduled `clm release sync` is the sanctioned path and is *not*
   precluded, but the first cut ships the manual trigger.
2. **Access control / authentication** on published repos.
3. **HTML prev/next navigation regeneration** for partially-populated channels. The author has
   confirmed notebooks are the primary format and dangling "next" links in the secondary HTML are
   acceptable while a course is incomplete.
4. **In-place reveal** in the student repo (the author chose the separate-solutions-repo model).

---

## Resolved Decisions

- **Q1 — Channel destination layout → RESOLVED: one repository per cohort.** Each channel is its own
  git repository (1:1 with a remote), not subfolders of a shared repo. This mirrors the existing
  output-target↔repo mapping, removes shared-repo push coupling, and gives independent
  history/access/timing per cohort. (See R8.4.)
- **Q2 — Frozen-manifest location → RESOLVED: in the destination repository.** Self-describing
  artifact; freeze record and content commit atomically in one place so they cannot drift; the
  repo's git history *is* the release audit log. Per-cohort repos (Q1) strengthen this. Shipped size
  is ~15 KB (per-topic), so publishing it is a non-issue.

## Open Questions

- **Q3 — Dir-group ownership scope.** Whether topic-scoped dir-group release (R6.3, the only part
  that reaches into build-output ownership) ships with the first cut or as an immediate fast-follow.
  Author is fine either way and intends to complete quickly; design sequences it as step 1's
  manifest work so it is available when the engine lands.

---

## References

1. Design: [per-topic-solution-release.md](../design/per-topic-solution-release.md)
2. Predecessor (shipped): [delayed-solution-release.md](./delayed-solution-release.md)
3. `clm info commands`, `clm info spec-files`, `clm info migration`
