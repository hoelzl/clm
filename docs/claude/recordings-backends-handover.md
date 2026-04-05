# Recordings Backends Refactor — Handover

**Status**: Design approved, ready to implement Phase A.
**Branch**: Not yet created — suggested name `feature/recordings-auphonic-backend`.
**Design doc**: [`docs/claude/design/recordings-backend-architecture.md`](design/recordings-backend-architecture.md)
**Predecessor**: [`docs/claude/recordings-pipeline-handover.md`](recordings-pipeline-handover.md) (merged via PR #26).

---

## 1. Feature Overview

**Name**: Recordings post-processing backend architecture refactor + Auphonic integration.

**One-paragraph description**: Refactor the recordings workflow to support multiple pluggable post-processing backends with fundamentally different shapes (audio-first like the current ONNX / iZotope RX 11 pipeline, and video-in/video-out like Auphonic). Add Auphonic as a new cloud backend that produces higher-quality output than the local pipeline. The user selects one backend per config; the CLI, watcher, and web UI are backend-agnostic. Future features (cut list download and review, filler removal, transcripts) will be added without further architectural changes.

**Problem it solves**:

- Auphonic is an online service that takes a video in and returns a processed video out, with superior quality to the current local DeepFilterNet3 pipeline. The user has evaluated it and wants it as the preferred path.
- The current `ProcessingBackend` protocol (`src/clm/recordings/workflow/backends.py`) signs `process(video, output_wav) -> None` — it's audio-centric and cannot express a video-in/video-out service. Adding Auphonic naively would require a third hardcoded branch in `RecordingsWatcher` and an awkward violation of the existing contract.
- The watcher currently has per-mode branches (`_handle_external` for `.wav`, `_handle_onnx` for video). This doesn't scale to more backends and conflates "what triggers me" with "what I do".
- There's no job lifecycle abstraction. Auphonic is asynchronous (upload → process 2-30 min → download), and today's design has no concept of an in-flight job that survives process restarts.

**Why now**: the recordings pipeline (PR #26) is merged, so the ground is stable for a larger architectural change. Adding Auphonic without a refactor would entrench the problems above.

**Related work**:

- PR #26: `Add recordings module: audio pipeline, OBS integration, web dashboard, file watcher` — merged, provides the foundation this work modifies.
- Auphonic API reference: <https://auphonic.com/help/api/>

---

## 2. Design Decisions

### Backend abstraction level

**Decision**: The `ProcessingBackend` protocol abstracts at the "raw recording → final recording" level, not at the audio-processing level. Internal steps (extract audio, mux, upload, download) are the backend's business.

**Why**: The old protocol was shaped around "produce a .wav alongside the raw video". Auphonic doesn't produce a .wav — it produces a final video directly. Trying to force Auphonic through the old shape would require an artificial "fake wav" step. Raising the abstraction level makes both workflows fit naturally.

**Rejected alternative**: A single unified protocol with `extract_audio()`, `process_audio()`, `mux()` hooks. Rejected because Auphonic doesn't have an `extract_audio` step — it sends the whole file. Forcing the template method on Auphonic would mean most hooks are no-ops, which is a smell.

### Pattern combination: Strategy + Template Method

**Decision**:

- Strategy pattern at the top level: `ProcessingBackend` Protocol, swappable via config.
- Template Method (`AudioFirstBackend` ABC) for audio-first backends that share a common flow (produce `.wav` → mux → archive). `OnnxAudioFirstBackend` and `ExternalAudioFirstBackend` inherit from it.
- `AuphonicBackend` implements the Protocol directly (no ABC), because its flow (upload/poll/download) doesn't share structure with the audio-first backends.

**Why**: Two audio-first backends already share ~80% of their flow. A Template Method captures that sharing cleanly. Forcing Auphonic into the same hierarchy would create an inheritance that only shares one method — that's Strategy, not Template Method.

### Job as a first-class concept

**Decision**: A `ProcessingJob` Pydantic model with an explicit state machine (`QUEUED → UPLOADING → PROCESSING → DOWNLOADING → ASSEMBLING → COMPLETED/FAILED/CANCELLED`). A `JobManager` owns job persistence, the event bus, and the polling loop.

**Why**: Unifies sync (ONNX) and async (Auphonic) backends behind a single observable shape. The web UI, CLI, and CLI tests all speak in terms of jobs. Persistence survives process restarts — important because an Auphonic job might take 30 minutes.

**Rejected alternative**: Each backend manages its own in-flight tracking. Rejected because backends would each reimplement persistence, event publishing, and state transitions. Central ownership by `JobManager` is DRY and gives one place to look when debugging.

### Polling, no webhooks in v1

**Decision**: Auphonic job status is checked via polling. Polling cadence is a code-level constant in `backends/auphonic.py`, with a single user-facing `poll_timeout_minutes` override. Webhooks are **out of scope for v1**.

**Why**: Webhooks require the user to expose a public URL (ngrok, Cloudflare Tunnel, reverse proxy), which is operational burden disproportionate to the benefit for a single-user laptop workflow. Polling with backoff (30s early, 5min after 30 minutes) is simple and sufficient for lecture-length videos. Webhooks can be added as a new method on the Protocol later without breaking existing backends.

**Backoff policy** (code constants in `backends/auphonic.py`):

- `AUPHONIC_POLL_INITIAL_SECONDS = 30` — first 30 minutes of each job
- `AUPHONIC_POLL_BACKOFF_AFTER_MINUTES = 30` — switch to slow polling after this
- `AUPHONIC_POLL_LONG_SECONDS = 300` — 5 minutes once in slow mode
- `AUPHONIC_POLL_TIMEOUT_MINUTES = 120` — fail the job after this total wait

### Auphonic API choice: Complex JSON API, not Simple API

**Decision**: Use the three-step Complex JSON API (`create production` → `upload` → `start`), not the one-step Simple API.

**Why**:

1. Complex API supports a separate upload step, allowing progress reporting during multi-GB video uploads.
2. If upload fails, we can retry without recreating the production.
3. Complex API allows inline algorithm configuration, so we can ship a zero-setup default without requiring the user to create a preset.

### Preset bootstrap: inline-default with optional managed preset

**Decision**: `AuphonicBackend` sends the full algorithm config inline on every production by default (no Auphonic-side state required). A `clm recordings auphonic preset sync` command creates a named preset (`"CLM Lecture Recording"`) in the user's Auphonic account; setting `[recordings.auphonic] preset = "CLM Lecture Recording"` switches the backend to reference-by-name mode.

**Why**: Inline is the lowest-friction first-run experience — set API key, submit a file. Managed presets are valuable for power users who want to edit the preset in Auphonic's web UI without touching CLM config. Supporting both costs ~100 LOC (conditional in `submit()` plus the `preset sync` CLI command).

### Default backend: `onnx`, not `auphonic`

**Decision**: Fresh installs default to `processing_backend = "onnx"`. Users opt into Auphonic by setting the config field and providing an API key.

**Why**: CLM should work out of the box without cloud credentials. Startup validation raises an error if `auphonic` is selected but `api_key` is empty.

### External backend trigger inversion

**Decision**: `ExternalAudioFirstBackend.accepts_file(path)` returns True for `.wav` files (not video files). The `submit()` method then resolves the matching raw video from the same directory.

**Why**: In the external (iZotope RX 11) workflow, the user records lecture → `topic--RAW.mp4` appears → user manually processes in RX 11 → `topic--RAW.wav` appears → CLM should mux them. The trigger is the audio appearance, not the video appearance. Forcing the backend to react to video and then spin while waiting for audio would be operationally wasteful. A class docstring will note that `raw_path` is semantically the trigger file, not necessarily a video.

### Job store location

**Decision**: Per-recordings-tree at `<recordings-root>/.clm/jobs.json`. The `clm recordings jobs` CLI command takes a `--root` flag defaulting to `recordings.root_dir`.

**Why**: Multiple recordings trees (e.g., different courses on different drives) have independent job logs. A global user-level store would conflate them and produce confusing `clm recordings jobs` output.

### Process restart handling

**Decision**: On startup, the `JobManager` rehydrates non-terminal jobs. `PROCESSING` jobs are re-polled (Auphonic is authoritative). `UPLOADING` jobs are marked `FAILED` with a message instructing the user to re-submit.

**Why**: Auphonic's upload endpoint is not resumable. Trying to guess whether a partial upload succeeded would be more complex than failing cleanly and letting the user re-submit.

### Type name: `ProcessingBackend` (collides with legacy)

**Decision**: The new Protocol is called `ProcessingBackend`, matching `ProcessingJob` and `ProcessingOptions`. The existing `ProcessingBackend` protocol in `src/clm/recordings/workflow/backends.py` must be renamed before the new one is introduced.

**Why**: Consistency within the new type vocabulary. The rename of the legacy code is mechanical (see Phase A below).

---

## 3. Phase Breakdown

### Phase A — Rename legacy, introduce new abstractions (no behaviour change) [TODO]

**Goal**: Add the new abstraction surface alongside the existing code without changing runtime behaviour.

**Steps**:

1. **Mechanical rename**: `src/clm/recordings/workflow/backends.py` → `src/clm/recordings/workflow/backends_legacy.py`. Update imports in `watcher.py` and any tests. This is a no-behaviour-change commit — tests must pass. (Python cannot have both `backends.py` and `backends/` in the same directory, so this rename is required before step 2.)

2. **Create new package**: `src/clm/recordings/workflow/backends/` with `__init__.py`. Add `backends/base.py` containing the new `ProcessingBackend` Protocol, and `backends/audio_first.py` containing the `AudioFirstBackend` Template Method ABC.

3. **New types module**: `src/clm/recordings/workflow/jobs.py` with `JobState` enum, `ProcessingJob` Pydantic model, `ProcessingOptions`, `BackendCapabilities`. See §6.3 of the design doc for full field definitions.

4. **Job infrastructure**:
   - `src/clm/recordings/workflow/job_store.py` — `JobStore` Protocol + `JsonFileJobStore` implementation. Atomic writes via tmp + rename.
   - `src/clm/recordings/workflow/event_bus.py` — `EventBus` (simple pub/sub wrapper around the existing SSE queue so backends don't depend on FastAPI).
   - `src/clm/recordings/workflow/job_manager.py` — `JobManager` class. Owns jobs dict, persistence, poller loop. See §6.9 of the design doc.

5. **Port ONNX backend**: Create `backends/onnx.py` with `OnnxAudioFirstBackend` extending `AudioFirstBackend`. The `_produce_audio` method contains the body of today's `OnnxBackend.process` (audio extraction → ONNX denoise → FFmpeg filters → write .wav). The legacy `OnnxBackend` in `backends_legacy.py` is **not** deleted in this phase — the running watcher still uses it.

6. **Unit tests** for each new module: `tests/recordings/test_jobs.py`, `test_job_store.py`, `test_job_manager.py` (with a fake backend), `test_audio_first_backend.py`, `test_onnx_audio_first_backend.py`.

**Acceptance**:

- All existing tests pass unchanged.
- New unit tests pass. Coverage of the new modules is comprehensive.
- `ruff check` and `mypy` pass.
- No behaviour change visible to users; CLI, watcher, and web UI are untouched.

**Files involved**:

- New: `backends/__init__.py`, `backends/base.py`, `backends/audio_first.py`, `backends/onnx.py`, `jobs.py`, `job_store.py`, `event_bus.py`, `job_manager.py`
- Renamed: `backends.py` → `backends_legacy.py`
- Modified: `watcher.py` (import update only), tests importing from `backends`

### Phase B — Rewire the watcher and wire the JobManager end-to-end [TODO]

**Goal**: Swap the running code from the legacy protocol to the new one. Delete the per-mode branches in the watcher.

**Steps**:

1. **Refactor `RecordingsWatcher`** to the backend-agnostic shape: `__init__(root_dir, job_manager, backend, *, stability_interval, stability_checks)`. Delete `_handle_external` and `_handle_onnx`. The new `_on_file_event` asks `backend.accepts_file(path)` and, if True, dispatches to `job_manager.submit(path)` on a background thread after stability detection.

2. **Port `ExternalBackend`** to `backends/external.py` as `ExternalAudioFirstBackend`. The class inherits from `AudioFirstBackend`. `accepts_file` returns True for `.wav` files with `--RAW` suffix. `_produce_audio` resolves the matching video from the same directory and is a no-op if the video exists (the `.wav` is the output).

3. **Rewire the web app** (`src/clm/recordings/web/app.py`) to construct a `JobManager` during app startup and pass it to the watcher constructor. The `EventBus` is wired to the existing SSE response.

4. **Port watcher tests** (`tests/recordings/test_watcher.py`) to the new shape. Delete tests specific to the old mode branches; add tests using fake backends.

5. **Smoke test**: `clm recordings serve` comes up, submitting a raw file triggers the configured backend, the dashboard shows job progress.

**Acceptance**:

- `tests/recordings/` passes end to end.
- `clm recordings serve` works with both `onnx` and `external` configurations.
- No regression in the RX 11 manual workflow.

**Files involved**:

- New: `backends/external.py`
- Modified: `watcher.py`, `web/app.py`, `tests/recordings/test_watcher.py`
- Modified: anything still importing from `backends_legacy` is migrated off (except the legacy file itself, which is removed in Phase D)

### Phase C — Ship Auphonic [TODO]

**Goal**: Add the Auphonic backend as a selectable option. End of this phase: users can set `processing_backend = "auphonic"` and get processed video back.

**Steps**:

1. **`AuphonicClient`** (`backends/auphonic_client.py`) — httpx-based HTTP wrapper. Methods: `create_production`, `upload_input` (with `on_progress` callback, streamed), `start_production`, `get_production`, `download` (follows redirects), `delete_production`, `create_preset`, `update_preset`, `list_presets`. Tested with `respx` (httpx mock transport).

2. **`AuphonicBackend`** (`backends/auphonic.py`) — implements the Protocol. See §6.8 of the design doc for the `submit`/`poll`/`cancel` sketches. Contains the polling constants listed in Design Decisions above. Handles both inline-algorithms and preset-reference modes based on `config.preset` being empty or set.

3. **Config extension** (`src/clm/infrastructure/config.py`):
   - Add `AuphonicConfig` nested Pydantic model with fields: `api_key`, `preset`, `poll_timeout_minutes`, `request_cut_list`, `apply_cuts`, `base_url`, `upload_chunk_size`, `upload_retries`, `download_retries`.
   - Add `auphonic: AuphonicConfig` field to `RecordingsConfig`.
   - Change `processing_backend` default to `"onnx"` (it's already the effective default; this is explicit).
   - Add a validator: if `processing_backend == "auphonic"` and `auphonic.api_key == ""`, raise a clear error at startup.

4. **Backend factory** in `src/clm/recordings/workflow/backends/__init__.py` — `make_backend(config: RecordingsConfig) -> ProcessingBackend` that dispatches on `processing_backend` and constructs the appropriate class with its dependencies.

5. **CLI extensions** (`src/clm/cli/commands/recordings.py`):
   - `clm recordings backends` — list available backends with their capabilities (read from the factory; nice table output via `rich`).
   - `clm recordings submit <file>` — submit a single file to the active backend (wraps `JobManager.submit`).
   - `clm recordings jobs [--root DIR]` — list active and recent jobs.
   - `clm recordings jobs cancel <id>` — cancel an in-flight job.
   - `clm recordings auphonic preset sync` — create/update the managed preset via `AuphonicClient.create_preset`/`update_preset`. The preset template lives in a module constant (JSON dict with the CLM-default algorithm config).

6. **Web UI extensions** (`src/clm/recordings/web/routes.py`, `templates/`):
   - `GET /jobs` HTMX partial listing active and recent jobs with progress bars.
   - `POST /jobs/{id}/cancel` — cancel an in-flight job.
   - `GET /backends` JSON endpoint returning the active backend and its capabilities (used by the UI for conditional rendering).
   - Extend the SSE stream (`/events`) with `job` events.
   - Dashboard template: add a "Jobs" panel and conditionally render a "Cut list" checkbox based on `capabilities.supports_cut_lists`.

7. **Tests**:
   - `tests/recordings/test_auphonic_client.py` — `respx`-based tests of the HTTP client (happy path, upload with progress, redirect following, error responses).
   - `tests/recordings/test_auphonic_backend.py` — backend tests using a fake `AuphonicClient` (drives a job from QUEUED → UPLOADING → PROCESSING → DOWNLOADING → COMPLETED).
   - `tests/recordings/test_job_manager_polling.py` — JobManager with an async fake backend, verifies the poller drives jobs to completion and handles failures.
   - `tests/cli/test_recordings_auphonic.py` — CLI command tests.

8. **Documentation**:
   - New user guide: `docs/user-guide/recordings-auphonic.md` covering API key setup, config, and the `preset sync` command.
   - Update `CLAUDE.md` with the new backend and commands.
   - Update `src/clm/cli/info_topics/commands.md` with the new subcommands.

**Acceptance**:

- `clm recordings backends` lists all three backends with correct capabilities.
- End-to-end integration test (marked, requires real API key) successfully processes a small sample video through Auphonic.
- Web dashboard shows live upload progress, then processing progress, then "Done".
- `ruff check`, `mypy`, and full pytest (non-docker) pass.
- A fresh CLM install with `processing_backend = "auphonic"` and an API key in env works out of the box (no manual preset setup).

**Dependencies added to `pyproject.toml`**: `httpx` in `[recordings]` extra (if not already there). `respx` in `[dev]`.

### Phase D — Remove legacy [TODO]

**Goal**: Delete the legacy protocol and tidy up.

**Steps**:

1. Delete `src/clm/recordings/workflow/backends_legacy.py`.
2. Remove any remaining `LegacyProcessingBackend` aliases in imports.
3. Update `CLAUDE.md` class list in the "Key Classes" section to reference the new names.
4. Update `src/clm/cli/info_topics/spec-files.md` and `migration.md` if any command or option has changed.
5. Update `docs/claude/recordings-backends-handover.md` (this file): mark all phases DONE, move to archive per the `/retire-handover` convention.

**Acceptance**: Clean grep for "backends_legacy" in the codebase returns zero results. Full test suite passes.

---

## 4. Current Status

**Phase active**: None yet — design is approved, implementation has not started.

**Completed**:

- Auphonic API investigation (see [design doc §3](design/recordings-backend-architecture.md#3-background-auphonic-api-workflow)). Complex JSON API chosen for upload progress and inline algorithms.
- Architectural design with full code sketches for Protocol, Template Method, backends, `JobManager`, `JobStore`, and config. See [`docs/claude/design/recordings-backend-architecture.md`](design/recordings-backend-architecture.md).
- Every open question and outstanding decision resolved with the user (see §13 and §16 of the design doc).

**In progress**: Nothing.

**Blockers / open questions**: None. All design decisions are locked.

**Tests**: No new tests yet. Existing 162 recordings tests pass on `master` and must continue to pass throughout Phase A (the new code is added alongside, nothing existing changes behaviour).

**Uncommitted changes on disk** (as of handover creation):

- `docs/claude/design/recordings-backend-architecture.md` — untracked, the design doc. Commit before starting implementation.
- This handover file — untracked. Commit alongside the design doc.

---

## 5. Next Steps

**Start Phase A**. A fresh session should:

1. **Read the design doc** first: [`docs/claude/design/recordings-backend-architecture.md`](design/recordings-backend-architecture.md). It contains the full interface sketches, justifications, and the Auphonic API summary. This handover is the "where and why"; the design doc is the "what exactly".

2. **Create the feature branch**: `git checkout -b feature/recordings-auphonic-backend`.

3. **Commit the existing docs first**:
   - `docs/claude/design/recordings-backend-architecture.md`
   - `docs/claude/recordings-backends-handover.md`
   
   One commit, message along the lines of `Add design doc and handover for recordings backend refactor`.

4. **Phase A step 1**: Rename `src/clm/recordings/workflow/backends.py` → `backends_legacy.py`. Update imports in `watcher.py` and `tests/recordings/test_backends.py`, `test_watcher.py`, anything else that imports from it. Run `pytest tests/recordings/` to verify green. Commit as a dedicated mechanical rename.

5. **Phase A step 2-4**: Create the new `backends/` package and supporting modules (`jobs.py`, `job_store.py`, `event_bus.py`, `job_manager.py`). Write unit tests alongside each module. Keep commits small (one per module or logical grouping).

6. **Phase A step 5**: Port `OnnxBackend` into `backends/onnx.py` as `OnnxAudioFirstBackend`. The new class and tests go alongside the legacy (which stays in `backends_legacy.py` until Phase D).

**Gotchas**:

- **Python module/package shadowing**: you cannot have both `backends.py` and `backends/` in the same directory. The rename in step 1 is required before step 2.
- **Circular imports**: `job_manager.py` imports from `backends/base.py` (for the Protocol) and from `jobs.py`. The backends package imports from `jobs.py`. Keep `jobs.py` at the leaf with no workflow-internal imports.
- **`pydantic` vs `attrs`**: the codebase uses Pydantic for messages/config and attrs `@define` for internal structures. `ProcessingJob`, `ProcessingOptions`, `BackendCapabilities` serialize to SSE events and config, so they are Pydantic.
- **Lazy imports for optional deps**: `httpx` will be in `[recordings]`, not core. Follow the existing pattern in `OnnxBackend` of lazy-importing inside the method that needs it, so CI without `[recordings]` still works.
- **Thread safety**: the `JobManager` runs a background poller thread. All mutations to `_jobs` go through `self._lock` (RLock). Event emission should happen outside the lock to avoid reentrance in subscribers.
- **`run_subprocess` on Windows**: reuse `clm.recordings.processing.utils.run_subprocess` for any FFmpeg calls. It handles `CREATE_NO_WINDOW` correctly.

**What NOT to do**:

- Do not touch `RecordingsSession` or `ObsClient` in Phase A — they're unrelated to backend selection.
- Do not delete `backends_legacy.py` until Phase D.
- Do not add webhook handling in any phase — explicitly out of scope for v1 per §16 of the design doc.

---

## 6. Key Files & Architecture

### Files that will be created (Phases A-C)

```
src/clm/recordings/workflow/
  jobs.py                       # ProcessingJob, JobState, ProcessingOptions, BackendCapabilities (Phase A)
  job_manager.py                # JobManager + JobContext (Phase A)
  job_store.py                  # JobStore Protocol + JsonFileJobStore (Phase A)
  event_bus.py                  # EventBus pub/sub (Phase A)
  backends/
    __init__.py                 # make_backend() factory (Phase A skeleton, Phase C completes)
    base.py                     # ProcessingBackend Protocol, JobContext Protocol (Phase A)
    audio_first.py              # AudioFirstBackend ABC, Template Method (Phase A)
    onnx.py                     # OnnxAudioFirstBackend (Phase A)
    external.py                 # ExternalAudioFirstBackend (Phase B)
    auphonic.py                 # AuphonicBackend (Phase C)
    auphonic_client.py          # httpx wrapper for Auphonic API (Phase C)

tests/recordings/
  test_jobs.py                  # (Phase A)
  test_job_store.py             # (Phase A)
  test_job_manager.py           # (Phase A)
  test_audio_first_backend.py   # (Phase A)
  test_onnx_audio_first.py      # (Phase A)
  test_external_audio_first.py  # (Phase B)
  test_auphonic_client.py       # (Phase C, uses respx)
  test_auphonic_backend.py      # (Phase C, fake client)
  test_job_manager_polling.py   # (Phase C, async fake backend)

docs/user-guide/
  recordings-auphonic.md        # User-facing setup guide (Phase C)
```

### Files that will be modified

```
src/clm/recordings/workflow/watcher.py   # backend-agnostic refactor (Phase B)
src/clm/recordings/web/app.py            # JobManager wiring (Phase B)
src/clm/recordings/web/routes.py         # /jobs, /backends, SSE job events (Phase C)
src/clm/recordings/web/templates/*.html  # Jobs panel, capability-conditional UI (Phase C)
src/clm/cli/commands/recordings.py       # backends/submit/jobs/auphonic subcommands (Phase C)
src/clm/infrastructure/config.py         # AuphonicConfig nested model (Phase C)
src/clm/cli/info_topics/commands.md      # New subcommands documented (Phase C)
pyproject.toml                           # httpx in [recordings], respx in [dev] (Phase C)
CLAUDE.md                                # New backend + commands (Phase C)
```

### Files that will be deleted

```
src/clm/recordings/workflow/backends_legacy.py   # Phase D (after rename in Phase A)
```

### Entry points

- **CLI**: `clm recordings submit <file>` → `JobManager.submit` → `backend.submit` → job lifecycle.
- **Watcher**: filesystem event → `RecordingsWatcher._on_file_event` → `backend.accepts_file()` check → `JobManager.submit`.
- **Web**: `POST /jobs/submit` (TBD, or reuse CLI path) → `JobManager.submit`. SSE stream at `/events` carries `job` events.

### Key patterns to follow

- **Protocol for extensibility** (Strategy): new backend = new class implementing `ProcessingBackend`. No core changes required.
- **Capability flags over isinstance**: UI and CLI check `backend.capabilities.supports_*` rather than `isinstance(backend, AuphonicBackend)`.
- **Single mutator**: only `JobManager` mutates `ProcessingJob` instances. Backends read jobs and return updated ones; the manager persists and publishes.
- **Code-level tuning for operational knobs**: polling cadence is in code constants, not user config. User config only for things the user legitimately needs to change.
- **Lazy imports for optional extras**: Auphonic-specific code imports `httpx` inside functions, not at module top level.

---

## 7. Testing Approach

### Unit tests (most of the work)

- Each new module has a corresponding `test_*.py` in `tests/recordings/`.
- **Backends** are tested with fake contexts (`JobContext` implementations that record events) and fake HTTP clients. No real Auphonic calls.
- **`AuphonicClient`** is tested with `respx` — a mock transport for `httpx` — so we can assert exact request shapes (URLs, headers, multipart fields) and return canned responses.
- **`JobManager`** is tested with a fake backend that simulates sync and async behaviours.
- **`AudioFirstBackend`** Template Method is tested by a stub subclass overriding `_produce_audio`.

### Integration tests

- One marked `@pytest.mark.integration` test that runs a real small video through the ONNX backend end-to-end via `JobManager.submit`. Requires `ffmpeg` + the ONNX model cache.
- One marked `@pytest.mark.integration` Auphonic test that requires `AUPHONIC_API_KEY` env var — skipped in CI unless the secret is configured. Uses a 10-second test clip to keep credit consumption tiny.

### Existing tests

- All 162 existing recordings tests must continue to pass at every phase boundary. In Phase A, they pass unchanged (new code is additive). In Phase B, watcher tests are updated to the new constructor shape.

### How to run

```bash
# Fast unit tests
pytest tests/recordings/

# Include integration tests (real ffmpeg + ONNX)
pytest tests/recordings/ -m integration

# Include Auphonic integration (requires CLM_RECORDINGS__AUPHONIC__API_KEY)
pytest tests/recordings/ -m "integration and auphonic"

# Full recording suite before pushing
pytest tests/recordings/ -m ""
```

### Test data

- Small sample recordings for tests: there's no committed sample yet; generate one on the fly with `ffmpeg -f lavfi -i "testsrc=d=5:s=640x360" -f lavfi -i "sine=f=440:d=5" -c:v libx264 -c:a aac sample.mp4`. Put fixtures in `tests/recordings/fixtures/` if reused.

---

## 8. Session Notes

- **User preference on Auphonic**: the user has used it and considers the quality "very good" — it's the desired default for production work. The local pipeline is kept for offline/CI/no-credential scenarios.
- **User preference on iZotope RX 11**: remains the user's top choice for absolute quality in production recordings. `ExternalAudioFirstBackend` serves that workflow. It's ~100 LOC of maintenance and worth keeping.
- **Single-user workflow**: the user records lectures on a Windows laptop with OBS. The dashboard runs on `localhost`. No multi-user or server deployment is in scope. This is why webhooks were rejected — exposing `localhost` to Auphonic would require a tunnel, and the user is not interested in that setup burden.
- **Preset philosophy**: the user wants both inline (zero-setup) and managed-preset (editable in Auphonic web UI), not one or the other. Inline-default, `preset sync` as a power-user command.
- **Effort caps**: the user said "both if implementation effort is not excessive". The managed preset adds ~100 LOC for a real power-user win; that's within "not excessive".
- **Polling cadence**: the user explicitly wants this tunable in code, not user config ("in the code is probably enough"). Don't expose it as a TOML field except for the one `poll_timeout_minutes` override.
- **Naming debate**: user leaned toward `ProcessingBackend` over `PostProcessingBackend` because it pairs with `ProcessingJob`. The legacy collision is handled via the `backends.py` → `backends_legacy.py` rename in Phase A.
- **Commit discipline**: keep commits small. The Phase A rename is a single mechanical commit. Each new module is its own commit. Phase C has multiple commits (client, backend, config, CLI, web, docs).
- **The existing handover** at `docs/claude/recordings-pipeline-handover.md` is for the completed recordings pipeline work (PR #26). Don't confuse the two. That one is a candidate for retirement via `/retire-handover` once this work is underway.
