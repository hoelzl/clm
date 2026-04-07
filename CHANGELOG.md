# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Build pipeline integration (Phase 4C)**: Companion voiceover files are automatically
  merged during notebook processing, and internal metadata is stripped from all output.
  - When `voiceover_X.py` exists alongside `slides_X.py`, voiceover cells are merged
    in-memory for speaker output. Other output kinds (completed, code-along) filter them
    out via existing tag-based cell deletion.
  - `slide_id` and `for_slide` metadata are stripped from all output cell metadata —
    they never appear in generated HTML, notebooks, or code.
  - Companion files are excluded from the `other_files` payload to avoid duplication.
  - Unmatched `for_slide` references in companion files produce build warnings.
  - `NotebookFile.companion_voiceover_path` — detects companion voiceover files.
  - `merge_voiceover_text()` — in-memory merge function for the build pipeline.
- **Voiceover extract/inline (Phase 4B)**: New `clm extract-voiceover` and `clm inline-voiceover`
  commands and MCP tools.
  - `extract-voiceover` moves voiceover and notes cells from a slide file to a companion
    `voiceover_*.py` file, linked via `slide_id`/`for_slide` metadata.
  - Content cells without `slide_id` get auto-generated IDs before extraction.
  - `inline-voiceover` reverses the operation: merges companion cells back into the slide
    file by matching `for_slide` → `slide_id`, then deletes the companion file.
  - `--dry-run` and `--json` flags on both commands.
  - Companion file naming: `slides_X.py` → `voiceover_X.py` (also handles `topic_` and
    `project_` prefixes).
  - `clm.slides.voiceover_tools` — `ExtractionResult`, `InlineResult`,
    `extract_voiceover()`, `inline_voiceover()`, `companion_path()`.
- **Slide ID auto-generation (Phase 4A)**: New `slide_ids` operation in `normalize-slides`.
  - Auto-generates `slide_id` metadata for cells that lack it.
  - Markdown cells with headings → slugified heading text (e.g., `# Methoden` → `methoden`).
  - Code cells with `def`/`class` → function/class name (e.g., `def greet` → `greet`).
  - Fallback → `file-stem-cell-N` for cells without identifiable content.
  - Paired DE/EN cells get the same ID (German cell as source).
  - Collision resolution with `-2`, `-3` suffixes.
  - Cells with existing `slide_id` are preserved unchanged.
  - Available via `clm normalize-slides <path> --operations slide_ids`.
- **Suggest sync (Phase 3B)**: New `clm suggest-sync` command and MCP tool.
  - Compares a slide file against git HEAD to detect asymmetric bilingual edits.
  - Identifies modified, added, and deleted cells in the source language that
    lack corresponding changes in the target language.
  - Uses `slide_id` metadata for precise DE/EN pairing when available; falls back
    to positional pairing. Reports `pairing_method` as `slide_id`, `positional`,
    or `mixed`.
  - Auto-detects source language if `--source-language` is omitted (picks the
    language with more changes).
  - Handles untracked (new) files gracefully — all cells treated as added.
  - `--json` flag for structured output; human-readable summary by default.
  - `clm.slides.language_tools` — `SyncSuggestion`, `SyncResult`, `suggest_sync()`.
- **Language view (Phase 3A)**: New `clm language-view` command and MCP tool.
  - Extracts a single-language view of bilingual slide files (DE or EN).
  - Includes language-neutral cells (code, images) alongside the requested language.
  - `[original line N]` annotations before each cell for mapping edits back to the source.
  - `--include-voiceover` and `--include-notes` flags for optional narrative cells.
  - `clm.slides.language_tools` — `get_language_view()`.
- **Slide normalization (Phase 2D)**: New `clm normalize-slides` command and MCP tool.
  - Tag migration: renames `alt` → `completed` when immediately following a `start` cell;
    standalone `alt` cells are unchanged.
  - Workshop tag insertion: adds `workshop` tag to `## Workshop:` / `## Mini-Workshop:`
    heading cells.
  - Interleaving normalization: three-tier strategy (Tier 1 count check, Tier 2 positional
    pairing with 5 similarity checks, Tier 3 structured review for uncertain pairs).
  - `--dry-run` previews all changes without modifying files.
  - `--operations` filter for selective normalization (tag_migration, workshop_tags,
    interleaving).
  - Exit codes: 0 (clean/applied), 1 (partial — review items), 2 (blocked).
  - `clm.slides.normalizer` — `Change`, `ReviewItem`, `NormalizationResult`,
    `normalize_file()`, `normalize_directory()`, `normalize_course()`.
- **Slide validation (Phase 2C)**: New `clm validate-slides` command and MCP tool.
  - Deterministic checks: format (cell header syntax), tags (invalid/unclosed pairs,
    workshop constraints), and DE/EN pairing (count and tag mismatches).
  - Review material extraction: code quality (print calls, leading comments), voiceover
    gaps, and completeness (concepts vs workshop exercises).
  - `--quick` mode for fast syntax-only validation (format + tags).
  - `clm.slides.validator` — `Finding`, `ReviewMaterial`, `ValidationResult`,
    `validate_file()`, `validate_quick()`, `validate_directory()`, `validate_course()`.
- **Course spec validation (Phase 2B)**: New `clm validate-spec` command and MCP tool.
  - Detects unresolved topics with near-match suggestions (`difflib.get_close_matches`).
  - Detects ambiguous topics (same ID in multiple modules).
  - Detects duplicate topic references across sections.
  - Detects missing dir-group paths and empty sections.
  - `--json` flag for structured output; MCP tool `validate_spec`.
  - `clm.slides.spec_validator` — `SpecFinding`, `SpecValidationResult`, `validate_spec()`.
- **Tag system verification (Phase 2A)**: Added tests confirming `completed` and `workshop`
  tag behavior in the build pipeline. Implementation was done in Phase 1A; this phase
  added 15 explicit tests for output processing (code-along/completed/speaker) and
  tag validation (no spurious warnings).
- **MCP server (Phase 1C)**: New `clm.mcp` package providing a Model Context Protocol server
  for AI-assisted slide authoring via stdio transport.
  - `clm mcp` — start the MCP server (requires `[mcp]` extra).
  - `--data-dir` option and `CLM_DATA_DIR` env var for data directory resolution.
  - Three MCP tools: `resolve_topic`, `search_slides`, `course_outline`.
  - In-memory caching for course objects (keyed by spec file mtime).
  - New optional extras: `[slides]` (rapidfuzz) and `[mcp]` (mcp SDK + slides).
- **Slide authoring tools (Phase 1A+1B)**: New `clm.slides` package and `clm.core.topic_resolver`
  module for AI-assisted slide authoring. Part of the MCP server and slide tooling feature.
  - `clm resolve-topic` — resolve a topic ID to its filesystem path, with exact match,
    glob patterns (`what_is_ml*`), course-spec scoping, and JSON output.
  - `clm search-slides` — fuzzy search across topic names and slide file titles using
    `rapidfuzz` (with substring fallback when not installed).
  - `clm outline --format json` — structured JSON course outline alongside existing
    Markdown format.
  - `clm.core.topic_resolver` — standalone topic resolution extracted from
    `Course._build_topic_map()`. Functions: `build_topic_map()`, `resolve_topic()`,
    `find_slide_files()`, `get_course_topic_ids()`.
  - `clm.slides.tags` — canonical tag definitions, single source of truth for all
    recognized cell tags. Adds `completed` (solution after `start`, replaces `alt`
    in that role) and `workshop` (structural metadata for workshop heading cells).
  - `clm.slides.search` — fuzzy search library with `search_slides()`.
  - `slide_id` and `for_slide` metadata parsing in `CellMetadata` and
    `parse_cell_header()` (backward-compatible — existing files without these
    fields parse normally).
  - `completed` tag added to `CodeAlongOutput.tags_to_delete_cell` (processed
    identically to `alt`: deleted in code-along, kept in completed/speaker).
  - `workshop` tag recognized but has no effect on output processing (structural
    metadata for tooling).

### Changed
- `jupyter_utils.py` tag constants now imported from `clm.slides.tags` instead of
  defined locally. Tag sets are `frozenset` (immutable).
- `Course._build_topic_map()` delegates to `clm.core.topic_resolver.build_topic_map()`.
- `get_slide_tag()` uses `next(iter(...))` instead of `frozenset.pop()`.

### Removed
- **Legacy backend module (Phase D)**: Deleted `backends_legacy.py` and its companion
  test file `test_backends.py`. All legacy functionality was superseded by the new
  backend package in Phases A–C. Cleaned up all source-code references; only historical
  documentation (handover, design doc, changelog) retains mentions.

### Added
- **Auphonic cloud backend (Phase C)**: New video-in/video-out processing backend that
  uploads raw recordings to the [Auphonic](https://auphonic.com) cloud service for
  speech-aware denoising, leveling, loudness normalization, and optional cut lists.
  Users opt in by setting `processing_backend = "auphonic"` and providing an API key.
  - `AuphonicClient` — httpx-based HTTP wrapper for the Auphonic Complex JSON API with
    streamed uploads (progress reporting), redirect-following downloads, and preset CRUD.
  - `AuphonicBackend` — implements the `ProcessingBackend` Protocol directly (not
    `AudioFirstBackend`); submit creates a production + uploads + starts processing,
    then the `JobManager` poller drives the job through `PROCESSING → DOWNLOADING →
    COMPLETED`.
  - `AuphonicConfig` — nested Pydantic config model with `api_key`, `preset`,
    `poll_timeout_minutes`, `request_cut_list`, `apply_cuts`, `base_url`, and upload
    tuning fields. Config validator rejects `processing_backend="auphonic"` without
    an API key at startup.
  - 6 new CLI subcommands: `clm recordings backends` (capability table),
    `clm recordings submit` (file → backend), `clm recordings jobs list/cancel`,
    `clm recordings auphonic preset list/sync`.
  - Web dashboard gains a "Processing Jobs" panel with HTMX progress bars and cancel
    buttons, plus `GET /jobs`, `POST /jobs/{id}/cancel`, and `GET /backends` endpoints.
  - SSE bridge thread-safety fix: cross-thread events now marshal via
    `loop.call_soon_threadsafe` instead of the non-thread-safe `put_nowait` pattern.
  - `respx` added to `[dev]` dependencies for httpx mock transport tests.
  - 53 new tests across 4 files (355 total in `tests/recordings/`).
  - New user guide: `docs/user-guide/recordings-auphonic.md`.
- **Recordings backend architecture (Phase B)**: Rewired the watcher, web app, and
  `make_backend` factory onto the new Protocol. `ExternalAudioFirstBackend` ported to
  `backends/external.py`. Runtime code no longer imports from `backends_legacy`.
  `RecordingsWatcher` is now backend-agnostic: constructor is
  `RecordingsWatcher(root_dir, job_manager, backend, ...)`. 24 new tests.
- **Recordings backend architecture (Phase A, internal)**: Foundational types and
  abstractions for the pluggable post-processing backend refactor. Adds the new
  `clm.recordings.workflow.jobs` module (`ProcessingJob`, `JobState`,
  `ProcessingOptions`, `BackendCapabilities`), a new `clm.recordings.workflow.backends`
  package (`base.ProcessingBackend` Protocol, `audio_first.AudioFirstBackend` Template
  Method ABC, `onnx.OnnxAudioFirstBackend`), and supporting infrastructure
  (`event_bus.EventBus`, `job_store.JsonFileJobStore` with atomic writes,
  `job_manager.JobManager` with lazy async poller and UPLOADING-on-restart recovery).
  78 new unit tests.
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
- **OBS integration** (`recordings/workflow/obs.py`): OBS WebSocket client wrapper using
  `obsws-python`. Manages request and event clients, provides `RecordStateChanged` event
  callbacks, and queries recording status and output directory.
- **Recording session manager** (`recordings/workflow/session.py`): Thread-safe state
  machine coordinating the recording workflow. Tracks armed topics, responds to OBS
  start/stop events, and auto-renames output files into the structured `to-process/`
  directory tree. States: `idle → armed → recording → renaming → idle`.
- **OBS config fields**: Added `obs_host`, `obs_port`, `obs_password` to `RecordingsConfig`
  for OBS WebSocket connection settings.
- **obsws-python dependency**: Added `obsws-python>=1.7.0` to the `[recordings]` optional
  dependency group.
- **Recordings web dashboard** (`recordings/web/`): HTMX-based web UI for the recording
  workflow, launched via `clm recordings serve`. Features: lecture selection with arm/disarm
  buttons, real-time status dashboard with SSE updates, pending pairs view, OBS connection
  indicator, file watcher controls. Uses Pico CSS (CDN) and HTMX with Jinja2 templates —
  no JavaScript framework.
- **`clm recordings serve`** CLI command: Starts the recordings dashboard on localhost,
  connects to OBS WebSocket, loads course structure from a spec file.
- **File watcher** (`recordings/workflow/watcher.py`): Watchdog-based filesystem watcher
  that monitors `to-process/` for new files and triggers assembly automatically. Features
  stability detection (file-size polling), thread-safe file claim tracking, and
  backend-aware behaviour (watches for `.wav` in external mode, raw video in ONNX mode).
  Start/stop controllable from the web dashboard.
- **Processing backends** (`recordings/workflow/backends.py`): Pluggable processing backend
  protocol with two implementations:
  - `ExternalBackend` — waits for an external tool (e.g. iZotope RX 11) to produce
    processed `.wav` audio alongside the raw video
  - `OnnxBackend` — processes locally: extracts audio, runs DeepFilterNet3 ONNX noise
    reduction, applies FFmpeg audio filters, writes processed `.wav`
- **Watcher config fields**: Added `processing_backend` (`"external"` or `"onnx"`),
  `stability_check_interval` (seconds between file-size polls), and
  `stability_check_count` (consecutive identical readings = stable) to `RecordingsConfig`.

### Changed
- **Default processing backend changed to `onnx`**: `RecordingsConfig.processing_backend`
  now defaults to `"onnx"` instead of `"external"`. Fresh installs work offline without
  cloud credentials; users opt into Auphonic or external backends explicitly. The `onnx`
  backend runs fully locally via DeepFilterNet3 + FFmpeg.
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
