# Recordings Post-Processing Backend Architecture

**Status**: Draft for discussion
**Author**: Claude (Opus 4.6)
**Date**: 2026-04-05
**Scope**: `src/clm/recordings/workflow/` — backends, watcher, assembler, configuration

---

## 1. Goals

1. **Make Auphonic the default post-processing backend** for the recording workflow. Auphonic is a commercial cloud service that takes a video in and returns a processed video out, with superior quality to the current local pipeline.
2. **Keep the existing local pipeline as a fallback** for offline use, CI, and users without an Auphonic account.
3. **Keep "wait for external tool"** (iZotope RX 11) mode as a third option.
4. **Make the architecture open for future backends** (other cloud services, other local approaches) and **future features** (cut list generation/review, filler removal, chapter detection).
5. **A single configuration option selects the active backend** — the rest of the system (watcher, web UI, CLI) is backend-agnostic.

## 2. Non-Goals (for this design pass)

- Automatic application of Auphonic cut lists (download only; apply is Phase 2).
- Running multiple backends simultaneously on the same recording (A/B comparison is already handled by `clm recordings compare`).
- Replacing the existing `clm recordings process` one-shot command — that stays as a direct entry point to the local pipeline.
- Webhook support for Auphonic callbacks. v1 uses polling only; webhooks are a possible v2 extension and require no changes to the backend Protocol (only an additional method).

---

## 3. Background: Auphonic API Workflow

Summarized from `https://auphonic.com/help/api/` (simple_api, complex, details, query, webhook sections).

### 3.1 Authentication

- **API Key** (recommended for personal use): header `Authorization: bearer <api_key>`. Keys are created/reset on the Account Settings page.
- OAuth 2.0 is available for third-party apps; we don't need it.

### 3.2 Two API flavors

| Flavor | Endpoint | Use case |
|---|---|---|
| **Simple API** | `POST /api/simple/productions.json` (multipart) | Single request: upload + metadata + start. Good for small files, quick scripting. |
| **Complex JSON API** | `POST /api/productions.json` → `POST /api/production/{uuid}/upload.json` → `POST /api/production/{uuid}/start.json` | Three steps. Allows separate upload (progress, retry on upload failure without recreating the production), inline algorithm config, structured metadata. |

We'll use the **Complex JSON API** because lecture videos are large (hundreds of MB to several GB), and we want:
- Upload progress reporting to the dashboard
- Ability to retry a failed upload without recreating the production
- The option to reference a pre-configured preset by name but still override metadata per-recording

### 3.3 Presets vs inline configuration

Productions can reference a **preset** (by UUID or name) that pre-configures `output_files`, `algorithms`, and metadata templates. Inline fields on the production request override preset values.

**Our approach**: Users create a CLM-specific preset once in the Auphonic web UI (or via our CLI helper) called e.g. `"CLM Lecture Recording"`. The preset holds the algorithm config (denoise, leveler, loudness, output format = `"video"`). Each recording reference the preset by name and sets only the metadata (title, input file).

### 3.4 Output formats — video in, video out

The `output_files[].format` field supports `"video"`, which preserves the input video format and muxes the processed audio back in on Auphonic's side. This is exactly the black-box "video in → video out" contract we need.

Relevant formats (beyond `"video"`):
- `"cut-list"` with `ending: "DaVinciResolve.edl"` or `"ReaperRegions.csv"` — Phase 2.
- Audio formats (`mp3`, `aac`, `flac`, ...) — not needed for our use case.

### 3.5 Algorithms we will enable

Configured once on the preset; no need to send per-request:

| Field | Value | Purpose |
|---|---|---|
| `denoise` | `true` | Noise reduction |
| `denoisemethod` | `"dynamic"` or `"speech_isolation"` | Speech-aware denoising |
| `denoiseamount` | `0` (auto) or `12` | Reduction amount |
| `leveler` | `true` | Auto-leveling of speech |
| `normloudness` | `true` | Loudness normalization |
| `loudnesstarget` | `-16` | LUFS target (standard for online video) |
| `filtering` | `true` | Highpass + auto-EQ |
| `filler_cutter` | `false` initially, `true` in Phase 2 | Detect filler words |
| `silence_cutter` | `false` initially, `true` in Phase 2 | Detect long silences |
| `cut_mode` | `"apply_cuts"` (P2) or omit | Whether to actually cut vs just list |

### 3.6 Job lifecycle

```
POST /api/productions.json        # { preset, metadata, output_files, action: "save" }
  → { uuid: "<prod_uuid>", ... }

POST /api/production/<uuid>/upload.json   # multipart: input_file=@raw.mp4
  → { ... }

POST /api/production/<uuid>/start.json    # kicks off processing
  → { status: 0, ... }

# Wait for completion — v1 polls periodically
GET  /api/production/<uuid>.json          # polling
  → { status: 3 = Done, output_files: [{ download_url, ... }], ... }

# Status codes (from /api/info/production_status.json):
#   0 File Upload   1 Waiting   2 Error   3 Done   4 Incomplete Form
#   5 Production Not Started Yet   6 Production Outdated   7 Incomplete
#   9 Audio Processing   10 Audio Encoding   12 Speech Recognition
#   13 Outgoing File Transfer   ... etc.

GET <download_url>                        # Authorization: bearer <api_key>  -L
  → binary stream of the processed .mp4
```

Auphonic also supports webhooks (configured via `webhook` field on the production, POST with `uuid`/`status`/`status_string`), but v1 of this integration uses polling exclusively — see §10. Webhooks remain a v2 option and are cleanly addable as a new method on the Protocol.

### 3.7 Credits and rate limits

Auphonic uses a credit-per-hour-of-audio model. Not documented in the API section, but visible in the UI. We treat this as the user's concern and surface `used_credits` from the production response in the dashboard.

---

## 4. Current Architecture

```
┌──────────────────┐
│ RecordingSession │  ─── renames OBS output into to-process/
└──────────────────┘

┌─────────────────────┐       ┌─────────────────────────────┐
│ RecordingsWatcher   │ ◄──── │ backend: "external" | "onnx"│
│  (watchdog)         │       └─────────────────────────────┘
│  - _handle_external │──► watches for *.wav
│  - _handle_onnx     │──► watches for *--RAW.{mp4,mkv,...}
└──────────┬──────────┘
           │ calls into
           ▼
┌─────────────────────┐
│ ProcessingBackend   │  def process(video: Path, output_wav: Path) -> None
│  - ExternalBackend  │    (NotImplementedError — file appears externally)
│  - OnnxBackend      │    (extract audio → ONNX denoise → filters → write .wav)
└──────────┬──────────┘
           │ then
           ▼
┌─────────────────────┐
│ Assembler           │  mux raw .mp4 + processed .wav → final/.../topic.mp4
│  assemble_one()     │  + move originals to archive/
└─────────────────────┘
```

### 4.1 What's wrong with this for Auphonic

1. **`ProcessingBackend.process(video, output_wav)` is audio-centric**. It hardcodes the "produce a .wav alongside the video" contract. Auphonic produces a video, not a wav. There's no `output_wav` parameter that makes sense.

2. **The watcher has backend-specific event handlers** (`_handle_external`, `_handle_onnx`). Adding an Auphonic mode would mean a third hardcoded branch. The watcher is doing dispatching that should be delegated to the backend.

3. **Assembly is a fixed post-step**. For Auphonic the final video comes straight from the download — there's no mux step. Today's watcher always calls `_assemble_pair` after processing; that's wrong for a video-in/video-out backend.

4. **No job lifecycle abstraction**. The current design is "synchronous call with a callback". Auphonic is asynchronous: upload, then wait 2-30 minutes for processing, then download. There's no notion of an in-flight job that survives process restarts or polls a remote service.

5. **No capability model**. Different backends support different features (cut lists, filler removal). The UI can't know which options to show without hardcoded conditionals on the backend name.

6. **`ExternalBackend.process()` raises `NotImplementedError`**. This is a smell — `ExternalBackend` isn't really a backend in the same sense as `OnnxBackend`; it's a null object. That's fine, but it should be explicit in the type hierarchy.

---

## 5. Design Principles

1. **The backend is the unit of "raw recording → final recording"**. Whatever internal steps it needs (extract audio, mux, upload, wait, download) are encapsulated. Callers see a black box.
2. **Backends are pluggable via a Protocol** (Strategy pattern). Adding a backend is local and requires no changes to callers.
3. **Shared work (e.g., audio-first pipelines all need to mux and archive) lives in a Template Method base class**, not copy-pasted or in the orchestrator.
4. **Jobs are first-class**. A `ProcessingJob` is an observable object with a state machine. The orchestrator, web UI, and CLI all speak in terms of jobs.
5. **Dependency Inversion**: the watcher, web routes, and CLI depend on the `ProcessingBackend` protocol and `JobManager`, not on concrete backends.
6. **Capabilities are declarative**: a backend publishes what it can do (video in/out, cut lists, async, etc.); the UI adapts to the declaration.
7. **Sync and async backends coexist** behind the same interface. The `JobManager` hides polling from callers.
8. **Configuration is one field per backend choice**; backend-specific settings live in nested sections.

---

## 6. Proposed Architecture

### 6.1 Layered diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    Trigger Layer                              │
│    ┌──────────┐   ┌──────────┐   ┌────────────────────────┐ │
│    │ CLI      │   │ Watcher  │   │ Web (manual submit)    │ │
│    │ (batch)  │   │ (fs)     │   │                        │ │
│    └────┬─────┘   └────┬─────┘   └───────────┬────────────┘ │
│         └──────────────┴─────────────────────┘               │
└────────────────────────┼─────────────────────────────────────┘
                         ▼
            ┌────────────────────────────┐
            │       JobManager           │  ───► EventBus (SSE)
            │  - submit(raw, options)    │
            │  - cancel(job_id)          │
            │  - list_jobs()             │
            │  - persist()/restore()     │
            │  - poller loop (async)     │
            └───────────┬────────────────┘
                        │ delegates to
                        ▼
┌─────────────────────────────────────────────────────────────┐
│    ProcessingBackend (Protocol)                              │
│      capabilities: BackendCapabilities                       │
│      accepts_file(path) -> bool                              │
│      submit(raw, ctx) -> ProcessingJob                       │
│      poll(job) -> ProcessingJob                              │
│      cancel(job) -> None                                     │
└─────────┬────────────────────────┬──────────────────────────┘
          │                        │
          ▼                        ▼
┌──────────────────────┐  ┌────────────────────────────┐
│ AudioFirstBackend    │  │ AuphonicBackend            │
│ (Template Method)    │  │ (direct Protocol impl)     │
│  submit():           │  │  submit(): upload + create │
│    produce_audio()   │  │  poll(): GET status        │
│    assemble(v, a)    │  │  finalize(): download      │
│    archive()         │  │                            │
│                      │  └────────────────────────────┘
│  + OnnxAudioFirst    │
│  + ExternalAudio     │
│    (wait-for-wav)    │
└──────────────────────┘
```

### 6.2 Why this shape

- **Top-level Strategy** (`ProcessingBackend` Protocol) gives backend interchangeability for the JobManager.
- **Template Method** (`AudioFirstBackend`) captures what's shared between ONNX and External: produce a `.wav`, then mux, then archive. The two current backends become two subclasses differing only in how they produce the `.wav`.
- **Direct Protocol implementation** (`AuphonicBackend`) is allowed because Auphonic doesn't fit the audio-first template — it's a different shape (upload/download) and forcing it through `AudioFirstBackend` would create a broken inheritance.
- **JobManager** sits between the triggers and the backends. It is the single place that knows about persistence, the event bus, and polling loops — so backends don't each reimplement them.
- The **watcher** becomes a pure file-event source. It no longer has per-backend branches; it asks the backend (via `accepts_file`) whether a given file is relevant, then hands off to the JobManager.

### 6.3 Data types

```python
# src/clm/recordings/workflow/jobs.py

from __future__ import annotations
import enum
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from uuid import uuid4


class JobState(str, enum.Enum):
    QUEUED = "queued"           # accepted, not started
    UPLOADING = "uploading"     # local → remote (async backends only)
    PROCESSING = "processing"   # backend is doing work
    DOWNLOADING = "downloading" # remote → local (async backends only)
    ASSEMBLING = "assembling"   # audio-first: mux + archive
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingJob(BaseModel):
    """A single raw-recording → final-recording job.

    Owned by the JobManager.  Backends update fields via their public
    methods (submit / poll / cancel) — callers never mutate directly.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    backend_name: str                       # "auphonic" | "onnx" | "external"
    raw_path: Path                          # input file in to-process/
    final_path: Path                        # planned output in final/
    relative_dir: Path                      # course/section
    state: JobState = JobState.QUEUED
    progress: float = 0.0                   # 0.0–1.0, best-effort
    message: str = ""                       # human-readable current step
    error: str | None = None
    artifacts: dict[str, Path] = Field(default_factory=dict)
    # ^ extra outputs: "cut_list" → .edl, "transcript" → .srt, etc.
    backend_ref: str | None = None          # e.g. Auphonic production UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)


class ProcessingOptions(BaseModel):
    """Per-job options — overrides default backend behaviour."""
    request_cut_list: bool = False
    apply_cuts: bool = False       # Phase 2
    custom_preset: str | None = None   # Auphonic preset name override
    title: str | None = None       # metadata title (default = filename stem)
    extra: dict[str, object] = Field(default_factory=dict)


class BackendCapabilities(BaseModel):
    """Declarative description of what a backend can do.

    Used by the UI and CLI to decide which options to expose.
    """
    name: str                        # machine id: "auphonic"
    display_name: str                # "Auphonic (cloud)"
    description: str = ""
    # Processing model
    video_in_video_out: bool = False    # False = audio-first, needs assembly
    is_synchronous: bool = True          # False = long-running, needs polling
    requires_internet: bool = False
    requires_api_key: bool = False
    # Optional features
    supports_cut_lists: bool = False
    supports_filler_removal: bool = False
    supports_silence_removal: bool = False
    supports_transcript: bool = False
    supports_chapter_detection: bool = False
    # Limits
    max_file_size_mb: int | None = None
    supported_input_extensions: tuple[str, ...] = (".mp4", ".mkv", ".mov")
```

### 6.4 The backend Protocol

```python
# src/clm/recordings/workflow/backends/base.py

from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..jobs import ProcessingJob, ProcessingOptions, BackendCapabilities


class JobContext(Protocol):
    """What a backend needs from its environment to run a job.

    Supplied by the JobManager at submit time.  Lets backends update
    progress and emit events without knowing about the event bus.
    """
    def report(self, job: ProcessingJob) -> None: ...
    # Optional: a directory for intermediate files
    @property
    def work_dir(self) -> Path: ...


@runtime_checkable
class ProcessingBackend(Protocol):
    """Interface for post-processing backends.

    A backend takes a raw recording and yields a final recording.
    Internal steps are the backend's business.
    """

    @property
    def capabilities(self) -> BackendCapabilities: ...

    def accepts_file(self, path: Path) -> bool:
        """Should the watcher hand this file off to me?

        OnnxBackend → True for .mp4/.mkv with --RAW suffix
        ExternalAudioFirstBackend → True for .wav with --RAW suffix
        AuphonicBackend → True for .mp4/.mkv with --RAW suffix
        """
        ...

    def submit(
        self,
        raw_path: Path,
        final_path: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
    ) -> ProcessingJob:
        """Start a new processing job.

        Returns as soon as the backend has registered the work.  For
        sync backends this may block until completion (returning a
        COMPLETED job).  For async backends it returns after upload +
        remote start, in state PROCESSING.
        """
        ...

    def poll(self, job: ProcessingJob, *, ctx: JobContext) -> ProcessingJob:
        """Refresh the state of an async job.

        Sync backends should return job unchanged.  Async backends talk
        to the remote service.  May transition to COMPLETED (triggering
        finalize/archive inside the backend) or FAILED.
        """
        ...

    def cancel(self, job: ProcessingJob, *, ctx: JobContext) -> None:
        """Best-effort cancel. No-op if not cancellable."""
        ...
```

### 6.5 Template Method: audio-first backends

```python
# src/clm/recordings/workflow/backends/audio_first.py

from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path

from ..jobs import JobState, ProcessingJob, ProcessingOptions, BackendCapabilities
from ..assembler import assemble_one, PendingPair
from .base import JobContext, ProcessingBackend


class AudioFirstBackend(ABC):
    """Base class for backends that produce a .wav alongside the raw video.

    Encapsulates the common "produce audio → assemble → archive" flow
    as a Template Method.  Concrete subclasses implement _produce_audio.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    # Template method (final) --------------------------------------------
    def submit(
        self,
        raw_path: Path,
        final_path: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
    ) -> ProcessingJob:
        job = ProcessingJob(
            backend_name=self._name,
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=final_path.parent,  # placeholder
            state=JobState.PROCESSING,
            message="Producing audio",
        )
        ctx.report(job)

        try:
            audio_path = self._audio_output_path(raw_path)
            self._produce_audio(raw_path, audio_path, options=options, ctx=ctx, job=job)

            job.state = JobState.ASSEMBLING
            job.message = "Muxing and archiving"
            ctx.report(job)

            self._assemble(raw_path, audio_path, final_path, job)

            job.state = JobState.COMPLETED
            job.message = "Done"
            job.progress = 1.0
        except Exception as exc:
            job.state = JobState.FAILED
            job.error = str(exc)
        ctx.report(job)
        return job

    # Hooks --------------------------------------------------------------
    @abstractmethod
    def _produce_audio(
        self,
        raw: Path,
        output_wav: Path,
        *,
        options: ProcessingOptions,
        ctx: JobContext,
        job: ProcessingJob,
    ) -> None:
        """Create output_wav from raw.  Update job.progress/message.

        For ExternalAudioFirstBackend this waits for the .wav to appear.
        For OnnxBackend this runs the local pipeline.
        """

    # Shared helpers -----------------------------------------------------
    def _audio_output_path(self, raw: Path) -> Path:
        return raw.with_name(f"{raw.stem}.wav")

    def _assemble(self, raw: Path, audio: Path, final: Path, job: ProcessingJob) -> None:
        # Wraps assemble_one for the Template Method.
        ...

    # Common protocol surface --------------------------------------------
    def poll(self, job, *, ctx):  # sync — nothing to poll
        return job

    def cancel(self, job, *, ctx):  # best effort: let current call finish
        return
```

### 6.6 `OnnxAudioFirstBackend`

Thin subclass of `AudioFirstBackend` — produce_audio calls the existing `ProcessingPipeline` up through the filters and writes a `.wav` instead of muxing. Today's `OnnxBackend` already does essentially this; it just needs to be re-homed as a subclass with `capabilities` and `accepts_file`.

```python
class OnnxAudioFirstBackend(AudioFirstBackend):
    capabilities = BackendCapabilities(
        name="onnx",
        display_name="Local (DeepFilterNet3 + FFmpeg)",
        video_in_video_out=False,
        is_synchronous=True,
        requires_internet=False,
        requires_api_key=False,
        supports_cut_lists=False,
        max_file_size_mb=None,
    )

    def __init__(self, config: PipelineConfig | None = None) -> None:
        super().__init__(name="onnx")
        self._config = config or PipelineConfig()

    def accepts_file(self, path: Path) -> bool:
        return path.suffix.lower() in VIDEO_EXTENSIONS and _is_raw(path)

    def _produce_audio(self, raw, output_wav, *, options, ctx, job):
        # The body of today's OnnxBackend.process() goes here.
        # Report progress via ctx.report(job) between steps.
        ...
```

### 6.7 `ExternalAudioFirstBackend` (RX 11, manual workflow)

```python
class ExternalAudioFirstBackend(AudioFirstBackend):
    capabilities = BackendCapabilities(
        name="external",
        display_name="External tool (e.g. iZotope RX 11)",
        video_in_video_out=False,
        is_synchronous=False,         # we wait for a file
        requires_internet=False,
        requires_api_key=False,
    )

    def __init__(self, *, stability_interval: float, stability_checks: int) -> None:
        super().__init__(name="external")
        self._stability_interval = stability_interval
        self._stability_checks = stability_checks

    def accepts_file(self, path: Path) -> bool:
        # Triggered by the appearance of a .wav, not a video.
        return path.suffix.lower() == ".wav" and _is_raw(path)

    def _produce_audio(self, raw, output_wav, *, options, ctx, job):
        # 'raw' here is actually the .wav that showed up; we need to
        # resolve the matching video and reshape the flow.
        ...
```

> **Note**: External is awkward because the triggering file is the **audio**, not the video. This needs a small adaptation — see Open Question 1 in §13.

### 6.8 `AuphonicBackend`

```python
# src/clm/recordings/workflow/backends/auphonic.py

class AuphonicBackend:
    capabilities = BackendCapabilities(
        name="auphonic",
        display_name="Auphonic (cloud)",
        description="Cloud audio/video processing with speech-aware denoising, "
                    "leveling, loudness normalization, and optional cut lists.",
        video_in_video_out=True,
        is_synchronous=False,
        requires_internet=True,
        requires_api_key=True,
        supports_cut_lists=True,
        supports_filler_removal=True,
        supports_silence_removal=True,
        supports_chapter_detection=True,
        max_file_size_mb=None,
    )

    def __init__(self, config: AuphonicConfig, http: AuphonicClient) -> None:
        self._config = config
        self._http = http

    # ------------------------------------------------------------------
    def accepts_file(self, path: Path) -> bool:
        return path.suffix.lower() in VIDEO_EXTENSIONS and _is_raw(path)

    def submit(self, raw_path, final_path, *, options, ctx):
        job = ProcessingJob(
            backend_name="auphonic",
            raw_path=raw_path,
            final_path=final_path,
            relative_dir=final_path.parent,
            state=JobState.QUEUED,
        )

        # 1. Create production — either referencing a named preset or
        #    sending inline algorithm config (see §11 on preset modes).
        job.state = JobState.UPLOADING
        job.message = "Creating Auphonic production"
        ctx.report(job)
        production = self._http.create_production(
            preset=options.custom_preset or self._config.preset or None,
            algorithms=None if self._config.preset else self._inline_algorithms(),
            title=options.title or raw_path.stem.removesuffix("--RAW"),
            output_files=self._output_files_for(options),
        )
        job.backend_ref = production.uuid

        # 2. Upload file (streamed, with progress 0.0 → 0.4)
        job.message = "Uploading video"
        self._http.upload_input(
            production.uuid,
            raw_path,
            on_progress=lambda pct: self._bump(job, pct * 0.4, ctx),
        )

        # 3. Start processing
        job.state = JobState.PROCESSING
        job.message = "Processing on Auphonic"
        job.progress = 0.4
        ctx.report(job)
        self._http.start_production(production.uuid)

        # Async from here — the JobManager's poller takes over.
        return job

    def poll(self, job, *, ctx):
        assert job.backend_ref, "Cannot poll job without backend_ref"
        status = self._http.get_production(job.backend_ref)

        if status.status == AuphonicStatus.DONE:
            return self._finalize(job, status, ctx)
        elif status.status == AuphonicStatus.ERROR:
            job.state = JobState.FAILED
            job.error = status.error_message or "Auphonic reported error"
        else:
            # still in progress — update progress heuristically from status code
            job.message = f"Auphonic: {status.status_string}"
            job.progress = self._progress_for_status(status.status)
        ctx.report(job)
        return job

    def cancel(self, job, *, ctx):
        if job.backend_ref:
            self._http.delete_production(job.backend_ref)

    # ------------------------------------------------------------------
    def _finalize(self, job, status, ctx):
        """Download output files, write to final/, archive raw."""
        job.state = JobState.DOWNLOADING
        job.message = "Downloading processed video"
        ctx.report(job)

        for out in status.output_files:
            if out.format == "video":
                self._http.download(out.download_url, job.final_path,
                                    on_progress=lambda pct: ...)
            elif out.format == "cut-list":
                cut_path = job.final_path.with_suffix(".edl")
                self._http.download(out.download_url, cut_path)
                job.artifacts["cut_list"] = cut_path

        # Archive original
        _archive_raw(job.raw_path, job.relative_dir, ctx)

        job.state = JobState.COMPLETED
        job.progress = 1.0
        job.message = "Done"
        ctx.report(job)
        return job
```

### 6.9 `JobManager`

```python
# src/clm/recordings/workflow/job_manager.py

class JobManager:
    """Coordinates triggers and backends.  The ONLY thing that mutates jobs."""

    def __init__(
        self,
        backend: ProcessingBackend,
        root_dir: Path,
        *,
        poll_interval: float = 30.0,
        store: JobStore,
        bus: EventBus,
    ) -> None:
        self._backend = backend
        self._root = root_dir
        self._poll = poll_interval
        self._store = store
        self._bus = bus
        self._jobs: dict[str, ProcessingJob] = {}
        self._lock = threading.RLock()
        self._poller: threading.Thread | None = None
        self._stop = threading.Event()

        # Rehydrate in-flight jobs from disk on startup
        for j in self._store.load_all():
            if not j.is_terminal:
                self._jobs[j.id] = j
        if self._backend.capabilities.is_synchronous is False:
            self._start_poller()

    def submit(self, raw_path: Path, *, options: ProcessingOptions) -> ProcessingJob:
        final_path = self._derive_final_path(raw_path)
        ctx = self._make_context()
        job = self._backend.submit(raw_path, final_path, options=options, ctx=ctx)
        with self._lock:
            self._jobs[job.id] = job
        self._store.save(job)
        self._bus.publish("job", job)
        return job

    def list_jobs(self) -> list[ProcessingJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return
        self._backend.cancel(job, ctx=self._make_context())
        job.state = JobState.CANCELLED
        self._store.save(job)
        self._bus.publish("job", job)

    def _poller_loop(self) -> None:
        while not self._stop.is_set():
            ctx = self._make_context()
            with self._lock:
                in_flight = [j for j in self._jobs.values()
                             if j.state in (JobState.PROCESSING, JobState.UPLOADING)]
            for job in in_flight:
                try:
                    self._backend.poll(job, ctx=ctx)
                except Exception as exc:
                    logger.exception("Poll failed for {}: {}", job.id, exc)
                self._store.save(job)
                self._bus.publish("job", job)
            self._stop.wait(self._poll)

    def _make_context(self) -> JobContext:
        return _DefaultJobContext(manager=self, bus=self._bus, work_dir=...)
```

### 6.10 `JobStore` (persistence)

Plain JSON file under `<recordings-root>/.clm/jobs.json`. Keeping it in the recordings root (not user config) means each recordings tree has its own independent job log and multiple trees don't collide.

```python
class JobStore(Protocol):
    def load_all(self) -> list[ProcessingJob]: ...
    def save(self, job: ProcessingJob) -> None: ...
    def delete(self, job_id: str) -> None: ...

class JsonFileJobStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
    # Atomic write via tmp + rename
```

### 6.11 Events / SSE

Today the web UI gets updates via SSE from `RecordingSession` state and the pending-pairs scan. We extend the SSE stream with `job` events carrying a serialized `ProcessingJob`. The dashboard gains a "Jobs" panel showing in-flight and recent jobs with progress bars.

The `EventBus` is a simple publish-subscribe wrapper around the existing SSE queue, so backends/JobManager don't know about FastAPI. The recordings web app wires the bus to the SSE response.

---

## 7. Configuration Schema

```toml
[recordings]
root_dir = "D:/Recordings"
raw_suffix = "--RAW"
# Default is onnx (offline-safe). Users with an Auphonic key flip to "auphonic".
processing_backend = "onnx"  # "onnx" | "auphonic" | "external"

[recordings.auphonic]
api_key = ""                       # or CLM_RECORDINGS__AUPHONIC__API_KEY
preset = ""                        # empty = use inline algorithms; set to a
                                   # preset name (e.g. "CLM Lecture Recording")
                                   # if you've run `auphonic preset sync`
poll_timeout_minutes = 120         # give up on a job after this
# Optional feature flags for per-job options (defaults for the backend)
request_cut_list = false
apply_cuts = false
# Base URL override (tests / staging; default: https://auphonic.com)
base_url = "https://auphonic.com"
# Upload tuning
upload_chunk_size = 8_388_608       # 8 MiB
upload_retries = 3
download_retries = 3

[recordings.onnx]
# Nothing here for now — reuses [recordings.processing] for the pipeline.
# (Future: ONNX-specific overrides)

[recordings.external]
# stability_check_interval / stability_check_count already exist at [recordings];
# we leave them there because the watcher uses them regardless of backend.
```

**No user-facing polling knob.** The Auphonic poller cadence is a code-level constant (`AUPHONIC_POLL_INITIAL_SECONDS = 30`, `AUPHONIC_POLL_BACKOFF_AFTER_MINUTES = 30`, `AUPHONIC_POLL_LONG_SECONDS = 300`) in `backends/auphonic.py`. Changing it is a code change, not a config change. See §10.

**Migration rule**: if `processing_backend` is unset we default to `"onnx"`. If the user sets `"auphonic"` but `auphonic.api_key` is empty, the config validator raises at startup with a clear message.

---

## 8. Watcher changes

The watcher becomes backend-agnostic:

```python
class RecordingsWatcher:
    def __init__(
        self,
        root_dir: Path,
        job_manager: JobManager,
        backend: ProcessingBackend,   # same one the manager uses
        *,
        stability_interval: float,
        stability_checks: int,
    ) -> None: ...

    def _on_file_event(self, path: Path) -> None:
        if not self._backend.accepts_file(path):
            return
        if not self._state.try_claim(path):
            return
        threading.Thread(
            target=self._dispatch,
            args=(path,),
            daemon=True,
        ).start()

    def _dispatch(self, path: Path) -> None:
        try:
            self._wait_for_stable(path)
            self._job_manager.submit(path, options=ProcessingOptions())
        except Exception as exc:
            logger.error("Watcher dispatch failed: {}", exc)
        finally:
            self._state.release(path)
```

All the per-mode branching (`_handle_external` vs `_handle_onnx`) is gone. The only thing the watcher still owns is stability detection and file-level claims to prevent double-processing.

---

## 9. Web & CLI integration

### 9.1 Web dashboard

Routes added:

| Route | Method | Purpose |
|---|---|---|
| `/jobs` | GET | HTMX partial: list of active and recent jobs |
| `/jobs/{id}/cancel` | POST | Cancel an in-flight job |
| `/backends` | GET | JSON: active backend + capabilities (for conditional UI) |

The dashboard template renders per-capability:
- Cut-list checkbox only appears if `backend.capabilities.supports_cut_lists`
- "Backend: Auphonic (cloud)" header shows capabilities as badges

Existing SSE endpoint (`/events`) now includes `job` events alongside session and pairs events.

### 9.2 CLI

Existing commands continue to work unchanged by default (local pipeline). New subcommands:

```
clm recordings backends              # list available backends and their capabilities
clm recordings submit <file>         # submit a file to the configured backend
clm recordings jobs                  # list active/recent jobs
clm recordings jobs cancel <id>      # cancel one
clm recordings auphonic preset list  # list user's Auphonic presets (helper)
clm recordings auphonic preset sync  # create/update the default CLM preset
```

`clm recordings serve` boots the web dashboard with the JobManager + watcher wired to the active backend.

---

## 10. Polling Strategy

**Webhooks are out of scope for v1.** Requiring the user to expose a public URL is a significant operational burden (tunnel setup, security, firewall), and polling is sufficient for our workload. Lecture productions typically take 2-10 minutes on Auphonic, which polling handles well. Webhooks could be added later as a Protocol extension (new method on `ProcessingBackend`) without breaking existing backends.

### 10.1 How polling works

The `JobManager` runs a single background thread (`_poller_loop`) that, on each iteration:
1. Collects all non-terminal jobs across backends where `backend.capabilities.is_synchronous is False`
2. For each such job, calls `backend.poll(job, ctx=...)`
3. The backend talks to its remote service and returns an updated job
4. The manager persists the job and publishes a `job` event to the SSE bus
5. Sleeps until the next iteration

### 10.2 Cadence (code-level constants)

Defined in `src/clm/recordings/workflow/backends/auphonic.py`:

```python
# Tune these in code — not user-facing config.
AUPHONIC_POLL_INITIAL_SECONDS = 30       # first 30 minutes of a job
AUPHONIC_POLL_BACKOFF_AFTER_MINUTES = 30 # switch to slow polling after this
AUPHONIC_POLL_LONG_SECONDS = 300         # 5 minutes once we've been waiting
AUPHONIC_POLL_TIMEOUT_MINUTES = 120      # fail the job after this total wait
```

The backoff policy: for the first 30 minutes of a job, poll every 30 seconds (expected completion window). After that, drop to every 5 minutes to reduce load on Auphonic. At 120 minutes total, give up and mark the job `FAILED` with a timeout error — the user can re-submit.

A single config knob `recordings.auphonic.poll_timeout_minutes` exists to let users extend the timeout without touching code, since "my lecture is unusually long" is a legitimate reason. The other constants stay in code.

### 10.3 Why a single poller loop and not one-thread-per-job

Per-job polling threads are simpler to reason about but scale badly: if the user submits a batch of 20 recordings, we'd spawn 20 sleeping threads. One shared poller that iterates over all active jobs is cheap and handles any number of concurrent jobs. The loop runs at the shortest active cadence (30s), so batch semantics are unchanged.

### 10.4 Process restart behaviour

On startup, the JobManager rehydrates non-terminal jobs from the `JobStore`. For PROCESSING jobs, the next poller tick will contact Auphonic — if the production is still running, we pick up where we left off; if it's already done, we transition straight to DOWNLOADING. For UPLOADING jobs, per §Q5, we mark them FAILED with a message telling the user to re-submit, because uploads aren't resumable.

---

## 11. Future: Cut Lists (Phase 2)

The current design supports downloading cut lists as artifacts. Phase 2 adds:

1. **Review UI**: a dashboard page that plays the final video and overlays cut regions from the EDL. User can accept/reject individual cuts.
2. **Apply cuts**: after user approval, an ffmpeg step removes accepted cut regions with fade transitions.
3. **Alternative target**: export the cut list as a Premiere/DaVinci/Reaper project for manual editing.

Nothing in the current design precludes this — `ProcessingJob.artifacts["cut_list"]` is already the hand-off point.

Other future features the backend protocol supports without changes:
- Chapter markers (`artifacts["chapters"]`)
- Transcripts (`artifacts["transcript"]`)
- Per-topic preset switching (via `ProcessingOptions.custom_preset`)

---

## 12. Migration plan

The migration is phased so `master` stays green at every step. Note that the existing `workflow/backends.py` file and the **new** `workflow/backends/` package cannot coexist in the same directory (Python shadows the module with the package), so Phase A begins with a mechanical rename.

### Phase A — Rename legacy, introduce new abstractions (no behaviour change)

1. **Rename `workflow/backends.py` → `workflow/backends_legacy.py`** (mechanical). Update the two callers (`watcher.py`, tests) to import from `backends_legacy`. The old protocol keeps its name `ProcessingBackend` inside `backends_legacy.py` but is imported under an alias `LegacyProcessingBackend` where needed during the transition. No behaviour change; tests pass.
2. Create `workflow/backends/` package with `__init__.py`. Add `base.py` (new `ProcessingBackend` Protocol), `audio_first.py` (`AudioFirstBackend` Template Method), and the data types in `workflow/jobs.py`.
3. Add `workflow/job_manager.py`, `workflow/job_store.py`, `workflow/event_bus.py`. The JobManager is exercised by unit tests with a fake backend.
4. Port the current `OnnxBackend` logic to `backends/onnx.py` as `OnnxAudioFirstBackend`, extending `AudioFirstBackend`. The legacy `OnnxBackend` in `backends_legacy.py` is unchanged — it's still what the running watcher uses.

### Phase B — Rewire the watcher and wire the JobManager end-to-end

1. Refactor `RecordingsWatcher` to its backend-agnostic shape (`__init__(root_dir, job_manager, backend, ...)`). The old per-mode branches are removed in the same commit.
2. Port `ExternalBackend` to `backends/external.py` as `ExternalAudioFirstBackend`. Per resolved Q1, this backend's `accepts_file` returns True for `.wav` files and the "raw_path" semantically represents the trigger file; a comment in the class explains the inversion.
3. Switch the web app factory in `recordings/web/app.py` to construct a `JobManager` and pass it to the watcher.
4. Port `tests/recordings/test_watcher.py` to the new shape. Delete tests specific to the old mode branches.

### Phase C — Ship Auphonic

1. Add `backends/auphonic_client.py` (httpx-based HTTP wrapper). Test with `respx` (mock HTTP transport for httpx).
2. Add `backends/auphonic.py` (`AuphonicBackend`) with inline algorithm config as the default path.
3. Extend `RecordingsConfig` with `AuphonicConfig` nested model. Add startup validation (`api_key` required when `processing_backend == "auphonic"`).
4. Add CLI subcommands: `clm recordings backends`, `clm recordings jobs`, `clm recordings jobs cancel`, `clm recordings submit`.
5. Add `clm recordings auphonic preset sync` (creates or updates a managed preset in the user's Auphonic account).
6. Add integration tests: an `AuphonicClient` happy-path test using `respx`, an `AuphonicBackend` test with a fake client, and a `JobManager` test that drives a job from QUEUED to COMPLETED through polling.
7. Document Auphonic setup in `docs/user-guide/recordings-auphonic.md` and update `CLAUDE.md`.

### Phase D — Remove legacy

1. Delete `workflow/backends_legacy.py`.
2. Remove any remaining imports / aliases of `LegacyProcessingBackend`.
3. Update `clm info` topics and `CLAUDE.md` to refer only to the new architecture.

---

## 13. Resolved questions

All open questions have been resolved with the user. Recording them here for traceability.

### Q1. External backend trigger shape — RESOLVED

**Decision**: Backend-owned detection. `ExternalAudioFirstBackend.accepts_file(path)` returns True for `.wav` files, and its `submit` resolves the matching video from the same directory. The class docstring will explicitly note the inversion (the "raw_path" argument is semantically the trigger file, not necessarily a raw video).

### Q2. Job persistence scope — RESOLVED

**Decision**: Per-recordings-tree. The `JobStore` lives at `<recordings-root>/.clm/jobs.json`. `clm recordings jobs` takes an optional `--root` argument that defaults to `recordings.root_dir` from config. Multiple recordings trees have independent job logs.

### Q3. Upload progress granularity — RESOLVED

**Decision**: Yes, implement from day 1. Lecture videos are multi-GB and users need feedback during the upload phase. The `AuphonicClient.upload_input` takes an `on_progress: Callable[[float], None]` callback; the `AuphonicBackend` wires this to `job.progress` so the dashboard shows a live upload bar.

### Q4. Polling cadence — RESOLVED

**Decision**: Code-level constants in `backends/auphonic.py`, not user config. See §10.2 for the values. The only user-facing knob is `auphonic.poll_timeout_minutes` to override the total timeout for unusually long productions. Everything else is a code change.

### Q5. Restart handling — RESOLVED

**Decision**: On restart, PROCESSING jobs are re-polled (Auphonic is authoritative). UPLOADING jobs are marked FAILED with a message instructing the user to re-submit, because the Complex API's upload endpoint is not resumable.

### Q6. Preset bootstrap — RESOLVED

**Decision**: Both inline-default and managed-preset. The default code path sends the full algorithm config inline on every production (no Auphonic-side state, works immediately after setting the API key). For power users, `clm recordings auphonic preset sync` creates a preset named `"CLM Lecture Recording"` in their Auphonic account; setting `[recordings.auphonic] preset = "CLM Lecture Recording"` switches the backend to reference-by-name. The implementation cost is modest (~100 LOC for the sync command and the conditional in `AuphonicBackend.submit`).

### Q7. `AuphonicClient` location — RESOLVED

**Decision**: `clm.recordings.workflow.backends.auphonic_client`. If a second consumer appears (e.g. a future `clm voiceover` Auphonic ASR path), we promote to a higher-level location.

---

## 14. Summary of patterns used

| Pattern | Where | Why |
|---|---|---|
| **Strategy** | `ProcessingBackend` Protocol | Swap backends interchangeably |
| **Template Method** | `AudioFirstBackend` | Share assembly logic across ONNX and External |
| **Dependency Inversion** | `JobManager` depends on Protocol | Watcher/web/CLI don't know concrete backends |
| **Mediator** | `JobManager` | Single place that mutates jobs; triggers and backends are decoupled |
| **Observer / Pub-Sub** | `EventBus` | Backends/JobManager emit; Web/CLI subscribe |
| **Capability query** | `BackendCapabilities` | Conditional UI without instanceof checks |
| **Repository** | `JobStore` | Persistence abstraction, swap JSON for SQLite later |
| **Adapter** | `AuphonicClient` | HTTP API → Python domain objects |

---

## 15. Files created, modified, deleted

### New files
```
src/clm/recordings/workflow/
  jobs.py                         # ProcessingJob, JobState, ProcessingOptions, BackendCapabilities
  job_manager.py                  # JobManager, JobContext
  job_store.py                    # JobStore protocol + JsonFileJobStore
  event_bus.py                    # EventBus (already partially exists as SSE queue)
  backends/
    __init__.py
    base.py                       # ProcessingBackend Protocol
    audio_first.py                # AudioFirstBackend (Template Method)
    onnx.py                       # OnnxAudioFirstBackend (port from backends.py)
    external.py                   # ExternalAudioFirstBackend (port from backends.py)
    auphonic.py                   # AuphonicBackend (new)
    auphonic_client.py            # HTTP client for Auphonic API (new)
```

### Modified files
```
src/clm/infrastructure/config.py          # RecordingsConfig + AuphonicConfig
src/clm/recordings/workflow/watcher.py    # backend-agnostic
src/clm/recordings/web/app.py             # wire JobManager
src/clm/recordings/web/routes.py          # /jobs, /jobs/{id}/cancel, /backends
src/clm/recordings/web/templates/*.html   # Jobs panel, capability-conditional UI
src/clm/cli/commands/recordings.py        # backends/submit/jobs/auphonic subcommands
pyproject.toml                            # httpx in [recordings]
```

### Deleted files (after Phase D)
```
src/clm/recordings/workflow/backends.py   # old protocol + ExternalBackend/OnnxBackend
```

### New tests
```
tests/recordings/test_jobs.py
tests/recordings/test_job_manager.py
tests/recordings/test_job_store.py
tests/recordings/test_audio_first_backend.py
tests/recordings/test_auphonic_client.py   # uses respx or recorded cassettes
tests/recordings/test_auphonic_backend.py
tests/recordings/test_watcher_agnostic.py  # replaces parts of test_watcher.py
```

---

## 16. Confirmed decisions

All six outstanding decisions have been resolved with the user:

1. **Default backend**: `onnx`. Fresh installs work offline without an API key; users opt into Auphonic by setting `processing_backend = "auphonic"` and providing a key.
2. **Preset management**: both inline and managed-preset — see Q6 above. Inline is the default and zero-config path.
3. **Job store location**: per-recordings-tree at `<recordings-root>/.clm/jobs.json` — see Q2.
4. **Webhooks**: **not supported in v1**. Polling only. §10 describes the polling strategy. Webhooks can be added as a Protocol extension later if demand appears.
5. **External backend**: kept. Users with iZotope RX 11 licenses get the best quality this way.
6. **Type name**: `ProcessingBackend`, matching `ProcessingJob`, `ProcessingOptions`. The legacy protocol in `backends_legacy.py` uses the same name in its own module; imports disambiguate via `from .backends_legacy import ProcessingBackend as LegacyProcessingBackend` where needed during the transition.

The design is now ready for implementation. Phase A (§12) is the natural starting point.
