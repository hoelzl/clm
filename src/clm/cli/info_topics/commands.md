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

### `clm jobs`

Manage CLM jobs.

### `clm workers`

Manage CLM workers.

| Subcommand | Description |
|------------|-------------|
| `workers list` | List registered workers |
| `workers cleanup` | Clean up dead workers and orphaned processes |

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
