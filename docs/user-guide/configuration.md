# Configuration Guide

This guide covers configuration options for CLM courses and the CLM application.

## Course Specification Files

CLM uses XML-based course specification files. For complete documentation of the course spec format, see the **[Spec File Reference](spec-file-reference.md)**.

### Quick Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name>
        <de>Python Kurs</de>
        <en>Python Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Beschreibung</de>
        <en>Description</en>
    </description>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>introduction</topic>
            </topics>
        </section>
    </sections>
</course>
```

### Key Course Elements

| Element | Required | Description |
|---------|----------|-------------|
| `<name>` | Yes | Bilingual course name (`<de>`, `<en>`) |
| `<prog-lang>` | Yes | Programming language (python, cpp, csharp, java, typescript) |
| `<description>` | Yes | Bilingual description |
| `<sections>` | Yes | Section and topic definitions |
| `<output-targets>` | No | Multiple output directories (see below) |
| `<dir-groups>` | No | Additional directories to copy |

### Multiple Output Targets (New in v0.4.x)

Define separate output directories with selective content:

```xml
<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <kinds><kind>code-along</kind></kinds>
        <formats>
            <format>html</format>
            <format>notebook</format>
        </formats>
    </output-target>
    <output-target name="solutions">
        <path>./output/solutions</path>
        <kinds><kind>completed</kind></kinds>
    </output-target>
</output-targets>
```

**Output Kinds**:
| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared |
| `completed` | Notebooks with all solutions |
| `speaker` | Notebooks with speaker notes |

**Output Formats**:
| Format | Description |
|--------|-------------|
| `html` | HTML slides |
| `notebook` | Jupyter notebook (.ipynb) |
| `code` | Extracted source code (only for `completed`) |

For complete XML reference, see **[Spec File Reference](spec-file-reference.md)**.

---

## CLM Application Configuration

CLM can be configured using configuration files or environment variables.

### Configuration Files

CLM looks for configuration files in these locations (priority order):

1. **Project config**: `.clm/config.toml` or `clm.toml` (current directory)
2. **User config**: `~/.config/clm/config.toml` (Linux/macOS) or `%APPDATA%\clm\config.toml` (Windows)
3. **System config**: `/etc/clm/config.toml` (Linux/Unix only)

### Creating a Configuration File

```bash
# Create user-level config
clm config init

# Create project-level config
clm config init --location=project

# View current configuration
clm config show

# Find configuration files
clm config locate
```

### Configuration File Format

```toml
# CLM Configuration File (config.toml)

[paths]
cache_db_path = "clm_cache.db"
jobs_db_path = "clm_jobs.db"

[external_tools]
plantuml_jar = "/usr/local/share/plantuml-1.2024.6.jar"
drawio_executable = "/usr/local/bin/drawio"

[logging]
log_level = "INFO"
enable_test_logging = false

[logging.testing]
e2e_progress_interval = 10
e2e_long_job_threshold = 60

[git]
remote_template = "git@github.com-cam:Coding-Academy-Munich/{repo}.git"

[llm]
model = "anthropic/claude-sonnet-4-6"
# api_key = "..."        # Or use OPENAI_API_KEY env var
# api_base = "..."       # API endpoint (e.g. https://openrouter.ai/api/v1)
max_concurrent = 3
temperature = 0.3
```

---

## Environment Variables

Environment variables override configuration files.

### Paths

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_PATHS__CACHE_DB_PATH` | Cache database path | `clm_cache.db` |
| `CLM_PATHS__JOBS_DB_PATH` | Job queue database path | `clm_jobs.db` |

### External Tools

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |

**Platform-specific Draw.io paths**:
```bash
# Linux
export DRAWIO_EXECUTABLE="/usr/bin/drawio"

# macOS
export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"

# Windows
set DRAWIO_EXECUTABLE="C:\Program Files\draw.io\draw.io.exe"
```

### Slide Authoring

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_SIDECAR_LAYOUT` | Course-wide default for where **newly created** voiceover companions land: `subdir` (a `voiceover/` folder) or `sibling` (next to the slide). | (unset → sibling) |

This is a **write-time** default only — it affects where `clm voiceover extract`
/ `sync` create a *new* companion. It does **not** change the build, which always
reads a companion (and cassettes) from either layout. The precedence for a new
companion is: an explicit `--layout` flag → a `voiceover/` directory that already
exists in the topic → this course default → `sibling`. A value of `sibling` is a
no-op (it equals the built-in fallback).

The same default can be set per course repository in `pyproject.toml`, which the
environment variable overrides:

```toml
[tool.clm]
sidecar-layout = "subdir"   # or "sibling"
```

The setting is read from the nearest ancestor `pyproject.toml` of the slide
being written. Use `clm slides tidy` to move *existing* sidecars between layouts
in bulk.

### Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_LOGGING__LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | `INFO` |
| `CLM_LOGGING__ENABLE_TEST_LOGGING` | Enable logging during tests | `false` |
| `CLM_LOG_DIR` | Directory for `clm.log` and `workers/` logs. Overrides the platform default (Windows: `%LOCALAPPDATA%/clm/Logs`; macOS: `~/Library/Logs/clm`; Linux: `~/.local/state/clm/log`). Useful to relocate logs, or to give parallel processes their own log file so they don't race to open/rotate the shared one. | platform default |

### Git

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_GIT__REMOTE_TEMPLATE` | URL template for git remotes | `{repository_base}/{repo}` |
| `CLM_GIT__REMOTE_PATH` | Path segment between base URL and repo name (e.g., GitLab group). Per-target `<remote-path>` overrides still win. | (unset) |
| `CLM_GITLAB_TOKEN` | GitLab API token (`api` scope) used by `clm release provision` to share channel repos into access groups (issue #294), and — with `CLM_GIT_TOKEN_AUTH=1` — for git HTTPS transport. `GITLAB_TOKEN` is accepted as a fallback. | (unset) |
| `CLM_GIT_TOKEN_AUTH` | Set to `1` to authenticate the git operations run by `clm git` / `clm release sync --push` against HTTPS remotes with `CLM_GITLAB_TOKEN`, via an ephemeral credential helper (issue #341 — headless/CI pushes where no credential helper exists). The token never appears in URLs, `.git/config`, or the command line. Opt-in: without it, git's own credential machinery (e.g. Git Credential Manager) is used. | (unset) |

The remote template supports placeholders: `{repository_base}`, `{remote_path}`, `{repo}`, `{slug}`, `{lang}`, `{suffix}` — plus, for release-channel repos, `{stream}` (the release stream name, issue #291).
This is useful for SSH access with custom host aliases:

```bash
CLM_GIT__REMOTE_TEMPLATE="git@github.com-cam:Coding-Academy-Munich/{repo}.git"
```

Can also be set in `[git]` section of config files or `<remote-template>` in course spec XML.

### LLM (Summarize Command)

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_LLM__MODEL` | LLM model identifier | `anthropic/claude-sonnet-4-6` |
| `CLM_LLM__API_KEY` | API key (or use `OPENAI_API_KEY` env var) | |
| `CLM_LLM__API_BASE` | API base URL (e.g. `https://openrouter.ai/api/v1`) | |
| `CLM_LLM__MAX_CONCURRENT` | Max parallel LLM calls | `3` |
| `CLM_LLM__TEMPERATURE` | Sampling temperature | `0.3` |

Set `CLM_LLM__API_BASE` to your provider's OpenAI-compatible endpoint
and `CLM_LLM__API_KEY` (or `OPENAI_API_KEY`) to authenticate.

### Slide Sync (`clm slides sync`)

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_SYNC_PROVIDER` | Default backend for the edit-reconciliation judge: `openrouter` (Claude Sonnet via OpenRouter) or `local` (the offline Ollama daemon). The `--provider` flag overrides it. | `openrouter` |
| `CLM_SYNC__SHARED_DIVERGENCE` | How to handle a **language-neutral** code cell edited *differently* on both decks (a divergence the single-entity model would otherwise have to guess). `auto-heal` propagates the winning side (keyed direction, else newer file) and emits a **warning**; `error` surfaces it and writes nothing, so you resolve it by hand. | `auto-heal` |

The OpenRouter backends (the judge, the brand-new-slide translator, and the
opt-in `--llm-recover` alignment recoverer) authenticate with
`OPENROUTER_API_KEY` (or `OPENAI_API_KEY`), which `clm slides sync` also picks up
from a project `.env` automatically (pass `--no-env-file` to skip).

### Performance

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_MAX_CONCURRENCY` | Max concurrent operations | `50` |
| `CLM_MAX_WORKER_STARTUP_CONCURRENCY` | Max concurrent worker starts | `10` |
| `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB` | Skip output-write deduplication for files larger than this many megabytes. Repeat writes to a large-file output are reported as a single summary collision counter rather than per-event warnings. Set to `0` to force every write through the large-file fast path (useful for tests). | `50` |

### Database Retention

Finished job rows are diagnostic only (the results cache lives in separate
tables), but they used to accumulate forever, which made `clm status` and
`clm monitor` startup degrade over time. Old rows are now pruned at build
end by default.

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_RETENTION__COMPLETED_JOBS_RETENTION_DAYS` | Days to keep completed job rows before build-end cleanup deletes them. | `7` |
| `CLM_RETENTION__FAILED_JOBS_RETENTION_DAYS` | Days to keep failed job rows (longer, for debugging). | `30` |
| `CLM_RETENTION__CANCELLED_JOBS_RETENTION_DAYS` | Days to keep cancelled job rows. | `1` |
| `CLM_RETENTION__WORKER_EVENTS_RETENTION_DAYS` | Days to keep worker lifecycle events (audit log). | `30` |
| `CLM_RETENTION__AUTO_CLEANUP_ON_BUILD_END` | Run the retention cleanup after each build. | `true` |
| `CLM_RETENTION__AUTO_VACUUM_AFTER_CLEANUP` | Run `VACUUM` after cleanup to reclaim disk space (slow on large databases; the build prints a progress message while it runs). | `false` |

### Notebook Execution Diagnostics

These help diagnose builds that hang on a single notebook (see issue #143).

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_CELL_TIMEOUT_SECONDS` | Per-cell execution timeout (seconds) passed to nbclient. When set to a positive integer, a cell that does not return to idle within this window raises a cell timeout error (surfaced as a normal cell error) instead of blocking the worker until the build-level job timeout fires. Always takes precedence over the replay-mode default below. Unset / non-positive keeps the historical no-timeout behavior for non-replay builds. | (unset → no per-cell timeout, except replay builds — see next row) |
| `CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS` | Default per-cell timeout (seconds) applied **only to HTTP-replay-engaged jobs** (any `--http-replay` mode but `disabled`), so a replay-layer hang surfaces as a clean cell timeout instead of stalling to the build-level job timeout (issue #143). Real cells in replay decks finish in seconds, so only a genuine hang reaches this ceiling. `CLM_CELL_TIMEOUT_SECONDS` overrides it; set to `0` to opt out. | `600` |
| `CLM_HTTP_REPLAY_TRANSPORT` | HTTP-replay transport. `mitmproxy` (the only transport) is the default and the only accepted value; setting `vcrpy` **fails the build** with a migration pointer (the in-process transport was removed in issue #355 — re-record vcrpy-era cassettes (pre-1.10, or any course that kept the opt-out) with `--http-replay=refresh`). | `mitmproxy` |
| `CLM_SLOW_CELL_LOG_THRESHOLD_SECONDS` | Cells slower than this are logged at INFO (`slow cell N/total took Xs`) so a stalling notebook is visible without enabling DEBUG. | `60` |

### MCP Server

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_DATA_DIR` | Default data directory for the MCP server (contains `slides/`, `course-specs/`). Used by `clm mcp` and the `clm.slides` CLI tools when no `--data-dir` is given. | (cwd) |

### Google Calendar Push (`clm calendar push`)

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_GOOGLE_CREDENTIALS` | Path to the Google credentials JSON used by `clm calendar push` (same as `--credentials`). Either an OAuth "Desktop app" client — a browser consent flow runs once, then the token is cached in the user config dir (`google-calendar-token.json`) — or a service-account key for a service account the target calendar is shared with ("Make changes to events"). | (unset) |

The target calendar id can be stored per cohort in the calendar TOML instead of
passing `--calendar-id` on every push:

```toml
# release/<channel>.calendar.toml
[google]
calendar_id = "abc123…@group.calendar.google.com"
```

One-time Google-side setup: create a Google Cloud project, enable the
**Google Calendar API**, and create either an OAuth client (type "Desktop app")
or a service account; download the JSON. Requires the `[gcal]` extra.

### Recording Management (`clm recordings`)

The recordings module has its own `[recordings]` config section with a nested
`[recordings.auphonic]` table for the cloud backend. All fields are exposed as
environment variables using the `CLM_RECORDINGS__*` prefix (double underscore
for nested fields).

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_RECORDINGS__ROOT_DIR` | Root directory for the recording workflow. Contains `to-process/`, `final/`, `archive/`. | (unset) |
| `CLM_RECORDINGS__RAW_SUFFIX` | Suffix marking raw recording filenames (e.g. `python-basics--w03--RAW.mp4`). | `--RAW` |
| `CLM_RECORDINGS__PROCESSING_BACKEND` | Processing backend: `onnx` (local DeepFilterNet3), `external` (wait for iZotope RX 11 or similar), or `auphonic` (cloud). | `onnx` |
| `CLM_RECORDINGS__AUTO_PROCESS` | Auto-process recordings when detected by the file watcher. | `false` |
| `CLM_RECORDINGS__STABILITY_CHECK_INTERVAL` | Seconds between file-size polls (watcher stability detection). | `2.0` |
| `CLM_RECORDINGS__STABILITY_CHECK_COUNT` | Consecutive identical polls required before a file is considered stable. | `3` |
| `CLM_RECORDINGS__ACTIVE_COURSE` | Currently active course ID used for recording-to-lecture assignment. | (unset) |
| `CLM_RECORDINGS__OBS_OUTPUT_DIR` | Directory where OBS Studio saves its recordings. | (unset) |
| `CLM_RECORDINGS__OBS_HOST` | OBS WebSocket host. | `localhost` |
| `CLM_RECORDINGS__OBS_PORT` | OBS WebSocket port. | `4455` |
| `CLM_RECORDINGS__OBS_PASSWORD` | OBS WebSocket password. | (empty) |

**Processing pipeline tuning (ONNX backend)**:

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_RECORDINGS__PROCESSING__DENOISE_ATTEN_LIM` | Noise reduction attenuation limit in dB. | `35.0` |
| `CLM_RECORDINGS__PROCESSING__SAMPLE_RATE` | Audio sample rate. | `48000` |
| `CLM_RECORDINGS__PROCESSING__LOUDNORM_TARGET` | EBU R128 loudness target in LUFS. | `-16.0` |

**Auphonic backend** (required when `processing_backend = "auphonic"`):

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_RECORDINGS__AUPHONIC__API_KEY` | Auphonic API key. **Required** for the Auphonic backend. | (unset) |
| `CLM_RECORDINGS__AUPHONIC__PRESET` | Optional managed preset name. Leave empty to use inline algorithm settings. | (empty) |
| `CLM_RECORDINGS__AUPHONIC__POLL_TIMEOUT_MINUTES` | Max minutes to wait for an Auphonic job to complete. | `120` |
| `CLM_RECORDINGS__AUPHONIC__REQUEST_CUT_LIST` | Request a cut list on every production. | `false` |
| `CLM_RECORDINGS__AUPHONIC__BASE_URL` | API base URL override. | `https://auphonic.com` |

---

## Directory Structure

### Recommended Course Structure

```
my-course/
├── course.xml                  # Course specification
├── slides/
│   └── module_001/
│       └── topic_001_intro/
│           ├── slides.py       # Notebook source
│           └── diagram.puml    # PlantUML diagram
├── code/
│   └── examples/               # Code examples (dir-group)
└── output/                     # Generated files (gitignored)
    ├── students/
    └── solutions/
```

### File Naming Conventions

**Source Files**:
- Python notebooks: `*.py` (converted to .ipynb)
- PlantUML: `*.puml` or `*.plantuml`
- Draw.io: `*.drawio`

**Output Files**:
- Notebooks: `*.ipynb`
- HTML slides: `*.html`
- Extracted code: `*.py`, `*.cpp`, etc.
- Images: `*.png`, `*.svg`

---

## Best Practices

### Version Control

```bash
# .gitignore
output/
*.ipynb
clm_cache.db
clm_jobs.db
```

Commit source files (`.py`, `.puml`, `.drawio`), not generated files.

### Configuration Priority

Settings are loaded in this order (highest to lowest):

1. Environment variables
2. Project configuration file
3. User configuration file
4. System configuration file
5. Default values

---

## See Also

- **[Spec File Reference](spec-file-reference.md)** - Complete course XML format
- **[Quick Start Guide](quick-start.md)** - Building your first course
- **[Troubleshooting](troubleshooting.md)** - Common issues
- **[Installation](installation.md)** - Setup and dependencies
