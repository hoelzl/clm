# Recordings Pipeline — Handover Document

## Session Summary (2026-03-31)

This session accomplished two things and planned a third:

1. **Fixed `uv lock --upgrade` with `UV_EXCLUDE_NEWER`** — committed and pushed
2. **Evaluated audio denoising alternatives** — test app built, approaches compared
3. **Planned next steps** — replace deepfilternet with ONNX, build recording workflow automation

---

## Part 1: Dependency Fixes (Completed)

**Branch**: `feature/recordings-integration`

### Changes committed and pushed

**Commit `6a634fe`** — Supply-chain safety:
- `pyproject.toml`: Added `[tool.uv]` `exclude-newer = "14 days"` for supply-chain protection
- `[tool.uv.exclude-newer-package]`: Set `torch = false`, `torchvision = false`, `torchaudio = false` (PyTorch's cu130 index has no upload dates, incompatible with exclude-newer)
- Lowered `langgraph>=1.1.3` → `>=1.1.2` (1.1.3 not yet within cutoff)
- Refreshed `uv.lock`

**Commit `a5a0f4a`** — Recordings module:
- Full recordings module with CLI, processing pipeline, tests (52 tests)

### `deepfilternet` NOT added to pip dependencies
The `deepfilternet` package (v0.5.6) pins `numpy<2.0` and `packaging<24.0`, conflicting with the `[ml]` extra's `numpy>=2.0.1`. The package is also unmaintained (last update Oct 2024, no Python 3.12+ wheels for the Rust core library `deepfilterlib`). This was the motivation for the research below.

---

## Part 2: Audio Denoising Research (Completed)

### Test App

Location: `C:\Users\tc\Programming\Python\Tests\AudioDenoise\`

A standalone comparison tool that tests multiple noise reduction approaches using `uv` inline script metadata for dependency isolation. Each approach is a self-contained script.

#### Approaches tested

| Key | Approach | 48kHz | Quality | Speed (vs real-time) | Status |
|-----|----------|-------|---------|---------------------|--------|
| `onnx` | DeepFilterNet3 ONNX (onnxruntime) | Native | **Best** — on par with CLI | 0.24x (CPU) | **Working** |
| `clearvoice` | ClearVoice MossFormer2_SE_48K | Native | Good but slightly below ONNX | 0.19x (CPU) | Working (needed float32→PCM16 workaround) |
| `noisereduce` | Spectral gating (DSP) | Any | Noticeably worse, too aggressive | 0.02x | Working |
| `dfcli` | DeepFilterNet CLI subprocess | Native | Reference quality | N/A | Broken (no Python 3.12+ wheels) |
| `rnnoise` | Pedalboard + RNNoise VST3 | 48kHz only | Not tested | N/A | Needs VST3 download |
| `nsnet2` | Microsoft NSNet2 | 16kHz only | Not tested | N/A | Broken (scipy compat) |

#### Key findings

1. **DeepFilterNet3 ONNX is the clear winner** — uses a streaming single-file model from `yuyun2000/SpeechDenoiser` that bakes all preprocessing (STFT, ERB bands, normalization) into the model. Inference is ~30 lines of Python. Dependencies: just `onnxruntime` + `numpy` + `soundfile`. No numpy upper bound, works on Python 3.11–3.14.

2. **ClearVoice MossFormer2_SE_48K** has a bug in its audio reader (pydub misinterprets float32 WAV files). Workaround: convert to PCM_16 before passing. Quality is good but slightly below ONNX.

3. **Neither ONNX nor ClearVoice includes the FFmpeg post-processing** (highpass, compressor, EBU R128 loudness normalization) that the current pipeline applies after denoising. This is why the reference audio sounds significantly better — it's louder and more consistent due to compression + normalization. The denoising quality itself is comparable.

4. **No other 48kHz ONNX speech enhancement models exist** — DeepFilterNet3 is unique in this space.

5. **For Windows/production recordings, iZotope RX 11 remains far superior** to all automated approaches. The user will continue using RX 11 for final recordings.

#### ONNX model details

- **Source**: `https://github.com/yuyun2000/SpeechDenoiser/raw/refs/heads/main/48k/denoiser_model.onnx`
- **Size**: 15.4 MB, cached at `~/.cache/audio-denoise/models/deepfilter3_streaming.onnx`
- **Interface**: Streaming frame-by-frame: `input_frame[480]` + `states[45304]` + `atten_lim_db[1]` → `enhanced_frame[480]` + `new_states[45304]` + `lsnr[1]`
- **Originally exported by**: `grazder/DeepFilterNet` (torchDF-changes branch)

---

## Part 3: Planned Changes (For Next Session)

### 3A. Replace deepfilternet with ONNX in the processing pipeline [DONE]

**Completed 2026-04-01.** Replaced the `deepFilter` CLI subprocess with direct ONNX inference via `onnxruntime`.

**Changes made**:
- `utils.py`: Removed `find_deepfilter()`. Added `download_onnx_model()` (caches to platformdirs user cache), `run_onnx_denoise()` (frame-by-frame streaming inference), `check_onnxruntime()`. Updated `check_dependencies()`.
- `pipeline.py`: Replaced `_run_deepfilter()` with `_run_denoise()` (calls `run_onnx_denoise`). Removed deepfilter binary lookup from `__init__`.
- `config.py`: Renamed `deepfilter_atten_lim` → `denoise_atten_lim` (default 35.0 preserved).
- `infrastructure/config.py`: Same field rename in `RecordingsProcessingConfig`.
- `recordings.py` CLI: Updated `check` command for onnxruntime, updated config mapping.
- `pyproject.toml`: Added `onnxruntime>=1.17.0`, `soundfile>=0.12.0`, `numpy>=1.24.0` to `[recordings]`. Replaced `deepfilternet` with `onnxruntime`/`soundfile` in mypy overrides.
- Tests: Updated all `deepfilter_atten_lim` references. All 52 tests pass.

### 3B. Recording Workflow Automation (Web App)

**Goal**: Automate the recording → processing → assembly workflow, integrating with OBS Studio and optionally iZotope RX 11 on Windows.

#### Workflow Overview

Three phases:

```
Phase 1: RECORDING (automated naming)
  User selects lecture in CLM web UI → OBS records → file auto-renamed to structured name

Phase 2: AUDIO PROCESSING (manual or automated)
  Option A (Windows/quality): Drag files into RX 11 Batch Processor → wait → .wav output
  Option B (cross-platform): ONNX pipeline processes automatically

Phase 3: ASSEMBLY (fully automated)
  File watcher detects processed .wav → FFmpeg muxes video + processed audio → final output
```

#### Filename Convention

```
<course-slug>/<section-name>/<topic-name>--RAW.mp4
```

Where `section-name` and `topic-name` are sanitized names from the CLM course spec file (matching the folder structure used for course notebooks). The `--RAW` suffix signals an unprocessed recording. Final output drops the suffix:

```
<course-slug>/<section-name>/<topic-name>.mp4
```

#### Directory Structure

Three separate hierarchies under the recordings root, each mirroring the course folder structure:

```
<recordings-root>/                  # Configurable, often on a different drive (videos are large)
├── to-process/                     # Raw recordings land here; RX 11 also writes .wav here
│   ├── <course-slug>/
│   │   ├── <section-name>/
│   │   │   ├── <topic-name>--RAW.mp4   # Raw OBS recording
│   │   │   ├── <topic-name>--RAW.wav   # RX 11 processed audio (appears alongside .mp4)
│   │   │   └── ...
│   │   └── ...
│   └── ...
├── final/                          # Muxed output (watcher writes here)
│   ├── <course-slug>/
│   │   ├── <section-name>/
│   │   │   ├── <topic-name>.mp4        # Final video with processed audio
│   │   │   └── ...
│   │   └── ...
│   └── ...
└── archive/                        # RAW files moved here after successful assembly
    ├── <course-slug>/
    │   ├── <section-name>/
    │   │   ├── <topic-name>--RAW.mp4
    │   │   ├── <topic-name>--RAW.wav
    │   │   └── ...
    │   └── ...
    └── ...
```

**Workflow**: OBS writes to `to-process/`. RX 11 outputs `.wav` alongside the `.mp4` in the same `to-process/` subdirectory. When the watcher detects both `--RAW.mp4` and `--RAW.wav` present, it muxes the final video into `final/` and moves both RAW files to `archive/`. This avoids the watcher needing to check whether output already exists — the presence of files in `to-process/` always means work to do.

The root directory is configured independently of the notebook output directory (`CLM_RECORDINGS__OUTPUT_DIR` or similar).

#### FFmpeg Mux Command

The exact command for combining video + processed audio (from the user's existing `replace-audio` PowerShell function):

```bash
ffmpeg -i <input_video> -i <input_audio> -c:v copy -c:a aac -map 0:v:0 -map 1:a:0 <output_video>
```

#### Components to Build

**1. CLM Lecture Selection UI** (FastAPI + HTMX)
- Display course structure from CLM spec file (courses → sections → topics)
- Click a topic to "arm" it for recording
- Show currently armed topic with derived filename
- Un-arm button

**2. OBS Integration Module**
- Connect to OBS WebSocket v5 (built into OBS 28+, default `ws://localhost:4455`)
- Detect recording start/stop events
- After recording stops, rename the OBS timestamp-named file to the structured name
- Configurable: host, port, password
- Handle edge cases: OBS not running, multiple files, cancelled recordings

**3. Assembly Watcher**
- Monitor directories for new `--RAW.wav` files (using `watchdog`)
- Stability detection: wait for file size to stop changing
- Match `.wav` to corresponding `--RAW.mp4`
- Run FFmpeg mux command
- Archive RAW files after successful assembly
- Report progress via SSE

**4. Web Dashboard** (HTMX + SSE)
- Recording status: armed topic, OBS connection, recording in progress
- Processing queue: `--RAW.mp4` files awaiting audio processing
- Assembly queue: `--RAW.wav` files detected, assembly status
- Finished recordings list
- Quick actions: open folders in file manager

**5. Processing Backend** (pluggable)
- Protocol/base class: `process(input_wav: Path) -> Path`
- Implementation A: "Wait for external tool" (RX 11 mode — watcher detects `.wav` appearance)
- Implementation B: "Process locally" (ONNX pipeline mode — automatic)
- Selectable per-course or via UI toggle

#### Dependencies to Add

- `obsws-python` — OBS WebSocket v5 client
- `watchdog` — filesystem event monitoring
- `onnxruntime` — for ONNX-based processing (already needed for 3A)
- FastAPI, Jinja2, uvicorn — already in CLM's dependencies

#### Configuration

```toml
[recordings]
root_dir = "D:/Recordings"                # Root dir (to-process/, final/, archive/ created under this)
obs_host = "localhost"
obs_port = 4455
obs_password = ""
filename_separator = "--"
raw_suffix = "--RAW"
stability_check_interval = 2              # seconds between file size polls
stability_check_count = 3                 # consecutive identical polls = stable
processing_backend = "external"           # "external" (RX 11) or "onnx" (local)
```

---

## Current State of Recordings Module

### What exists (on `feature/recordings-integration`)

```
src/clm/recordings/
├── __init__.py
├── git_info.py              # Git commit capture at recording time
├── state.py                 # Per-course recording state (JSON CRUD)
└── processing/
    ├── __init__.py
    ├── batch.py             # Batch processing utilities
    ├── compare.py           # A/B audio comparison HTML page
    ├── config.py            # PipelineConfig (denoise_atten_lim), AudioFilterConfig
    ├── pipeline.py          # 5-step pipeline (extract → ONNX denoise → filters → AAC → mux)
    └── utils.py             # Binary finding, ONNX model download/inference, subprocess execution

src/clm/cli/commands/recordings.py  # CLI: check, process, batch, status, compare
src/clm/infrastructure/config.py    # RecordingsConfig, RecordingsProcessingConfig
tests/recordings/                   # 52 tests
```

### What needs to change

1. ~~**Pipeline step 2**: Replace deepFilter subprocess with ONNX inference~~ **[DONE]**
2. ~~**`utils.py`**: Remove `find_deepfilter()`, add `download_onnx_model()` + ONNX inference~~ **[DONE]**
3. ~~**`config.py`**: Rename `deepfilter_atten_lim` → `denoise_atten_lim`~~ **[DONE]**
4. ~~**CLI `check` command**: Check for `onnxruntime` instead of `deepFilter` binary~~ **[DONE]**
5. ~~**Dependencies**: Replace `deepfilternet` with `onnxruntime` in `[recordings]` extra~~ **[DONE]**
6. **New modules**: OBS integration, assembly watcher, web UI (Phase 3B) — **next**
