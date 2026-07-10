# Harvesting Narration from Video Recordings

`clm harvest` recovers spoken narration from recorded lecture videos into
slide decks: it transcribes the recording, detects slide transitions, matches
them to slides via OCR, aligns the transcript to the slide timeline, and
turns the result into voiceover cells.

The deterministic tier (ASR, transition detection, OCR matching, alignment)
is engine-owned, cached, and model-free. Curation and translation judgment
belong to the **driving agent** — the recommended workflow is the agent loop
below. For a fully automatic one-shot with embedded models, use
[`clm harvest autopilot`](#the-one-shot-clm-harvest-autopilot).

> `clm harvest` replaced the video side of the old `clm voiceover` group.
> The old command names were deleted, not aliased — see `clm info migration`
> for the rename table. `clm voiceover` still exists but holds only the
> written-narration text layer (`extract` / `inline` / `inline-notes`); see
> [Voiceover text layer](voiceover.md).

## Installation

Install CLM with the voiceover extra (the harvest pipeline lives in the
`clm.voiceover` package):

```bash
pip install "coding-academy-lecture-manager[voiceover]"
```

This installs the default backend (faster-whisper with CTranslate2). For
alternative backends, see [Backends](#backends) below.

### GPU Acceleration

For CUDA-accelerated transcription (recommended for the large-v3 model):

```bash
pip install "coding-academy-lecture-manager[voiceover]" ctranslate2
```

The `--device auto` default will use CUDA when available.

## The agent loop (recommended)

The canonical workflow is read → judge → write, with the judgment made by
the driving agent rather than an embedded model:

```
clm harvest report → (clm harvest task → judge → clm harvest accept [--record])*
                   → clm harvest verify → clm slides sync report
```

1. **`clm harvest report SLIDES VIDEO... --lang de`** — run the full
   deterministic tier (cached) and report, slide by slide, what the recording
   said and how it relates to the deck's existing voiceover. Read-only, no
   model, no API key. Exit code `1` means there are actionable items.

2. **`clm harvest task SLIDES VIDEO... --lang de --slide ID`** — frame one
   slide's judgment as a JSON task document: curation instructions, the
   baseline voiceover on both language sides, the aligned transcript, the
   bullet-list answer schema, and freshness fingerprints the answer must
   echo. `--kind curate` merges the recorded language; `--kind translate`
   frames the twin side from the already-curated source.

3. **`clm harvest accept SLIDES --answer FILE|-`** — validate the agent's
   bullet-list answer (schema, baseline freshness, structural re-parse;
   all-or-nothing) and write it through the v3 document model. Pass
   `--record` to bank the touched member into the sync ledger with
   provenance `harvest:<video-fingerprint>`.

4. **`clm harvest verify SLIDES`** — structural post-check on the pair.
   One-sided narrative members are not failures; they are listed as
   `pending_twins`.

5. **`clm slides sync report`** — twin translation continues in the regular
   sync loop; one-sided harvest writes show up there as translation work.

A minimal round trip, JSON end to end:

```bash
clm harvest report slides_intro.de.py recording.mp4 --lang de --json
# → {"summary": {"actionable": 2, ...},
#    "items": [{"key": "id:intro-motivation", "class": "no_existing_vo", ...}]}

clm harvest task slides_intro.de.py recording.mp4 --lang de \
    --slide intro-motivation --kind curate > task.json
# ... the agent judges task.json and writes an answer ...

clm harvest accept slides_intro.de.py --answer answer.json --record
clm harvest verify slides_intro.de.py --json
```

Bare `clm harvest DECK VIDEO...` runs the read-only default verb `report`,
so a dry look at a recording is a single command. For the full agent-facing
workflow reference (task/answer schemas, judgment rules, ledger semantics),
see `clm info harvest-agents`.

## The one-shot: `clm harvest autopilot`

`autopilot` is the legacy all-in-one pipeline **with embedded models**
(formerly `clm voiceover sync`). It runs the whole chain — transcribe,
detect, match, align, then a single-pass LLM merge that preserves existing
voiceover content and filters recording noise — and writes the result in one
command. It is key-gated (the merge needs an LLM API key) and never runs in
CI; agents should prefer the report → task → accept loop above.

```bash
clm harvest autopilot slides.py video.mp4 --lang de
```

This runs the full pipeline with recommended defaults:
1. Extracts audio from the video
2. Transcribes using faster-whisper (`large-v3` model)
3. Detects slide transitions via frame differencing
4. Matches transitions to slides via OCR + fuzzy matching
5. Aligns transcript segments to the slide timeline
6. Merges the transcript into existing voiceover content with an LLM
7. Inserts `tags=["voiceover"]` cells into the slide file

### Options

The signature is `clm harvest autopilot [OPTIONS] SLIDES VIDEOS...` — the
slide file comes first, followed by one or more video parts. Multiple parts
are processed independently and merged into a single timeline using running
offsets; part ordering is authoritative.

| Option | Default | Description |
|--------|---------|-------------|
| `--lang` | *(required)* | Video language: `de` or `en` |
| `--polish-level` | `standard` | How aggressively to clean up the transcript: `verbatim` (keep transcript as-is, no LLM call), `light`, `standard`, `heavy`, or `rewrite` |
| `--overwrite` | off | Overwrite existing voiceover cells instead of merging (old behavior) |
| `--whisper-model` | `large-v3` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `--device` | `auto` | `auto` (uses CUDA if available), `cpu`, or `cuda` |
| `--backend` | `faster-whisper` | Transcription backend (see below) |
| `--model` | *(OpenRouter default)* | LLM model for the merge/polish pass |
| `--tag` | `voiceover` | Cell tag: `voiceover` or `notes` |
| `--slides-range` | *(all)* | Limit to slide range, e.g. `5-20` |
| `--dry-run` | off | Show mapping without writing changes |
| `-o`, `--output` | *(in-place)* | Write to a different output file |
| `--keep-audio` | off | Keep the extracted WAV file |
| `--transcript` | *(none)* | Skip ASR and load a precomputed transcript JSON (from `clm harvest transcribe -o ...`). Single-part only; combine with a single video argument |
| `--alignment` | *(none)* | Skip ASR, detection, and matching; load a precomputed alignment JSON from a prior run (cached under the shared cache root's `alignments/`) |
| `--companion` / `--no-companion` | *(auto)* | Force companion-file merge on/off. Default: auto-detect whether a `voiceover_*.py` companion exists next to SLIDES |
| `--propagate-to` | *(none)* | After merging `--lang`, translate the changes into the given target language (`de` or `en`) and update its voiceover cells. Must differ from `--lang`; cannot combine with `--overwrite` |
| `--layout` | *(auto)* | Where to create a NEW companion: `subdir` (`voiceover/` folder) or `sibling` (next to SLIDES). Default: auto-detect an existing `voiceover/` folder, else sibling. Ignored when a companion already exists |

> **Deprecated:** `--mode` is a deprecated alias for `--polish-level`.
> `--mode polished` maps to `--polish-level standard`; `--mode verbatim`
> maps to `--polish-level verbatim`. Use `--polish-level` going forward.

### Examples

#### Basic usage (recommended defaults)

```bash
clm harvest autopilot slides/topic/slides_010v_intro.py recording.mp4 --lang de
```

#### Preview without writing changes

```bash
clm harvest autopilot slides.py recording.mp4 --lang de --dry-run
```

#### Verbatim mode (skip LLM polish)

```bash
clm harvest autopilot slides.py recording.mp4 --lang de --polish-level verbatim
```

#### Write to a separate file

```bash
clm harvest autopilot slides.py recording.mp4 --lang de -o output_slides.py
```

#### Use a different LLM for the merge

```bash
clm harvest autopilot slides.py recording.mp4 --lang de --model gpt-4o
```

To use OpenRouter models, set `OPENAI_API_KEY` to your OpenRouter key and
configure the base URL via the `CLM_LLM__API_BASE` environment variable.

## Diagnostic Subcommands

### Transcribe only

```bash
clm harvest transcribe video.mp4 --lang de
clm harvest transcribe video.mp4 --lang de -o transcript.json
```

### Detect slide transitions

```bash
clm harvest detect video.mp4
```

### Identify slides in video (OCR matching)

```bash
clm harvest identify video.mp4 slides.py --lang de
```

### Cache and traces

Every expensive intermediate (transcripts, transitions, timelines,
alignments) is cached in a **shared, deck-independent** root:
`<shared-cache-dir>/voiceover/`, where the shared cache dir resolves like
the LLM cache (`$CLM_CACHE_DIR` → `tool.clm.cache_dir` in the project's
`pyproject.toml` → `<project-root>/.clm-cache/`). Entries keyed by the video
alone (transcripts, transitions) are computed once per recording and reused
by every deck in the repository — forking or moving a deck does not re-run
ASR (issue #568). Entries in the older per-deck
`<deck dir>/.clm/voiceover-cache/` location are found on a miss and promoted
into the shared root automatically. Manage the cache with
`clm harvest cache list/prune/clear`. LLM merge calls are trace-logged under
`.clm/voiceover-traces/`; inspect a log with `clm harvest trace show PATH`.
The group-level flags `--cache-root`, `--no-cache`, and `--refresh-cache`
apply to every `clm harvest` subcommand.

## History-aware harvest

When a recording was made against an older revision of a slide file, these
subcommands recover the voiceover and port it forward onto the current HEAD:

- `clm harvest backfill SLIDE_FILE VIDEOS...` — one-shot pipeline that
  composes the three steps below (identify the recorded revision, run the
  pipeline against it, port forward). Patch-by-default: writes a unified diff
  under `.clm/voiceover-backfill/` and prints it; pass `--apply` to mutate
  the file.
- `clm harvest identify-rev SLIDE_FILE VIDEOS...` — find the git revision the
  video was recorded against, scored by OCR fingerprint matching.
- `clm harvest sync-at-rev SLIDE_FILE VIDEOS... --rev <sha> -o scratch.py` —
  export `SLIDE_FILE` at `--rev` to scratch and run the autopilot pipeline
  against it (the working tree is never touched).
- `clm harvest port SOURCE TARGET` — file-to-file transfer of voiceover
  cells from an older synced `SOURCE` onto the current `TARGET`.
- `clm harvest compare SOURCE TARGET` — read-only sibling of `port`; the LLM
  labels each bullet as covered/rewritten/added/dropped/manual_review.
  Neither file is modified. Re-render a saved `--json` report with
  `clm harvest compare-report REPORT.json`.
- `clm harvest compare-from-inventory SLIDE_FILE --inventory <map.json>` —
  look up the video(s) recorded against `SLIDE_FILE` in an inventory mapping,
  then run identify-rev → sync-at-rev → compare automatically.

**Typical backfill workflow:** run `clm harvest backfill slides.py
video.mp4 --lang de` to get a patch, review the printed diff, then re-run
with `--apply` to write the ported voiceover onto the current slide file.
The intermediate `identify-rev` / `sync-at-rev` / `port` commands are
available for running the steps manually when you need finer control.

## Backends

### faster-whisper (default, recommended)

Uses CTranslate2-based Whisper models. Best quality with `large-v3`, fast on
CUDA. Produces timestamped segments for accurate per-slide alignment.

```bash
pip install "coding-academy-lecture-manager[voiceover]"
```

### Cohere Transcribe

2B parameter conformer model (Apache 2.0). Requires `transformers>=5.4.0`.

```bash
pip install "coding-academy-lecture-manager[voiceover-cohere]"
clm harvest autopilot slides.py video.mp4 --lang de --backend cohere
```

### Granite Speech

IBM Granite 4.0 1B Speech model (Apache 2.0). Compact model for
resource-constrained environments.

```bash
pip install "coding-academy-lecture-manager[voiceover-granite]"
clm harvest autopilot slides.py video.mp4 --lang de --backend granite
```

**Note:** Cohere and Granite backends produce a single transcript segment
without per-segment timestamps, which limits per-slide alignment accuracy.
The faster-whisper backend is recommended for best results.

## Model Quality Comparison

Tested on a 5-minute German lecture recording:

| Model | Accuracy | Speed | Notes |
|-------|----------|-------|-------|
| `large-v3` | Best | ~30s (CUDA) | Recommended. Correct technical terms, minimal errors |
| `medium` | Good | ~15s (CUDA) | Good alternative if large-v3 is too slow |
| `base` | Fair | ~5s (CUDA) | Misses some technical terms (Stakeholder, GRASP) |
| `tiny` | Poor | ~3s (CPU) | Many errors, only for quick tests |

The default polish level (`standard`) uses an LLM to fix remaining
transcription errors (capitalization of acronyms like SOLID/GRASP, filler word
removal, grammar cleanup). This produces output quality comparable to
hand-written speaker notes. Use `--polish-level verbatim` to skip the LLM
entirely and keep the raw transcript.

## Tips

- Start with `clm harvest report` (or `--dry-run` on autopilot) to verify
  the slide matching before writing anything
- The `large-v3` model is ~3 GB; first run will download it
- For batch processing, the model is cached after the first download
- The agent loop and autopilot's default merge preserve existing
  voiceover/notes content; `--overwrite` replaces it
- Only the specified language is modified; the other language is untouched —
  one-sided writes surface as translation work in `clm slides sync report`
- Companion file placement (`voiceover/` subdir vs. sibling) is documented
  in [Voiceover text layer](voiceover.md)
