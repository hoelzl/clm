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
# Note: database paths are NOT configured here — use --cache-db-path /
# --jobs-db-path (or the CLM_*_DB_PATH env vars).

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

These are the env-var forms of the global `--cache-db-path` / `--jobs-db-path` /
`--telemetry-db-path` options, honored by every command (an explicit path is
respected verbatim — it is not re-anchored to the project root the way the bare
default is):

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_CACHE_DB_PATH` | Cache database path (persistent — processed-file results) | `clm_cache.db` |
| `CLM_JOBS_DB_PATH` | Job-queue database path (jobs, workers, events). Ephemeral: only needs to survive a single `clm` run, so it can live on a RAM disk (e.g. `Z:\clm_jobs.db`) to spare the SSD. `clm status` / `clm monitor` honor it too, so they inspect the same DB a redirected build wrote. **Direct worker mode only** — a host RAM-disk path is not visible inside Docker workers. | `clm_jobs.db` |
| `CLM_TELEMETRY_DB_PATH` | Execution-telemetry database (per-deck kernel crash/flake history). Kept separate from the cache DB so clearing the cache never erases the history. | `clm_telemetry.db` next to the cache DB |

> The database paths are **not** part of the `[…]` config-file model. They are
> resolved from the global CLI options / the `CLM_*_DB_PATH` env vars above. (The
> pre-1.19 `[paths]` config section and its `CLM_PATHS__*` variables never
> actually relocated the databases a command opened and have been removed.)

### External Tools

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |

These can equivalently be set in the config file, which now takes effect for
**Direct** workers (the env var still wins if both are set):

```toml
[external_tools]
plantuml_jar = "/usr/local/share/plantuml.jar"
drawio_executable = "/usr/bin/drawio"
```

(Docker workers use the tools baked into the worker image, so a host path does
not apply to them.)

**Platform-specific Draw.io paths**:
```bash
# Linux
export DRAWIO_EXECUTABLE="/usr/bin/drawio"

# macOS
export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"

# Windows
set DRAWIO_EXECUTABLE="C:\Program Files\draw.io\draw.io.exe"
```

### Diagnostics

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_PROFILE_BUILD` | Set to `1` to make `clm build` emit `[build-profile]` lines (to stderr) measuring the completion poll loop's health: per-cycle gaps, the worst stall and how many completions it hid, and the on-loop vs offloaded submission cost. Use when the progress bar appears to stall behind the workers. Zero overhead when unset. | (unset) |

The `scripts/profile_build_stall.py` harness drives a throwaway synthetic course
with this enabled (against isolated temp databases) to reproduce and measure the
build's submission / poll-loop behavior.

### Slide Authoring

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_SIDECAR_LAYOUT` | Course-wide override for where **newly created** authoring sidecars land: `subdir` (a per-type `voiceover/` / `cassettes/` folder) or `sibling` (next to the slide). | (unset → per-deck default below) |

This is a **write-time** override only — it affects where `clm voiceover extract`
/ `sync` create a *new* companion, and where a build records a topic's *first*
HTTP-replay cassette. It does **not** change build *output*, which always reads a
companion (and cassettes) from either layout. The precedence for a new sidecar
is: an explicit `--layout` flag → a per-type directory that already exists in the
topic → this course override → the **per-deck default**. As of CLM 1.14 that
default prefers the subdir: a brand-new sidecar goes into the `voiceover/` /
`cassettes/` folder *unless* the deck already has a sibling sidecar, which stays
a sibling so a deck is never split across layouts. Set `sibling` to force the old
flat layout; set `subdir` to force the folder even for decks that still have
siblings.

The course-wide default resolves highest precedence first: the
`CLM_SIDECAR_LAYOUT` environment variable, then the `<sidecar-layout>` element in
the course spec (`clm info spec-files`), then `[tool.clm] sidecar-layout` in
`pyproject.toml`:

```toml
[tool.clm]
sidecar-layout = "subdir"   # or "sibling"
```

The spec's `<sidecar-layout>` is consulted by the build for cassette placement;
the env var and `pyproject.toml` key additionally drive `clm voiceover
extract`/`sync` (which run without a loaded spec). The `pyproject.toml` setting
is read from the nearest ancestor of the slide being written. Use `clm slides
tidy` to move *existing* sidecars between layouts in bulk.

### Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_LOGGING__LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Honored by `clm build` for both host and worker logging; the `--log-level` flag overrides it. Also settable as `[logging] log_level` in `clm.toml`. | `INFO` |
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
| `CLM_MAX_WORKERS` | Hard cap on the effective worker count per type (the friendly short form of the `[worker_management] max_workers_cap` config field). Further clamped against CPU/RAM-derived caps at pool start. `--max-workers` on `clm build` overrides it. | (auto caps only) |
| `CLM_MAX_CONCURRENCY` | Max concurrent operations | `50` |
| `CLM_MAX_WORKER_STARTUP_CONCURRENCY` | Max concurrent worker starts | `10` |
| `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB` | Skip output-write deduplication for files larger than this many megabytes. Repeat writes to a large-file output are reported as a single summary collision counter rather than per-event warnings. Set to `0` to force every write through the large-file fast path (useful for tests). | `50` |

### Build progress

Tuning for the build's progress logging (also settable as `[progress]` in
`clm.toml`):

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_PROGRESS__UPDATE_INTERVAL` | Seconds between progress log updates | `5` |
| `CLM_PROGRESS__LONG_JOB_THRESHOLD` | Seconds before warning about a long-running job | `30` |
| `CLM_PROGRESS__SHOW_WORKER_DETAILS` | Show per-worker details in progress output | `true` |

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

#### One-time setup

1. **Install the extra:** `pip install "coding-academy-lecture-manager[gcal]"`
   (from a source checkout: `uv sync --extra gcal`).

2. **Create (or pick) the target calendar.** A dedicated calendar is cleanest —
   in [Google Calendar](https://calendar.google.com), *Other calendars* → **+**
   → *Create new calendar*. Open its **Settings → Integrate calendar** and copy
   the **Calendar ID** (looks like `…@group.calendar.google.com`).

3. **Create credentials** in a Google Cloud project
   ([console.cloud.google.com](https://console.cloud.google.com)) with the
   **Google Calendar API** enabled (*APIs & Services → Library*). Choose one:

   - **OAuth client, type "Desktop app" (recommended).** *APIs & Services →
     Credentials → Create credentials → OAuth client ID → Desktop app*; download
     the client JSON (add yourself as a test user on the consent screen if
     prompted). The first push opens a browser for a one-time consent, then the
     refresh token is cached in the user config dir
     (`google-calendar-token.json`). Because OAuth acts **as you**, you can push
     to any calendar **you** own (e.g. the one from step 2) with **no sharing
     step**.
   - **Service-account key (only if your org permits it).** *Credentials →
     Create credentials → Service account*, then *Keys → Add key → JSON*. Share
     the target calendar with the service account's `client_email` granting
     **"Make changes to events"**. Note: many organizations disable
     service-account key creation
     (`constraints/iam.disableServiceAccountKeyCreation`); if you see that, use
     the OAuth Desktop client above — it is **not** affected by that policy.

   > **API keys do not work here.** A Google API key authorizes only *public*
   > data and carries no identity, so it cannot read a private calendar or
   > create/update/delete events. CLM's credential loader accepts **only** a
   > service-account key (`"type": "service_account"`) or an OAuth client
   > (`"installed"`/`"web"`) — not an API key.

4. **Point CLM at it.** Set the calendar id (in the calendar TOML's `[google]`
   table above, or `--calendar-id`) and the credentials path (`--credentials`
   or `CLM_GOOGLE_CREDENTIALS`).

#### A permanent test calendar

To eyeball calendar changes (event titles/bodies, projection tweaks) without
touching a live cohort, keep a dedicated test calendar and give it its **own**
calendar file — e.g. `release/test.calendar.toml`. The push namespace is derived
from the file's stem (`test`), so CLM-managed events there can never collide with
a real cohort's push. Mirror a real cohort calendar (same `start`/`holidays`/
`adjustments`, so the projection exercises real multi-deck/split/insert/merge
cases) and just swap in the test calendar id:

```toml
# release/test.calendar.toml — copy of a real cohort calendar, different target
start = 2026-04-13
# … holidays / adjustments copied from the real cohort …
[google]
calendar_id = "c_…test…@group.calendar.google.com"
```

Then:

```bash
# Credential-free preview of the exact titles/bodies (no Google access):
clm calendar generate <spec> --calendar release/test.calendar.toml -f ics

# Dry run — authenticates and READS the calendar to diff, but writes nothing:
clm calendar push <spec> --calendar release/test.calendar.toml \
    --credentials <oauth-client.json> --dry-run

# Real push:
clm calendar push <spec> --calendar release/test.calendar.toml \
    --credentials <oauth-client.json>
```

Note that `--dry-run` still requires credentials (it reads the existing managed
events to compute the plan); for a fully offline look at the entry text, use
`clm calendar generate -f ics`.

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
