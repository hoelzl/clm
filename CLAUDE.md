# CLM - AI Assistant Guide

This document provides essential information about the CLM (Coding-Academy Lecture Manager eXperimental) codebase for AI assistants.

## Project Overview

**CLM** is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats.

**Version**: 1.1.9 | **License**: MIT | **Python**: 3.11, 3.12, 3.13, 3.14

## Architecture

CLM uses a clean four-layer architecture with SQLite job queue and Direct/Docker worker execution:

```
clm/
├── core/           # Domain logic (Course, Section, Topic, CourseFile)
├── infrastructure/ # Job queue, worker management, backends, LLM client
├── workers/        # Worker implementations (notebook, plantuml, drawio)
└── cli/            # Command-line interface
```

## Installation

```bash
# Core package only
pip install -e .

# With all workers (for direct execution mode)
pip install -e ".[all-workers,dev]"

# Everything (recommended for development/testing)
pip install -e ".[all]"
```

**Optional Dependencies**:
- `[notebook]`: Notebook processing worker
- `[plantuml]`: PlantUML conversion worker
- `[drawio]`: Draw.io conversion worker
- `[all-workers]`: All worker dependencies
- `[recordings]`: Video recording management and audio processing (jinja2, python-multipart, obsws-python)
- `[summarize]`: LLM-powered course summaries and polish (openai)
- `[voiceover]`: Video-to-speaker-notes pipeline (faster-whisper, opencv-python, pytesseract, rapidfuzz)
- `[slides]`: Slide authoring tools with fuzzy search (rapidfuzz)
- `[mcp]`: MCP server for AI-assisted slide authoring (mcp, rapidfuzz)
- `[ml]`: ML/LLM packages (PyTorch, FastAI, LangChain, OpenAI, etc.)
- `[dev]`: Development tools (pytest, mypy, ruff)
- `[tui]`: TUI monitoring (`clm monitor`)
- `[web]`: Web dashboard (`clm serve`)
- `[all]`: All of the above (including voiceover)

## Key Commands

```bash
clm build <course.yaml>         # Build/convert course
clm build --watch <course.yaml> # Watch mode with auto-rebuild
clm status                      # Show system status
clm info [topic]                # Show version-accurate docs (spec-files, commands, migration)
clm summarize <spec> --audience client  # LLM-powered course summaries (requires [summarize])
clm workers list                # List registered workers
clm docker list                 # List available Docker images
clm docker pull                 # Pull Docker images from Hub
clm voiceover sync V S --lang de # Video → speaker notes (requires [voiceover])
clm polish slides.py --lang de  # LLM-polish speaker notes (requires [summarize])
clm recordings check            # Check recording deps (ffmpeg, onnxruntime)
clm recordings process F.mkv    # Process single recording through audio pipeline
clm recordings batch DIR        # Batch-process recordings in a directory
clm recordings status COURSE    # Show recording status for a course
clm recordings compare A B      # A/B audio comparison HTML page
clm recordings assemble DIR     # Mux paired video+audio, archive originals
clm recordings serve DIR        # Web dashboard for recording workflow
clm recordings backends         # List available backends and capabilities
clm recordings submit FILE      # Submit a file to the configured backend
clm recordings jobs list        # List active/recent processing jobs
clm recordings jobs cancel ID   # Cancel an in-flight job
clm recordings auphonic preset list  # List Auphonic presets
clm recordings auphonic preset sync  # Create/update managed preset
clm resolve-topic <id>          # Resolve topic ID to filesystem path
clm resolve-topic "what_is_ml*" # Glob pattern matching
clm search-slides "decorators"  # Fuzzy search across slides
clm outline <spec> --format json # Structured JSON course outline
clm validate-spec <spec>        # Validate course spec (topics, dir-groups)
clm validate-spec <spec> --json # JSON output for programmatic use
clm validate-slides <path>      # Validate slide files (format, tags, pairing)
clm validate-slides <path> --quick  # Fast syntax-only check
clm normalize-slides <path>     # Normalize slides (tag migration, interleaving)
clm normalize-slides <path> --dry-run  # Preview changes without modifying
clm normalize-slides <path> --operations tag_migration  # Specific operations
clm language-view <file> de     # Single-language view with line annotations
clm language-view <file> en --include-voiceover  # Include voiceover cells
clm mcp                         # MCP server for AI slide authoring (requires [mcp])
clm mcp --data-dir /path        # MCP server with explicit data directory
clm monitor                     # TUI monitoring (requires [tui])
clm serve                       # Web dashboard (requires [web])
```

## Testing

```bash
pytest                # Fast unit tests only (default)
pytest -m integration # Include integration tests
pytest -m e2e         # Include e2e tests
pytest -m ""          # Run ALL tests
```

**Test Markers**:
- `@pytest.mark.integration`: Real workers, requires external tools
- `@pytest.mark.e2e`: Full course conversion
- `@pytest.mark.requires_plantuml`: Requires PlantUML JAR and Java
- `@pytest.mark.requires_drawio`: Requires DrawIO executable

## Repository Structure

```
clm/
├── src/clm/                    # CLM package source (v1.1.9)
│   ├── core/                   # Domain logic
│   │   ├── course.py           # Main Course class
│   │   ├── course_file.py      # Base file class
│   │   ├── course_spec.py      # Course specification parsing
│   │   ├── topic_resolver.py   # Standalone topic resolution
│   │   ├── output_target.py    # Multiple output targets support
│   │   ├── course_files/       # File type handlers
│   │   └── operations/         # File operations
│   ├── infrastructure/         # Runtime support
│   │   ├── backends/           # SqliteBackend, LocalOpsBackend
│   │   ├── database/           # SQLite job queue
│   │   ├── llm/                # LLM client, prompts, summary cache
│   │   ├── messaging/          # Pydantic payloads/results
│   │   └── workers/            # Worker management
│   ├── workers/                # Worker implementations (v1.1.9)
│   │   ├── notebook/           # Notebook processing
│   │   ├── plantuml/           # PlantUML conversion
│   │   └── drawio/             # Draw.io conversion
│   ├── notebooks/              # Slide file utilities (parser, writer, polish)
│   ├── slides/                 # Slide authoring tools (tags, search, validation, language view)
│   ├── mcp/                    # MCP server for AI-assisted slide authoring
│   ├── voiceover/              # Video-to-speaker-notes pipeline
│   ├── recordings/             # Video recording management and audio processing
│   │   ├── processing/         # Audio pipeline (DeepFilterNet3 ONNX + FFmpeg)
│   │   ├── workflow/           # Recording workflow automation (naming, dirs, assembly, OBS)
│   │   ├── web/               # HTMX web dashboard (FastAPI + Jinja2 + SSE)
│   │   ├── state.py            # Per-course recording state (JSON CRUD)
│   │   └── git_info.py         # Git commit capture at recording time
│   └── cli/                    # Click-based CLI
│       └── info_topics/        # Markdown docs for `clm info` command
├── tests/                      # All tests
│   ├── core/                   # Core module tests
│   ├── infrastructure/         # Infrastructure tests
│   ├── cli/                    # CLI tests
│   ├── notebooks/              # Slide parser/writer/polish tests
│   ├── voiceover/              # Voiceover pipeline tests
│   ├── recordings/             # Recording module tests (162 tests)
│   ├── slides/                 # Slide tooling tests (tags, search, spec validation)
│   ├── mcp/                    # MCP server tool tests
│   └── e2e/                    # End-to-end tests
├── docs/                       # Documentation
│   ├── user-guide/             # User documentation
│   ├── developer-guide/        # Developer documentation
│   └── claude/                 # AI assistant working documents
└── pyproject.toml              # Package configuration
```

## Key Classes

### Core

- `Course` - Main course representation (`core/course.py`)
- `Section` - Course section (`core/section.py`)
- `Topic` - Individual topic (`core/topic.py`)
- `CourseFile` - Base class for all file types (`core/course_file.py`)
- `NotebookFile`, `PlantUmlFile`, `DrawioFile` - Concrete file handlers
- `OutputTarget` - Output directory configuration (`core/output_target.py`)

### Infrastructure

- `Backend` - Backend interface (`infrastructure/backend.py`)
- `SqliteBackend` - Primary backend (`infrastructure/backends/sqlite_backend.py`)
- `JobQueue` - Job queue operations (`infrastructure/database/job_queue.py`)
- `WorkerBase` - Abstract worker class (`infrastructure/workers/worker_base.py`)
- `PoolManager` - Worker pool management (`infrastructure/workers/pool_manager.py`)
- `ClmConfig` - Main config with pydantic-settings (`infrastructure/config.py`)
- `GitConfig` - Git remote template config (`infrastructure/config.py`)
- `run_subprocess` - Subprocess execution with retry (`infrastructure/services/subprocess_tools.py`)
- `RetryConfig` - Configurable retry behavior for subprocesses
- `LLMConfig` - LLM settings (model, API key, temperature) (`infrastructure/config.py`)
- `SummaryCache` - SQLite cache for LLM summaries (`infrastructure/llm/cache.py`)

### Workers

- `NotebookWorker` - Entry point: `python -m clm.workers.notebook`
- `PlantUmlWorker` - Entry point: `python -m clm.workers.plantuml`
- `DrawioWorker` - Entry point: `python -m clm.workers.drawio`

### Notebooks (Slide Utilities)

- `slide_parser` - Parse percent-format `.py` files into `SlideGroup` objects (`notebooks/slide_parser.py`). `CellMetadata` includes `slide_id` and `for_slide` fields.
- `slide_writer` - Insert/update notes cells in `.py` files (`notebooks/slide_writer.py`)
- `polish` - LLM-powered notes cleanup via openai SDK (`notebooks/polish.py`)

### Slides (Authoring Tools)

- `tags` - Canonical tag definitions: `ALL_VALID_TAGS`, `EXPECTED_CODE_TAGS`, `EXPECTED_MARKDOWN_TAGS`, plus per-category sets (`slides/tags.py`)
- `search_slides` - Fuzzy search across topic names and slide titles (`slides/search.py`)
- `validate_spec` - Course spec validation: unresolved/ambiguous topics, duplicates, missing dir-groups, near-match suggestions (`slides/spec_validator.py`)
- `validate_file`, `validate_directory`, `validate_course` - Slide file validation: format, tags, DE/EN pairing checks plus review material extraction (`slides/validator.py`)
- `normalize_file`, `normalize_directory`, `normalize_course` - Slide normalization: tag migration (`alt`→`completed`), workshop tag insertion, DE/EN interleaving with three-tier pairing strategy (`slides/normalizer.py`)
- `get_language_view` - Extract single-language view of bilingual slide files with `[original line N]` annotations (`slides/language_tools.py`)

### Topic Resolution

- `build_topic_map(slides_dir)` - Scan slides/ directory and return `dict[topic_id, list[TopicMatch]]` (`core/topic_resolver.py`)
- `resolve_topic(topic_id, slides_dir, *, course_topic_ids)` - Resolve topic ID or glob pattern to filesystem path(s) (`core/topic_resolver.py`)
- `find_slide_files(topic_path)` - Return all slide files within a topic (`core/topic_resolver.py`)
- `get_course_topic_ids(course_spec)` - Extract topic ID set from a CourseSpec (`core/topic_resolver.py`)

### MCP Server

- `create_server(data_dir)` - Create and configure the MCP server with stdio transport (`mcp/server.py`)
- `run_server(data_dir)` - Run the MCP server on stdio (`mcp/server.py`)
- `handle_resolve_topic()` - Async tool handler for topic resolution (`mcp/tools.py`)
- `handle_search_slides()` - Async tool handler for slide search (`mcp/tools.py`)
- `handle_course_outline()` - Async tool handler for course outline (`mcp/tools.py`)
- `handle_validate_spec()` - Async tool handler for spec validation (`mcp/tools.py`)
- `handle_validate_slides()` - Async tool handler for slide validation (`mcp/tools.py`)
- `handle_normalize_slides()` - Async tool handler for slide normalization (`mcp/tools.py`)
- `handle_get_language_view()` - Async tool handler for language view extraction (`mcp/tools.py`)

### Voiceover (Video Pipeline)

- `transcribe` - Whisper ASR with pluggable backend protocol (`voiceover/transcribe.py`)
- `keyframes` - Frame extraction + transition detection (`voiceover/keyframes.py`)
- `matcher` - OCR + fuzzy matching for slide identification (`voiceover/matcher.py`)
- `aligner` - Transcript-to-slide assignment with backtracking (`voiceover/aligner.py`)

### Recordings (Recording Management)

- `ProcessingPipeline` - 5-step audio pipeline: extract → DeepFilterNet3 ONNX → FFmpeg filters → AAC → mux (`recordings/processing/pipeline.py`)
- `run_onnx_denoise` - DeepFilterNet3 frame-by-frame ONNX inference (`recordings/processing/utils.py`)
- `download_onnx_model` - Auto-download and cache the ONNX model (`recordings/processing/utils.py`)
- `PipelineConfig`, `AudioFilterConfig` - Pydantic config for the processing pipeline (`recordings/processing/config.py`)
- `CourseRecordingState` - Per-course recording state with assign/reassign/update CRUD (`recordings/state.py`)
- `LectureState`, `RecordingPart` - Pydantic models for lecture/recording tracking (`recordings/state.py`)
- `find_video_files`, `process_batch`, `BatchResult` - Batch processing utilities (`recordings/processing/batch.py`)
- `get_git_info` - Capture git commit at recording time (`recordings/git_info.py`)
- `RecordingsConfig` - Recording settings in CLM's config system (`infrastructure/config.py`)
- `recording_relative_dir`, `raw_filename`, `final_filename`, `parse_raw_stem` - Naming convention helpers (`recordings/workflow/naming.py`)
- `ensure_root`, `validate_root`, `find_pending_pairs`, `PendingPair` - Directory structure management (`recordings/workflow/directories.py`)
- `assemble_one`, `assemble_all`, `mux_video_audio` - Assembly: mux video + audio, archive originals (`recordings/workflow/assembler.py`)
- `ObsClient`, `RecordingEvent` - OBS WebSocket client wrapper with event callbacks (`recordings/workflow/obs.py`)
- `RecordingSession`, `SessionState`, `ArmedTopic`, `SessionSnapshot` - Recording session state machine: arm/disarm topics, auto-rename on OBS stop (`recordings/workflow/session.py`)
- `ProcessingBackend`, `JobContext` - Backend Protocol abstracting at the "raw recording → final recording" level, plus the execution context supplied by `JobManager` (`recordings/workflow/backends/base.py`).
- `AudioFirstBackend` - Template Method ABC for audio-first backends that share the produce-audio → mux → archive flow (`recordings/workflow/backends/audio_first.py`)
- `OnnxAudioFirstBackend` - Local DeepFilterNet3 audio-first backend subclass (`recordings/workflow/backends/onnx.py`). Wired into the watcher + web app in Phase B.
- `ExternalAudioFirstBackend` - Audio-first backend for iZotope RX 11 / other external tool workflows (`recordings/workflow/backends/external.py`). Overrides `submit()` (not `_produce_audio`) because the `.wav` trigger file is already the finished audio; resolves the matching raw video in the same directory and hands the pair to the assembler.
- `make_backend(config, *, root_dir)` - Factory that constructs the backend selected by `config.processing_backend` (`recordings/workflow/backends/__init__.py`). Dispatches on `"onnx"` / `"external"` / `"auphonic"`.
- `AuphonicBackend` - Cloud video-in/video-out backend; creates Auphonic productions, uploads video, polls for completion, downloads the result (`recordings/workflow/backends/auphonic.py`). Implements `ProcessingBackend` Protocol directly (not `AudioFirstBackend`). Supports inline algorithms and managed-preset modes.
- `AuphonicClient` - httpx-based HTTP wrapper for the Auphonic Complex JSON API (`recordings/workflow/backends/auphonic_client.py`). Methods: `create_production`, `upload_input` (streamed with progress), `start_production`, `get_production`, `download` (follows redirects), `delete_production`, `create_preset`, `update_preset`, `list_presets`.
- `AuphonicConfig` - Pydantic nested config for the Auphonic backend (`infrastructure/config.py`). Fields: `api_key`, `preset`, `poll_timeout_minutes`, `request_cut_list`, `apply_cuts`, `base_url`, `upload_chunk_size`, `upload_retries`, `download_retries`.
- `ProcessingJob`, `JobState`, `ProcessingOptions`, `BackendCapabilities`, `TERMINAL_STATES` - Pydantic job lifecycle types; leaf module with no workflow-internal imports (`recordings/workflow/jobs.py`)
- `JobManager`, `_DefaultJobContext`, `JOB_EVENT_TOPIC`, `DEFAULT_POLL_INTERVAL_SECONDS` - Single mutator of `ProcessingJob` instances, owns the lazy async poller thread, rehydrates jobs on startup and fails interrupted UPLOADING jobs (`recordings/workflow/job_manager.py`)
- `JobStore`, `JsonFileJobStore`, `DEFAULT_JOBS_FILE` - Job persistence Protocol + single-file JSON implementation with atomic tmp+rename writes, default at `<recordings-root>/.clm/jobs.json` (`recordings/workflow/job_store.py`)
- `EventBus`, `EventHandler` - Thread-safe synchronous pub/sub, used by `JobManager` to publish job-lifecycle events without depending on FastAPI (`recordings/workflow/event_bus.py`)
- `RecordingsWatcher`, `WatcherState` - Watchdog-based file watcher (`recordings/workflow/watcher.py`). Backend-agnostic after Phase B: constructor is `RecordingsWatcher(root_dir, job_manager, backend, *, stability_interval, stability_checks, on_submitted, on_error)`. Dispatches accepted files to `job_manager.submit` on a background thread after stability detection. Exposes `backend_name` (delegates to `backend.capabilities.name`).
- `create_app` - Recordings web dashboard FastAPI app factory (`recordings/web/app.py`). Builds `JsonFileJobStore`, `EventBus`, backend (via `make_backend`), and `JobManager`, then constructs the watcher with them. Subscribes an `EventBus` handler that forwards job lifecycle events onto the SSE queue.

## Import Examples

```python
# Convenience imports
from clm import Course, Section, Topic, CourseFile, CourseSpec

# Explicit imports
from clm.core import Course, Section, Topic
from clm.core.course_files import NotebookFile, PlantUmlFile, DrawioFile
from clm.core.output_target import OutputTarget
from clm.infrastructure.backends import SqliteBackend
from clm.infrastructure.database import JobQueue
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `CLM_MAX_CONCURRENCY` | Max concurrent operations (default: 50) |
| `CLM_DATA_DIR` | Default data directory for MCP server (contains slides/, course-specs/) |
| `CLM_GIT__REMOTE_TEMPLATE` | Git remote URL template (e.g., `git@github.com-cam:Org/{repo}.git`) |
| `CLM_LLM__MODEL` | Default LLM model for summarize (default: `anthropic/claude-sonnet-4-6`) |
| `CLM_LLM__API_KEY` | API key for LLM provider |
| `CLM_LLM__API_BASE` | Custom API base URL for LLM |
| `CLM_RECORDINGS__OBS_OUTPUT_DIR` | Directory where OBS saves recordings |
| `CLM_RECORDINGS__ACTIVE_COURSE` | Currently active course ID for recording assignment |
| `CLM_RECORDINGS__AUTO_PROCESS` | Auto-process recordings when detected (default: false) |
| `CLM_RECORDINGS__ROOT_DIR` | Root directory for recording workflow (to-process/, final/, archive/) |
| `CLM_RECORDINGS__RAW_SUFFIX` | Suffix for raw recording filenames (default: `--RAW`) |
| `CLM_RECORDINGS__OBS_HOST` | OBS WebSocket host (default: `localhost`) |
| `CLM_RECORDINGS__OBS_PORT` | OBS WebSocket port (default: `4455`) |
| `CLM_RECORDINGS__OBS_PASSWORD` | OBS WebSocket password (default: empty) |
| `CLM_RECORDINGS__PROCESSING_BACKEND` | Processing backend: `onnx` (default), `external`, or `auphonic` |
| `CLM_RECORDINGS__STABILITY_CHECK_INTERVAL` | Seconds between file-size polls (default: `2.0`) |
| `CLM_RECORDINGS__STABILITY_CHECK_COUNT` | Consecutive identical polls = stable (default: `3`) |
| `CLM_RECORDINGS__AUPHONIC__API_KEY` | Auphonic API key (required when `processing_backend = "auphonic"`) |
| `CLM_RECORDINGS__AUPHONIC__PRESET` | Optional managed preset name (empty = inline algorithms) |
| `CLM_RECORDINGS__AUPHONIC__POLL_TIMEOUT_MINUTES` | Max minutes per Auphonic job (default: `120`) |
| `CLM_RECORDINGS__AUPHONIC__REQUEST_CUT_LIST` | Request cut list on every production (default: `false`) |
| `CLM_RECORDINGS__AUPHONIC__BASE_URL` | API base URL override (default: `https://auphonic.com`) |

## Recent Features

### Voiceover Pipeline (v1.1.9+)

The `clm voiceover` commands synchronize video recordings with slide files to auto-generate
speaker notes:

```bash
clm voiceover sync video.mp4 slides.py --lang de              # Full pipeline
clm voiceover sync video.mp4 slides.py --lang en --mode polished  # With LLM cleanup
clm voiceover transcribe video.mp4 --lang de                   # Transcript only
clm voiceover detect video.mp4                                  # Slide transitions only
clm voiceover identify video.mp4 slides.py --lang de           # Slide matching only
```

- Requires `[voiceover]` extra (`pip install -e ".[voiceover]"`)
- Uses faster-whisper for ASR, OpenCV for frame analysis, Tesseract for OCR, rapidfuzz for matching
- External tools: ffmpeg (audio extraction), Tesseract OCR
- Supports `--mode verbatim|polished`, `--slides-range`, `--dry-run`
- `--mode polished` also requires `[summarize]` extra (openai)

### Recording Management (v1.2.0+)

The `clm recordings` commands manage video recording workflows for educational courses:

```bash
clm recordings check                                    # Check ffmpeg/onnxruntime
clm recordings process raw.mkv                          # Process single recording
clm recordings process raw.mkv -o final.mp4             # Custom output path
clm recordings batch ~/Recordings -o ~/Processed        # Batch process directory
clm recordings batch ~/Recordings -r                    # Recursive search
clm recordings status python-basics                     # Show lecture recording status
clm recordings compare a.mp4 b.mp4 --label-a "iZotope" --label-b "DeepFilterNet"
clm recordings assemble ~/Recordings                    # Mux paired video+audio, archive
clm recordings assemble ~/Recordings --dry-run          # Preview pending pairs
clm recordings serve ~/Recordings                       # Start web dashboard
clm recordings serve ~/Recordings --spec-file course.xml # With lecture listing
clm recordings serve ~/Recordings --obs-host 192.168.1.5 # Custom OBS host
```

- Audio processing pipeline: extract audio → DeepFilterNet3 ONNX noise reduction → FFmpeg filters (highpass, compressor, two-pass EBU R128 loudness normalization) → AAC encode → mux
- Assembly workflow: three-tier directory structure (`to-process/`, `final/`, `archive/`), auto-detect paired `--RAW.mp4` + `--RAW.wav` files, mux via FFmpeg, archive originals
- Per-course recording state stored as JSON under `~/.config/clm/recordings/`
- Auto-assignment of recordings to lectures with `continue_current_lecture` mode
- Git commit capture at recording assignment time
- File watcher: monitors `to-process/` for new files, triggers assembly automatically, stability detection via file-size polling
- Pluggable processing backends: `onnx` (local DeepFilterNet3, default), `external` (wait for iZotope RX 11 or similar), or `auphonic` (cloud video-in/video-out with speech-aware denoising, cut lists, filler removal)
- Configuration integrates into CLM's TOML config under `[recordings]`
- External tools required: `ffmpeg`; ONNX model auto-downloaded on first use
- Cross-platform: Windows and Linux

### LLM Polish (v1.1.9+)

The `clm polish` command cleans up existing speaker notes using an LLM:

```bash
clm polish slides.py --lang de                          # Polish all notes
clm polish slides.py --lang en --slides-range 5-10      # Polish range
clm polish slides.py --lang de --dry-run                # Preview without writing
clm polish slides.py --lang de --model openai/gpt-4o    # Custom model
```

- Requires `[summarize]` extra (`pip install -e ".[summarize]"`)
- Removes filler words, fixes grammar, preserves technical terms
- Works standalone or as part of voiceover pipeline (`--mode polished`)

### LLM-Powered Course Summaries (v1.1.9+)

The `clm summarize` command generates markdown summaries of course content using LLMs:

```bash
clm summarize course.xml --audience client --dry-run    # Preview without LLM calls
clm summarize course.xml --audience trainer -o summary.md
clm summarize course.xml --audience client --style bullets
clm summarize course.xml --audience trainer --model openai/gpt-4o
```

- Requires `[summarize]` extra (`pip install -e ".[summarize]"`)
- Uses the openai SDK for LLM access (works with any OpenAI-compatible API)
- Per-notebook content caching (SHA-256 based)
- Supports `--audience client|trainer`, `--style prose|bullets`, `--granularity notebook|section`
- Configurable via `CLM_LLM__MODEL`, `CLM_LLM__API_KEY`, `CLM_LLM__API_BASE` env vars

### Git Amend and Force Push (v1.1.9+)

The `clm git` commands support `--amend` and `--force-with-lease` for iterative
workflows (e.g., tweaking slides during dry-runs):

```bash
clm git commit <spec> --amend              # Amend, keep previous message
clm git commit <spec> --amend -m "new msg" # Amend with new message
clm git push <spec> --force-with-lease     # Safe force push
clm git sync <spec> --amend               # Amend + force push (one step)
clm git sync <spec> --force-with-lease -m "msg"  # Normal commit + force push
```

- `-m` is optional when `--amend` is used (reuses previous message via `--no-edit`)
- `--amend` on `sync` implies `--force-with-lease` and skips the "remote is ahead" check
- Without `-m` or `--amend`, `commit` and `sync` produce a usage error

### Git Remote URL Template (v1.1.9+)

The git remote URL can be customized via a template with placeholders:

```bash
# Via environment variable or .env file
CLM_GIT__REMOTE_TEMPLATE="git@github.com-cam:Coding-Academy-Munich/{repo}.git"
```

Available placeholders: `{repository_base}`, `{repo}`, `{slug}`, `{lang}`, `{suffix}`.
Can also be set in TOML config (`[git] remote_template`) or course spec XML (`<remote-template>`).

### Markdown Notebook Files and Project Documents (v1.1.9+)

Files with `project_` prefix (e.g., `project_setup.md`) are recognized as notebook files
alongside the existing `slides_` and `topic_` prefixes. This enables markdown-based project
documents to be processed through the full notebook pipeline (jupytext → nbconvert → HTML/ipynb).

Markdown (`.md`) files support both jupytext markdown variants — the format is auto-detected
from the file's YAML header:

- **Standard markdown** (default): code cells are fenced code blocks
- **MyST markdown**: code cells use `{code-cell}` directive syntax

````markdown
<!-- Standard markdown -->
```python
print("Hello")
```

<!-- MyST markdown -->
```{code-cell} python
print("Hello")
```
````

**Programming language resolution for `.md` files** follows a priority chain:
1. `prog-lang` attribute on `<topic>` element (most specific)
2. Course-level `<prog-lang>` element
3. Default: `python`

```xml
<!-- Topic-level override -->
<topic prog-lang="java">capstone_project/phase_01</topic>

<!-- Course-level applies to all .md files without a topic override -->
<prog-lang>python</prog-lang>
```

For non-`.md` files (`.py`, `.cpp`, etc.), the file extension determines the language as before.
The topic-level `prog-lang` attribute can still override even for those files.

### Automatic .env File Loading (v1.1.1)

The `build` command automatically walks up the directory tree to find a `.env` file
and loads it before spawning workers. This eliminates the need to manually set
environment variables in the shell.

### `clm info` Command (v1.0.9)

Version-accurate documentation for agents and users. Topics live in `src/clm/cli/info_topics/*.md`
and use `{version}` placeholders replaced at output time.

### Multiple Output Targets (v0.4.x)

Courses can define multiple output directories with selective content generation:

```xml
<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <kinds><kind>code-along</kind></kinds>
        <formats><format>html</format><format>notebook</format></formats>
    </output-target>
    <output-target name="solutions">
        <path>./output/solutions</path>
        <kinds><kind>completed</kind></kinds>
    </output-target>
</output-targets>
```

### Shared Image Storage

Images are now stored once and symlinked/copied to output directories, eliminating duplication.

### Output Kinds and Formats

| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared |
| `completed` | Notebooks with all solutions |
| `speaker` | Notebooks with speaker notes |

| Format | Description |
|--------|-------------|
| `html` | HTML slides |
| `notebook` | Jupyter notebook (.ipynb) |
| `code` | Extracted source code |

## Worker Execution Modes

1. **Direct Mode** (Default): Workers run as subprocesses, faster for development
2. **Docker Mode**: Workers run in containers, better isolation

## Database Architecture

Two separate SQLite databases:
- `clm_jobs.db` - Job queue (jobs, workers, events, results_cache)
- `clm_cache.db` - Cache (processed_files with pickled results)

Uses DELETE journal mode (not WAL) for cross-platform Docker compatibility.

## Code Conventions

- **Type hints**: Required for all public APIs
- **Async/await**: Preferred for I/O operations
- **Dataclasses**: Use attrs `@define` for internal structures, Pydantic for messages
- **Logging**: Use `logging.getLogger(__name__)`

## Documentation

| Document | Location | Purpose |
|----------|----------|---------|
| User Guide | `docs/user-guide/` | End-user documentation |
| Developer Guide | `docs/developer-guide/` | Development documentation |
| Spec File Reference | `docs/user-guide/spec-file-reference.md` | Course XML format |
| Architecture | `docs/developer-guide/architecture.md` | System design |
| Known Issues | `docs/claude/TODO.md` | Bugs and planned improvements |

### Agent-Facing Info Topics (`clm info`)

The `clm info <topic>` command outputs version-accurate markdown documentation
that agents in downstream course repositories use to understand CLM's current
behavior. The content lives in `src/clm/cli/info_topics/*.md`.

**When you change CLM behavior that affects how course repositories are structured
or built, you MUST update the corresponding info topic.** Examples:

- Spec file format changes (new/renamed/removed elements, attribute changes) → update `spec-files.md`
- CLI command changes (new commands, changed options, removed flags) → update `commands.md`
- Breaking changes or migration steps → update `migration.md`

Downstream agents run `clm info spec-files` to learn the current spec format
before editing course XML files. If the info topics are stale, those agents will
produce incorrect output. Keeping these files current is as important as updating
tests.

The files use `{version}` placeholders that are replaced with the installed
CLM version at output time — do not hardcode version numbers.

## Versioning

Uses [bump-my-version](https://github.com/callowayproject/bump-my-version). Config in `[tool.bumpversion]` in `pyproject.toml`.

```bash
bump-my-version bump patch   # e.g., 1.2.3 → 1.2.4
bump-my-version bump minor   # e.g., 1.2.3 → 1.3.0
bump-my-version bump major   # e.g., 1.2.3 → 2.0.0
```

Automatically updates version in 7 files, creates a commit, and tags.

## Releasing

**IMPORTANT**: Before publishing a release, you **MUST** update documentation, run the local test suite, and verify CI passes.

### Step 1: Update documentation

Before bumping the version, ensure all documentation reflects the current state of the code:

1. **CHANGELOG.md** — Add an entry for the new version with a summary of changes
2. **README.md** — Update if there are new features, changed commands, or altered setup instructions
3. **CLAUDE.md** — Update if there are new/changed commands, environment variables, classes, architecture, or conventions
4. **`clm info` topics** (`src/clm/cli/info_topics/*.md`) — Update if spec file format, CLI commands, or migration steps have changed (see "Agent-Facing Info Topics" section above)
5. **`docs/`** — Update relevant user-guide or developer-guide pages for any user-facing changes

All documentation updates should be committed before the version bump so they are included in the release commit.

### Step 2: Run local tests (unit + integration + e2e, excluding Docker)

Docker-marked tests require CI-built images (`lite-test`, `test` tags) that are not
available locally. Run local tests excluding Docker tests:

```bash
uv run pytest -m "not docker"
```

All non-Docker tests must pass before proceeding.

### Step 3: Bump version, build, and push to CI

```bash
# Bump the version (creates commit + tag)
uv run bump-my-version bump patch  # or minor/major

# Build the package
uv build

# Push commit and tags to trigger CI
git push && git push --tags
```

### Step 4: Verify CI passes

Wait for the GitHub Actions CI pipeline to complete. The CI runs the full test suite
including Docker tests (it builds `lite-test` images from scratch).

```bash
# Check CI status
gh run list --limit 5
gh run view <run-id>
```

### Step 5: Publish to PyPI (only after CI passes)

```bash
uv publish
```

**Rules for Claude Code**:

- Never publish a release without updating documentation first
- Never publish a release if any local test fails
- Never publish if CI has not passed for the tagged commit
- Use `pytest -m "not docker"` for local testing (Docker tests are validated in CI)
- If tests fail, fix the issues first and re-run before retrying
- The `uv build` output goes to `dist/` (sdist + wheel)

## Git Workflow

- Branch prefix: `claude/` for AI-generated branches
- **Pre-commit hooks**: Install with `uv run pre-commit install` (runs ruff and mypy automatically)
- Manual checks: `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`
- Run all tests before pushing: `pytest -m ""`

## Troubleshooting

### Common Issues

1. **Tests failing**: Check external tools (PlantUML, Draw.io), enable logging with `CLM_ENABLE_TEST_LOGGING=1`
2. **Worker issues**: Run `python diagnose_workers.py`
3. **Import errors**: Ensure `pip install -e .` in correct environment

### Known Issues

See `docs/claude/TODO.md` for current bugs and planned improvements.

---

**Repository**: https://github.com/hoelzl/clm/ | **Issues**: https://github.com/hoelzl/clm/issues

**Last Updated**: 2026-04-05
