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
clm voiceover sync --lang de video.mp4 slides.py
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

| Option | Default | Description |
|--------|---------|-------------|
| `--lang` | *(required)* | Video language: `de` or `en` |
| `--mode` | `polished` | `polished` cleans up with LLM; `verbatim` keeps raw transcript |
| `--whisper-model` | `large-v3` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `--device` | `auto` | `auto` (uses CUDA if available), `cpu`, or `cuda` |
| `--backend` | `faster-whisper` | Transcription backend (see below) |
| `--model` | `gpt-4o-mini` | LLM model for polished mode |
| `--tag` | `voiceover` | Cell tag: `voiceover` or `notes` |
| `--slides-range` | *(all)* | Limit to slide range, e.g. `5-20` |
| `--dry-run` | off | Show mapping without writing changes |
| `-o` | *(in-place)* | Write to a different output file |
| `--keep-audio` | off | Keep the extracted WAV file |

## Examples

### Basic usage (recommended defaults)

```bash
clm voiceover sync --lang de recording.mp4 slides/topic/slides_010v_intro.py
```

### Preview without writing changes

```bash
clm voiceover sync --lang de --dry-run recording.mp4 slides.py
```

### Verbatim mode (skip LLM polish)

```bash
clm voiceover sync --lang de --mode verbatim recording.mp4 slides.py
```

### Write to a separate file

```bash
clm voiceover sync --lang de -o output_slides.py recording.mp4 slides.py
```

### Use a different LLM for polishing

```bash
clm voiceover sync --lang de --model gpt-4o recording.mp4 slides.py
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
clm voiceover sync --lang de --backend cohere video.mp4 slides.py
```

### Granite Speech

IBM Granite 4.0 1B Speech model (Apache 2.0). Compact model for
resource-constrained environments.

```bash
pip install "coding-academy-lecture-manager[voiceover-granite]"
clm voiceover sync --lang de --backend granite video.mp4 slides.py
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

The `polished` mode (default) uses an LLM to fix remaining transcription
errors (capitalization of acronyms like SOLID/GRASP, filler word removal,
grammar cleanup). This produces output quality comparable to hand-written
speaker notes.

## Tips

- Always use `--dry-run` first to verify the slide matching before writing
- The `large-v3` model is ~3 GB; first run will download it
- For batch processing, the model is cached after the first download
- Existing voiceover/notes cells are replaced; other cells are preserved
- Only the specified language is modified; the other language is untouched
