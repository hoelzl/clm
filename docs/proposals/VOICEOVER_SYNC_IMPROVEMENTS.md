# Proposal: `clm voiceover sync` — Multi-File and Update Mode

**Status:** Draft
**Scope:** `clm voiceover sync` (and the MCP equivalent)

Two concrete improvements are wanted on `clm voiceover sync`, driven by real
authoring experience during the AZAV ML course restructure (W04 streaming
generators topic, April 2026).

Both improvements address a mismatch between how OBS recordings are actually
produced and how the current `sync` command consumes them.

---

## Improvement 1 — Multi-file input for part-based recordings

### Problem

OBS recordings are frequently split into multiple parts (`Teil 1.mp4`, `Teil
2.mp4`, `Teil 3.mp4`, …). The reason is mainly that students dislike very long
videos, so we split recordings into ~20-30 min chunks at natural breakpoints
(e.g. slide transitions). But the recording process itself is also more robust
when split into parts — if something goes wrong during recording, you only lose
a small part instead of the whole thing. This also means that multi-file videos
contain voiceover content that is only present because of the split, e.g.
greetings at the start of each part ("Hallo, willkommen zurück zu Teil 2…") and
sign-offs at the end ("So, das war's für heute, bis zum nächsten Mal!"). These
are noise for the voiceover and should be filtered out, but they exist in the
recording.

The current `clm voiceover sync` command takes exactly one `VIDEO`
positional argument:

```python
@click.argument("video", type=click.Path(exists=True, path_type=Path))
```

Callers are forced to either:

1. **Concatenate the parts manually** with `ffmpeg -f concat`, producing a new
   file that lives alongside the originals and has to be cleaned up later.
2. **Run sync once per part**, which is the wrong shape — the slide-transition
   detector and aligner want to see the whole timeline in order to
   (a) assign transcript text to the right slide, and (b) produce a single
   coherent notes_map.
3. **Skip sync entirely**, which is what happened for `topic_045_streaming_generators`
   during the W04 Phase 4 pass. The existing notes cells were already good
   voiceover content, so we retagged manually and moved on — but we lost the
   opportunity to pick up any improvisations or corrections from the recording.

None of these is acceptable as a default workflow.

### Proposal

Let `sync` accept one or more videos:

```bash
clm voiceover sync --lang de \
  --part "Teil 1.mp4" \
  --part "Teil 2.mp4" \
  --part "Teil 3.mp4" \
  slides_010v_streaming_generators.py
```

or, preferred, via a variadic positional (if click/typer makes that ergonomic
alongside the slides argument):

```bash
clm voiceover sync --lang de \
  "Teil 1.mp4" "Teil 2.mp4" "Teil 3.mp4" \
  -- slides_010v_streaming_generators.py
```

(or swap the order so slides comes first and videos are variadic — whichever
reads better.)

### Behavior

- CLM concatenates the parts **internally** for transcription / keyframe
  detection purposes, without touching the source files.
  - Either via the `ffmpeg concat` demuxer into a temp file,
  - or by driving the transcription backend segment-by-segment and merging
    the resulting transcripts + keyframe timestamps with a running offset.
  - The second approach avoids re-encoding and is friendlier to very long
    total runtimes, but is more work to implement.
- The ordering of parts is authoritative: CLM must not re-sort by mtime or
  filename.
- The existing `detect_transitions`, `match_events_to_slides`, and
  `align_transcript` pipeline then sees a single logical timeline spanning
  all parts.
- Slide-range filtering (`--slides-range`) still applies to the merged
  timeline, not per-part.

### Edge cases to handle

- A greeting or sign-off at the boundary of each part ("Hallo, willkommen zurück
  zu Teil 2, ich hatte die Aufnahme kurz unterbrochen…") — this is noise that
  should NOT end up in the voiceover. See Improvement 2 for the general noise
  filter.
- Part files with different encoding parameters. Internal concatenation has to
  handle this (either transcode to a common format or fall back to segment-wise
  transcription).
- Timestamps in the aligner must stay consistent across parts: if
  `Teil 1.mp4` is 12:34 long, all events in `Teil 2.mp4` should be offset by
  754 s before being handed to the matcher.

### Out of scope

- Automatic detection of "these files are parts of one recording". The caller
  specifies the parts explicitly and in order.
- Glob expansion (`Teil *.mp4`). If we get to it, it's a nice-to-have that sits
  on top of the explicit-parts interface.

---

## Improvement 2 — Update / merge mode

### Problem

Once a slide file already has voiceover cells (either hand-written or produced
by an earlier `sync` run), running `sync` again today **overwrites** them.
`write_narrative` doesn't currently have a concept of "merge this into whatever
is already there."

That's a problem in two directions:

**Direction A — the trainer improvises during recording.**
While recording a video, the trainer often notices something missing on a
slide and explains it live. Examples from real recordings:

- "Oh, and by the way — `extend` mutates the list in place, it doesn't return
  a new one. That's a common mistake."
- "I should mention: the free OpenRouter tier has a rate limit of ~20 requests
  per minute, so if you're testing, don't spam it."
- "One thing I forgot to put on the slide: you can also pass `system_prompt`
  as a regular string instead of a `SystemMessage` in newer LangChain
  versions."

These are valuable additions. When we re-record the video later (for a course
revision), we want to **make sure these additions end up on the slides or in
the voiceover**, so we don't have to remember them from memory or re-watch the
old recording. Today there's no good place to park them — editing them into
the voiceover manually is fine but tedious, and running `sync` against the
new recording would wipe the hand-edits.

**Direction B — the transcript also contains stuff that must NOT land in
voiceover.** Examples:

- Greetings and sign-offs at the boundary of multi-part recordings ("Hallo,
  willkommen zurück, wir sind jetzt in Teil 2 angelangt…").
- Recording mistakes:
  - "Moment, ich hab da was übersehen, lass mich kurz zurückscrollen…"
  - "Oh sorry, das war der falsche Slide, ich muss da nochmal hin."
  - "Uh, entschuldigung, das Mikrofon hat gerade kurz ausgesetzt."
- Environment noise ("mein Docker-Container ist rot, weil ich das falsche
  Environment habe" — useful for the trainer but not for the student).
- Off-topic tangents that were cut in post-production but are still in the
  audio.

Today these all end up in the polished notes map because the LLM polish step
doesn't have a strong enough signal that they should be dropped.

### Proposal: a new `--update` mode (alias: `--merge`)

```bash
clm voiceover sync --lang de \
  --update \
  Teil*.mp4 \
  slides_010v_streaming_generators.py
```

Semantics:

1. **Read the existing voiceover cells** from the slide file before doing
   anything. For each slide group, capture the current voiceover text (per
   language) as the **baseline**.
2. **Run the normal pipeline** (transcribe, detect transitions, match, align)
   to produce a fresh `notes_map` from the recording. Call this the
   **candidate**.
3. **Diff** the candidate against the baseline, per slide. For each slide:
   - Compute what's in the candidate that is NOT in the baseline — call these
     **additions**.
   - Pass additions through an LLM **noise-filter** prompt that rejects
     content matching noise patterns (see list below).
   - Present surviving additions to the user as **proposed merge hunks**:
     "Slide 7: the recording adds this sentence about `extend` mutability —
     append it?" (Interactive, or batch-approved via `--accept-all-additions`
     / `--dry-run` as today.)
   - Never delete or modify existing baseline content. Only append.
4. **Write** the merged voiceover back into the file.

### Noise filter

The noise filter is a prompt (for the polish LLM) that takes a candidate
addition and decides whether to keep or drop it. Examples of drop patterns:

- Mid-recording greetings / part transitions (`"willkommen zurück"`,
  `"jetzt in Teil 2"`, `"so, weiter geht's"`).
- Recording self-corrections (`"moment"`, `"lass mich kurz"`, `"falscher
  slide"`, `"ich muss da nochmal"`, `"entschuldigung"` at sentence start).
- Trainer-environment remarks about their own setup (`"mein Docker-Container"`,
  `"mein Environment"`, `"das Mikrofon"`, `"mein Editor zeigt rot"`).
- Content that was clearly said to the recording operator, not the student
  ("kannst du das nachher rausschneiden", "das kommt in den Schnitt").

The filter should be **conservative** — the default is "keep", and it only
drops on high-confidence pattern matches. When in doubt, it keeps the
addition and lets the trainer decide in the interactive review.

### Why not diff on words?

A naive word-level diff won't work because the trainer paraphrases between
takes and between what's on the slide. The merge needs to operate at the
**semantic** level: "does this candidate sentence express an idea that is
already covered by any sentence in the baseline?" That's an LLM call, not a
diff.

Rough shape of the per-slide merge prompt:

> You are reviewing an improvement to existing voiceover text.
>
> BASELINE (existing voiceover, known good):
> {baseline}
>
> CANDIDATE (new voiceover from a re-recording):
> {candidate}
>
> Return:
> 1. A list of sentences from CANDIDATE that add *new, substantive*
>    information not already present (even in different wording) in BASELINE.
> 2. For each such sentence, drop it if it is greeting/sign-off noise, a
>    recording self-correction, or trainer-environment commentary.
> 3. Rewrite the surviving sentences into the same voice and style as
>    BASELINE (bullet list, direct student address, consistent tense).
>
> If nothing substantive is added, return an empty list.

Output is the set of **bullets to append** to the baseline for that slide.

### Interactive review

Default `--update` behavior is interactive: for each slide with proposed
additions, show baseline + addition + accept/skip/edit prompt, like
`git add -p`. Non-interactive flags:

- `--dry-run` — already exists; show what *would* be appended and exit.
- `--accept-all-additions` — merge without prompting (use with care).
- `--reject-all-additions` — equivalent to running sync without `--update`
  against an already-written file, i.e. a no-op. Mainly useful for testing
  the noise filter without committing.

### Interaction with existing flags

- `--tag voiceover` / `--tag notes` — baseline is read from the same tag the
  command would write. Updating voiceover does not touch notes and vice
  versa.
- `--slides-range` — only slides in the range are candidates for merging.
  Out-of-range slides retain their baseline unchanged.
- `--mode polished` / `--mode verbatim` — verbatim mode probably shouldn't
  offer `--update`, since without the polish LLM there's no noise filter.
  Either make `--update` imply `polished` or error out when combined with
  `verbatim`.

### Out of scope (for this first pass)

- Multi-language merge coordination (DE/EN). Treat each language
  independently. If the trainer recorded only in DE, the EN voiceover is
  untouched.
- Merging into companion voiceover files (`extract-voiceover` output).
  Start with inline voiceover cells; extend later if needed.
- Preserving ordering of additions relative to the slide's bullet structure
  (just append to the end of the existing VO cell for now).

---

## Priority and sequencing

**Improvement 1** (multi-file input) is the prerequisite that unblocks
real-world use of sync for the AZAV ML course restructure. It's relatively
mechanical.

**Improvement 2** (update mode) is the bigger lift but is what makes
re-recording a video cheap. Without it, every revision pass either loses
hand-edits or loses new improvisations — both unacceptable.

Suggested order: ship (1) first, use it for a few topics to validate the
pipeline, then add (2).

## Reference — real case that prompted this proposal

`slides_010v_streaming_generators.py` (AZAV ML, W04 Phase 4, 2026-04-11).

- Recording exists as three separate files in
  `D:\OBS\Recordings\AZAV ML\05 RAG mit LangChain (Part 1)\` —
  `02 Streaming mit Generatoren (Teil 1).mp4`,
  `… (Teil 2).mp4`,
  `… (Teil 3).mp4`.
- The handover doc for the restructure prescribed running
  `clm voiceover sync` against a file named `(Teil 1+2+3).mp4`, which does
  not exist and has never existed — the name was aspirational.
- The pragmatic workaround was to skip sync entirely and retag the
  hand-authored `notes` cells (which were already voiceover-style and well
  paired in DE/EN) to `voiceover`. This worked for the first pass but means
  we can't easily ingest any improvisations from a future re-recording.
- Both improvements above would have let us ingest the existing recording
  cleanly: Improvement 1 handles the three-part input, Improvement 2 makes
  the merge safe against both the retagged hand-authored baseline and the
  inevitable boundary-greetings between Teil 1/2/3.
