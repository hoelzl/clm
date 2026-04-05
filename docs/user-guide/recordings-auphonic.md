# Auphonic Backend for Recordings

CLM's recordings workflow supports three processing backends. This guide
covers the **Auphonic** backend — a commercial cloud service that applies
speech-aware denoising, leveling, loudness normalization, and optional
filler/silence removal to your lecture recordings.

## Prerequisites

- An Auphonic account with an API key (<https://auphonic.com/engine/account/>).
- `ffmpeg` available in your PATH (for the fallback backends and local playback).
- CLM installed with the `[recordings]` extra:

  ```bash
  pip install -e ".[recordings]"
  ```

## Configuration

Set the backend to `"auphonic"` and provide your API key. The two
required fields can be set via environment variables or the TOML config
file.

### Via environment variables

```bash
export CLM_RECORDINGS__PROCESSING_BACKEND=auphonic
export CLM_RECORDINGS__AUPHONIC__API_KEY=<your-api-key>
```

### Via TOML config

In your project `clm.toml` or user config (`~/.config/clm/config.toml`):

```toml
[recordings]
processing_backend = "auphonic"

[recordings.auphonic]
api_key = "<your-api-key>"
```

### Full Auphonic config reference

| Field | Default | Description |
|-------|---------|-------------|
| `api_key` | `""` | Auphonic API key (required when backend is `auphonic`) |
| `preset` | `""` | Managed preset name; empty = inline algorithms |
| `poll_timeout_minutes` | `120` | Max minutes to wait for a job before marking it failed |
| `request_cut_list` | `false` | Request a DaVinci Resolve EDL cut list on every production |
| `apply_cuts` | `false` | Reserved for Phase 2 (auto-apply cuts) |
| `base_url` | `https://auphonic.com` | API base URL (override for tests/staging only) |
| `upload_chunk_size` | `8388608` | Streaming upload chunk size in bytes (8 MiB) |
| `upload_retries` | `3` | Upload retry attempts on transient failure |
| `download_retries` | `3` | Download retry attempts on transient failure |

## Quick Start

### 1. Verify the backend is selected

```bash
clm recordings backends
```

You should see `auphonic` marked with a checkmark under "Active".

### 2. Submit a recording

```bash
clm recordings submit path/to/topic--RAW.mp4 --root ~/Recordings
```

For synchronous backends this blocks until done; for Auphonic it returns
once the video is uploaded and processing has started on Auphonic's
servers.

### 3. Track progress

```bash
# CLI
clm recordings jobs list --root ~/Recordings

# Web dashboard
clm recordings serve ~/Recordings
```

The web dashboard shows a live "Processing Jobs" panel with upload and
processing progress bars. It refreshes automatically via SSE.

### 4. Cancel a job

```bash
clm recordings jobs cancel <id-prefix> --root ~/Recordings
```

ID prefixes are accepted for convenience (e.g. `a3b` matches `a3b4e56f...`).

## Managed Presets (Optional)

By default, CLM sends the full algorithm configuration inline with every
Auphonic production — no Auphonic-side setup required. For power users
who want to edit the preset in Auphonic's web UI:

```bash
# Create or update the "CLM Lecture Recording" preset in your account
clm recordings auphonic preset sync

# Then reference it in config
# [recordings.auphonic]
# preset = "CLM Lecture Recording"
```

Both modes (inline and preset-reference) produce identical output. The
preset route is convenient when you want to tweak denoise strength or
loudness targets in Auphonic's UI without editing CLM config files.

```bash
# List all presets in your account
clm recordings auphonic preset list
```

## How It Works

1. **Create** an Auphonic production (with inline algorithms or a
   preset reference).
2. **Upload** the raw video (streamed; the dashboard shows upload
   progress).
3. **Start** processing. The job enters `PROCESSING` state.
4. CLM's **poller** queries Auphonic every 30 seconds. After 30 minutes
   it backs off to 5-minute intervals.
5. On **DONE**: download the processed video to `final/`, archive the
   raw file to `archive/`, mark the job `COMPLETED`.
6. On **ERROR** or local timeout: mark the job `FAILED`. The user can
   re-submit.

## File Watcher Integration

The file watcher (`clm recordings serve`) detects new `--RAW.mp4` files
in `to-process/` and submits them to the configured backend
automatically. For Auphonic, this means a recording that finishes in OBS
is uploaded and processed without manual intervention.

## Comparison with Other Backends

| Feature | ONNX (local) | External (RX 11) | Auphonic (cloud) |
|---------|--------------|-------------------|-------------------|
| Requires internet | No | No | Yes |
| Requires API key | No | No | Yes |
| Processing model | audio-first, sync | audio-first, sync | video-in/video-out, async |
| Cut lists | No | No | Yes |
| Filler removal | No | No | Yes |
| Silence removal | No | No | Yes |
| Chapter detection | No | No | Yes |

## Troubleshooting

- **"api_key is empty"** error at startup: set `CLM_RECORDINGS__AUPHONIC__API_KEY`
  in your environment or TOML config.
- **Upload timeouts**: check your internet connection; the default upload
  timeout is 1 hour. For very large files on slow connections, you may
  need to use a faster link.
- **Job stuck in PROCESSING for > 2 hours**: CLM marks it `FAILED` by
  default after 120 minutes. If your recordings are unusually long (e.g.
  > 3 hours of audio), increase `poll_timeout_minutes`.
- **Credits**: Auphonic uses a credit-per-hour-of-audio model. Check
  your balance at <https://auphonic.com/engine/account/>.
