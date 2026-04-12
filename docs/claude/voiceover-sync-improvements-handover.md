# Voiceover Sync Improvements — Handover

**Status**: Phase 1 complete. Phase 2 (merge mode) is next.
**Branch**: `worktree-purring-strolling-crab` (worktree off `master`).
**Source of truth (design)**: [`docs/proposals/VOICEOVER_SYNC_IMPROVEMENTS.md`](../proposals/VOICEOVER_SYNC_IMPROVEMENTS.md)
**Related prior work**: [`docs/claude/voiceover-design.md`](voiceover-design.md), [`docs/claude/voiceover-sync-windows-crash.md`](voiceover-sync-windows-crash.md)

This handover tracks implementation state across four phases. The proposal
doc is the design source of truth — do not duplicate design content here.
If you need to know *what* to build or *why* a design choice was made, read
the proposal. This file tells you *where we are* and *what to do next*.

---

## 1. Feature Overview

**Name**: `clm voiceover sync` — multi-file input and merge mode.

**One-paragraph description**: Two improvements to `clm voiceover sync` that
together make the command usable on real OBS recordings. (1) Accept
multiple video parts on a single invocation and process them per-part with
running offsets, so the aligner sees one logical timeline without the
pipeline ever concatenating files on disk. (2) Change the default behavior
from "overwrite voiceover cells" to "merge into existing voiceover cells"
via a single-pass LLM call that preserves baseline content, integrates
substantive additions from the transcript, filters noise (greetings,
recording self-corrections, code-typing narration), and reports any
baseline rewrites as structured metadata. Observability via Langfuse and
a local trace log that accumulates training data are layered on top.

**Problem it solves**:

- OBS recordings are routinely split into multiple parts for pedagogical
  and robustness reasons. The current CLI accepts exactly one video, so
  users either concat manually (throwaway files), run sync per part
  (wrong — aligner needs one timeline), or skip sync entirely. The last
  workaround is what actually happened for `topic_045_streaming_generators`
  during AZAV ML W04 Phase 4.
- Re-recording a video today either loses hand-edits (if you run sync
  over them) or loses improvisations (if you don't run sync). Neither is
  acceptable for a course that gets revised.
- The existing polish LLM step has no way to reject code-typing dictation
  or part-boundary greetings, so these end up in the voiceover.

**Why this matters**: The AZAV ML course restructure is the immediate
consumer. Without these improvements, every topic that was recorded as
multiple parts either skips sync or loses content on every revision pass.

---

## 2. Design Decisions

Only the decisions that drove the architecture are captured here. Full
rationale lives in the proposal doc — this section is for a fresh session
that needs to understand the *shape* of the implementation in one minute.

### Segment-wise per-part processing (not concatenation)

**Decision**: Each video part is transcribed and keyframe-detected
independently; the results are merged into a single logical timeline using
running offsets (`Σ duration(part_0..i-1)`). No ffmpeg concat demuxer, no
fused temp file.

**Why**:

1. The merge prompt (Phase 2) needs boundary metadata to recognize
   greeting/sign-off noise at part boundaries. Concatenation erases this.
2. A fused file creates a visual discontinuity at each concat point that
   the slide-transition detector will almost certainly flag as a fake
   slide change. Per-part avoids the class of bug entirely.
3. Encoding mismatch across parts becomes a non-issue because each part
   is decoded by its own ASR call.

**Rejected alternative**: ffmpeg concat demuxer to temp file, then run
the existing single-video pipeline. Rejected for the three reasons above.
The proposal originally framed this as "simpler but worse"; on
reflection, segment-wise is *also* simpler because it eliminates the
fake-transition post-processing.

### Single-pass `polish_and_merge` LLM call

**Decision**: Merge is one LLM call per slide (or per batch of slides) —
not a multi-pass "compute additions → anchor → insert" pipeline. The LLM
receives `(baseline, transcript, slide_content, language, boundary_hint)`
and returns the merged bullet list plus structured rewrites.

**Why**: Multi-pass workflows are slower, more expensive, and more
failure-prone in production. A single LLM call with a disciplined prompt
handles noise filtering, content preservation, addition integration, and
bullet ordering holistically. Git diff is the review layer for placement
and wording — we don't need the pipeline to second-guess the LLM's
decisions.

**Rejected alternative**: A two-pass approach where the LLM first returns
an "additions list" with anchor indices, and a second pass inserts them.
Rejected in discussion: more brittle, slower, and the anchor metadata
adds complexity for no real benefit when git diff does the job.

### Relaxed "baseline is sacred" rule (structured rewrite log)

**Decision**: The LLM MAY rewrite a baseline bullet if the transcript
*directly contradicts or corrects* that bullet. Style tweaks and
paraphrases are forbidden — only factual contradictions. Every rewrite is
reported in a structured `rewrites` field so reviewers can spot them
without hunting through diffs.

**Why**: Trainers often correct baseline errors live during recording
("actually, `extend` mutates in place, not returns a new list"). A strict
preserve-baseline rule would lose these corrections. The structured
rewrite log plus dry-run annotation keeps the relaxation auditable.

**Rejected alternative**: Strict preservation. Rejected because the user
explicitly prefers to capture live corrections over safe append-only
merging.

### Merge as the new default; `--overwrite` for old behavior

**Decision**: `sync` merges into existing voiceover cells by default.
`--overwrite` restores the old destructive behavior.

**Why**: Merge is less destructive. Running sync twice by accident should
not lose content. The only current user (the proposal author) has
confirmed the breaking-change cost is acceptable.

**Rejected alternative**: `--update` flag to opt in to merge. Rejected:
merge is the safer default and the flag inversion follows.

### Local JSON trace log + optional Langfuse

**Decision**: Phase 2 always writes a local JSONL trace log to
`.clm/voiceover-traces/<topic>-<timestamp>.jsonl`. Langfuse integration
in Phase 3 is additive, env-var gated, and wraps
`clm.infrastructure.llm.client._build_client` at a single point so all
LLM-using modules benefit.

**Why**: Langfuse is optional per-deployment (the user has a local
Docker, but not everyone will). A local file-based log is durable,
independent of Langfuse availability, and is the substrate the Phase 4
training-data extraction tool reads. Trace collection starts from day
one of Phase 2 so a corpus accumulates before anyone wants to train.

### No MCP tool for video transcription

**Decision**: The MCP server (`src/clm/mcp/tools.py`) will not get a tool
that runs `clm voiceover sync` or any other video-transcription operation.

**Why**: Transcription is minutes long with multi-MB inputs and
multi-KB outputs — a batch CLI job, not an MCP round trip. Confirmed
during design discussion: the MCP server currently exposes only
slide-file manipulation (`extract_voiceover`, `inline_voiceover`,
`suggest_sync`) and will stay that way.

### Breaking CLI change: `sync SLIDES VIDEO...`

**Decision**: Positional argument order flips from current
`sync VIDEO SLIDES` to `sync SLIDES VIDEO...` (slides first, videos
variadic via click's `nargs=-1`).

**Why**: Click's `nargs=-1` consumes positional arguments greedily, so
the variadic argument must come last. Slides-first reads naturally
("sync these slides against these recordings").

**Migration**: Acceptable because the only current user is the proposal
author. No deprecation shim needed.

---

## 3. Phase Breakdown

### Phase 1 — Multi-file input [DONE]

**Goal**: `clm voiceover sync` accepts one or more video parts and
processes them segment-wise into a single logical timeline.

**Files touched**:

- `src/clm/cli/commands/voiceover.py` — rewrite the `sync` command
  signature and orchestration loop.
- `src/clm/voiceover/transcribe.py` — either add a multi-part entry point
  or call the existing `transcribe_video` per part and merge results.
- `src/clm/voiceover/keyframes.py` — same pattern for `detect_transitions`.
- `src/clm/voiceover/matcher.py` — `match_events_to_slides` currently
  takes a single `video` arg for OCR; must become part-aware.
- `src/clm/voiceover/aligner.py` — no change expected; it already
  operates on `Transcript` + `list[TimelineEntry]`.
- New: `src/clm/voiceover/timeline.py` (or similar) to hold the
  per-part duration probe, offset arithmetic, and merge helpers.
- Tests: `tests/voiceover/test_multi_part_*.py`.

**Acceptance criteria**:

1. `clm voiceover sync slides.py part1.mp4 part2.mp4 part3.mp4 --lang de`
   runs end-to-end on a real three-part recording and produces a sensible
   notes_map.
2. Running with one part produces the same result as the current
   single-file behavior (regression check).
3. Each `TranscriptSegment` carries a `source_part_index` (or equivalent)
   that downstream consumers can read.
4. Each slide-transition event carries the same index.
5. The aligner produces a single `AlignmentResult` spanning all parts;
   timestamps are offset correctly.
6. Part ordering is preserved as passed on the CLI (no mtime/name sort).
7. Failure on a single unreadable part reports the part index clearly.
8. `--slides-range` filters against the merged timeline.
9. Existing single-video tests still pass (argument-order flip aside).

**Out of Phase 1**: no LLM changes, no merge behavior, no trace log.

---

### Phase 2 — Merge mode (core) [TODO]

**Goal**: `sync` merges into existing voiceover cells by default;
`--overwrite` restores old behavior. Single-pass `polish_and_merge`
handles noise filtering, content preservation, additions, and baseline
rewrites. Local trace log is written on every run.

**Files touched**:

- `src/clm/notebooks/polish.py` — add `polish_and_merge` function
  (generalization of `polish_text`). Build structured system prompt with
  invariants, filter rules, and style guidance. Expect JSON-formatted
  LLM output.
- `src/clm/notebooks/slide_parser.py` — need a way to read existing
  voiceover cell content per slide group (inspect whether this already
  exists; the MCP `extract_voiceover` tool does something similar).
- `src/clm/notebooks/slide_writer.py` — `write_narrative` may need a
  merge-aware variant that replaces in place rather than appending.
- `src/clm/cli/commands/voiceover.py` — `--overwrite` flag, default
  behavior change, merge orchestration, dry-run diff output, conflict
  error when `--mode verbatim` meets merge.
- New: `src/clm/voiceover/merge.py` — batching logic (pack slides up to
  char budget, parse JSON response, per-slide fallback on parse failure).
- New: `src/clm/voiceover/trace_log.py` — JSONL writer for
  `.clm/voiceover-traces/<topic>-<timestamp>.jsonl`.
- New: `src/clm/voiceover/prompts/` — language-specific prompt variants
  (`merge_de.md`, `merge_en.md`, or equivalent).
- Tests: fixtures for noise-filter keep/drop cases (see
  §7 Testing Approach), snapshot tests for `polish_and_merge` with a
  mocked LLM, end-to-end test against a small real recording.

**Acceptance criteria**:

1. Running `sync` on a slide file with existing voiceover cells produces
   a merged result that preserves baseline content and integrates
   substantive transcript additions.
2. Running `sync --overwrite` on the same file produces the old
   destructive behavior byte-for-byte.
3. Noise fixtures (greetings, self-corrections, code-typing dictation)
   are dropped from the merged output.
4. A factual contradiction in the transcript produces a structured
   rewrite entry and the rewritten bullet is marked in the dry-run diff.
5. `--dry-run` emits a unified diff to stdout; no file is touched.
6. `--mode verbatim` combined with merge (default) errors out with a
   clear message; `--mode verbatim --overwrite` works as before.
7. A slide with only the *other* tag (e.g. `notes` when `--tag voiceover`)
   sees an empty baseline and gets a fresh voiceover cell inserted.
8. Each run writes a JSONL trace log under
   `.clm/voiceover-traces/`; `.clm/` is added to `.gitignore` by default.
9. Batched LLM calls respect the 20k char budget; JSON parse failure
   falls back to per-slide calls for that batch only.

---

### Phase 3 — Langfuse tracing [TODO]

**Goal**: LLM calls are traced to Langfuse when
`LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are all set.

**Files touched**:

- `src/clm/infrastructure/llm/client.py` — `_build_client` returns a
  Langfuse-wrapped `openai.AsyncOpenAI` when env vars are present.
- `pyproject.toml` — add `langfuse` to the `[voiceover]` extra.
- `src/clm/voiceover/merge.py` — thread `session_id`, metadata, and
  tags into LLM calls so Langfuse groups them meaningfully.
- `src/clm/voiceover/trace_log.py` — record the Langfuse trace ID in the
  local JSONL log for later correlation.
- Tests: smoke test that Langfuse-wrapped client falls back gracefully
  when Langfuse is unreachable; unit test that env var absence → plain
  client.

**Acceptance criteria**:

1. With all three Langfuse env vars set and a reachable host, a `sync`
   invocation populates a Langfuse session containing one trace per LLM
   batch, with input/output captured automatically.
2. With env vars unset, the pipeline runs identically to Phase 2 (no
   Langfuse calls, no warnings).
3. With env vars set but host unreachable, the pipeline logs a warning
   and continues to completion.
4. The local JSONL trace log contains `langfuse_trace_id` when tracing
   was active.

---

### Phase 4 — Training data extraction [TODO]

**Goal**: `clm voiceover extract-training-data` reads local JSONL trace
logs and correlates them with the current slide state to produce
training triples.

**Files touched**:

- New: `src/clm/cli/commands/voiceover.py` — add subcommand.
- New: `src/clm/voiceover/training_export.py` — trace log reader,
  per-entry correlation (re-parse slide file at `git_head` and at HEAD,
  diff the voiceover cell for the relevant `slide_id`).
- Tests: round-trip fixtures for the reader and the correlator.

**Acceptance criteria**:

1. Given a trace log from a Phase 2 run plus a committed hand-edited
   slide file, the command emits one JSONL line per slide with fields
   `input.baseline`, `input.transcript`, `llm_output`, `human_final`,
   `delta_vs_llm`.
2. Slides where `human_final == llm_output` (no hand edits) are emitted
   with an empty `delta_vs_llm` — these are useful as positive training
   examples too.
3. Trace entries whose `git_head` is unreachable (detached commit, etc.)
   are skipped with a warning.

---

## 4. Current Status

**Phase 1 is complete.** Multi-file input implemented and tested.
Phase 2 (merge mode) is next.

**Completed**:

- [`docs/proposals/VOICEOVER_SYNC_IMPROVEMENTS.md`](../proposals/VOICEOVER_SYNC_IMPROVEMENTS.md)
  rewritten as the source of truth (2026-04-12).
- This handover doc created (2026-04-12).
- **Phase 1 implemented (2026-04-12)**:
  - CLI argument order flipped to `sync SLIDES VIDEO...` with variadic
    videos via `nargs=-1`.
  - `TranscriptSegment` and `TransitionEvent` carry `source_part_index`.
  - `TransitionEvent` also carries `local_timestamp` for per-part frame
    extraction in the matcher.
  - New `src/clm/voiceover/timeline.py` with `VideoPart`, `probe_duration`
    (ffprobe), `build_parts`, `offset_transcript`, `offset_events`,
    `merge_transcripts`.
  - Matcher accepts optional `video_paths` and `total_duration` params;
    new `_extract_event_frame` routes to correct video part.
  - `sync` orchestration rewritten with per-part loop.
  - `src/clm/cli/info_topics/commands.md` updated.
  - 40 new tests (21 in `test_timeline.py`, 19 in `test_multi_part.py`).
  - All 132 voiceover tests pass; 2960 total tests pass.

**In progress**: none.

**Blockers**: none.

**Resolved open questions from Phase 1**:

- **`--keep-audio` vs `--keep-temp`**: kept `--keep-audio` (existing flag).
  In multi-part mode it preserves all per-part audio extractions.
- **Matcher per-part strategy**: added `video_paths: list[Path] | None`
  and `total_duration: float | None` parameters to
  `match_events_to_slides`. A new `_extract_event_frame` helper uses
  `event.local_timestamp` + `event.source_part_index` to seek the
  correct video part. Sequential alignment runs across all events
  (all parts) in one pass.

**Open questions** (for Phase 2):

- `slide_parser` / `slide_writer` expose voiceover cell reading via the
  MCP `extract_voiceover` path (see `src/clm/slides/voiceover_tools.py`).
  Evaluate whether to reuse this function directly or add a lower-level
  helper. Decide during Phase 2.

**Tests**: 132 voiceover tests pass. Fast suite runs via pre-commit.
Use `pytest -m "not docker"` for the pre-release full run.

---

## 5. Next Steps

**Start Phase 2 — Merge mode.** In order:

1. **Read existing voiceover cell extraction code.** Check
   `src/clm/slides/voiceover_tools.py` (MCP's `extract_voiceover`)
   and `src/clm/notebooks/slide_parser.py` to understand how existing
   voiceover cell content is read per slide group. Decide whether to
   reuse the MCP helper or add a lower-level function.

2. **Read `src/clm/notebooks/polish.py`.** This is the hook point —
   `polish_and_merge` generalizes `polish_text`. When `baseline == ""`
   it degrades to the current polish behavior.

3. **Design the merge prompt.** Build the structured system prompt with
   invariants (preserve baseline, filter noise, relaxed rewrite rule)
   per the proposal doc §"Prompt structure". Create language-specific
   variants in `src/clm/voiceover/prompts/`.

4. **Implement `polish_and_merge` in `src/clm/notebooks/polish.py`.**
   Takes `(baseline_bullets, transcript_text, slide_content, language,
   boundary_hint)` and returns a `MergeResult` with `merged_bullets`,
   `rewrites`, `dropped_from_transcript`. Expects JSON from the LLM.

5. **Implement batching in `src/clm/voiceover/merge.py`.** Pack slides
   up to 20k char budget per LLM call; parse JSON response keyed by
   `slide_id`; fall back to per-slide calls on parse failure.

6. **Wire into `sync` orchestration.** Add `--overwrite` flag (default:
   merge). Error on `--mode verbatim` without `--overwrite`. Read
   baseline from existing voiceover cells. `--dry-run` emits unified
   diff. Update `commands.md`.

7. **Implement local trace log** in `src/clm/voiceover/trace_log.py`.
   Write one JSONL line per LLM call to
   `.clm/voiceover-traces/<topic>-<timestamp>.jsonl`. Add `.clm/` to
   `.gitignore`.

8. **Write tests.** Noise-filter fixtures (see §8 Session Notes for
   seed data), snapshot tests with mocked LLM, rewrite detection,
   `--overwrite` parity, trace log assertions.

**Gotchas**:

- **Single-pass LLM only.** Do not build a multi-pass
  additions/anchor pipeline. See Design Decisions §2.2.
- **Relaxed baseline rule is auditable.** Every rewrite must appear
  in the structured `rewrites` field. See Design Decisions §2.3.
- **`--mode verbatim` + merge = error.** Verbatim has no noise
  filter; merging raw transcript is unsafe.
- **Boundary hint from Phase 1.** The `source_part_index` on
  segments tells the merge prompt which slides span a part boundary,
  so it can be extra suspicious of greeting/sign-off noise there.

---

## 6. Key Files & Architecture

### Files that exist today and will be touched

| File | Role | Phase |
|---|---|---|
| `src/clm/cli/commands/voiceover.py` | `sync` command definition and orchestration | 1, 2, 4 |
| `src/clm/voiceover/transcribe.py` | ASR wrapper (`transcribe_video`) | 1 |
| `src/clm/voiceover/keyframes.py` | Slide-transition detection (`detect_transitions`) | 1 |
| `src/clm/voiceover/matcher.py` | Events → slide-timeline mapping (`match_events_to_slides`) | 1 |
| `src/clm/voiceover/aligner.py` | Transcript → per-slide notes (`align_transcript`) | 1 (likely unchanged) |
| `src/clm/notebooks/polish.py` | `polish_text` → generalized into `polish_and_merge` | 2 |
| `src/clm/notebooks/slide_parser.py` | Percent-format slide parser | 2 (baseline read) |
| `src/clm/notebooks/slide_writer.py` | `write_narrative` | 2 (merge-aware variant) |
| `src/clm/slides/voiceover_tools.py` | Existing voiceover cell utilities (MCP-exposed) | 2 (reuse candidates) |
| `src/clm/infrastructure/llm/client.py` | `_build_client` factory | 3 |
| `src/clm/cli/info_topics/commands.md` | Version-accurate CLI docs (downstream contract) | 1, 2 |
| `pyproject.toml` | `[voiceover]` extras (add `langfuse`) | 3 |

### Files created in Phase 1

| File | Purpose |
|---|---|
| `src/clm/voiceover/timeline.py` | `VideoPart` dataclass, `probe_duration` (ffprobe), `build_parts`, `offset_transcript`, `offset_events`, `merge_transcripts` |
| `tests/voiceover/test_timeline.py` | 21 unit/integration tests for timeline module |
| `tests/voiceover/test_multi_part.py` | 19 tests for cross-module multi-part support (data classes, matcher routing, CLI signature, serialization) |

### New files planned (future phases)

| File | Purpose | Phase |
|---|---|---|
| `src/clm/voiceover/merge.py` | Batching, prompt packing, JSON response parsing, per-slide fallback | 2 |
| `src/clm/voiceover/trace_log.py` | JSONL writer for `.clm/voiceover-traces/` | 2 |
| `src/clm/voiceover/prompts/merge_de.md` | German merge prompt (invariants + filter rules + style) | 2 |
| `src/clm/voiceover/prompts/merge_en.md` | English merge prompt | 2 |
| `src/clm/voiceover/training_export.py` | Trace-log reader + slide-state correlator for training triples | 4 |
| `tests/voiceover/test_merge_*.py` | Merge-mode tests including noise fixtures | 2 |
| `tests/voiceover/test_langfuse_*.py` | Langfuse fallback tests | 3 |

### How the components connect

Phase 1 data flow (single sync invocation with N parts):

```
CLI parses SLIDES + [VIDEO0, VIDEO1, ...]
  │
  ▼
probe_durations([VIDEO0, VIDEO1, ...])  →  [VideoPart(0, ..., offset=0),
                                             VideoPart(1, ..., offset=d0),
                                             VideoPart(2, ..., offset=d0+d1)]
  │
  ▼
for part in parts:
    transcribe_video(part.path) → segments with offset applied, tagged
    detect_transitions(part.path) → events with offset applied, tagged
  │
  ▼
merged Transcript + merged events
  │
  ▼
match_events_to_slides(events, slides, parts, lang) → timeline
  │
  ▼
align_transcript(merged_transcript, timeline) → AlignmentResult  [unchanged]
  │
  ▼
polish (Phase 1) / polish_and_merge (Phase 2)
  │
  ▼
write_narrative (Phase 1 fresh) / merge-aware variant (Phase 2)
```

Phase 2 additions layer cleanly on top: the merge call replaces the
polish call, baseline is read from the slide file before the pipeline
runs, and every merge call writes to the trace log as a side effect.

---

## 7. Testing Approach

### Phase 1

- **Unit**: duration probe, offset arithmetic, per-part tagging.
- **Integration**: synthetic three-part fixture (e.g. one real short
  video split into three pieces) run through the full `sync` pipeline;
  assert timeline is monotonic and aligner output matches a
  single-file baseline for the reassembled duration.
- **Regression**: existing single-video sync tests run with the
  positional-argument flip. Any test that previously passed
  `sync VIDEO SLIDES` now passes `sync SLIDES VIDEO`.
- **Real-data smoke**: run against `topic_045_streaming_generators`
  three-part recording at
  `D:\OBS\Recordings\AZAV ML\05 RAG mit LangChain (Part 1)\`. This is
  the real case that motivated the feature; if it doesn't produce
  sensible output, Phase 1 is not done.

### Phase 2

- **Noise fixtures (most important)**: hand-authored `(transcript_text,
  expected_drop_or_keep)` pairs covering every category in the prompt's
  filter list — greetings, part transitions, self-corrections,
  environment remarks, operator asides, code-typing dictation, off-topic
  tangents. These become the test corpus for prompt tuning. Each
  category needs ≥3 examples in each language (DE, EN).
- **Snapshot tests** for `polish_and_merge` with a mocked LLM client.
  The mock returns canned structured JSON; the test asserts that the
  writer produces the expected cells.
- **Rewrite detection**: fixture where baseline says X, transcript
  contradicts with Y → assert the LLM output contains a non-empty
  `rewrites` field and the dry-run diff marks it.
- **Overwrite flag parity**: `sync --overwrite` on a fresh file must
  produce byte-for-byte the same output as Phase 1 `sync` on the same
  file.
- **Trace log**: assert one line per LLM call, all required fields
  present, `git_head` matches current HEAD.

### Phase 3

- **Env-var gating**: Langfuse client not constructed when env vars
  absent (mock `os.environ`, check branch).
- **Failure isolation**: Langfuse server unreachable → warning logged,
  pipeline completes. Mock the Langfuse client to raise.

### Phase 4

- **Round-trip**: write a synthetic trace log, write a synthetic "final"
  slide state, assert the extraction produces the expected training
  triples including `delta_vs_llm` diffs.

### Running tests

```bash
pytest                                     # fast suite (~30s)
pytest -m "not docker"                     # pre-release full run
pytest tests/voiceover/ -v                 # voiceover-only
```

---

## 8. Session Notes

### User preferences captured during design

- **Multi-pass LLM workflows are bad.** The user explicitly rejected a
  two-pass additions-list + anchor-insert design in favor of a
  single-pass merge call. Rationale: slower, more expensive,
  unpredictable production failures. Apply this instinct to future
  design decisions — prefer one well-prompted LLM call over chained
  calls unless there is a concrete, demonstrated reason otherwise.

- **Git diff is the review layer.** The user will review merges via
  `git diff` after the fact rather than through an in-CLI interactive
  flow. Do not invest in prompt_toolkit / curses / `git add -p`-style
  review UX. `--dry-run` outputs a unified diff; anything else is out
  of scope.

- **Observability matters from day one.** Local JSONL trace log is
  part of Phase 2, not a later add-on. The user wants to accumulate a
  training corpus as soon as the merge feature is used, not after.

- **MCP is for slide-file manipulation, not batch jobs.** The user
  confirmed during design that a video-transcription MCP tool would be
  wrong-shape. If you find yourself wanting to add one, reread
  Design Decisions §2.6 first.

- **CLM is Windows-first.** Prefer Python scripts over bash.

- **Auto-memory is in use.** A number of cross-cutting preferences
  (worker test polling, Python 3.14 blocker, mypy cross-platform
  ignores) live in the user's memory system under
  `C:\Users\tc\.claude\projects\C--Users-tc-Programming-Python-Projects-clm\memory\`
  and load into every conversation. Honor them without waiting for a
  reminder.

### Noise filter fixture seed (German)

Concrete examples the user provided during design that should seed the
Phase 2 fixture set:

**Greetings / sign-offs / part transitions (drop)**:

- "Hallo, willkommen zurück zu Teil 2, ich hatte die Aufnahme kurz
  unterbrochen."
- "So, das war's für heute, bis zum nächsten Mal!"
- "Jetzt in Teil 2 angelangt."

**Self-corrections (drop)**:

- "Moment, ich hab da was übersehen, lass mich kurz zurückscrollen."
- "Oh sorry, das war der falsche Slide, ich muss da nochmal hin."
- "Uh, entschuldigung, das Mikrofon hat gerade kurz ausgesetzt."

**Environment remarks (drop)**:

- "Mein Docker-Container ist rot, weil ich das falsche Environment habe."
- "Mein Editor zeigt da rot, das ist aber egal."

**Operator asides (drop)**:

- "Kannst du das nachher rausschneiden."
- "Das kommt in den Schnitt."

**Code-typing dictation (drop — NEW category from user)**:

- "And then we define the function — def — fact — open paren — n —
  colon…"
- "For m comma n in range…"
- "Close paren, colon, return."

**Substantive additions (keep)**:

- "Oh, and by the way — `extend` mutates the list in place, it doesn't
  return a new one."
- "The free OpenRouter tier has a rate limit of ~20 requests per minute,
  so don't spam it when testing."
- "One thing I forgot to put on the slide: you can also pass
  `system_prompt` as a regular string instead of a `SystemMessage` in
  newer LangChain versions."

**Factual contradictions (keep AND mark as rewrite)**:

- Baseline: "`extend` returns a new list."
  Transcript: "actually, `extend` mutates in place and returns None."
  → rewrite baseline bullet, record evidence.
