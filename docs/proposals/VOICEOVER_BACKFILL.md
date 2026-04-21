# Proposal: `clm voiceover backfill` — Extract voiceover from historical recordings

**Status:** Design — ready for phased implementation
**Scope:** New CLI feature that composes existing voiceover machinery with
git-history analysis. Also absorbs infrastructure pieces from
`CLM_VOICEOVER_COMPARE_SPEC.md` (artifact cache, trace-log schema, shared
prompt scaffolding) that both features need.

This document is the source of truth for implementation. It evolved in a
design discussion (April 2026) between the user and the CLM agent; open
questions resolved in that discussion are noted inline.

---

## 1. Motivation

We have ~400 existing OBS lecture recordings (per
`PythonCourses/planning/VIDEO_TO_SLIDE_MAPPING.md`) recorded against slide
decks that have since been revised — sometimes slightly, often
significantly. We want to ingest voiceover content from those recordings
into the current slide files, without manual re-transcription.

`clm voiceover sync` handles the video-to-notes step but presumes the slide
file on disk matches what was recorded. For outdated recordings it
produces notes that refer to content that no longer exists, and misses
material that was on the slides during recording but has been removed.

The proposal: a **three-step pipeline** that identifies a likely historical
slide revision, runs sync against that revision, and ports the resulting
voiceover onto the current HEAD slides.

## 2. User-visible commands

Each step is separately invocable (useful for debugging and incremental
rollout) plus a one-shot wrapper.

```bash
# Step 1 — identify a likely historical slide revision from the video
clm voiceover identify-rev <slide-file> <video>...
    [--lang de|en] [--top 5] [--since <date>]

# Step 2 — run the existing sync pipeline against a historical revision
clm voiceover sync-at-rev <slide-file> <video>...
    --rev <sha> --output <scratch-path>

# Step 3 — port voiceover from one slide file onto another
#   (file-to-file; no git involvement here)
clm voiceover port-voiceover <source> <target>
    [--dry-run] [--lang de|en]

# One-shot wrapper
clm voiceover backfill <slide-file> <video>...
    [--rev <sha>]       # skip Step 1 if user supplies the revision
    [--top 5]           # how many candidates to show
    [--auto]            # pick top candidate without prompting (Step 1)
    [--dry-run]         # emit unified diff only
    [--apply]           # REQUIRED to mutate the working tree (default: patch only)
    [--keep-scratch]

# Spike / debug tool (throwaway; informs Step 1 scoring)
clm voiceover debug voiceover-commits <slide-file>
    [--since <date>] [--threshold 0.7]
```

**Patch-by-default policy.** `backfill` writes
`.clm/voiceover-backfill/<topic>-<YYYYMMDD-HHMMSS>/port.patch` and prints
the unified diff to stdout. Mutation of the working tree requires
`--apply`. Auto-mode (`--auto`) governs Step 1 candidate selection only,
not writes.

**Scope.** One slide file at a time. No batch operations; the use cases
are too variable to invest in that up front. Part-based recordings
(`Teil 1.mp4`, `Teil 2.mp4`) are supported because the underlying `sync`
already supports them.

## 3. Step-by-step design

### 3.1 Step 1 — Identify a likely git revision

**Approach.**

For each commit touching the slide file
(`git log --follow --format=%H -- <path>`), compute a cheap **slide
fingerprint**: the ordered list of slide titles (reuse `_extract_title`
from `src/clm/notebooks/slide_parser.py`). The video gives an analogous
fingerprint via the existing `matcher.py` OCR path: detected keyframe
texts in temporal order.

Score each candidate revision by sequence similarity (LCS with fuzzy title
equality, `rapidfuzz.fuzz.token_set_ratio` threshold). Return the top-N
revisions with scores — not just "the" answer. The user should see
`rev A: 0.93, rev B: 0.88, rev C: 0.41` and pick.

**Narrative-commit prior.** A commit whose diff is dominated by
additions/edits to cells tagged `voiceover` or `notes` is a strong signal
that a recording session happened around that time. Both `voiceover` and
`notes` count (pre-voiceover-era commits used `notes`). The heuristic
applies to **narrative runs** — consecutive narrative-heavy commits
collapsed into a single run — and evaluates **both endpoints** of each
run, because recording order varies:

- **Pre-run** — the parent of the run's earliest commit (slides as they
  were going into the recording-and-note-taking session).
- **Post-run** — the run's latest commit (slides as they were when
  note-taking finished; may include minor slide edits made while
  authoring voiceover).

Score both candidates against the video fingerprint; pick the higher
scorer, break ties toward pre-run. A spike (§6.2) validates whether the
narrative-commit signal is strong enough to fold into the main scorer as
a multiplicative prior.

**Problems and mitigations.**

- **Search space.** Cap at 50 most recent candidates before video mtime +
  buffer. Each scoring is just fingerprint LCS (no LLM, no full pipeline
  run), so brute force is fine.
- **`--follow` only tracks single renames.** If the slide file was
  split/merged from ancestors, we miss history. Accept
  `--since-path <old-path>` as an override; union histories when given.
- **OCR noise in video titles.** Reuse `matcher.py` normalization.
  When available, use `slide_id="..."` attributes as the primary key
  (more stable than titles); fall back to titles, then first content
  line.
- **Untitled code slides.** Weight by title-bearing slides only.
- **No revision matches well.** If the top score is below threshold
  (≈0.6), refuse to proceed without `--force-rev <sha>`.

### 3.2 Step 2 — Sync at the identified revision

Export the slide file at the chosen revision to a scratch location with
`git show <rev>:<path>` (safer than `git checkout`; no working-tree
mutation). Run the existing `clm voiceover sync` against that file plus
the video. Write output to a scratch file — never the working copy.

**Notes.**

- Related files (helper modules, images) may have changed too. For
  voiceover sync alone this does not matter; if we later evaluate content
  rendering, `git worktree add` would be more honest.
- Bilingual files require `--lang`, same as today.
- The new `--transcript PATH` / `--alignment PATH` override flags (see
  §4.2) are useful here when iterating: run sync once to populate the
  cache, iterate on the port step without re-running ASR.

### 3.3 Step 3 — Port forward to HEAD

**Inputs.**

- `slides@rev-with-voiceover` (from Step 2) — call it **B**
- `slides@HEAD` as-is — may already have hand-written voiceover — call it **C**
- Optional: `slides@rev` pre-sync — the ancestor, **A** (rarely needed)

**Slide matching (B↔C).** Primary key: `slide_id` attribute (parsed at
`slide_parser.py:30`). Fallback: fuzzy title match, then content-
fingerprint similarity. Produce explicit buckets:

- `unchanged` — same id/title, content diff below threshold
- `modified` — same id, content diff significant
- `new_at_head` — in C, no match in B
- `removed_at_head` — in B, no match in C

**Per-slide transform.**

- `unchanged` + C empty → insert B's voiceover verbatim.
- `unchanged` + C has baseline → `polish_and_port(baseline=C,
  prior_voiceover=B, slide_content=HEAD)`.
- `modified` → same, but also pass old slide content; flag output for
  review.
- `new_at_head` → no transfer; list in report.
- `removed_at_head` → drop; list in report.
- Split/merged slides → `manual review` bucket (no cleverness in MVP).

**The new primitive.**

```python
# in src/clm/notebooks/polish.py
async def polish_and_port(
    baseline_bullets: str,            # HEAD voiceover (may be empty)
    prior_voiceover: str,             # video-derived bullets from old rev
    slide_content_head: str,
    slide_content_prior: str | None,  # None if unresolvable
    language: str,
    content_changed: bool,
    *,
    model: str = "gpt-4o-mini",
) -> PortResult:
```

Sibling to `polish_and_merge`. Different invariants (no noise filter —
input is already clean), same structured output. Shares the per-slide
input packing and the `covered/dropped/added/rewritten` status enum with
the future `compare` judge (see §4.4).

**Problems and mitigations.**

- **`slide_id` is optional.** Many existing slides lack one. → Run
  `clm normalize-slides --operations slide_ids <path>` first (already
  shipped; see §6.1). Improves matching quality for every later port.
- **Reordered slides.** Title/id matching handles this; no positional
  assumptions.
- **Duplicate titles.** Disambiguate with content fingerprint when titles
  collide. If still ambiguous → `manual review`.
- **Notes already at HEAD.** Default must preserve C and augment from B.
  Never silently overwrite. Inherit the merge mode semantics.

## 4. Absorbed from the compare spec

The user's `PythonCourses/planning/CLM_VOICEOVER_COMPARE_SPEC.md` proposes
three pieces (artifact cache, `compare` command, MCP exposure). Three of
those are **infrastructure that both features need** and are absorbed
into this work. The compare **command** itself is deferred to a capstone
follow-up PR because it's a small wrapper once the infrastructure exists.

**Absorbed (implemented as part of backfill work):**

### 4.1 Artifact cache

Full implementation per compare spec §2.

Layout: `<repo>/.clm/voiceover-cache/` with `transcripts/`,
`transitions/`, `timelines/`, `alignments/` subdirectories. Keys:

- **`video_hash`** = `sha1(abspath + str(mtime_ns) + str(size))[:16]`
  (cheap; no full-file hash for GB-scale videos).
- **`slides_hash`** = `sha1(normalized_source_bytes)[:16]` with trailing
  whitespace stripped per line.
- Cache entries embed the operation config (for transcripts:
  `(backend, model, lang, device_class)`) — config mismatch is a miss,
  not an error.

API: `clm/voiceover/cache.py` with `VideoKey`, `SlidesKey`,
`TranscribeConfig`, and the four artifact kinds. Atomic writes (temp +
rename). `None` on miss; corrupt JSON = miss + overwrite.

Retrofit `transcribe` / `detect` / `identify` / `sync` to read and write.
New flags on the `voiceover` group: `--cache-root PATH`, `--no-cache`,
`--refresh-cache`. New subgroup: `clm voiceover cache list/prune/clear`.

**Acceptance** (from compare spec §8):

- `clm voiceover transcribe VIDEO -o t.json` second call with identical
  inputs returns in <1s.
- `clm voiceover sync SLIDES VIDEO --dry-run` second invocation completes
  in ~15s on a 120 MB video (was minutes).

### 4.2 Override flags on `sync`

`sync --transcript PATH` and `sync --alignment PATH` skip ASR/alignment
respectively when a precomputed artifact is supplied. Useful for
iterating on the merge step without re-running the full pipeline, and
required by `backfill` when iterating on port.

### 4.3 Trace-log schema v1

Per compare spec §5. Promote `.clm/voiceover-traces/<stem>-<ts>.jsonl` to
a versioned schema. Top-level field `"schema": "clm.voiceover.trace/1"`;
per-slide fields `slide_id`, `cell_index`, `baseline`,
`transcript_segments`, `llm_merged`, `dropped_from_transcript`,
`added_from_baseline`, `model`, `mode`, `timestamp`. Add
`clm voiceover trace show PATH`.

### 4.4 Shared judge/port scaffolding + prompts

A common module for per-slide LLM-input packing and a shared structured-
response schema (bullet ids, per-bullet status enum). Both `compare`'s
judge and `polish_and_port` consume it.

Author both prompt sets now (`compare_{de,en}.md`, `port_{de,en}.md`)
while context is loaded. Prompts diverge (compare is evaluative/read-
only; port is constructive), but plumbing converges.

**Deferred:**

- **`clm voiceover compare` command** (compare spec §3) — the wrapper
  itself. Lands after backfill as a capstone PR.
- **MCP exposure** (compare spec §6) — trivial once the command exists.
- **LLM-output caching keyed on prompt hash** (compare spec §4.2
  option 2) — optional in the spec; defer unless dry-run iteration is
  still too slow after the artifact cache lands.

## 5. PR layout

1. **PR 1 — Cache + trace schema + override flags.**
   Pure infrastructure; no new user-facing commands.
   Acceptance: compare spec §8 criteria 1–2.
2. **PR 2 — `identify-rev` + narrative-commit heuristic.**
   Includes the shippable outcome of the spike (§6.2) if it validates.
3. **PR 3 — Shared scaffolding + `polish_and_port` + `port-voiceover` +
   prompts for both port and compare.**
4. **PR 4 — `backfill`.** Thin composition; patch-by-default.
5. **PR 5 (follow-up) — `compare` command** wrapping the existing
   scaffolding. May be picked up by CLM or handed back to the original
   spec owner.

## 6. Preparatory work

### 6.1 `assign-ids` — already shipped

The `_apply_slide_ids` normalizer operation
(`src/clm/slides/normalizer.py:603`) auto-generates `slide_id` metadata:
markdown heading → slug, code `def`/`class` → name, fallback
`<stem>-cell-<n>`. DE/EN pairs get the same id; collisions resolved with
`-2/-3` suffixes; idempotent.

Exposed as `clm normalize-slides --operations slide_ids <path>`
(see `info_topics/commands.md:245`). No new command needed. The backfill
docs and the pre-recording workflow should point users here.

**Action:** add a note in `clm info commands` that running
`clm normalize-slides --operations slide_ids` on newly-written slide
files improves future port/backfill matching quality.

### 6.2 Narrative-commit spike

Throwaway prototype that validates (or rejects) the narrative-commit
prior before we wire it into `identify-rev`.

**Command:**
`clm voiceover debug voiceover-commits <slide-file> [--since DATE] [--threshold 0.7]`

**Algorithm:**

1. `git log --follow --format="%H|%ai|%s" -- <path>`, optionally filtered
   by `--since`.
2. For each commit, `git show <commit>:<path>` and
   `git show <parent>:<path>`; parse both with `parse_cells`.
3. Classify cells by `metadata.is_narrative` (true for `notes` or
   `voiceover` tagged cells — see `slide_parser.py:46`). Compute
   character totals per class for both versions; delta = absolute
   difference.
4. `ratio = narrative_delta / (narrative_delta + content_delta + 1)`.
   Commit is narrative-heavy if `ratio >= threshold` AND
   `narrative_delta >= FLOOR` (filters trivial whitespace commits).
5. Collapse **consecutive** narrative-heavy commits in file-history order
   into **runs**.
6. For each run: emit pre-run SHA (parent of oldest commit) and post-run
   SHA (latest commit). Print run metadata.

**Outputs:**

- A Rich table with columns `SHA, date, ratio, nar-Δ, con-Δ, heavy?,
  run-id`.
- Run summary: `run #1 length=2 pre-run=abc123 post-run=def456`.

**Decision point after spike.** Run against a real AZAV slide file with
a known recording. If the narrative-commit ratio cleanly distinguishes
recording sessions from content edits, fold into `identify-rev` as a
multiplicative prior alongside fingerprint matching. If noisy, drop and
keep pure fingerprint scoring.

**First-run finding (2026-04-21).** The naive implementation compares
total characters in narrative vs content cells between parent and commit
(`abs(after − before)` per class). Run against
`slides_010v_streaming_generators.py` (recent history, 20 commits) it
rejected the "finish Phase 4 W04 punch list — EN voiceover" commit:
narrative-Δ 13921 vs content-Δ 15473, ratio 0.47, below the 0.7
threshold. That commit reorganized *both* narrative and content
substantially; net-char-delta cannot distinguish "recording-session
note addition" from "general rewrite". Most other commits show 0/0
(rename-only churn from module renumbering).

**Implication.** A hunk-based classifier is needed before the prior is
useful. Proposed upgrade: for each commit, parse the unified diff, map
each +/- line to the cell it belongs to in the relevant file version
(via `parse_cells` line offsets), and sum added+removed lines per
class. This double-counts churn on both sides of a rewrite and
preserves the signal we want. Keep the spike module as the
ratio-computation scaffold; the upgrade is the hunk classifier that
feeds better inputs into the same scoring function. Revisit the
decision point after the fingerprint scorer is in place — the prior
may not be needed at all if fingerprint matching alone is decisive.

## 7. Open questions (resolved in discussion)

- **Output mode for `backfill`.** Patch-by-default; `--apply` required
  for working-tree mutation.
- **Scope.** One slide file at a time.
- **Scratch location.** `.clm/voiceover-backfill/<topic>-<ts>/`,
  gitignored.
- **`port-voiceover` git integration.** Keep file-to-file for now; let
  `backfill` handle git extraction.
- **Compare-tool ownership.** Absorb infrastructure (cache, trace
  schema, scaffolding, prompts); defer `compare` command itself to a
  follow-up PR.
- **Narrative tags.** Both `voiceover` and `notes` count as narrative
  for the commit heuristic (pre-voiceover-era used `notes`).
- **Narrative-run endpoints.** Score both pre-run and post-run; pick
  higher; tie-break toward pre-run.

## 8. Unresolved / deferred

- **Compare-output performance acceptance** (compare spec §8 criterion
  for <10s compare re-run): cross-check as part of PR 1 that the cache
  covers everything except the LLM judge step.
- **Split/merged slide detection** in port Step 3: MVP dumps to
  `manual review`; revisit after real-world use.
- **Modified-slide LLM adaptation** (second LLM pass that sees both
  content versions): deferred to PR 4 polish if needed.
- **Integration with `VIDEO_TO_SLIDE_MAPPING.md`**: `identify-rev`
  output should be compatible with the mapping doc's schema so the
  course-side tool can consume it. Detailed mapping deferred until
  after PR 2.
