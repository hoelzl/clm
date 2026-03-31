# CLM {version} — CLI Command Reference

## Global Options

```
clm [OPTIONS] COMMAND [ARGS]...
```

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `--cache-db-path PATH` | Path to cache database (default: `clm_cache.db`) |
| `--jobs-db-path PATH` | Path to job queue database (default: `clm_jobs.db`) |

## Commands

### `clm build`

Build a course from a spec file.

```
clm build [OPTIONS] SPEC_FILE
```

Key options:

| Option | Description |
|--------|-------------|
| `-d, --data-dir DIR` | Source data directory |
| `-o, --output-dir DIR` | Output directory (overrides spec targets) |
| `-w, --watch` | Watch for changes and auto-rebuild |
| `--watch-mode [fast\|normal]` | `fast` = notebooks only; `normal` = all formats |
| `--ignore-cache` | Reprocess all files (still updates cache) |
| `--clear-cache` | Clear cache before building |
| `--incremental` | Keep directories, only write newly processed files |
| `--workers [direct\|docker]` | Worker execution mode |
| `--notebook-workers N` | Number of notebook workers |
| `--plantuml-workers N` | Number of PlantUML workers |
| `--drawio-workers N` | Number of Draw.io workers |
| `--notebook-image TEXT` | Docker image for notebook workers |
| `-O, --output-mode [default\|verbose\|quiet\|json]` | Progress output mode |
| `-L, --language [de\|en]` | Generate only one language |
| `--speaker-only` | Generate only speaker notes |
| `-T, --targets TEXT` | Comma-separated target names from spec |
| `--image-mode [duplicated\|shared]` | Image storage strategy |
| `--image-format [png\|svg]` | Image output format |
| `--inline-images` | Embed images as base64 in notebooks |

Examples:

```bash
clm build course.xml
clm build course.xml -w --watch-mode fast
clm build course.xml --workers docker -T students,solutions
clm build course.xml --clear-cache -L en
```

### `clm targets`

List output targets defined in a course spec file.

```
clm targets SPEC_FILE
```

### `clm outline`

Generate a Markdown outline of a course.

```
clm outline [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `-o, --output FILE` | Write to file (mutually exclusive with `-d`) |
| `-d, --output-dir DIR` | Write both languages to directory |
| `-L, --language [de\|en]` | Language selection |

Examples:

```bash
clm outline course.xml
clm outline course.xml -L de
clm outline course.xml -d ./docs
```

### `clm status`

Show CLM system status (workers, databases, configuration).

```
clm status
```

### `clm info`

Show version-accurate CLM documentation for agents and users.

```
clm info [TOPIC]
```

Without a topic argument, lists available topics. With a topic, displays
the full documentation for that topic.

Examples:

```bash
clm info                # List available topics
clm info spec-files     # Spec file format reference
clm info commands       # CLI command reference
clm info migration      # Breaking changes and migration guide
```

### `clm config`

Manage CLM configuration files.

| Subcommand | Description |
|------------|-------------|
| `config init` | Create an example configuration file |
| `config locate` | Show configuration file locations |
| `config show` | Show current configuration values |

### `clm db`

Database management commands.

| Subcommand | Description |
|------------|-------------|
| `db info` | Show database information |
| `db vacuum` | Compact databases |

### `clm delete-database`

Delete CLM databases (job queue and/or cache).

### `clm docker`

Build and push CLM Docker images.

| Subcommand | Description |
|------------|-------------|
| `docker build` | Build Docker images for CLM workers |
| `docker build-quick` | Quick rebuild using local cache |
| `docker cache-info` | Show build cache information |
| `docker list` | List available services and images |
| `docker pull` | Pull images from Docker Hub |
| `docker push` | Push images to Docker Hub |

### `clm git`

Manage git repositories for course output directories.

| Subcommand | Description |
|------------|-------------|
| `git init SPEC_FILE` | Initialize git repos in output directories |
| `git status SPEC_FILE` | Show status of all output repos |
| `git commit SPEC_FILE` | Stage and commit changes |
| `git push SPEC_FILE` | Push commits to remote |
| `git sync SPEC_FILE -m MSG` | Commit and push in one operation |
| `git reset SPEC_FILE` | Reset to remote tracking branch |

Key options for `git commit`, `git push`, and `git sync`:

| Option | Commands | Description |
|--------|----------|-------------|
| `-m, --message` | commit, sync | Commit message (required unless `--amend`) |
| `--amend` | commit, sync | Amend previous commit instead of creating new one |
| `--force-with-lease` | push, sync | Safe force push (implied by `--amend` on sync) |
| `--target` | all | Filter to specific output target |
| `--dry-run` | all | Show what would be done |

Examples:

```bash
clm git commit course.xml -m "Update slides"
clm git commit course.xml --amend              # amend, keep message
clm git commit course.xml --amend -m "new msg" # amend with new message
clm git push course.xml --force-with-lease     # safe force push
clm git sync course.xml -m "Weekly update"     # commit + push
clm git sync course.xml --amend                # amend + force push
clm git sync course.xml --force-with-lease -m "msg"  # commit + force push
```

### `clm jobs`

Manage CLM jobs.

### `clm workers`

Manage CLM workers.

| Subcommand | Description |
|------------|-------------|
| `workers list` | List registered workers |
| `workers cleanup` | Clean up dead workers and orphaned processes |

### `clm summarize`

Generate LLM-powered markdown summaries of course content. Requires `clm[summarize]` extra.

```
clm summarize [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `--audience [client\|trainer]` | Target audience (required) |
| `--granularity [notebook\|section]` | Summary level (default: `notebook`) |
| `--style [prose\|bullets]` | Output formatting (default: `prose`) |
| `-L, --language [de\|en]` | Language for outline structure (default: `en`) |
| `-o, --output FILE` | Write output to file |
| `-d, --output-dir DIR` | Write to directory with auto-generated filename |
| `--model TEXT` | LLM model identifier |
| `--api-base TEXT` | Custom API base URL |
| `--no-cache` | Skip cache, re-generate all summaries |
| `--dry-run` | Show what would be summarized (no LLM calls) |
| `--no-progress` | Disable progress bar |

Examples:

```bash
clm summarize course.xml --audience client --dry-run
clm summarize course.xml --audience trainer -o summary.md
clm summarize course.xml --audience client -d ./docs
clm summarize course.xml --audience trainer --model openai/gpt-4o
clm summarize course.xml --audience client --style bullets
```

### `clm voiceover`

Synchronize video recordings with slide files to generate speaker notes.
Requires `clm[voiceover]` extra.

#### `clm voiceover sync`

Full pipeline: transcribe video, detect transitions, match slides, insert notes.

```
clm voiceover sync VIDEO SLIDES --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) (required) |
| `--mode [verbatim\|polished]` | `verbatim` = raw transcript; `polished` = LLM cleanup (default: `verbatim`) |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--slides-range TEXT` | Slide range to update (e.g. `5-20`) |
| `--dry-run` | Show mapping without writing |
| `-o, --output PATH` | Output file |
| `--keep-audio` | Keep extracted audio file |
| `--model TEXT` | LLM model for polished mode |

#### `clm voiceover transcribe`

Extract transcript from a video file.

```
clm voiceover transcribe VIDEO [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `-o, --output PATH` | Output file |

#### `clm voiceover detect`

Detect slide transitions in a video using frame differencing.

```
clm voiceover detect VIDEO [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output PATH` | Output file |

#### `clm voiceover identify`

Match video frames to slides using OCR + fuzzy matching.

```
clm voiceover identify VIDEO SLIDES --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) (required) |
| `-o, --output PATH` | Output file |

Examples:

```bash
clm voiceover sync video.mp4 slides.py --lang de
clm voiceover sync video.mp4 slides.py --lang en --mode polished
clm voiceover sync video.mp4 slides.py --lang de --slides-range 5-20 --dry-run
clm voiceover transcribe video.mp4 --lang de -o transcript.txt
clm voiceover detect video.mp4 -o transitions.txt
clm voiceover identify video.mp4 slides.py --lang de
```

### `clm polish`

Polish existing speaker notes in slide files using an LLM. Removes filler words,
fixes grammar, and preserves technical terms. Requires `clm[summarize]` extra (openai).

```
clm polish SLIDES --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Language of notes (`de` or `en`) (required) |
| `--slides-range TEXT` | Slide range to polish (e.g. `5-10`) |
| `--dry-run` | Show polished text without writing |
| `-o, --output PATH` | Output file |
| `--model TEXT` | LLM model identifier |

Examples:

```bash
clm polish slides.py --lang de
clm polish slides.py --lang en --slides-range 5-10 --dry-run
clm polish slides.py --lang de --model openai/gpt-4o -o polished.py
```

### `clm recordings`

Manage video recordings for educational courses. Provides audio processing,
recording-to-lecture assignment, and status tracking.

#### `clm recordings check`

Check that recording dependencies (ffmpeg, deepfilter) are installed.

```
clm recordings check
```

#### `clm recordings process`

Process a single recording through the audio pipeline (DeepFilterNet + FFmpeg filters).

```
clm recordings process INPUT_FILE [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output PATH` | Output file (default: auto-named `*_final.mp4`) |
| `-c, --config PATH` | Config JSON file |
| `--keep-temp` | Keep intermediate files for debugging |

#### `clm recordings batch`

Batch-process all recordings in a directory. Skips files that already have output.

```
clm recordings batch INPUT_DIR [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output-dir DIR` | Output directory (default: `INPUT_DIR/processed`) |
| `-c, --config PATH` | Config JSON file |
| `-r, --recursive` | Search subdirectories |

#### `clm recordings status`

Show recording status for a course, including per-lecture recording state.

```
clm recordings status COURSE_ID
```

#### `clm recordings compare`

Generate an A/B audio comparison HTML page with embedded audio players
and blind test mode.

```
clm recordings compare VERSION_A VERSION_B [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--label-a TEXT` | Label for version A (default: "Version A") |
| `--label-b TEXT` | Label for version B (default: "Version B") |
| `--original PATH` | Original unprocessed file (optional) |
| `-o, --output PATH` | Output HTML file (default: `comparison.html`) |
| `--start FLOAT` | Start time in seconds |
| `--duration FLOAT` | Duration in seconds (default: 60) |

Examples:

```bash
clm recordings check
clm recordings process raw.mkv
clm recordings process raw.mkv -o final.mp4 --keep-temp
clm recordings batch ~/Recordings -o ~/Processed -r
clm recordings status python-basics
clm recordings compare izotope.mp4 deepfilter.mp4 --label-a "iZotope RX" --label-b "DeepFilterNet"
```

### `clm monitor`

Launch real-time TUI monitoring dashboard. Requires `clm[tui]` extra.

### `clm serve`

Start web dashboard server. Requires `clm[web]` extra.

### `clm zip`

Create and manage ZIP archives of course output.

| Subcommand | Description |
|------------|-------------|
| `zip create SPEC_FILE` | Create ZIP archives of output directories |
| `zip list SPEC_FILE` | List directories that would be archived |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `CLM_MAX_CONCURRENCY` | Max concurrent operations (default: 50) |
| `CLM_GIT__REMOTE_TEMPLATE` | Git remote URL template (e.g., `git@github.com-cam:Org/{repo}.git`) |
| `CLM_LLM__MODEL` | Default LLM model for summarize (default: `anthropic/claude-sonnet-4-6`) |
| `CLM_LLM__API_KEY` | API key for LLM provider (or use `OPENAI_API_KEY`) |
| `CLM_LLM__API_BASE` | API base URL (e.g. `https://openrouter.ai/api/v1`) |
| `CLM_LLM__MAX_CONCURRENT` | Max parallel LLM calls (default: 3) |
| `CLM_LLM__TEMPERATURE` | LLM sampling temperature (default: 0.3) |
| `CLM_RECORDINGS__OBS_OUTPUT_DIR` | Directory where OBS saves recordings |
| `CLM_RECORDINGS__ACTIVE_COURSE` | Currently active course ID |
| `CLM_RECORDINGS__AUTO_PROCESS` | Auto-process recordings when detected (default: false) |
| `CLM_RECORDINGS__PROCESSING__DEEPFILTER_ATTEN_LIM` | DeepFilterNet attenuation limit (default: 35.0) |
| `CLM_RECORDINGS__PROCESSING__SAMPLE_RATE` | Audio sample rate (default: 48000) |
| `CLM_RECORDINGS__PROCESSING__LOUDNORM_TARGET` | Loudness target in LUFS (default: -16.0) |
