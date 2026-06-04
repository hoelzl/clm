# Voiceover Synchronization

CLM can transcribe video recordings and synchronize the transcript with slide
files, automatically inserting voiceover cells into `slides_*.py` files.

## Installation

Install CLM with the voiceover extra:

```bash
pip install "coding-academy-lecture-manager[voiceover]"
```

This installs the default backend (faster-whisper with CTranslate2). For
alternative backends, see [Backends](#backends) below.

### GPU Acceleration

For CUDA-accelerated transcription (recommended for large-v3 model):

```bash
pip install "coding-academy-lecture-manager[voiceover]" ctranslate2
```

The `--device auto` default will use CUDA when available.

## Quick Start

```bash
clm voiceover sync --lang de slides.py video.mp4
```

This runs the full pipeline with recommended defaults:
1. Extracts audio from the video
2. Transcribes using faster-whisper (`large-v3` model)
3. Detects slide transitions via frame differencing
4. Matches transitions to slides via OCR + fuzzy matching
5. Aligns transcript segments to the slide timeline
6. Polishes the text with an LLM (`gpt-4o-mini`)
7. Inserts `tags=["voiceover"]` cells into the slide file

## Options

The signature is `clm voiceover sync [OPTIONS] SLIDES VIDEOS...` — the slide
file comes first, followed by one or more video parts.

| Option | Default | Description |
|--------|---------|-------------|
| `--lang` | *(required)* | Video language: `de` or `en` |
| `--polish-level` | `standard` | How aggressively to clean up the transcript: `verbatim` (keep transcript as-is, no LLM call), `light`, `standard`, `heavy`, or `rewrite` |
| `--overwrite` | off | Overwrite existing voiceover cells instead of merging (old behavior) |
| `--whisper-model` | `large-v3` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `--device` | `auto` | `auto` (uses CUDA if available), `cpu`, or `cuda` |
| `--backend` | `faster-whisper` | Transcription backend (see below) |
| `--model` | `gpt-4o-mini` | LLM model for polished/merge mode |
| `--tag` | `voiceover` | Cell tag: `voiceover` or `notes` |
| `--slides-range` | *(all)* | Limit to slide range, e.g. `5-20` |
| `--dry-run` | off | Show mapping without writing changes |
| `-o`, `--output` | *(in-place)* | Write to a different output file |
| `--keep-audio` | off | Keep the extracted WAV file |
| `--transcript` | *(none)* | Skip ASR and load a precomputed transcript JSON (from `clm voiceover transcribe -o ...`). Single-part only; combine with a single video argument |
| `--alignment` | *(none)* | Skip ASR, detection, and matching; load a precomputed alignment JSON from a prior sync run (cached under `.clm/voiceover-cache/alignments/`) |
| `--companion` / `--no-companion` | *(auto)* | Force companion-file merge on/off. Default: auto-detect whether a `voiceover_*.py` companion exists next to SLIDES |
| `--propagate-to` | *(none)* | After merging `--lang`, translate the changes into the given target language (`de` or `en`) and update its voiceover cells. Must differ from `--lang`; cannot combine with `--overwrite` |
| `--layout` | *(auto)* | Where to create a NEW companion: `subdir` (`voiceover/` folder) or `sibling` (next to SLIDES). Default: auto-detect an existing `voiceover/` folder, else sibling. Ignored when a companion already exists |

> **Deprecated:** `--mode` is a deprecated alias for `--polish-level`.
> `--mode polished` maps to `--polish-level standard`; `--mode verbatim`
> maps to `--polish-level verbatim`. Use `--polish-level` going forward.

## Examples

### Basic usage (recommended defaults)

```bash
clm voiceover sync --lang de slides/topic/slides_010v_intro.py recording.mp4
```

### Preview without writing changes

```bash
clm voiceover sync --lang de --dry-run slides.py recording.mp4
```

### Verbatim mode (skip LLM polish)

```bash
clm voiceover sync --lang de --polish-level verbatim slides.py recording.mp4
```

### Write to a separate file

```bash
clm voiceover sync --lang de -o output_slides.py slides.py recording.mp4
```

### Use a different LLM for polishing

```bash
clm voiceover sync --lang de --model gpt-4o slides.py recording.mp4
```

To use OpenRouter models, set `OPENAI_API_KEY` to your OpenRouter key and
configure the base URL via the `CLM_LLM__API_BASE` environment variable.

## Diagnostic Subcommands

### Transcribe only

```bash
clm voiceover transcribe --lang de video.mp4
clm voiceover transcribe --lang de -o transcript.json video.mp4
```

### Detect slide transitions

```bash
clm voiceover detect video.mp4
```

### Identify slides in video (OCR matching)

```bash
clm voiceover identify --lang de video.mp4 slides.py
```

## History-aware voiceover extraction

When a recording was made against an older revision of a slide file, these
subcommands recover the voiceover and port it forward onto the current HEAD:

- `clm voiceover backfill SLIDE_FILE VIDEOS...` — one-shot pipeline that
  composes the three steps below (identify the recorded revision, sync against
  it, port forward). Patch-by-default: writes a unified diff under
  `.clm/voiceover-backfill/` and prints it; pass `--apply` to mutate the file.
- `clm voiceover identify-rev SLIDE_FILE VIDEOS...` — find the git revision the
  video was recorded against, scored by OCR fingerprint matching.
- `clm voiceover sync-at-rev SLIDE_FILE VIDEOS... --rev <sha> -o scratch.py` —
  export `SLIDE_FILE` at `--rev` to scratch and run `sync` against it (the
  working tree is never touched).
- `clm voiceover port-voiceover SOURCE TARGET` — file-to-file transfer of
  voiceover cells from an older synced `SOURCE` onto the current `TARGET`.
- `clm voiceover compare SOURCE TARGET` — read-only sibling of
  `port-voiceover`; the LLM labels each bullet as covered/rewritten/added/
  dropped/manual_review. Neither file is modified.
- `clm voiceover compare-from-inventory SLIDE_FILE --inventory <map.json>` —
  look up the video(s) recorded against `SLIDE_FILE` in an inventory mapping,
  then run identify-rev → sync-at-rev → compare automatically.

**Typical backfill workflow:** run `clm voiceover backfill slides.py
video.mp4 --lang de` to get a patch, review the printed diff, then re-run with
`--apply` to write the ported voiceover onto the current slide file. The
intermediate `identify-rev` / `sync-at-rev` / `port-voiceover` commands are
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
clm voiceover sync --lang de --backend cohere slides.py video.mp4
```

### Granite Speech

IBM Granite 4.0 1B Speech model (Apache 2.0). Compact model for
resource-constrained environments.

```bash
pip install "coding-academy-lecture-manager[voiceover-granite]"
clm voiceover sync --lang de --backend granite slides.py video.mp4
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

- Always use `--dry-run` first to verify the slide matching before writing
- The `large-v3` model is ~3 GB; first run will download it
- For batch processing, the model is cached after the first download
- Existing voiceover/notes cells are replaced; other cells are preserved
- Only the specified language is modified; the other language is untouched

### Companion file placement

Voiceover companion files (`voiceover_*.py`) can live either in a `voiceover/`
subdirectory next to the topic or as siblings of the slide file. Control the
location for new companions with `--layout subdir|sibling` on `clm voiceover
sync`. The course-wide default is set via the `CLM_SIDECAR_LAYOUT` environment
variable or the `[tool.clm] sidecar-layout` setting in `pyproject.toml`. To
reorganize existing companions in bulk, use `clm slides tidy PATH --layout
subdir` (or `--layout sibling` to flatten them back).
