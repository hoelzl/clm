# Bug Report: `clm voiceover sync` crashes with exit code 127 on Windows

## Summary

The `clm voiceover sync` command crashes with exit code 127 on Windows when using
the `faster-whisper` backend with CUDA. The crash occurs during or after the
transcription step, preventing the pipeline from reaching the transition detection
and slide-matching stages. The transcription itself completes successfully — the
crash appears to be triggered by ctranslate2/CUDA resource cleanup.

## Environment

- **OS:** Windows 11 Pro 10.0.26200
- **Python:** 3.13 (managed by uv)
- **CLM version:** 1.1.9 (installed both as uv tool and in project venv)
- **GPU:** NVIDIA GPU with CUDA support (ctranslate2 v4.7.1 with full CUDA)
- **faster-whisper model:** large-v3
- **Device:** auto (resolves to cuda)

## Reproduction

### Minimal reproduction

```bash
clm voiceover sync --lang de --dry-run \
  "D:\OBS\Recordings\AZAV ML\01 Kursübersicht und Vorbereitung\03 Das Requests Package (Teil 1).mp4" \
  "slides\module_550_ml_azav\topic_017_requests_azav\slides_010v_requests.py"
```

**Output:**
```
Parsing slides:
slides\module_550_ml_azav\topic_017_requests_azav\slides_010v_requests.py
  Found 40 slide groups
Transcribing: D:\OBS\Recordings\AZAV ML\01 Kursübersicht und Vorbereitung\03
Das Requests Package (Teil 1).mp4 (backend=faster-whisper, device=auto)
```
Then the process exits with code 127. No Python traceback. No error message.

The same behavior occurs with:
- `--dry-run` or without it
- `--keep-audio` or without it
- Running via `clm.exe` (uv tool), `.venv/Scripts/clm.exe`, or `python -c "..."` with the CLI entry point
- Any video file (tested with 4 different videos, 90–155 MB)

### What works

1. **Transcription alone works:**
   ```python
   from faster_whisper import WhisperModel
   model = WhisperModel('large-v3', device='auto', compute_type='float16')
   segments, info = model.transcribe(video_path, language='de')
   for seg in segments:
       pass  # consumes iterator
   print(f'{count} segments')  # prints successfully
   # Exit code: 0
   ```

2. **Audio extraction alone works:**
   ```python
   from clm.voiceover.transcribe import extract_audio
   audio = extract_audio(video_path)  # calls ffmpeg via subprocess
   # Exit code: 0
   ```

3. **Step-by-step CLM pipeline works (but still exits 127):**
   ```python
   from clm.voiceover.transcribe import extract_audio, create_backend
   audio = extract_audio(video)        # OK
   backend = create_backend('faster-whisper', device='auto')  # OK
   transcript = backend.transcribe(audio, language='de')       # OK, returns result
   print(f'{len(transcript.segments)} segments')               # prints "346 segments"
   # Exit code: 127 (crash during Python shutdown, AFTER script completes)
   ```

4. **Transition detection alone works:**
   ```bash
   clm voiceover detect "video.mp4"
   # Exit code: 0
   ```
   This uses OpenCV (cv2) for frame extraction without loading CUDA/ctranslate2.

### What crashes

5. **`transcribe_video()` function — crash prevents return:**
   ```python
   from clm.voiceover.transcribe import transcribe_video
   transcript = transcribe_video(video, language='de', device='auto')
   print(f'{len(transcript.segments)} segments')  # NEVER REACHED
   # Exit code: 127
   ```
   The function internally calls `extract_audio()` then `backend.transcribe()`.
   Logger output (when redirected to a file) confirms the transcription completes:
   ```
   clm.voiceover.transcribe: Transcription complete: 367 segments, 1487.0s duration
   ```
   But the function never returns to the caller — the process crashes between
   the logger statement and the `return` in `transcribe_video()`.

6. **Full CLI `voiceover sync` — same crash point:**
   The Rich console output stops after "Transcribing: ..." and the process exits 127.
   With logging redirected to a file, we can confirm the transcription completes but
   the console.print on line 110-113 of `voiceover.py` never executes.

## Key observations

### The crash is NOT during transcription

The transcription itself succeeds every time (verified via logging and via the
step-by-step approach where `backend.transcribe()` returns). The crash happens
**after** the transcription completes, during one of:

- The `finally` block in `transcribe_video()` that calls `audio_path.unlink()`
- The return from `transcribe_video()` back to the `sync` command
- Python garbage collection / reference counting cleanup of ctranslate2 objects

### Step-by-step vs `transcribe_video()` difference

When calling `extract_audio()` and `backend.transcribe()` separately (test 3),
the script completes and the crash happens during **Python interpreter shutdown**.
When calling `transcribe_video()` (test 5), the crash happens **inside the function**
before it can return. The difference may be related to:

- Stack frame depth / object lifetimes
- The `try/finally` block that calls `audio_path.unlink()`
- Interaction between the tempfile handle and ctranslate2 CUDA state

### Exit code 127 is unusual

Exit code 127 normally means "command not found" in bash. In this context it likely
indicates a native code crash (segfault or similar) in ctranslate2 or the CUDA
runtime that the Python interpreter translates to this exit code on Windows. There
is no Python traceback because the crash happens at the C/C++ level.

## Working workaround

We successfully ran the full pipeline by splitting it into two separate Python
processes:

**Process 1 — Transcribe (crashes at shutdown, but saves result first):**
```python
from clm.voiceover.transcribe import extract_audio, create_backend
audio = extract_audio(video)
backend = create_backend('faster-whisper', device='auto')
transcript = backend.transcribe(audio, language='de')
# Save to JSON immediately
json_path.write_text(json.dumps({...}))
del backend  # attempt cleanup before shutdown
# Process exits 127 during shutdown — but JSON is saved
```

**Process 2 — Detect + Match + Align + Polish + Write (no CUDA, exits 0):**
```python
from clm.voiceover.transcribe import Transcript, TranscriptSegment
# Load transcript from JSON
transcript = Transcript(segments=[...], ...)
# Rest of pipeline: detect_transitions, match_events_to_slides, align_transcript, etc.
# Exits 0 — no CUDA involvement
```

## Suggested investigation paths

1. **Isolate the crash to ctranslate2 shutdown:** Try adding `del model` / `del backend._model`
   and `gc.collect()` before `transcribe_video()` returns, to force cleanup while the
   process is still in a good state. If this fixes the crash, the issue is in ctranslate2's
   destructor interacting with CUDA shutdown order.

2. **Check `audio_path.unlink()` interaction:** The `finally` block in `transcribe_video()`
   deletes the temp audio file. Test whether removing this (or making it conditional)
   changes the crash behavior. The `--keep-audio` flag didn't help in our tests, but
   the flag only controls the `if not keep_audio:` branch — the `finally` block still
   runs.

3. **Test with `device='cpu'`:** If the crash disappears with CPU-only transcription,
   it confirms this is a CUDA cleanup issue.

4. **Check ctranslate2 issue tracker:** This may be a known issue with ctranslate2 on
   Windows when CUDA contexts are destroyed during Python finalization.

5. **Subprocess isolation:** A robust fix might be to run the transcription step in a
   subprocess automatically (via `multiprocessing` or `subprocess`), similar to our
   manual workaround. This avoids the CUDA cleanup crash entirely.

6. **Rich Console interaction:** The crash might be exacerbated by Rich Console holding
   file handles or terminal state. Test with `console = Console(quiet=True)` or plain
   `print()` to rule this out.

## Relevant source files

| File | Role |
|------|------|
| `src/clm/cli/commands/voiceover.py` | CLI `sync` command — calls `transcribe_video()` at line 101-109 |
| `src/clm/voiceover/transcribe.py` | `transcribe_video()` — `extract_audio()` + `backend.transcribe()` + cleanup |
| `src/clm/voiceover/transcribe.py:399-455` | `extract_audio()` — ffmpeg subprocess call |
| `src/clm/voiceover/transcribe.py:74-178` | `FasterWhisperBackend` — lazy model loading + transcription |
| `src/clm/voiceover/keyframes.py` | Transition detection (OpenCV, no CUDA) — works fine |

## Impact

The crash makes `clm voiceover sync` unusable as a single command on Windows with
CUDA. The manual workaround (two separate processes) works but requires custom
scripting. An in-CLI fix would restore the intended single-command workflow.
