# CLM Voiceover: Design Document

## Video-to-Speaker-Notes Synchronization

**Status:** Design phase
**Date:** 2026-03-07
**Authors:** Matthias Hölzl, Claude

---

## 1. Problem Statement

### Background

CLM courses consist of video lectures where a trainer presents slides, followed
by private study and live sessions. Many slides have speaker notes
(`tags=["notes"]`) containing the voiceover text. These notes serve as the
script for recording and as reference material.

### The Problem

Speaker notes fall out of sync with the actual recordings for several reasons:

1. **Improvised adjustments:** The trainer modifies the text while recording
   (slightly or significantly), so the notes no longer match the video.
2. **Missing notes:** Older slides have no speaker notes at all, or only brief
   bullet points that don't reflect the actual voiceover.
3. **Post-recording edits:** Slides are revised or bugs are fixed after
   recording, requiring re-recording. The new recording may diverge from the
   original notes.

### Goal

Build a tool that takes a video recording and the corresponding slide source
file, and inserts or updates the speaker notes with the actual voiceover from
the video.

### Two Modes

- **Verbatim mode:** Use the transcript as-is (for slides recorded from existing
  notes, or slides edited after recording). Only fix obvious ASR errors.
- **Polished mode:** Clean up the spoken text without removing content (for
  slides recorded without notes, where there may be false starts, ungrammatical
  sentences, or filler words).

---

## 2. Slide Format

### Source Format

Slides are stored as `.py` files using the percent-format Jupyter notebook
convention. Each cell is a comment block starting with `# %%` or
`# %% [markdown]`.

Key metadata in cell headers:
- `lang="de"` / `lang="en"` — language variant
- `tags=["slide"]` — marks a new slide
- `tags=["subslide"]` — marks a sub-slide (new visual slide within same section)
- `tags=["notes"]` — speaker notes for the preceding slide
- `tags=["keep"]` — code cell preserved in all output variants
- `class="fragment"` — progressive reveal within a slide (in HTML `<li>` tags)

### Structure Example

```python
# %% [markdown] lang="de" tags=["slide"]
# ## Function Call
#
# - Function name
# - Arguments of the call, in brackets

# %% tags=["subslide"]
def example():
    return 42

# %%
example()

# %% [markdown] lang="de" tags=["notes"]
#
# - Now we have defined the function, and we have already called it.
# - Here's a quick summary of how function calls work...
# [... full voiceover text ...]

# %% [markdown] lang="de" tags=["slide"]
# ## Next Topic
```

### Key Structural Properties

- **Bilingual:** Each slide exists in `lang="de"` and `lang="en"` variants.
  The video is in one language; only the notes for that language should be
  updated.
- **Notes cells follow their slide:** A `tags=["notes"]` cell is placed after
  the slide cell(s) it belongs to, before the next slide begins.
- **Slide groups:** A single visual slide may span multiple cells (markdown +
  code cells). The notes apply to the entire group.
- **Fragments:** Some slides use `class="fragment"` for progressive reveal
  (rare). Current notes for fragments are written as a single block after all
  fragment cells. We do not need to track which fragment is active — just avoid
  misalignment.
- **Workshops:** Some notes cells contain structural instructions like "please
  pause the video now."

---

## 3. Video Characteristics

### Recording Setup

- **Screen recordings** (not camera recordings) — pixel-perfect frames, no
  perspective distortion.
- **RISE presentation mode** (older videos): Jupyter notebooks displayed as
  interactive slides via RISE. Plain white background, no header/footer chrome,
  no slide numbers. The trainer live-codes within slides.
- **VS Code slide mode** (newer videos): Has a footer line displaying the slide
  number. This could be exploited for matching.
- **Slide transitions:** RISE uses brief transition animations between slides.

### Complications

1. **Live coding within slides:** The trainer types code, which appears
   character by character. Autocompletion popups may appear and disappear.
   Output from cell execution can cause content to shift vertically (e.g., a
   centered code cell moves up when output appears below it).

2. **No fixed reference region:** Unlike PowerPoint-style presentations, RISE
   slides have no consistent header, footer, or title bar that remains stable
   within a slide. The entire content area can change during live coding.

3. **Slide transitions:** Brief animations between slides (motion blur, fade,
   or black frames). These are actually a useful signal for detecting
   transitions.

4. **Backtracking:** The trainer occasionally navigates back to a previous slide
   during an explanation (rare). The tool should accumulate all text spoken
   during any visit to a slide.

5. **Partial notebook coverage:** A video may cover only part of a notebook
   (e.g., slides 1-20 of 58). Multiple videos may cover different parts of the
   same notebook.

6. **Cross-slide speech:** The trainer sometimes begins speaking about the next
   slide while the previous slide is still showing, or finishes a sentence from
   the previous slide after transitioning. Notes should not split mid-sentence.

---

## 4. Architecture Overview

### Pipeline

```
                    +--------------------------------------------------+
                    |              INPUT                                |
                    |  - Video file (.mp4, .mkv, etc.)                 |
                    |  - Slide source (.py file)                       |
                    |  - Language (de/en)                               |
                    |  - Mode (verbatim/polished)                      |
                    |  - Optional: slide range (start-end)             |
                    +-------------------------+------------------------+
                                              |
                         +--------------------+--------------------+
                         |                                         |
                         v                                         v
              +---------------------+              +-------------------------+
              | 1. TRANSCRIBE       |              | 2. KEYFRAME EXTRACTION  |
              |                     |              |    + TRANSITION          |
              | Whisper ASR         |              |    DETECTION             |
              | -> word/segment     |              |                         |
              |    timestamps       |              | Frame sampling (2fps)   |
              |                     |              | Frame differencing      |
              +----------+----------+              | Transition candidates   |
                         |                         +------------+------------+
                         |                                      |
                         |                                      v
                         |                         +-------------------------+
                         |                         | 3. SLIDE IDENTIFICATION |
                         |                         |                         |
                         |                         | OCR at candidates       |
                         |                         | Fuzzy match vs. slides  |
                         |                         | Two-pass alignment      |
                         |                         +------------+------------+
                         |                                      |
                         v                                      v
              +----------------------------------------------------+
              | 4. ALIGNMENT                                       |
              |                                                    |
              | Map transcript segments to slides via timestamps   |
              | Sentence-level assignment (no mid-sentence splits) |
              | Backtracking accumulation                          |
              | Optional: transcript-content cross-validation      |
              +-------------------------+--------------------------+
                                        |
                                        v
              +----------------------------------------------------+
              | 5. POLISH (optional, --mode polished)              |
              |                                                    |
              | Per-slide LLM call: clean up disfluencies          |
              | Preserve all content and meaning                   |
              | Slide content provided as context                  |
              +-------------------------+--------------------------+
                                        |
                                        v
              +----------------------------------------------------+
              | 6. WRITER                                          |
              |                                                    |
              | Parse .py file structure                            |
              | Insert/replace tags=["notes"] cells                |
              | Only touch slides covered by video                 |
              | Respect lang (only update matching language)        |
              +----------------------------------------------------+
```

Steps 1 and 2 run in parallel (independent of each other). Steps 3-6 are
sequential.

---

## 5. Component Design

### 5.1 Slide Parser

**Purpose:** Parse the `.py` slide source file into a structured representation.

**Input:** Path to a `.py` slide file, target language (de/en).

**Output:** Ordered list of "slide groups," where each group contains:
- A unique slide ID (sequential index)
- The slide type (slide/subslide)
- The language
- All cells belonging to this slide (markdown, code, fragments)
- The text content (markdown stripped of formatting, for matching)
- Any existing notes cell(s)

**Key decisions:**
- A "slide group" is everything from one `tags=["slide"]` or
  `tags=["subslide"]` cell to the next. This is the unit of matching.
- Fragment cells within a group are part of the same group.
- Only cells matching the target language are considered for matching. The
  parser still tracks all cells for correct insertion of notes.

### 5.2 Transcription (Whisper)

**Purpose:** Generate a timestamped transcript from the video's audio track.

**Input:** Video file path, language hint.

**Output:** List of segments, each with:
- `start_time` (seconds)
- `end_time` (seconds)
- `text` (transcribed text)

**Implementation:**
- Primary: `faster-whisper` (CTranslate2-based, lighter than OpenAI's whisper,
  still runs locally, good quality).
- The transcription backend should be pluggable (protocol/interface) so that
  other models or APIs (OpenAI Whisper API, future local models) can be
  swapped in later without changing the pipeline.
- Extract audio from video using `ffmpeg` (via subprocess).

**Why faster-whisper over openai-whisper:**
- Significantly faster (4-8x) and lower memory usage.
- Same model weights (Whisper large-v3), same quality.
- No PyTorch dependency (uses CTranslate2 instead).
- Segment-level timestamps out of the box. Word-level timestamps available
  if needed.

### 5.3 Keyframe Extraction and Transition Detection

**Purpose:** Identify points in the video where the slide changes.

**Input:** Video file path.

**Output:** Ranked list of transition candidates, each with:
- `timestamp` (seconds)
- `confidence` (0.0 to 1.0)
- `frame_before` and `frame_after` (images for OCR)

**Algorithm — Two-tier change detection:**

Within a slide, changes are **gradual and localized** (typing character by
character, output appearing line by line). Between slides, changes are **sudden
and global** (entire frame content changes, possibly with a transition animation).

1. **Extract frames** at 2 fps using ffmpeg/OpenCV.
2. **Compute frame-to-frame difference** using normalized mean absolute pixel
   difference (0.0 = identical, 1.0 = completely different).
3. **Detect spikes** using adaptive thresholds:
   - Relative: frame difference > `threshold_factor` * rolling median
   - Absolute: frame difference > auto-calibrated minimum (percentile-based)
   - **Note (from prototype):** On 3840x2160 RISE recordings with white
     backgrounds, real slide transitions produce differences of 0.01-0.05.
     Fixed thresholds don't work; percentile-based auto-calibration is needed.
4. **Cluster nearby candidates:** RISE transition animations span 2-3 frames
   (~1-1.5 seconds). Merge candidates within a configurable window (default:
   3 seconds) into single transition events, keeping the highest-confidence
   frame.
5. **Extract stabilized frame:** For each cluster, capture the frame ~1 second
   after the peak (when the new slide has fully rendered), not the peak frame
   itself (which may be mid-animation).
6. **Rank clusters** by peak difference magnitude.

**Key insight (validated by prototype):** Frame differencing alone cannot
distinguish slide transitions from large within-slide changes (e.g., code
execution output appearing). It serves as a pre-filter to identify candidate
moments; OCR confirmation is essential. See `docs/claude/voiceover-prototype-findings.md`.

**VS Code slide mode bonus:** For newer videos using VS Code, we could
additionally OCR the footer region to extract slide numbers. RISE also shows
slide numbers in the bottom-right corner (format: `section.slide`). Both are
future enhancements.

### 5.4 Slide Identification (Matcher)

**Purpose:** Determine which source slide corresponds to each detected
transition.

**Input:** Transition candidates (with frames), parsed slide list.

**Output:** Timeline of `(slide_id, start_time, end_time)` entries.

**Algorithm — Two-pass alignment with OCR:**

**Pass 1 — Anchor matching (high-confidence transitions):**
1. Take the top-N highest-confidence transition candidates.
2. OCR the stabilized frame after each transition (using Tesseract or similar).
3. Fuzzy-match the OCR text against each slide's text content using
   `rapidfuzz` (token-set ratio handles partial matches well).
4. Apply sequential ordering constraint: matches must be roughly monotonic
   (with allowed local backtracking). Use this to disambiguate close scores.
5. Result: a set of anchor points — `(timestamp, slide_id)` pairs we're
   confident about.

**Pass 2 — Gap filling:**
1. Between consecutive anchors, check if any slides are unaccounted for.
2. If slides are missing, lower the confidence threshold and examine
   additional transition candidates in the gap.
3. OCR and match these lower-confidence candidates.
4. If still missing, fall back to **OCR-first scanning**: sample frames at
   regular intervals within the gap and OCR each one, looking for the
   missing slide's content.

**Pass 3 — Validation:**
1. Check that the final timeline accounts for a plausible subset of the
   slide sequence.
2. Flag any anomalies (unexpected ordering, very short slide durations,
   duplicate appearances suggesting backtracking).

**Backtracking handling:**
- If the matched slide ID goes backwards (e.g., 5 → 6 → 7 → 6 → 7 → 8),
  record all intervals. During alignment (step 5.5), accumulate transcript
  text across all visits to the same slide.

**Sequential constraint as prior information (critical):**
- Slides appear roughly in order. If the previous confirmed slide was #7,
  the next one is very likely #8 or #9 (or #6-7 for backtracking).
- This dramatically reduces ambiguity: even with imperfect OCR, we only need
  to distinguish between a handful of candidate slides, not the entire deck.
- **Prototype finding:** This constraint is essential, not just helpful.
  Real courses have slides with identical titles (e.g., "Transformation"
  appears as both slide [1] and slide [4], "Filterung" as [2] and [5]).
  OCR scores are nearly identical for these; only the sequential constraint
  disambiguates them.

**Header/title slide handling:**
- Videos typically start with a header slide generated by the `j2` macro
  (shows course title + author name). This slide is not in the parsed slide
  groups.
- The matcher should detect the initial low-confidence OCR match (no slide
  content matches well) and treat it as a title/header slide, assigning no
  transcript text to it.
- Alternatively, the slide parser can recognize the `j2` header macro and
  emit a synthetic "header" slide group.

### 5.5 Transcript-to-Slide Alignment

**Purpose:** Assign each transcript segment to a slide.

**Input:** Timestamped transcript segments, slide timeline.

**Output:** Map of `slide_id -> list[str]` (the notes text for each slide).

**Algorithm:**

1. For each transcript segment, find which slide was visible during most of
   the segment's duration.
2. **Sentence-level integrity:** Never split a sentence across two slides.
   If a segment straddles a slide boundary, assign it to the slide where the
   majority of its duration falls. In ambiguous cases (roughly 50/50), lean
   towards the **previous** slide — because the trainer tends to finish a
   thought before the visual transition completes.
3. **Backtracking accumulation:** If a slide appears multiple times in the
   timeline (because the trainer navigated back to it), the text from the
   revisit must be clearly marked. The aligner tracks `max_slide_seen` —
   the highest slide index encountered so far. When appending text to a
   slide whose index is less than `max_slide_seen`, it inserts a
   `**[Revisited]**` marker before the new content:

   ```
   # %% [markdown] lang="de" tags=["notes"]
   #
   # - So our Pythagorean function has two parameters...
   # - That means we have to pass it two arguments.
   # - For example, the numbers 3 and 4.
   #
   # - **[Revisited]** And just to clarify what I said earlier about
   #   parameters — think of them as the variable names on the left side
   #   of the equals sign.
   ```

   Without the marker, the notes would read as a single continuous block
   and the trainer would not know that the revisited portion was spoken
   out of sequence. The marker is also easy to grep for, allowing
   programmatic detection of backtracking segments.

**Optional cross-validation:**
- Compute word overlap between each transcript segment and each slide's text
  content. If the best text match disagrees with the timestamp-based
  assignment, flag it (but prefer the timestamp-based assignment, since the
  trainer may be explaining concepts not literally written on the slide).

### 5.6 LLM Polish

**Purpose:** Clean up transcript or existing notes text.

**Input:** Raw text (transcript or existing notes) for a slide, the slide's
content as context.

**Output:** Polished text.

**Design principle:** This component is **independent of the voiceover
pipeline**. It takes slide content + text to polish and returns cleaned text.
This makes it reusable:
- The voiceover pipeline calls it to clean up transcript text.
- A standalone `clm polish` command calls it to improve existing speaker
  notes that have quality issues (e.g., bullet-point outlines that should
  be fleshed out, or notes with grammatical problems).

**Prompt strategy:**
- Provide the slide content as context so the LLM understands what's being
  discussed.
- Instruct: remove filler words, false starts, and repetitions. Fix grammar.
  Do not remove any substantive content. Keep the style natural and spoken
  (not overly formal). Preserve technical terms exactly.
- Use the same LLM infrastructure as `clm summarize` (litellm, configurable
  model via `CLM_LLM__MODEL`).

**Mode interaction (within voiceover pipeline):**
- `--mode verbatim`: Skip this step entirely. Only fix obvious ASR errors
  (this could be a lightweight LLM pass or rule-based).
- `--mode polished`: Full cleanup pass.

**Standalone usage:**
```bash
clm polish slides.py --lang de              # Polish all notes in file
clm polish slides.py --lang de --slides 5-10  # Polish specific slides
clm polish slides.py --lang en --dry-run    # Preview without writing
```

### 5.7 Writer

**Purpose:** Insert or update speaker notes in the `.py` source file.

**Input:** Slide-to-notes mapping, path to `.py` file, target language.

**Output:** Modified `.py` file (in-place or to a specified output path).

**Algorithm:**

1. Parse the `.py` file into cells (reuse the slide parser's cell-level
   parsing).
2. For each slide covered by the video:
   a. Find the insertion point: after the last cell of the slide group,
      before the next slide group begins.
   b. If a `tags=["notes"]` cell for the target language already exists at
      that position, replace its content.
   c. If no notes cell exists, insert a new one.
3. Do not touch slides outside the video's coverage range.
4. Do not touch notes cells for the other language.
5. Preserve all other content exactly.

**Notes cell format:**
```python
# %% [markdown] lang="de" tags=["notes"]
#
# - First line of speaker notes.
# - Second line of speaker notes.
# - Continuation of the voiceover text...
```

The notes text should be formatted as a markdown list (lines starting with
`# - `) to match the existing convention in the codebase. Each logical
sentence or thought should be a separate list item.

---

## 6. CLI Design

### Voiceover Command (`clm voiceover`)

```bash
# Synchronize speaker notes from video
clm voiceover sync video.mp4 slides.py --lang de
clm voiceover sync video.mp4 slides.py --lang en --mode polished

# Limit to specific slide range
clm voiceover sync video.mp4 slides.py --lang de --slides 5-20

# Dry run: show the mapping without writing changes
clm voiceover sync video.mp4 slides.py --lang de --dry-run

# Diagnostic subcommands (for debugging and tuning)
clm voiceover transcribe video.mp4 --lang de -o transcript.json
clm voiceover detect video.mp4 -o transitions.json
clm voiceover identify video.mp4 slides.py --lang de -o mapping.json
```

### Polish Command (`clm polish`)

A standalone command for improving existing speaker notes, independent of the
voiceover pipeline:

```bash
# Polish all notes in a file
clm polish slides.py --lang de

# Polish specific slides only
clm polish slides.py --lang de --slides 5-10

# Preview changes without writing
clm polish slides.py --lang en --dry-run

# Write to a different file
clm polish slides.py --lang de -o slides_polished.py
```

### Voiceover Key Options

| Option | Description |
|--------|-------------|
| `--lang` | Language of the video (de/en). Required. |
| `--mode` | `verbatim` (default) or `polished`. |
| `--slides` | Slide range to update (e.g., `5-20`). Auto-detected if omitted. |
| `--dry-run` | Show mapping and transcript assignment without writing. |
| `--whisper-model` | Whisper model size (default: `large-v3`). |
| `--output` / `-o` | Write to a different file instead of modifying in-place. |

### Polish Key Options

| Option | Description |
|--------|-------------|
| `--lang` | Language of the notes to polish (de/en). Required. |
| `--slides` | Slide range to polish (e.g., `5-10`). All slides if omitted. |
| `--dry-run` | Show polished text without writing. |
| `--output` / `-o` | Write to a different file instead of modifying in-place. |

---

## 7. Code Organization

### Rationale: Single Package

The voiceover tool lives **inside the CLM package** rather than as a separate
`clm-voiceover` package. This was chosen because:

1. **Shared components:** The slide parser, writer, and polish components are
   useful beyond voiceover (e.g., a standalone `clm polish` command, future
   notebook utilities). Keeping them in CLM avoids duplication and circular
   dependencies.
2. **Precedent:** CLM already uses optional dependency groups for heavy
   features (`[summarize]` with litellm, `[tui]` with textual, `[web]` with
   flask). The `[voiceover]` extra follows the same pattern.
3. **Single repo:** One CI pipeline, one release cycle, one set of tests.
   The alternative (two tightly coupled packages with a dependency edge
   between them) adds coordination overhead with no real benefit.

### Source Layout

```
src/clm/
├── core/                        # Existing domain logic
├── infrastructure/              # Existing runtime support
│   └── llm/                     # Existing LLM client (reused by polish)
├── workers/                     # Existing workers
├── cli/
│   └── commands/
│       ├── voiceover.py         # clm voiceover subcommand
│       └── polish.py            # clm polish subcommand (standalone)
├── notebooks/                   # NEW: shared notebook/slide utilities
│   ├── __init__.py
│   ├── slide_parser.py          # Parse .py slide files into structured slides
│   ├── slide_writer.py          # Insert/update cells in .py files
│   └── polish.py                # LLM text cleanup (reusable component)
└── voiceover/                   # NEW: voiceover-specific components
    ├── __init__.py
    ├── transcribe.py            # Whisper integration, pluggable backend
    ├── keyframes.py             # Frame extraction + transition detection
    ├── matcher.py               # OCR + fuzzy matching of frames to slides
    └── aligner.py               # Transcript-to-slide assignment
```

**Key separation:**
- `clm.notebooks` — general-purpose utilities for working with slide/notebook
  `.py` files. No heavy dependencies. Usable by any CLM component or future
  tool.
- `clm.voiceover` — voiceover-specific logic. Heavy dependencies (whisper,
  opencv, tesseract) gated behind the `[voiceover]` optional extra.
- `clm.notebooks.polish` — the LLM polish component lives in `notebooks`
  (not `voiceover`) because it is independently useful. It depends on litellm
  (gated behind `[summarize]` or a new `[polish]` extra, or we reuse the
  existing `[summarize]` extra since both need litellm).

### Dependencies

```toml
# In CLM's pyproject.toml
[project.optional-dependencies]
voiceover = [
    "faster-whisper",       # ASR (CTranslate2-based, no PyTorch needed)
    "opencv-python",        # Frame extraction and differencing
    "pytesseract",          # OCR
    "rapidfuzz",            # Fuzzy text matching
    "Pillow",               # Image handling
]
# litellm is already in the [summarize] extra; polish reuses it.
# If we want polish without summarize, we could add:
# polish = ["litellm"]
```

**External tools required:** `ffmpeg` (audio/video extraction), `tesseract`
(OCR). Both widely available via system package managers.

### CLI Registration

```python
# In clm/cli/main.py — same pattern as existing commands
try:
    from clm.cli.commands.voiceover import voiceover_group
    cli.add_command(voiceover_group)
except ImportError:
    pass  # voiceover dependencies not installed

try:
    from clm.cli.commands.polish import polish
    cli.add_command(polish)
except ImportError:
    pass  # litellm not installed
```

The `try/except ImportError` guards ensure `clm` works without the optional
dependencies. Users who haven't installed `[voiceover]` simply don't see the
`clm voiceover` command, similar to how `clm monitor` requires `[tui]`.

---

## 8. Design Decisions and Rationale

### Why not render slides and compare screenshots?

Rendering the slides to HTML (which CLM can do) and screenshotting each slide
state would give us perfect reference images for matching. However:

- It requires a headless browser (Playwright/Puppeteer) to render RISE slides.
- RISE rendering in headless mode may not match the actual recording exactly.
- The live-coding within slides means the actual frame content diverges
  significantly from the static rendered slide anyway.
- The OCR + fuzzy matching approach is simpler, has fewer dependencies, and
  is robust enough given the sequential ordering constraint.

### Why probabilistic transition detection + OCR, not OCR-first?

**OCR-first** (OCR every frame, detect slide changes by noticing when the
best-matching slide changes) is conceptually simple but expensive: a 30-minute
video at 0.5 fps produces ~900 frames to OCR. While Tesseract is fast, this
adds up.

**Frame differencing as pre-filter** reduces OCR calls from ~900 to ~50-100
(only at transition candidates), making the pipeline much faster. The
frame-diff signal is cheap to compute and reliable for detecting the sudden
global changes that characterize slide transitions.

We keep OCR-first as a **fallback** for gap-filling when frame differencing
misses a transition.

### Why not a vision LLM for end-to-end matching?

Sending hundreds of video frames to a vision API (Claude, GPT-4o) for
matching is:
- **Too expensive** for routine use
- **Too slow** (minutes per video)
- **Context-limited** (can't fit hundreds of frames in one call)
- **Less reliable** than deterministic OCR + fuzzy matching for this specific
  task

Vision LLMs could be useful as a fallback for individual ambiguous frames,
but not as the primary matching strategy.

### Why sentence-level alignment with previous-slide bias?

The trainer sometimes changes slides while still finishing a sentence from the
previous topic. Splitting a sentence across two notes blocks would be
distracting and unnatural. Assigning the entire sentence to the previous slide
(when it straddles the boundary) is a simple heuristic that matches the
trainer's actual intent: the sentence belongs to the thought being concluded,
not the new slide being introduced.

### Why a single package with optional extras (not a separate package)?

Initially we considered a separate `clm-voiceover` package, but this was
reconsidered because:

- **Shared components:** The slide parser, writer, and polish modules are
  useful beyond voiceover. A separate package would require either duplicating
  them or introducing a dependency from `clm-voiceover` back to `clm`, making
  the separation illusory.
- **Optional dependency groups** already solve the dependency isolation problem.
  Users who don't need voiceover simply don't install `[voiceover]`, just like
  `[tui]` or `[web]`. The heavy dependencies (faster-whisper, opencv) are
  never pulled in unless explicitly requested.
- **Single repo** means one CI pipeline, one release cycle, and no cross-package
  coordination overhead.
- **Precedent:** `clm summarize` with its litellm dependency already
  demonstrates this pattern successfully.

### Why faster-whisper as the default ASR?

- Same Whisper model quality (large-v3) as OpenAI's reference implementation.
- 4-8x faster inference via CTranslate2.
- No PyTorch dependency (significant size reduction).
- Provides segment-level and word-level timestamps.
- Active maintenance and community support.
- The transcription interface is designed to be pluggable, so other backends
  (OpenAI API, future models) can be added later without changing the pipeline.

---

## 9. Risk Assessment

### High Risk: Slide Matching Accuracy

The entire pipeline depends on correctly identifying which slide is shown at
each point in the video. Mitigation:
- Multiple matching signals (frame diff, OCR, sequential constraint, optional
  transcript-content overlap)
- Two-pass approach with gap filling
- Diagnostic subcommands for debugging
- **Planned prototype:** Test frame differencing and OCR matching on a real
  video before building the full pipeline.

### Medium Risk: OCR Quality During Live Coding

While the trainer is typing, OCR quality may degrade (partial words, cursor
artifacts, autocompletion popups). Mitigation:
- We OCR the frame *after* a detected transition (i.e., the new slide's
  initial state), not during typing.
- Fuzzy matching tolerates imperfect OCR.
- The sequential constraint means we only need to distinguish between a few
  candidate slides.

### Medium Risk: Whisper Transcription Quality

Whisper may produce errors, especially with technical terms, code identifiers,
or mixed-language content. Mitigation:
- Whisper large-v3 is very good for both German and English.
- In polished mode, the LLM cleanup pass can fix many ASR errors using slide
  content as context.
- In verbatim mode, ASR errors may persist — but the alternative (manual
  transcription) is far worse.

### Low Risk: Cross-Slide Speech Misalignment

The sentence-level assignment with previous-slide bias should handle most
cases. Edge cases (very long sentences spanning multiple slide transitions)
are rare and acceptable.

---

## 10. Prototype Plan

### Phase 1: Diagnostic Prototype (COMPLETED)

**Status:** Completed 2026-03-07. Strategy 4 (hybrid) validated.

**Results:** See `docs/claude/voiceover-prototype-findings.md` for detailed
findings. Diagnostic tool at `tools/voiceover_diagnostic.py`.

**Summary:** Frame differencing produces clean spikes at slide transitions.
OCR + fuzzy matching reliably identifies slides (scores 87-100 for correct
matches). Sequential ordering constraint is critical for slides with
duplicate titles. All slides detected in both test videos (3-slide short
video and 12-slide longer video with live coding).

### Phase 2: Core Pipeline

Build the components in this order:
1. `clm.notebooks.slide_parser` — can be tested immediately with real slide
   files
2. `clm.voiceover.transcribe` — test with a real video
3. `clm.voiceover.keyframes` — frame extraction and transition detection
4. `clm.voiceover.matcher` — OCR + fuzzy matching
5. `clm.voiceover.aligner` — transcript-to-slide assignment
6. `clm.notebooks.slide_writer` — insert/update notes cells

### Phase 3: Polish and Integration

1. `clm.notebooks.polish` — LLM cleanup (independent of voiceover)
2. `clm.cli.commands.voiceover` — voiceover CLI
3. `clm.cli.commands.polish` — standalone polish CLI
4. CLI registration in `main.py`
5. Documentation

---

## 11. Future Enhancements

- **VS Code slide mode support:** Exploit footer slide numbers for easier
  matching.
- **Confidence reporting:** Output a per-slide confidence score so the user
  knows which assignments to double-check.
- **Interactive review mode:** Show the mapping in a TUI before writing (lower
  priority — git diff serves this purpose).
- **Batch processing:** Process all videos for a course in one command.
- **Incremental updates:** Only re-process slides whose video has changed.
- **Alternative ASR backends:** OpenAI Whisper API, local models via
  whisper.cpp, etc.
- **Content-based cross-validation:** Use transcript word overlap with slide
  text as an additional matching signal.
- **Additional `clm.notebooks` utilities:** The slide parser and writer are
  designed to be reusable. Future tools could include: bulk notes editing,
  notes quality checking, notes translation assistance, slide content
  analysis, etc.
