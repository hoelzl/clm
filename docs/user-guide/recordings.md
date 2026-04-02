# Recording Management

CLM includes a recording management module for handling the video recording
workflow of educational courses. It automates audio processing, tracks which
recordings belong to which lectures, and provides tools for comparing
processing pipelines.

## Overview

The recording workflow is:

1. **Record** a lecture with OBS (produces `.mkv` or `.mp4` files)
2. **Process** the recording through the audio pipeline (noise reduction, loudness normalization)
3. **Track** which recordings belong to which lectures in your course

The audio processing pipeline replaces manual tools like iZotope RX with
an automated, cross-platform solution using DeepFilterNet3 (via ONNX Runtime) and FFmpeg.

## Installation

```bash
# Install CLM with recording support
pip install -e ".[recordings]"
```

### External Dependencies

The recording module requires FFmpeg as an external tool. The DeepFilterNet3
ONNX model is downloaded automatically on first use and cached locally.

| Tool | Purpose | Install |
|------|---------|---------|
| **ffmpeg** | Audio extraction, filtering, encoding, muxing | `winget install FFmpeg` (Windows) or `pacman -S ffmpeg` (Arch) |
| **onnxruntime** | AI-based noise reduction (DeepFilterNet3) | Included in `[recordings]` extra |

Verify installation:

```bash
clm recordings check
```

This shows the status of each required dependency (ffmpeg path, onnxruntime version).

## Processing Recordings

### Single File

```bash
# Process with auto-naming (creates raw_recording_final.mp4)
clm recordings process raw_recording.mkv

# Specify output path
clm recordings process raw_recording.mkv -o final_lecture.mp4

# Keep intermediate files for debugging
clm recordings process raw_recording.mkv --keep-temp
```

The processing pipeline performs five steps:

1. **Extract audio** from video as mono 16-bit WAV
2. **DeepFilterNet3 ONNX** AI noise reduction (streaming frame-by-frame inference)
3. **FFmpeg filters**: highpass (remove rumble), compressor (even out volume), two-pass EBU R128 loudness normalization
4. **AAC encode** the processed audio
5. **Mux** processed audio back into the original video (video stream is copied, not re-encoded)

### Batch Processing

```bash
# Process all videos in a directory
clm recordings batch ~/Recordings -o ~/Processed

# Search subdirectories
clm recordings batch ~/Recordings -r

# Use custom config
clm recordings batch ~/Recordings -c my_config.json
```

Supported video formats: `.mkv`, `.mp4`, `.avi`, `.mov`, `.webm`, `.ts`.

Batch processing automatically skips files that already have output, so you
can safely re-run it after adding new recordings.

### Custom Configuration

Processing settings can be configured via:

1. **CLM's TOML config file** (`~/.config/clm/config.toml`):

   ```toml
   [recordings.processing]
   denoise_atten_lim = 35.0      # Noise reduction strength (0=unlimited, 30-50)
   sample_rate = 48000           # Audio sample rate
   audio_bitrate = "192k"        # AAC bitrate
   video_codec = "copy"          # "copy" = no re-encoding
   highpass_freq = 80            # Remove rumble below this Hz
   loudnorm_target = -16.0       # Target loudness in LUFS
   ```

2. **A standalone JSON config file** (passed via `-c`):

   ```json
   {
     "denoise_atten_lim": 35.0,
     "sample_rate": 48000,
     "audio_bitrate": "192k",
     "video_codec": "copy",
     "output_extension": "mp4",
     "audio_filters": {
       "highpass_freq": 80,
       "loudnorm_target": -16.0
     }
   }
   ```

3. **Environment variables**:

   ```bash
   export CLM_RECORDINGS__PROCESSING__DENOISE_ATTEN_LIM=40.0
   export CLM_RECORDINGS__PROCESSING__LOUDNORM_TARGET=-18.0
   ```

## A/B Comparison

When migrating between audio processing tools (e.g., iZotope RX to the
ONNX pipeline), use the comparison tool to evaluate quality:

```bash
# Compare two processed versions
clm recordings compare izotope.mp4 onnx.mp4 \
    --label-a "iZotope RX 11" \
    --label-b "DeepFilterNet3 ONNX"

# Include original for reference, compare first 30 seconds
clm recordings compare izotope.mp4 onnx.mp4 \
    --original raw.mkv \
    --start 10 --duration 30 \
    -o comparison.html
```

This generates a self-contained HTML page with:
- Side-by-side audio players for each version
- A blind test mode where versions are randomly assigned to X and Y

Open `comparison.html` in a browser to listen and compare.

## Recording State

Each course can track which recordings belong to which lectures. State is
stored as JSON files under `~/.config/clm/recordings/`.

### Viewing Status

```bash
clm recordings status python-basics
```

This shows a table of all lectures with their recording status, part counts,
and which lecture is next to record.

### State File Format

Each course has a JSON state file (`~/.config/clm/recordings/<course-id>.json`):

```json
{
  "course_id": "python-basics",
  "lectures": [
    {
      "lecture_id": "010-intro",
      "display_name": "Introduction to Python",
      "parts": [
        {
          "part": 1,
          "raw_file": "/obs/2025-03-01_10-15-22.mkv",
          "processed_file": "/output/010-intro_part1.mp4",
          "git_commit": "a1b2c3def456",
          "git_dirty": false,
          "recorded_at": "2025-03-01T10:15:22",
          "status": "processed"
        }
      ]
    }
  ],
  "next_lecture_index": 1,
  "continue_current_lecture": false
}
```

Recording status values: `pending`, `processing`, `processed`, `failed`.

### Assignment Modes

- **Sequential mode** (default): Each new recording is assigned to the next
  unrecorded lecture, advancing `next_lecture_index`.
- **Continue mode** (`continue_current_lecture: true`): New recordings are
  assigned as additional parts of the current lecture (for multi-part lectures).
- **Manual assignment**: Recordings can be assigned to any specific lecture
  or reassigned after the fact.

### Git Integration

When a recording is assigned to a lecture, the tool captures the current
git commit hash and dirty status of the course repository. This lets you
correlate recordings with the exact version of the slides that were presented.

## Course Configuration

Courses are configured in CLM's TOML config:

```toml
[recordings]
obs_output_dir = "/path/to/obs/recordings"
active_course = "python-basics"
auto_process = false

[[recordings.courses]]
id = "python-basics"
name = "Python for Beginners"
spec_file = "/path/to/python-basics/course.xml"
course_repo = "/path/to/python-basics"
input_dir = "/path/to/obs/recordings"
output_dir = "/path/to/processed/python-basics"
```

## Troubleshooting

### "onnxruntime: NOT FOUND"

Install the `[recordings]` extra which includes onnxruntime:

```bash
pip install -e ".[recordings]"
```

The DeepFilterNet3 ONNX model (~15 MB) is downloaded automatically on first
use and cached locally.

### "ffmpeg: NOT FOUND"

Install FFmpeg for your platform:
- **Windows**: `winget install FFmpeg` or download from https://ffmpeg.org/download.html
- **Arch Linux**: `pacman -S ffmpeg`
- **Ubuntu/Debian**: `apt install ffmpeg`

### Processing is slow

- The video codec defaults to `copy` (no re-encoding), which is fast. If
  you changed `video_codec`, set it back to `copy`.
- ONNX noise reduction is the slowest step. It runs on CPU by default.
- Loudness normalization uses two-pass for accuracy, which doubles the
  FFmpeg filter step.

### Output audio sounds wrong

Check your filter settings:
- `highpass_freq`: 80 Hz is safe for voice. Raise to 100-120 if you have
  persistent low-frequency hum.
- `loudnorm_target`: -16 LUFS is standard for YouTube/online video. Use
  -14 for louder output or -18 for more dynamic range.
- `denoise_atten_lim`: 35 dB is moderate. Lower values (25-30) are
  gentler; higher values (40-50) are more aggressive. 0 = unlimited.
