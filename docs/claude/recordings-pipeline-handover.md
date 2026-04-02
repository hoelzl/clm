# Recordings Pipeline — Handover Document

## Branch: `feature/recordings-integration`

---

## Completed Work

### Part 1: Dependency Fixes [DONE]

**Commit `6a634fe`** — Supply-chain safety: `UV_EXCLUDE_NEWER` in pyproject.toml, PyTorch cu130 exemption, refreshed lock file.

### Part 2: Audio Denoising Research [DONE]

Test app at `C:\Users\tc\Programming\Python\Tests\AudioDenoise\`. Compared 6 approaches; DeepFilterNet3 ONNX won (best quality, pure-Python dependencies, works on 3.11-3.14).

**Key findings**:
- ONNX model: `yuyun2000/SpeechDenoiser` — 15.4 MB streaming model, cached at `~/.cache/clm/models/deepfilter3_streaming.onnx`
- Interface: frame-by-frame `input_frame[480]` + `states[45304]` + `atten_lim_db[1]`
- For production recordings, iZotope RX 11 on Windows remains far superior to all automated approaches

### Part 3A: Replace deepfilternet with ONNX [DONE]

**Commit `a4e4d65`** — Replaced deepFilter CLI subprocess with ONNX Runtime inference. All 52 tests pass. Changes: `utils.py` (ONNX download + inference), `pipeline.py` (ONNX step), `config.py` + `infrastructure/config.py` (renamed `deepfilter_atten_lim` to `denoise_atten_lim`), `pyproject.toml` (onnxruntime/soundfile/numpy deps).

---

## Part 3B: Recording Workflow Automation [TODO]

**Goal**: Automate the recording -> processing -> assembly workflow, integrating with OBS Studio and optionally iZotope RX 11 on Windows.

### Workflow Overview

```
Phase 1: RECORDING (automated naming)
  User selects lecture in CLM web UI -> OBS records -> file auto-renamed to structured name

Phase 2: AUDIO PROCESSING (manual or automated)
  Option A (Windows/quality): Drag files into RX 11 Batch Processor -> wait -> .wav output
  Option B (cross-platform): ONNX pipeline processes automatically

Phase 3: ASSEMBLY (fully automated)
  File watcher detects processed .wav -> FFmpeg muxes video + processed audio -> final output
```

### Directory Layout

Three hierarchies under a configurable recordings root:

```
<recordings-root>/                  # Configurable, often on a different drive (videos are large)
+-- to-process/                     # Raw recordings land here; RX 11 also writes .wav here
|   +-- <course-slug>/
|       +-- <section-name>/
|           +-- <topic-name>--RAW.mp4   # Raw OBS recording
|           +-- <topic-name>--RAW.wav   # RX 11 processed audio (appears alongside .mp4)
+-- final/                          # Muxed output (watcher writes here)
|   +-- <course-slug>/
|       +-- <section-name>/
|           +-- <topic-name>.mp4        # Final video with processed audio
+-- archive/                        # RAW files moved here after successful assembly
    +-- <course-slug>/
        +-- <section-name>/
            +-- <topic-name>--RAW.mp4
            +-- <topic-name>--RAW.wav
```

### Filename Convention

```
<course-slug>/<section-name>/<topic-name>--RAW.mp4     # Raw recording
<course-slug>/<section-name>/<topic-name>--RAW.wav     # Processed audio (from RX 11 or ONNX)
<course-slug>/<section-name>/<topic-name>.mp4           # Final muxed output
```

Section and topic names are sanitized from the CLM course spec file. The `--RAW` suffix signals unprocessed; final output drops it.

### FFmpeg Mux Command

```bash
ffmpeg -i <input_video> -i <input_audio> -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 <output_video>
```

(From the user's existing `replace-audio` PowerShell function.)

---

## Sub-Phase Breakdown

### Sub-Phase 3B-1: Foundation — Directory Management and Assembly [DONE]

**Scope**: Build the directory structure manager, filename convention helpers, and the core assembly logic (detect paired .mp4/.wav, mux, archive). No web UI or OBS integration yet — everything is testable via unit tests and a CLI command.

**Files to create**:
- `src/clm/recordings/workflow/__init__.py` — Subpackage for workflow automation
- `src/clm/recordings/workflow/naming.py` — Filename convention: sanitize topic/section names, build structured paths, parse `--RAW` suffix, derive final output path
- `src/clm/recordings/workflow/directories.py` — Directory manager: initialize `to-process/`, `final/`, `archive/` under root; validate structure; list pending pairs
- `src/clm/recordings/workflow/assembler.py` — Assembly logic: find paired `--RAW.mp4` + `--RAW.wav`; mux via FFmpeg (reuse `run_subprocess` from `processing/utils.py`); move originals to `archive/`; report results
- `tests/recordings/test_naming.py` — Unit tests for naming helpers
- `tests/recordings/test_directories.py` — Unit tests for directory manager
- `tests/recordings/test_assembler.py` — Unit tests for assembly logic (with mocked FFmpeg)

**Files to modify**:
- `src/clm/infrastructure/config.py` — Extend `RecordingsConfig` with new fields: `root_dir` (recordings root), `raw_suffix` (default `"--RAW"`), `filename_separator` (default `"--"`)
- `src/clm/cli/commands/recordings.py` — Add `clm recordings assemble <root-dir>` command: scan for ready pairs, mux, archive
- `pyproject.toml` — No new dependencies expected (uses existing FFmpeg + pathlib)

**Key design decisions**:
- Reuse `run_subprocess` and `find_ffmpeg` from `recordings/processing/utils.py` — do NOT duplicate
- Assembly mux command is simpler than the full processing pipeline (just video copy + audio encode, no denoise/filters)
- The assembler should be usable both as a one-shot CLI command and as a callable function (for the watcher in 3B-3)
- Naming helpers must handle Windows path limitations (no `:`, `?`, etc.) and keep names filesystem-safe

**Acceptance criteria**:
1. `recordings/workflow/naming.py` can sanitize arbitrary topic/section names and produce valid paths
2. `recordings/workflow/directories.py` can create the three-tier directory structure under any root
3. `recordings/workflow/assembler.py` can: (a) find paired .mp4/.wav files, (b) mux them via FFmpeg, (c) move originals to archive
4. `clm recordings assemble <root>` works end-to-end from the CLI
5. All new code has tests; existing 52 tests still pass
6. `ruff check` and `mypy` pass

**Testing approach**:
- Unit tests with `tmp_path` fixtures for directory and naming logic
- Assembly tests mock `run_subprocess` to avoid requiring real FFmpeg
- One integration test (marked `@pytest.mark.integration`) that runs real FFmpeg if available

---

### Sub-Phase 3B-2: OBS Integration [DONE]

**Scope**: Connect to OBS Studio via WebSocket v5, detect recording start/stop events, and auto-rename the OBS timestamp-named file to the structured name based on the currently "armed" topic.

**Depends on**: Sub-Phase 3B-1 (naming helpers)

**Files to create**:
- `src/clm/recordings/workflow/obs.py` — OBS WebSocket client: connect/disconnect, event subscriptions (RecordStateChanged), get current recording status, rename output file after recording stops
- `src/clm/recordings/workflow/session.py` — Recording session manager: arm/disarm a topic for recording, track current recording state (idle/recording/post-processing), derive the target filename from armed topic using naming helpers
- `tests/recordings/test_obs.py` — Unit tests with mocked WebSocket
- `tests/recordings/test_session.py` — Unit tests for session state machine

**Files to modify**:
- `src/clm/infrastructure/config.py` — Add OBS fields to `RecordingsConfig`: `obs_host` (default `"localhost"`), `obs_port` (default `4455`), `obs_password` (default `""`)
- `pyproject.toml` — Add `obsws-python>=1.7.0` to `[recordings]` extra

**Key design decisions**:
- Use `obsws-python` library (official OBS WebSocket v5 Python client)
- Session manager is a state machine: `idle -> armed -> recording -> renaming -> idle`
- OBS client should handle: OBS not running, connection drops, reconnection
- File rename happens after OBS writes the file (poll for file existence + stability before rename)
- The session manager should be UI-agnostic — usable from both CLI and web UI

**Acceptance criteria**:
1. OBS client can connect, subscribe to events, and detect recording start/stop
2. Session manager tracks armed topic and transitions through states correctly
3. After recording stops, the OBS output file is renamed to the structured name in `to-process/`
4. Handles edge cases: OBS not running, connection lost mid-recording, no topic armed
5. All new tests pass; existing tests unaffected

**Testing approach**:
- Mock `obsws-python` for unit tests (no real OBS needed)
- State machine tests cover all transitions and edge cases
- Integration tests (marked) require a running OBS instance — optional

---

### Sub-Phase 3B-3: Web UI — Lecture Selection and Dashboard [DONE]

**Scope**: Build an HTMX-based web UI for the recording workflow. Two views: (1) lecture selection to arm topics for recording, (2) dashboard showing recording/processing/assembly status with real-time updates via SSE.

**Depends on**: Sub-Phase 3B-1 (directories, naming), Sub-Phase 3B-2 (OBS session)

**Existing infrastructure to build on**:
- `src/clm/web/app.py` — FastAPI app factory with CORS, WebSocket, static file serving
- `src/clm/web/api/routes.py` — REST API router at `/api/`
- `src/clm/cli/commands/monitoring.py` — `clm serve` command (launches uvicorn)
- Jinja2 already in `[recordings]` dependencies

**Files to create**:
- `src/clm/recordings/web/__init__.py` — Recordings web subpackage
- `src/clm/recordings/web/app.py` — Recordings FastAPI app (separate from the main CLM dashboard): HTMX routes, SSE endpoint, Jinja2 template rendering
- `src/clm/recordings/web/routes.py` — Routes: GET `/` (dashboard), GET `/lectures` (lecture list from spec), POST `/arm/{topic_id}` (arm topic), POST `/disarm` (disarm), GET `/status` (JSON status), GET `/events` (SSE stream)
- `src/clm/recordings/web/templates/` — Jinja2 HTML templates: `base.html` (layout + HTMX script), `dashboard.html` (recording status, processing queue, assembly queue, finished list), `lectures.html` (course structure tree with arm buttons)
- `src/clm/recordings/web/static/` — Minimal CSS (can use a classless CSS framework like Pico CSS)
- `src/clm/cli/commands/recordings.py` — Add `clm recordings serve` subcommand
- `tests/recordings/test_web.py` — Test routes with FastAPI TestClient

**Files to modify**:
- `pyproject.toml` — Ensure `uvicorn` is in `[recordings]` extra (or rely on it being a core dep already)

**Key design decisions**:
- This is a **separate** FastAPI app from the main `clm serve` dashboard — recordings have different lifecycle and dependencies
- Use HTMX for dynamic updates (no JavaScript framework needed): `hx-get`, `hx-post`, `hx-trigger="sse:status"` for real-time
- SSE (Server-Sent Events) for push updates instead of WebSocket — simpler for one-way server->client updates
- Course structure loaded from CLM spec file (`CourseSpec.from_spec_file()`) at startup
- The web UI calls the session manager (3B-2) and directory manager (3B-1) — it is a thin presentation layer

**Acceptance criteria**:
1. `clm recordings serve` starts a web server on localhost
2. Dashboard shows: armed topic, OBS connection status, pending/processing/finished recordings
3. Clicking a topic arms it for recording; disarm button works
4. SSE endpoint pushes status updates when state changes
5. UI works in a modern browser without JavaScript frameworks (HTMX only)
6. All routes tested with FastAPI TestClient

**Testing approach**:
- FastAPI TestClient for route testing (no browser needed)
- Mock session manager and directory manager
- Manual browser testing for HTMX interactions (not automated)

---

### Sub-Phase 3B-4: File Watcher and Pluggable Processing Backend [DONE]

**Scope**: Add a filesystem watcher that monitors `to-process/` for new files and triggers assembly automatically. Add a pluggable processing backend so users can choose between "wait for external tool" (RX 11) and "process locally" (ONNX pipeline).

**Depends on**: Sub-Phase 3B-1 (assembler), Sub-Phase 3B-3 (SSE for progress reporting)

**Files to create**:
- `src/clm/recordings/workflow/watcher.py` — Filesystem watcher using `watchdog`: monitor `to-process/` for new `.wav` files; stability detection (wait for file size to stop changing); trigger assembly when `.mp4` + `.wav` pair is complete; report progress via callback
- `src/clm/recordings/workflow/backends.py` — Processing backend protocol + implementations:
  - `ProcessingBackend` protocol: `process(input_video: Path) -> Path` (returns path to processed audio)
  - `ExternalBackend` — "Wait for external tool" mode: the watcher simply waits for the `.wav` to appear (RX 11 writes it)
  - `OnnxBackend` — "Process locally" mode: extract audio, run ONNX denoise + FFmpeg filters, write `.wav` (reuse `ProcessingPipeline` from `recordings/processing/pipeline.py`)
- `tests/recordings/test_watcher.py` — Unit tests for watcher with mocked filesystem events
- `tests/recordings/test_backends.py` — Unit tests for both backends

**Files to modify**:
- `src/clm/infrastructure/config.py` — Add `processing_backend` field to `RecordingsConfig`: `"external"` (default, RX 11) or `"onnx"` (local)
- `src/clm/infrastructure/config.py` — Add stability check config: `stability_check_interval` (default 2 sec), `stability_check_count` (default 3)
- `src/clm/recordings/web/routes.py` — Add SSE events for watcher progress; add UI toggle for processing backend
- `src/clm/recordings/web/templates/dashboard.html` — Show watcher status, processing queue, backend selector

**Key design decisions**:
- `watchdog` is already a core dependency (used for `clm build --watch`)
- Watcher runs in a background thread, communicates via callbacks
- Stability detection: poll file size every N seconds, require M consecutive identical polls before considering the file stable
- ExternalBackend is just "do nothing and wait" — the watcher detects the `.wav` appearing. OnnxBackend actively processes.
- The watcher should be startable/stoppable from the web UI and work headlessly from CLI

**Acceptance criteria**:
1. Watcher detects new `.wav` files in `to-process/` and triggers assembly
2. Stability detection prevents acting on partially-written files
3. ExternalBackend mode: watcher waits for `.wav` to appear alongside `.mp4`, then assembles
4. OnnxBackend mode: watcher detects new `.mp4`, runs ONNX pipeline to produce `.wav`, then assembles
5. Processing backend is selectable via config and via web UI toggle
6. Progress reported via SSE to the dashboard
7. All new tests pass; full suite green

**Testing approach**:
- Watcher tests use `tmp_path` + direct `watchdog` event simulation (no real filesystem polling in unit tests)
- Backend tests mock the processing pipeline
- Integration test (marked) runs the watcher against a real temp directory with a small test file

---

## Key Files Reference

### Existing recordings module (do not rewrite, build on these)

| File | Purpose |
|------|---------|
| `src/clm/recordings/__init__.py` | Package init |
| `src/clm/recordings/state.py` | Per-course recording state: `CourseRecordingState`, `LectureState`, `RecordingPart` — JSON CRUD, assign/reassign/update, progress tracking |
| `src/clm/recordings/git_info.py` | Git commit capture at recording time |
| `src/clm/recordings/processing/pipeline.py` | 5-step audio pipeline: extract -> ONNX denoise -> FFmpeg filters -> AAC -> mux |
| `src/clm/recordings/processing/config.py` | `PipelineConfig`, `AudioFilterConfig` |
| `src/clm/recordings/processing/utils.py` | `find_ffmpeg`, `find_ffprobe`, `run_subprocess`, `download_onnx_model`, `run_onnx_denoise`, `check_dependencies` |
| `src/clm/recordings/processing/batch.py` | `find_video_files`, `process_batch`, `BatchResult` |
| `src/clm/recordings/processing/compare.py` | A/B audio comparison HTML generator |
| `src/clm/cli/commands/recordings.py` | CLI: `check`, `process`, `batch`, `status`, `compare`, `assemble`, `serve` |
| `src/clm/infrastructure/config.py` | `RecordingsConfig` (incl. `root_dir`, `raw_suffix`, `processing_backend`, `stability_check_interval`, `stability_check_count`), `RecordingsCourseConfig`, `RecordingsProcessingConfig` |
| `src/clm/recordings/workflow/naming.py` | Filename convention: `raw_filename`, `final_filename`, `parse_raw_stem`, `recording_relative_dir` — delegates to `sanitize_file_name` |
| `src/clm/recordings/workflow/directories.py` | `ensure_root`, `validate_root`, `find_pending_pairs`, `PendingPair` — three-tier dir management |
| `src/clm/recordings/workflow/assembler.py` | `mux_video_audio`, `assemble_one`, `assemble_all`, `AssemblyResult`, `AssemblyBatchResult` |
| `src/clm/recordings/workflow/obs.py` | OBS WebSocket client: `ObsClient`, `RecordingEvent` — connect/disconnect, event callbacks, recording status queries |
| `src/clm/recordings/workflow/session.py` | Recording session state machine: `RecordingSession`, `SessionState`, `ArmedTopic`, `SessionSnapshot` — arm/disarm, OBS event handling, auto-rename |
| `src/clm/recordings/workflow/backends.py` | `ProcessingBackend` protocol, `ExternalBackend` (no-op), `OnnxBackend` (extract + denoise + filter) |
| `src/clm/recordings/workflow/watcher.py` | `RecordingsWatcher`: watchdog-based file watcher with stability detection, backend-aware event handling, `WatcherState` for thread-safe claims |
| `src/clm/recordings/web/app.py` | Recordings FastAPI app factory: OBS lifecycle, watcher lifecycle, SSE queue, Jinja2 templates |
| `src/clm/recordings/web/routes.py` | Dashboard, lectures, arm/disarm, watcher start/stop, status JSON/partial, SSE stream, pending pairs |
| `src/clm/recordings/web/templates/` | Jinja2 templates: `base.html` (Pico CSS + HTMX), `dashboard.html`, `lectures.html`, `partials/status.html`, `partials/pairs.html` |

### Existing web infrastructure (extend for recordings UI)

| File | Purpose |
|------|---------|
| `src/clm/web/app.py` | FastAPI app factory with CORS, WebSocket, static files |
| `src/clm/web/api/routes.py` | REST API: `/api/health`, `/api/status`, `/api/workers`, `/api/jobs` |
| `src/clm/web/api/websocket.py` | WebSocket endpoint with subscription management |
| `src/clm/web/models.py` | Pydantic response models |
| `src/clm/web/services/monitor_service.py` | Service layer for build monitoring |
| `src/clm/cli/commands/monitoring.py` | `clm serve` command |

### Test files

| File | Tests |
|------|-------|
| `tests/recordings/test_state.py` | Recording state CRUD, assign/reassign/update |
| `tests/recordings/test_processing_pipeline.py` | Pipeline steps with mocked FFmpeg/ONNX |
| `tests/recordings/test_processing_config.py` | Config defaults and overrides |
| `tests/recordings/test_batch.py` | Batch processing |
| `tests/recordings/test_cli_recordings.py` | CLI command invocations |
| `tests/recordings/test_git_info.py` | Git info capture |
| `tests/recordings/test_naming.py` | Naming convention helpers (17 tests) |
| `tests/recordings/test_directories.py` | Directory management and pair scanning (21 tests) |
| `tests/recordings/test_assembler.py` | Assembly mux + archive (11 unit + 1 integration) |
| `tests/recordings/test_obs.py` | OBS client connection, queries, event dispatching (16 tests) |
| `tests/recordings/test_session.py` | Session state machine: arm/disarm, OBS events, rename, callbacks (28 tests) |
| `tests/recordings/test_backends.py` | Processing backend protocol, ExternalBackend, OnnxBackend pipeline steps/config/errors (9 tests) |
| `tests/recordings/test_watcher.py` | WatcherState, init/start/stop, stability, external/onnx mode, video matching, live events (35 tests) |
| `tests/recordings/test_web.py` | Web dashboard routes: dashboard, lectures, arm/disarm, status, SSE, pairs, watcher controls (22 tests) |

---

## Configuration (target state after all sub-phases)

```toml
[recordings]
root_dir = "D:/Recordings"                # Root dir (to-process/, final/, archive/ under this)
raw_suffix = "--RAW"                      # Suffix for unprocessed files
processing_backend = "external"           # "external" (RX 11) or "onnx" (local)
obs_host = "localhost"
obs_port = 4455
obs_password = ""
stability_check_interval = 2              # Seconds between file size polls
stability_check_count = 3                 # Consecutive identical polls = stable
```

---

## Dependencies to Add (across all sub-phases)

| Package | Sub-Phase | Purpose |
|---------|-----------|---------|
| `obsws-python>=1.7.0` | 3B-2 | OBS WebSocket v5 client (added) |

All other dependencies (`fastapi`, `uvicorn`, `jinja2`, `watchdog`, `onnxruntime`, `soundfile`, `numpy`) are already present.

---

## Notes for Future Sessions

- The `[recordings]` extra already includes `jinja2` and `python-multipart` — no need to re-add for the web UI
- `watchdog` is a core dependency (not under `[recordings]`) — used by `clm build --watch`
- The existing `clm serve` dashboard is for build monitoring, completely separate from the recordings workflow
- The recordings web app should be a separate FastAPI app with its own `clm recordings serve` command
- `run_subprocess` in `recordings/processing/utils.py` handles Windows `CREATE_NO_WINDOW` — always reuse it for FFmpeg calls
- Course structure can be loaded from spec files via `clm.core.course_spec.CourseSpec`
