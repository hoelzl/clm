# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Recording management module** (`clm recordings`): New optional module for managing
  the video recording workflow for educational courses. Integrates the standalone
  recording processing pipeline into CLM as an optional `[recordings]` extra.
  - `clm recordings check` — verify recording dependencies (ffmpeg, onnxruntime)
  - `clm recordings process` — process a single recording through the 5-step audio
    pipeline (extract → DeepFilterNet3 ONNX noise reduction → FFmpeg filters → AAC → mux)
  - `clm recordings batch` — batch-process all recordings in a directory
  - `clm recordings status` — show per-lecture recording status for a course
  - `clm recordings compare` — generate A/B audio comparison HTML with blind test mode
  - `clm recordings assemble` — scan for paired raw video + processed audio, mux final
    output via FFmpeg, and archive originals
- **Recording workflow automation** (`recordings/workflow/`): Foundation for automating
  the recording → processing → assembly pipeline.
  - `naming.py` — filename convention helpers (raw/final filenames, `--RAW` suffix parsing),
    delegates sanitization to existing `sanitize_file_name` from core utils
  - `directories.py` — three-tier directory structure (`to-process/`, `final/`, `archive/`)
    management and pending pair scanning
  - `assembler.py` — mux video + processed audio via FFmpeg and archive originals
- **Recording state manager** (`recordings/state.py`): Pydantic models for per-course
  recording state stored as JSON files. Supports auto-assignment of recordings to lectures,
  reassignment, and status tracking.
- **Git commit capture** (`recordings/git_info.py`): Captures HEAD commit hash and dirty
  state of the course repository at recording assignment time.
- **RecordingsConfig**: New `[recordings]` section in CLM's TOML configuration system
  with settings for OBS output directory, course list, active course, auto-processing,
  and audio processing pipeline parameters. Includes `root_dir` (recordings root) and
  `raw_suffix` (default `--RAW`) for the workflow automation.

### Changed
- **Replaced DeepFilterNet CLI with ONNX inference**: The audio processing pipeline now
  uses the DeepFilterNet3 streaming ONNX model via `onnxruntime` instead of the
  `deepfilternet` CLI subprocess. This removes the dependency on the unmaintained
  `deepfilternet` package (which pins `numpy<2.0` and lacks Python 3.12+ wheels).
  Dependencies: `onnxruntime`, `soundfile`, `numpy`. The ONNX model is auto-downloaded
  and cached on first use.
- **Renamed config field**: `deepfilter_atten_lim` → `denoise_atten_lim` in both
  `PipelineConfig` and `RecordingsProcessingConfig`.

## [1.1.9] - 2026-03-25

### Changed
- **Replaced litellm with openai SDK**: The `[summarize]` extra now uses the `openai`
  package directly instead of `litellm`, reducing the dependency footprint. The LLM
  client, polish module, and summarize pipeline all use the OpenAI SDK natively.
- **Added langfuse dependency**: Added `langfuse` to the `[summarize]` optional
  dependency group for LLM observability and tracing.

### Fixed
- **mypy type annotation**: Fixed `cv2.cvtColor` return type annotation in
  `voiceover/keyframes.py`.

## [1.1.8] - 2026-03-17

### Added
- **bm25s dependency**: Added `bm25s[core]>=0.3.2.post1` as a core dependency for BM25
  sparse retrieval support in notebooks.
- **Docker notebook image**: Added `bm25s[core]` to both lite and full variants of the
  notebook-processor Docker image.

## [1.1.7] - 2026-03-17

### Added
- **`voiceover` cell tag**: New tag that behaves identically to `notes` (private,
  deleted from completed/code-along output, kept in speaker output) but renders with
  a light amber background (`#FFEEBA`) instead of yellow, to visually distinguish
  voiceover-originated content from hand-written speaker notes.

### Changed
- **Renamed `is_notes` → `is_narrative`** in `slide_parser` and `slide_writer`: The
  property now returns `True` for both `notes` and `voiceover` tags, reflecting that
  both are speaker-facing narrative content attached to slides.

## [1.1.6] - 2026-03-10

### Added
- **ipywidgets dependency**: Added `ipywidgets>=8.1.0` to the `[notebook]` optional
  dependency group to fix tqdm "IProgress not found" warning in Jupyter notebooks.

## [1.1.5] - 2026-03-09

### Added
- **`project_` file prefix**: Files named `project_*.py`, `project_*.md`, etc. are now
  recognized as notebook files and processed through the full notebook pipeline (jupytext →
  nbconvert → HTML/ipynb), alongside the existing `slides_` and `topic_` prefixes. This
  enables markdown-based project documents to be converted to notebooks and HTML slides.
- **`prog-lang` attribute on `<topic>`**: Individual topics can now override the course-level
  programming language with `<topic prog-lang="java">my_topic</topic>`. This is especially
  useful for `.md` notebook files where the language cannot be inferred from the file extension.

### Changed
- **`.md` default language changed from Rust to Python**: Markdown notebook files (`.md`) now
  default to Python instead of Rust when no course-level or topic-level `prog-lang` is set.
  The programming language for `.md` files follows a priority chain:
  topic `prog-lang` attribute → course `<prog-lang>` element → Python (default).

### Fixed
- **Markdown notebook parsing**: `.md` files are now correctly parsed using jupytext's `"md"`
  format, which auto-detects both standard markdown (fenced code blocks) and MyST
  (`{code-cell}`) variants. Previously, `.md` files were incorrectly parsed using the
  programming language's format (e.g., `"py:percent"`), causing the entire file content to be
  treated as a single code cell.
- Forbid Markdown headings in trainer summaries to preserve heading hierarchy in
  generated summary documents.

## [1.1.3] - 2026-03-05

### Added
- **Voiceover pipeline** (`clm voiceover`): Synchronize video recordings with slide files.
  Extracts audio, transcribes with Whisper, detects slide transitions via frame differencing,
  matches transitions to slides using OCR + fuzzy matching, and inserts speaker notes into
  percent-format `.py` slide files. Requires the `[voiceover]` extra.
  - `clm voiceover sync` — Full pipeline: video + slides → speaker notes
  - `clm voiceover transcribe` — Extract transcript from video
  - `clm voiceover detect` — Detect slide transitions in video
  - `clm voiceover identify` — Match video frames to slides via OCR
- **LLM polish** (`clm polish`): Clean up existing speaker notes using an LLM. Removes filler
  words, fixes grammar, and preserves technical terms. Works standalone or as part of the
  voiceover pipeline (`--mode polished`). Requires the `[summarize]` extra.
- **`clm.notebooks` module**: Shared slide file utilities for parsing, writing, and polishing
  percent-format `.py` slide files (`slide_parser`, `slide_writer`, `polish`).
- **`clm.voiceover` module**: Video processing pipeline with pluggable transcription backend,
  frame-based transition detection, OCR + fuzzy slide matching, and transcript-to-slide alignment.
- **`[voiceover]` optional dependency group**: `faster-whisper`, `opencv-python`, `pytesseract`,
  `rapidfuzz`, `Pillow`.
- 129 new tests across voiceover, notebooks, and CLI modules.

### Changed
- Voiceover optional dependencies use lazy imports so CI works without the `[voiceover]` extra.

## [1.1.2] - 2026-03-05

### Added
- **`clm summarize` command**: Generate LLM-powered markdown summaries of course content.
  Supports `--audience client|trainer`, `--style prose|bullets`, `--granularity notebook|section`,
  per-notebook caching, and configurable LLM models via the openai SDK. Requires the `[summarize]` extra.
- **`--amend` flag for `clm git commit` and `clm git sync`**: Amend the previous commit
  instead of creating a new one. When used without `-m`, reuses the previous commit message
  (`--no-edit`). When used with `-m`, replaces the commit message.
- **`--force-with-lease` flag for `clm git push` and `clm git sync`**: Safe force push
  for rewritten history. `--amend` on `sync` implies `--force-with-lease` automatically.
  When force-pushing, the "remote is ahead" safety check is skipped.

### Fixed
- Bullet-style client output formatting in summarize command.

## [1.1.1] - 2026-03-05

### Added
- Automatic `.env` file loading: The `build` command now walks up the directory tree to
  find a `.env` file and loads it before spawning workers.

### Changed
- Reorganized optional dependencies: moved data-science packages from `[notebook]` to
  `[ml]` extra, organized by category.

### Fixed
- CLI help text formatting for multi-line examples.
- Suppressed `RequestsDependencyWarning` from the requests library.

## [1.1.0] - 2026-02-27

### Added
- **Remote URL template for git operations**: Trainers can now override the git remote
  URL pattern via a configurable template with placeholders (`{repository_base}`, `{repo}`,
  `{slug}`, `{lang}`, `{suffix}`). Set via `CLM_GIT__REMOTE_TEMPLATE` environment variable,
  `[git] remote_template` in TOML config, or `<remote-template>` in the course spec XML.
  Enables SSH access with custom host aliases (e.g., `git@github.com-cam:Org/{repo}.git`).

### Changed
- **Flatten speaker kind subfolder**: Speaker output no longer creates a redundant `Speaker/`
  subfolder in the output path. Paths are now `.../Html/Section/` instead of
  `.../Html/Speaker/Section/`, since speaker output has only one variant.

## [1.0.9] - 2025-11-29

### Added
- `clm info <topic>` command for version-accurate documentation that downstream agents
  can query at runtime. Topics: `spec-files`, `commands`, `migration`.

### Changed
- `<project-slug>` promoted to top-level course spec element (previously inside `<github>`).
  The old location still works but is deprecated and logs a warning.

## [1.0.8] - 2025-11-28

### Added
- `docker.io/` registry prefix for Podman compatibility.
- `.python-version` file for Arch Linux compatibility.

### Fixed
- `sanitize_path` no longer strips leading dots from path components.

## [1.0.7] - 2025-11-27

### Added
- LangSmith and Ragas to ML optional dependencies.
