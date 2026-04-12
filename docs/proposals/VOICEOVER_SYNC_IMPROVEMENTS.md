# Proposal: `clm voiceover sync` — Multi-File Input and Merge Mode

**Status:** Accepted — ready for implementation
**Scope:** `clm voiceover sync` (CLI only; no MCP tool for video transcription)

Two concrete improvements to `clm voiceover sync`, driven by real authoring
experience during the AZAV ML course restructure (W04 streaming generators
topic, April 2026). Both address a mismatch between how OBS recordings are
actually produced and how the current `sync` command consumes them.

This document is the source of truth for implementation. Design decisions
discussed and agreed upon with the user (April 2026) are captured in full
below. A companion handover document at
`docs/claude/voiceover-sync-improvements-handover.md` tracks phase-level
implementation state.

---

## Improvement 1 — Multi-file input for part-based recordings

### Problem

OBS recordings are split into multiple parts (`Teil 1.mp4`, `Teil 2.mp4`, …)
as a matter of course. Two reasons:

- **Pedagogical:** students dislike very long videos, so we split recordings
  into ~20-30 min chunks at natural breakpoints (slide transitions).
- **Robustness:** if something goes wrong during recording, only a small
  part is lost instead of the whole session.

A consequence: multi-file recordings contain voiceover content that is only
present because of the split — greetings at the start of each part ("Hallo,
willkommen zurück zu Teil 2…") and sign-offs at the end ("So, das war's für
heute, bis zum nächsten Mal!"). These are noise for the voiceover and must
be filtered out (handled in Improvement 2), but they exist in every
recording and the pipeline must know where they live.

The current `clm voiceover sync` command takes exactly one `VIDEO`
positional argument:

```python
@click.argument("video", type=click.Path(exists=True, path_type=Path))
```

Callers are forced into unacceptable workarounds: manual ffmpeg
concatenation, running sync once per part (wrong — the aligner wants a
single timeline), or skipping sync entirely (what we did for
`topic_045_streaming_generators` during W04 Phase 4, losing any
improvisations from the recording).

### Proposal: `sync SLIDES VIDEO...`

The CLI signature becomes:

```bash
clm voiceover sync --lang de \
  slides_010v_streaming_generators.py \
  "Teil 1.mp4" "Teil 2.mp4" "Teil 3.mp4"
```

Slides first, videos variadic via click's `nargs=-1`. This is a breaking
change to the positional argument order; acceptable because the only
current user is the proposal author and the migration is trivial.

### Behavior: segment-wise per-part processing

CLM does **not** concatenate the video files. Each part is transcribed and
keyframe-detected independently, and results are merged into a single
logical timeline using running offsets. Rationale:

1. **Boundary metadata is preserved.** The filter in Improvement 2 needs to
   know exactly where part boundaries fall so it can be extra suspicious of
   greeting/sign-off noise at those points. Concatenation erases this
   metadata.
2. **No spurious transitions.** If we concat into a fused file, the concat
   point creates a visual discontinuity that the slide-transition detector
   will almost certainly flag as a fake slide change. Per-part processing
   avoids this class of bug entirely.
3. **No encoding compatibility problem.** Each part is decoded by its own
   ASR / keyframe call. Different codecs across parts are a non-issue.

Per-part processing:

- `transcribe(part_i) → segments_i`, merge with offset
  `Σ duration(part_0..i-1)`
- `detect_transitions(part_i) → events_i`, merge with the same offset
- Each segment and event is tagged with `source_part_index` for downstream
  use (the merge prompt gets a `boundary_hint` when a slide spans a part
  boundary)
- `align_transcript` sees the single logical timeline it already expects

`--slides-range` filtering applies to the merged timeline, as before.

Part ordering is authoritative: CLM must **not** re-sort by mtime or
filename. The caller passes parts in the order they should be stitched.

### Edge cases

- **Encoding mismatch between parts:** no special handling needed (each
  part is processed independently). If a specific part is unreadable by
  ffmpeg, fail loudly with the part index in the error message.
- **Part boundary alignment:** when a transcript segment straddles a part
  boundary, it is assigned to whichever side of the boundary holds the
  majority of its duration (existing aligner behavior, unchanged).

### Debug support

`--keep-temp` preserves the per-part audio extractions next to the source
videos for debugging.

### Out of scope

- Automatic detection of "these files are parts of one recording." Caller
  specifies parts explicitly.
- Glob expansion (`Teil *.mp4`). Nice-to-have on top of the explicit
  interface.
- **MCP tool for video transcription.** A transcription job is a
  multi-minute process with large inputs and outputs — that is a batch CLI
  job, not an MCP round trip. The existing MCP server
  (`src/clm/mcp/tools.py`) exposes only slide-file manipulation
  (`extract_voiceover`, `inline_voiceover`, `suggest_sync`); no video-level
  tool exists and none will be added.

---

## Improvement 2 — Merge mode (new default)

### Problem

Once a slide file has voiceover cells — whether hand-written or from an
earlier `sync` run — running `sync` again today **overwrites** them. That
is unsafe for two reasons:

**Direction A — trainer improvises during recording.** While recording,
the trainer often notices something missing on a slide and explains it
live. Examples:

- "Oh, and by the way — `extend` mutates the list in place, it doesn't
  return a new one."
- "The free OpenRouter tier has a rate limit of ~20 requests per minute,
  so don't spam it when testing."
- "One thing I forgot to put on the slide: you can also pass
  `system_prompt` as a regular string instead of a `SystemMessage` in
  newer LangChain versions."

These are valuable additions. When the video is re-recorded later, we want
the additions to end up in the voiceover without re-watching the old
recording from memory.

**Direction B — transcript also contains stuff that must NOT land in
voiceover.** Examples:

- Greetings and sign-offs at part boundaries ("Hallo, willkommen zurück,
  wir sind jetzt in Teil 2 angelangt…").
- Recording self-corrections ("Moment, lass mich kurz zurückscrollen…",
  "Oh sorry, das war der falsche Slide…").
- Trainer-environment remarks ("mein Docker-Container ist rot, weil ich
  das falsche Environment habe").
- **Code-typing narration:** the trainer reading out syntax tokens while
  live-coding ("And then we define the function — def — fact — open
  paren — n — colon…"). Explanations of code stay; dictation of code
  drops.
- Off-topic tangents.

### Proposal: merge by default, `--overwrite` for old behavior

`sync` is changed so that its default behavior is to merge into existing
voiceover cells. The old destructive behavior is available via
`--overwrite`. Rationale: merge is less destructive, and a user who runs
`sync` twice against the same file by accident should not lose content.

```bash
# Default: merge into existing voiceover cells
clm voiceover sync --lang de slides_010v.py "Teil 1.mp4" "Teil 2.mp4"

# Old behavior: overwrite everything the sync produces
clm voiceover sync --lang de --overwrite slides_010v.py "Teil 1.mp4" "Teil 2.mp4"
```

`--dry-run` produces output (see "Dry-run output" below) without touching
disk.

### Semantics

1. **Read the existing voiceover cells** from the slide file for the
   current `--tag` (default: `voiceover`). Per slide group, capture the
   current text as the **baseline**.
2. **Run the normal pipeline** (transcribe, detect transitions, match,
   align) to produce raw aligned transcript text per slide. Call this the
   **candidate**.
3. **For each slide**, call
   `polish_and_merge(baseline, candidate, slide_content, language,
   boundary_hint)` — a single LLM call (batched, see below) that produces
   the merged voiceover bullet list.
4. **Write** the merged voiceover back into the file.

Slides with no baseline in the current tag get the current polish behavior
(insert fresh cell). The merge path degrades cleanly to the fresh-insert
path when baseline is empty. There is no separate "new slide" code path.

### The `polish_and_merge` function

This is a natural generalization of the existing `polish_text` in
`src/clm/notebooks/polish.py`. It takes:

```python
async def polish_and_merge(
    baseline_bullets: str,        # existing voiceover cell ("" if none)
    transcript_text: str,         # raw aligned transcript for this slide
    slide_content: str,           # slide code/markdown for context
    language: str,                # de / en
    boundary_hint: bool = False,  # transcript spans a part boundary
    *,
    model: str = "gpt-4o-mini",
    ...
) -> MergeResult
```

When `baseline_bullets == ""`, it is the current polish behavior (clean
transcript → bullets). When non-empty, it merges. One function, two
behaviors.

### Prompt structure

The LLM handles noise filtering, content preservation, addition
integration, and position-in-list **all in one call**. No multi-pass
diff/anchor/insert scheme. Ordering of bullets in the output is the LLM's
decision; git diff is the review layer (see "Dry-run output" below).

System prompt shape (language-specific versions swapped based on `--lang`):

```
You are an expert editor for educational course voiceover cells. You
receive an existing voiceover (baseline bullets) and a raw transcript of
what the trainer said while this slide was visible. Produce an updated
voiceover as a bulleted list.

Invariants:
1. Default: PRESERVE every substantive point in the baseline. Integrate
   new substantive content from the transcript in the narrative position
   that matches how the trainer explained it.
2. EXCEPTION: If the transcript directly contradicts or corrects a
   specific baseline bullet, you MAY rewrite that bullet to incorporate
   the correction. This is only permitted when the transcript makes a
   clear factual contradiction (e.g., baseline says "extend returns a new
   list", transcript says "extend mutates in place and doesn't return a
   new list"). Style improvements, paraphrases, or clarifications are NOT
   corrections — leave those alone.
3. Never silently drop a baseline bullet. If you rewrite one, the
   rewritten version replaces it; nothing disappears.
4. Do not hallucinate. Every bullet must come from the baseline or the
   transcript.

Filter (drop from transcript, never from baseline):
- Greetings, sign-offs, part-boundary transitions ("willkommen zurück",
  "so, weiter geht's", "das war's für heute").
- Recording self-corrections ("moment", "falscher Slide", "lass mich
  kurz", "entschuldigung" at sentence start).
- Trainer environment remarks ("mein Docker-Container", "das Mikrofon",
  "mein Editor zeigt rot").
- Content said to the recording operator ("kannst du das rausschneiden",
  "das kommt in den Schnitt").
- Code-typing narration: trainer reading out syntax tokens while
  live-coding ("def fact open paren n colon", "and then a for loop, for
  m comma n"). Keep explanations of the code; drop dictation of it.
- Off-topic tangents.

Style:
- Bulleted markdown ("- " prefix), one thought per bullet.
- Direct student address, consistent tense (match baseline).
- Same language as input (do not translate).

Return STRUCTURED JSON per slide (see response schema).
```

User prompt per slide:

```
SLIDE CONTEXT (do not include in output):
{slide_content}

BASELINE VOICEOVER (preserve; may rewrite only on factual contradiction):
{baseline_bullets}

TRANSCRIPT (candidate additions, filter aggressively):
{transcript_text}

{boundary_hint_line if boundary_hint}
```

### Structured response schema

Per slide, the LLM returns:

```json
{
  "slide_id": "slides_010v/7",
  "merged_bullets": "- ...\n- ...",
  "rewrites": [
    {
      "original": "- extend returns a new list",
      "revised": "- extend mutates the list in place and returns None",
      "transcript_evidence": "actually, extend mutates in place, it doesn't return anything"
    }
  ],
  "dropped_from_transcript": [
    "willkommen zurück zu Teil 2",
    "moment, falscher slide"
  ]
}
```

Fields:

- `merged_bullets` is what gets written to the slide file.
- `rewrites` lists every baseline bullet that was modified under invariant
  2. Empty list = no rewrites happened. `--dry-run` surfaces these
  prominently (e.g., `⚠ slide 7: 1 baseline rewrite`) so reviewers don't
  have to hunt through diffs.
- `dropped_from_transcript` is a best-effort record of noise the filter
  rejected. Useful for tuning the filter and as a fixture source for
  testing.

### Batching

LLM calls are batched across slides to reduce overhead and improve style
consistency:

- Input: list of `{slide_id, baseline, candidate, slide_content,
  boundary_hint}` items packed into one user message up to a character
  budget.
- Default budget: 20,000 characters (configurable via
  `--batch-char-limit`).
- Response: JSON list of per-slide results keyed by `slide_id` — so
  batch-internal ordering drift cannot corrupt the mapping.
- On JSON parse failure for a whole batch: fall back to per-slide calls
  for that batch.
- Slides where both `baseline` and `transcript` are trivially empty are
  early-returned without an LLM call.

### Dry-run output

`--dry-run` emits a **unified diff** of baseline → merged to stdout,
scoped to the voiceover cells that changed. One diff per slide file,
standard `diff -u` headers. Easy to scan, pipeable to `less`, matches the
git-review mental model.

Rewrites (under invariant 2) are annotated in the dry-run output with a
warning marker so they do not get lost among append-only changes.

### Interaction with existing flags

- `--tag voiceover` / `--tag notes`: baseline is read from and written to
  the same tag. Merging voiceover does not touch notes and vice versa.
  The default tag is `voiceover`.
- **Baseline tag fallback: none.** If a slide has content only in the
  *other* tag (e.g., `--tag voiceover` set but the slide only has a
  `notes` cell), the merge sees an empty baseline and inserts a fresh
  voiceover cell alongside the existing notes cell. Users who want to use
  existing notes as baseline must retag manually first. This keeps the
  rule simple: one run, one tag.
- `--slides-range`: only slides in the range are candidates for merging.
  Out-of-range slides retain their baseline unchanged.
- `--mode polished` / `--mode verbatim`: **error** when `--mode verbatim`
  is combined with merge (the new default). Verbatim mode has no polish
  LLM and therefore no noise filter; merging raw transcript text into
  existing voiceover would be unsafe. Users who want verbatim behavior
  must also pass `--overwrite`.

### Out of scope (for this first pass)

- Multi-language merge coordination (DE/EN). Each language is merged
  independently. If the trainer recorded only in DE, the EN voiceover is
  untouched.
- Merging into companion voiceover files (`extract-voiceover` output).
  Start with inline voiceover cells; extend later if needed.

---

## Observability: Langfuse tracing

Langfuse integration is added to
`clm.infrastructure.llm.client._build_client`. The change is minimal: when
Langfuse environment variables are set, `_build_client` returns a
Langfuse-wrapped `openai.AsyncOpenAI` instance; otherwise it returns the
plain client. Every existing LLM call benefits automatically, not just
voiceover.

### Configuration

Three standard environment variables:

- `LANGFUSE_HOST` (e.g. `http://localhost:3000` for a local Docker
  instance)
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

Absence of any of these disables tracing silently. Failure to reach
Langfuse at runtime logs a warning and continues; tracing must never break
the pipeline.

The `langfuse` package is added to the `[voiceover]` extra. If Langfuse
spreads to other modules, it can be promoted to a top-level `[tracing]`
extra later.

### Trace shape

- `session_id = f"voiceover-sync-{topic}-{timestamp}"` — groups everything
  from one `sync` invocation into a Langfuse dashboard view.
- One trace per LLM call (each merge batch, each polish call).
- `trace.name = "voiceover_merge_batch"` or `"polish_text"`.
- `trace.metadata = {slide_ids, language, mode, topic, batch_char_count}`.
- `trace.tags = ["voiceover-sync", language, mode]`.
- `trace.user_id = <git user.name>`.
- Input and output are captured automatically via the Langfuse openai
  wrapper.

---

## Training data collection

### Local trace log (always on)

For every `sync` invocation, CLM writes a JSONL trace log to
`.clm/voiceover-traces/<topic>-<YYYYMMDD-HHMMSS>.jsonl` inside the course
repo. One line per LLM call, containing:

```json
{
  "timestamp": "2026-04-12T01:20:20Z",
  "slide_file": "slides_010v_streaming_generators.py",
  "slide_id": "slides_010v/7",
  "language": "de",
  "baseline": "- ...",
  "transcript": "...",
  "llm_merged": "- ...",
  "rewrites": [...],
  "dropped_from_transcript": [...],
  "langfuse_trace_id": "...",
  "git_head": "<commit hash at sync time>"
}
```

`.clm/voiceover-traces/` is added to the project's `.gitignore` by
default. Users who want to version the logs can un-ignore them explicitly.

The trace log is independent of Langfuse. It is the durable, local-first
substrate for training data extraction. Langfuse, if configured, receives
the same data via its own channels.

### Training data extraction (later phase)

A new command `clm voiceover extract-training-data <trace-log>` reads the
trace log and correlates each entry against the current slide file to
produce training triples:

```json
{
  "input": {"baseline": "...", "transcript": "..."},
  "llm_output": "...",
  "human_final": "...",
  "delta_vs_llm": "<diff>"
}
```

Two kinds of training signal fall out:

1. `(baseline + transcript) → human_final`: end-to-end supervised training
   for a merge model.
2. `(baseline + transcript + llm_output) → human_final`: correction
   training for a critic/editor model.

Both are suitable for LoRA / fine-tuning.

The trace log itself ships as part of Phase 2 (so data accumulation starts
immediately). The extraction command is deferred to Phase 4, since a
corpus must accumulate before training is meaningful.

---

## Phase plan

1. **Phase 1 — Multi-file input (Improvement 1).**
   - `SLIDES VIDEO...` CLI signature (breaking change).
   - Segment-wise transcription and keyframe detection per part.
   - Running-offset merge into single logical timeline.
   - `source_part_index` tagging on transcript segments and slide events.
   - Boundary metadata plumbed through to downstream consumers.
   - `--keep-temp` for debugging.
   - No LLM changes.

2. **Phase 2 — Merge mode (Improvement 2 core).**
   - Default behavior changed to merge; `--overwrite` flag restores old
     behavior.
   - `polish_and_merge` in `src/clm/notebooks/polish.py` with
     relaxed-baseline prompt returning structured rewrites.
   - Batched LLM calls with 20k char budget and JSON-keyed response.
   - Unified-diff `--dry-run` output, with rewrite annotations.
   - Same-tag-only baseline policy.
   - `--mode verbatim` + merge errors out.
   - Language-specific prompt variants.
   - Boundary hint forwarded from Phase 1.
   - Local trace log (`.clm/voiceover-traces/`) wired in from day one.

3. **Phase 3 — Langfuse integration.**
   - Wrap `_build_client` to return Langfuse-enabled client when env vars
     are set.
   - Session / trace / span structure as specified above.
   - `langfuse` added to `[voiceover]` extra.
   - Benefits all LLM-using modules, not just voiceover.

4. **Phase 4 — Training data extraction command.**
   - `clm voiceover extract-training-data` reads trace logs, correlates
     with current slide state, emits training jsonl.
   - Low priority until a corpus has accumulated.

---

## Reference — real case that prompted this proposal

`slides_010v_streaming_generators.py` (AZAV ML, W04 Phase 4, 2026-04-11).

- Recording exists as three separate files in
  `D:\OBS\Recordings\AZAV ML\05 RAG mit LangChain (Part 1)\` —
  `02 Streaming mit Generatoren (Teil 1).mp4`,
  `… (Teil 2).mp4`,
  `… (Teil 3).mp4`.
- The handover doc for the restructure prescribed running
  `clm voiceover sync` against a file named `(Teil 1+2+3).mp4`, which
  does not exist and has never existed — the name was aspirational.
- The pragmatic workaround was to skip sync entirely and retag the
  hand-authored `notes` cells (which were already voiceover-style and
  well paired in DE/EN) to `voiceover`. This worked for the first pass
  but means we cannot easily ingest improvisations from a future
  re-recording.
- Both improvements above would have let us ingest the existing recording
  cleanly: Phase 1 handles the three-part input, Phase 2 makes the merge
  safe against both the retagged hand-authored baseline and the
  inevitable boundary-greetings between Teil 1/2/3.
