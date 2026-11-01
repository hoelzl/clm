- `clm harvest report` (and the other harvest/recordings commands that run
  ffmpeg, ffprobe or the ASR worker) no longer consume the caller's stdin.
  Driving them from a `while read …; done <<LIST` shell loop silently skipped
  iterations because a child process inherited the pipe; ffmpeg reads stdin
  for its interactive `q` command. Every media-tool child (voiceover
  `extract_audio` / PCM decode / ASR worker / `probe_duration`, and the
  recordings `run_subprocess` helper) now runs with `stdin=DEVNULL`, and
  ffmpeg gets `-nostdin` (#1103).
