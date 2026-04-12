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
| `--only-sections TEXT` | Comma-separated selector tokens; rebuild only those sections and leave unselected section output untouched. Dir-group processing is skipped in this mode. See "Iterating on a single section" below. |
| `--workers [direct\|docker]` | Worker execution mode |
| `--notebook-workers N` | Number of notebook workers |
| `--plantuml-workers N` | Number of PlantUML workers |
| `--drawio-workers N` | Number of Draw.io workers |
| `--max-workers N` | Hard cap on effective worker count per type. Applied on top of automatic CPU/RAM-derived caps. Also settable via the `CLM_MAX_WORKERS` environment variable. Use to keep an oversized spec file (e.g. an 18-worker course override) from saturating a small dev laptop. |
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
clm build course.xml --only-sections w03
clm build course.xml --only-sections w03,w04 -w
clm build course.xml --only-sections "Week 03"
```

#### Iterating on a single section

`--only-sections` is a dev-time iteration flag for large courses. It
rebuilds only the selected sections and leaves every other section's
output directory untouched — much faster than a full clean-and-rebuild
on a 20+ week course.

Selector syntax (comma-separated tokens):

- **Bare tokens** try in order: exact `id` match → 1-based index →
  case-insensitive substring match on either the German or English
  section name. First strategy that yields ≥1 match wins.
- **Prefixed tokens** force one strategy: `id:w03`, `idx:3`,
  `name:"Woche 03"`.
- Section indices are 1-based and count **all** sections in declared
  order, including disabled ones — toggling `enabled="false"` does not
  renumber the sections that follow.

Selector errors:

- Empty token or whitespace-only value → error, not silent full build.
- Zero matches → error with a listing of all available sections.
- Ambiguous bare substring (e.g. `"Introduction"` matching two sections)
  → error; disambiguate with a prefixed form.
- A mixed list containing disabled sections → skip each disabled
  section with a warning and build the rest.
- A selection that matches *only* disabled sections → error.

What `--only-sections` does **not** do:

- It does **not** run dir-group processing. Dir-groups produce the
  final shipping state of a course; run a full build when you need
  them.
- It does **not** detect section renames. If you rename a section,
  `--only-sections <new-name>` will warn that the old output directory
  is missing — run a full build once to clean up the stale name.
- It does **not** modify other sections' output directories, the
  top-level course files (README, `pyproject.toml`, etc.), or any git
  metadata.

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
| `--format [markdown\|json]` | Output format (default: markdown) |
| `--include-disabled` | Include sections marked `enabled="false"` with a `(disabled)` marker (default: omitted) |

Examples:

```bash
clm outline course.xml
clm outline course.xml -L de
clm outline course.xml -d ./docs
clm outline course.xml --format json
clm outline course.xml --include-disabled
```

### `clm resolve-topic`

Resolve a topic ID to its filesystem path.

```
clm resolve-topic [OPTIONS] TOPIC_ID
```

| Option | Description |
|--------|-------------|
| `--course-spec FILE` | Scope resolution to topics in this course spec |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--json` | Output as JSON |

Examples:

```bash
clm resolve-topic what_is_ml
clm resolve-topic "decorators*"
clm resolve-topic intro --course-spec course-specs/python.xml
```

### `clm search-slides`

Fuzzy search across topic names and slide file titles.

```
clm search-slides [OPTIONS] QUERY
```

| Option | Description |
|--------|-------------|
| `--course-spec FILE` | Limit search to topics in this course spec |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--language [de\|en]` | Search titles in this language only |
| `--max-results N` | Maximum results to return (default: 10) |

Examples:

```bash
clm search-slides decorators
clm search-slides "RAG introduction" --language en
clm search-slides lists --course-spec course-specs/python.xml
```

### `clm validate-spec`

Validate a course specification XML file for consistency.

```
clm validate-spec [OPTIONS] SPEC_FILE
```

Checks that all referenced topic IDs resolve to exactly one existing
topic directory, that there are no duplicate topic references, and
that referenced dir-group paths exist.

| Option | Description |
|--------|-------------|
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--json` | Output as JSON |
| `--include-disabled` | Also validate sections marked `enabled="false"`; each finding from a disabled section has `(disabled)` appended to its message (default: disabled sections are skipped) |

Examples:

```bash
clm validate-spec course-specs/python-basics.xml
clm validate-spec course-specs/ml-azav.xml --json
clm validate-spec course-specs/ml-azav.xml --include-disabled
```

### `clm validate-slides`

Validate slide files for format, tag, and pairing correctness. Runs deterministic
checks and extracts structured review material for content-quality checks.

```
clm validate-slides [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--checks TEXT` | Comma-separated checks: `format`, `pairing`, `tags`, `code_quality`, `voiceover`, `completeness` (default: all deterministic) |
| `--quick` | Fast syntax-only check (format + tags). Useful for PostToolUse hooks |
| `--json` | Output as JSON |
| `--data-dir DIR` | Course data directory (contains slides/) |

`PATH` can be a single slide file, a topic directory, or a course spec XML file.

Examples:

```bash
clm validate-slides slides/module_010/topic_100_intro/slides_intro.py
clm validate-slides slides/module_010/ --json
clm validate-slides slides/module_010/topic_100_intro/ --quick
```

### `clm normalize-slides`

Normalize slide files by applying mechanical fixes: tag migration (`alt`→`completed`),
workshop tag insertion, DE/EN interleaving, and slide ID auto-generation.

```
clm normalize-slides [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--operations TEXT` | Comma-separated operations: `tag_migration`, `workshop_tags`, `interleaving`, `slide_ids`, `all` (default: `all`) |
| `--dry-run` | Preview changes without modifying files |
| `--json` | Output as JSON |
| `--data-dir DIR` | Course data directory (contains slides/) |

Examples:

```bash
clm normalize-slides slides/module_010/topic_100_intro/slides_intro.py
clm normalize-slides slides/module_010/ --dry-run
clm normalize-slides slides/module_010/ --operations tag_migration
clm normalize-slides slides/module_010/ --operations slide_ids --json
```

### `clm language-view`

Extract a single-language view of a bilingual slide file. Each cell is
preceded by an `[original line N]` annotation so edits can be mapped back.

```
clm language-view FILE {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--include-voiceover` | Include voiceover cells |
| `--include-notes` | Include speaker-notes cells |

Examples:

```bash
clm language-view slides_intro.py de
clm language-view slides_intro.py en --include-voiceover
clm language-view slides_intro.py en --include-notes
```

### `clm suggest-sync`

Compare a slide file against git HEAD and detect asymmetric bilingual edits.
Suggests which cells need translation updates. Does not modify the file.

```
clm suggest-sync [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--source-language [de\|en]` | The language that was edited (auto-detected if omitted) |
| `--json` | Output as JSON |

Examples:

```bash
clm suggest-sync slides_intro.py
clm suggest-sync slides_intro.py --source-language de --json
```

### `clm extract-voiceover`

Extract voiceover and notes cells from a slide file to a companion
`voiceover_*.py` file, linked via `slide_id`/`for_slide` metadata.
Content cells without `slide_id` get auto-generated IDs before extraction.

```
clm extract-voiceover [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--json` | Output as JSON |

Examples:

```bash
clm extract-voiceover slides_intro.py
clm extract-voiceover slides_intro.py --dry-run
```

### `clm inline-voiceover`

Inline voiceover cells from a companion `voiceover_*.py` file back into the
slide file, matching via `for_slide`/`slide_id` metadata. Deletes the companion
file after successful inlining.

```
clm inline-voiceover [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--json` | Output as JSON |

Examples:

```bash
clm inline-voiceover slides_intro.py
clm inline-voiceover slides_intro.py --dry-run
```

### `clm authoring-rules`

Look up merged authoring rules (common + course-specific) for a course.
Reads per-course `.authoring.md` files from the `course-specs/` directory.

```
clm authoring-rules [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--course-spec TEXT` | Course spec path or slug (e.g. `machine-learning-azav`) |
| `--slide-path PATH` | Path to a slide file; resolves to the course(s) containing it |
| `--data-dir DIR` | Course data directory (contains course-specs/, slides/) |
| `--json` | Output as JSON |

At least one of `--course-spec` or `--slide-path` must be provided.

Examples:

```bash
clm authoring-rules --course-spec python-basics
clm authoring-rules --slide-path slides/module_010/topic_100_intro/slides_intro.py
clm authoring-rules --course-spec python-basics --json
```

### `clm mcp`

Start the MCP server for AI-assisted slide authoring.

```
clm mcp [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--data-dir DIR` | Course data directory (default: `CLM_DATA_DIR` or cwd) |
| `--log-level TEXT` | Log level for stderr output |

The MCP server exposes 11 tools over stdio transport:

| Tool | Description |
|------|-------------|
| `resolve_topic` | Resolve topic ID or glob pattern to filesystem path |
| `search_slides` | Fuzzy search across topic names and slide titles |
| `course_outline` | Generate structured JSON course outline |
| `validate_spec` | Validate course specification XML |
| `validate_slides` | Validate slide files (format, tags, pairing) |
| `normalize_slides` | Apply mechanical fixes (tag migration, interleaving, slide IDs) |
| `get_language_view` | Extract single-language view with line annotations |
| `suggest_sync` | Detect asymmetric bilingual edits vs git HEAD |
| `extract_voiceover` | Move voiceover cells to companion file |
| `inline_voiceover` | Merge voiceover cells back from companion file |
| `course_authoring_rules` | Look up merged authoring rules for a course |

All tools accept paths relative to the data directory or as absolute paths.
Most return JSON; `get_language_view` returns annotated plain text.

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
| `db stats` | Show database statistics |
| `db prune` | Prune old jobs, events, and cache entries |
| `db vacuum` | Compact databases |
| `db clean` | Combined prune + vacuum (with confirmation) |

`db prune` options:

| Option | Description |
|--------|-------------|
| `--completed-days N` | Days to keep completed jobs (default: keep all) |
| `--failed-days N` | Days to keep failed jobs (default: keep all) |
| `--events-days N` | Days to keep worker events (default: 30) |
| `--cache-versions N` | Cache versions to keep per file (default: 1) |
| `--dry-run` | Show what would be deleted |
| `--remove-missing` | Remove entries for source files no longer on disk |

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
| `git init SPEC_FILE` | Initialize git repos in output directories (idempotent — re-run to add remotes) |
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

`git init` is idempotent — re-running it after creating remote repositories will
detect and add them as origin. The behavior matrix:

| | No local repo | Local repo exists |
|---|---|---|
| **No remote** | Create local-only repo | Skip (print remote URL if configured) |
| **Remote exists** | Clone/restore from remote | Add remote origin if missing |

### `clm jobs`

Manage CLM jobs.

### `clm workers`

Manage CLM workers.

| Subcommand | Description |
|------------|-------------|
| `workers list` | List registered workers |
| `workers cleanup` | Delete stale worker DB rows (does not kill processes) |
| `workers reap` | Kill surviving worker processes + trees *and* clean DB rows |

`workers reap` is the self-service recovery command for crashed or
task-killed builds that left `python -m clm.workers.*` processes
running. It:

1. Marks in-flight job rows as failed (same as a clean pool shutdown would).
2. Scans for surviving worker processes via `psutil`.
3. Matches each one against `--jobs-db-path` (via the worker's `DB_PATH`
   env var) and kills its whole process tree.
4. Cleans up stale worker rows (same as `workers cleanup`).

| Option | Description |
|--------|-------------|
| `--jobs-db-path PATH` | Path to the job queue DB (default: `clm_jobs.db`) |
| `--dry-run` | Show what would be reaped without killing anything |
| `--force` | Skip the confirmation prompt |
| `--all` | Also reap processes whose env is unreadable or `DB_PATH` does not match (dangerous across worktrees) |

Unmatched processes are listed but not killed by default, so running
`reap` from one worktree cannot accidentally kill workers from another.
Use `--all` only when you are sure every surviving worker belongs to
you.

```bash
clm workers reap --dry-run         # preview
clm workers reap --force           # reap this worktree's orphans
clm workers reap --all --force     # emergency cross-worktree cleanup
```

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

Full pipeline: transcribe one or more video parts, detect transitions, match
slides, and merge voiceover cells in the .py file. By default, existing
voiceover content is preserved and transcript additions are merged in using
a single-pass LLM call that also filters recording noise (greetings,
self-corrections, code-typing dictation). Use `--overwrite` to replace
existing voiceover cells instead of merging.

Multiple video parts are processed independently and merged into a single
timeline using running offsets — no on-disk concatenation.

```
clm voiceover sync SLIDES VIDEO... --lang {de|en} [OPTIONS]
```

**Note:** The argument order is `SLIDES` first, then one or more `VIDEO` files.
Part ordering is authoritative — pass parts in the order they should be stitched.

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) (required) |
| `--mode [verbatim\|polished]` | `verbatim` = raw transcript; `polished` = LLM cleanup (default: `polished`) |
| `--overwrite` | Overwrite existing voiceover cells instead of merging (old behavior) |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend [faster-whisper\|cohere\|granite]` | Transcription backend (default: `faster-whisper`) |
| `--device [auto\|cpu\|cuda]` | Device for transcription (default: `auto`) |
| `--tag TEXT` | Cell tag for inserted cells: `voiceover` (default) or `notes` |
| `--slides-range TEXT` | Slide range to update (e.g. `5-20`) |
| `--dry-run` | Show unified diff without writing changes |
| `-o, --output PATH` | Output file |
| `--keep-audio` | Keep extracted audio files |
| `--model TEXT` | LLM model for merge/polished mode (default: `anthropic/claude-sonnet-4-6` via OpenRouter) |

**Merge behavior (default):**
- Existing voiceover cells are read as baseline; transcript additions are
  integrated while preserving all baseline content.
- Recording noise (greetings, self-corrections, code-typing dictation,
  operator asides) is filtered from the transcript.
- If the transcript contradicts a baseline bullet, the bullet is rewritten
  and logged in a structured rewrite report.
- `--dry-run` emits a unified diff showing exactly what would change.
- A JSONL trace log is written to `.clm/voiceover-traces/` on every run.
- `--mode verbatim` without `--overwrite` is an error (verbatim has no
  noise filter, so merging raw transcript would be unsafe).

**Overwrite behavior (`--overwrite`):**
- Old behavior: voiceover cells are replaced entirely with transcript content.
- `--mode verbatim --overwrite` writes raw transcript without LLM cleanup.

#### `clm voiceover transcribe`

Extract transcript from a video file.

```
clm voiceover transcribe VIDEO [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend [faster-whisper\|cohere\|granite]` | Transcription backend (default: `faster-whisper`) |
| `--device [auto\|cpu\|cuda]` | Device for transcription (default: `auto`) |
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

#### `clm voiceover extract-training-data`

Extract training data from a voiceover merge trace log. Reads a JSONL trace
log produced by `clm voiceover sync` and correlates each entry with the
current slide file state to produce training triples suitable for fine-tuning.

```
clm voiceover extract-training-data TRACE_LOG [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--base-dir PATH` | Project root for resolving slide file paths (default: inferred from trace log location) |
| `--tag TEXT` | Cell tag to read from slide files: `voiceover` (default) or `notes` |
| `--no-check-git` | Skip `git_head` reachability check |
| `-o, --output PATH` | Output file (default: stdout) |

Output fields per JSONL line: `input.baseline`, `input.transcript`,
`llm_output`, `human_final`, `delta_vs_llm` (empty = no hand edits, valid
positive training example).

Examples:

```bash
clm voiceover sync slides.py video.mp4 --lang de
clm voiceover sync slides.py video.mp4 --lang de --dry-run
clm voiceover sync slides.py "Teil 1.mp4" "Teil 2.mp4" "Teil 3.mp4" --lang de
clm voiceover sync slides.py video.mp4 --lang de --overwrite
clm voiceover sync slides.py video.mp4 --lang de --overwrite --mode verbatim
clm voiceover sync slides.py video.mp4 --lang de --slides-range 5-20 --dry-run
clm voiceover extract-training-data .clm/voiceover-traces/slides_intro-20260412-012020.jsonl
clm voiceover extract-training-data trace.jsonl -o training.jsonl --no-check-git
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

Check that recording dependencies (ffmpeg, onnxruntime) are installed.

```
clm recordings check
```

#### `clm recordings process`

Process a single recording through the audio pipeline (DeepFilterNet3 ONNX + FFmpeg filters).

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

#### `clm recordings assemble`

Mux paired video + audio files in `to-process/`, write results to `final/`,
and archive originals to `archive/`.

```
clm recordings assemble ROOT_DIR [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--raw-suffix TEXT` | Override raw file suffix (default: from config or `--RAW`) |
| `--dry-run` | Show pending pairs without assembling |

Examples:

```bash
clm recordings assemble ~/Recordings
clm recordings assemble ~/Recordings --dry-run
```

#### `clm recordings serve`

Start the recordings web dashboard (HTMX + SSE). Provides file watcher
controls, job status, lecture assignment, and OBS integration.
Requires `clm[web]` extra.

```
clm recordings serve ROOT_DIR [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--host TEXT` | Host to bind to (default: `127.0.0.1`) |
| `--port INT` | Port to bind to (default: `8008`) |
| `--spec-file PATH` | CLM course spec XML for lecture listing |
| `--obs-host TEXT` | OBS WebSocket host (default: from config) |
| `--obs-port INT` | OBS WebSocket port (default: from config) |
| `--obs-password TEXT` | OBS WebSocket password |
| `--no-browser` | Do not auto-open browser |

Examples:

```bash
clm recordings serve ~/Recordings
clm recordings serve ~/Recordings --spec-file course.xml
clm recordings serve ~/Recordings --obs-host 192.168.1.5 --port 9000
```

#### `clm recordings backends`

List available processing backends and their capabilities.

```
clm recordings backends
```

#### `clm recordings submit`

Submit a file to the configured processing backend. For synchronous
backends (onnx, external), this blocks until completion; for
asynchronous backends (auphonic), it returns once the upload finishes
and processing starts.

```
clm recordings submit INPUT_FILE [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--root DIR` | Recordings root (defaults to config) |
| `--request-cut-list` | Ask the backend to produce a cut list (Auphonic only) |
| `--title TEXT` | Metadata title override |

#### `clm recordings jobs list`

List recording processing jobs from the on-disk store.

```
clm recordings jobs list [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--root DIR` | Recordings root (defaults to config) |
| `--all` | Include terminal jobs (completed/failed) |
| `-n / --limit` | Max number of jobs to show (default: 20) |

#### `clm recordings jobs cancel`

Cancel an in-flight job by ID (prefix matches accepted).

```
clm recordings jobs cancel JOB_ID [OPTIONS]
```

#### `clm recordings auphonic preset sync`

Create or update the managed ``CLM Lecture Recording`` preset in the
user's Auphonic account. Idempotent.

#### `clm recordings auphonic preset list`

List all presets in the authenticated Auphonic account.

Examples:

```bash
clm recordings check
clm recordings process raw.mkv
clm recordings process raw.mkv -o final.mp4 --keep-temp
clm recordings batch ~/Recordings -o ~/Processed -r
clm recordings status python-basics
clm recordings compare izotope.mp4 onnx.mp4 --label-a "iZotope RX" --label-b "DeepFilterNet3 ONNX"
clm recordings assemble ~/Recordings
clm recordings assemble ~/Recordings --dry-run
clm recordings serve ~/Recordings --spec-file course.xml
clm recordings backends
clm recordings submit topic--RAW.mp4 --root ~/Recordings
clm recordings jobs list --root ~/Recordings --all
clm recordings jobs cancel a3b4e56f --root ~/Recordings
clm recordings auphonic preset sync
clm recordings auphonic preset list
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
| `CLM_DATA_DIR` | Default data directory for MCP server (contains slides/, course-specs/) |
| `CLM_GIT__REMOTE_TEMPLATE` | Git remote URL template (e.g., `git@github.com-cam:Org/{repo}.git`) |
| `CLM_GIT__REMOTE_PATH` | Default remote path between base URL and repo name (e.g., GitLab group) |
| `CLM_LLM__MODEL` | Default LLM model for summarize (default: `anthropic/claude-sonnet-4-6`) |
| `CLM_LLM__API_KEY` | API key for LLM provider (or use `OPENAI_API_KEY`) |
| `CLM_LLM__API_BASE` | API base URL (e.g. `https://openrouter.ai/api/v1`) |
| `CLM_LLM__MAX_CONCURRENT` | Max parallel LLM calls (default: 3) |
| `CLM_LLM__TEMPERATURE` | LLM sampling temperature (default: 0.3) |
| `CLM_RECORDINGS__OBS_OUTPUT_DIR` | Directory where OBS saves recordings |
| `CLM_RECORDINGS__ACTIVE_COURSE` | Currently active course ID |
| `CLM_RECORDINGS__AUTO_PROCESS` | Auto-process recordings when detected (default: false) |
| `CLM_RECORDINGS__ROOT_DIR` | Root directory for recording workflow (to-process/, final/, archive/) |
| `CLM_RECORDINGS__RAW_SUFFIX` | Suffix for raw recording filenames (default: `--RAW`) |
| `CLM_RECORDINGS__PROCESSING_BACKEND` | Processing backend: `onnx` (default), `external`, `auphonic` |
| `CLM_RECORDINGS__STABILITY_CHECK_INTERVAL` | Seconds between file-size polls (default: `2.0`) |
| `CLM_RECORDINGS__STABILITY_CHECK_COUNT` | Consecutive identical polls = stable (default: `3`) |
| `CLM_RECORDINGS__OBS_HOST` | OBS WebSocket host (default: `localhost`) |
| `CLM_RECORDINGS__OBS_PORT` | OBS WebSocket port (default: `4455`) |
| `CLM_RECORDINGS__OBS_PASSWORD` | OBS WebSocket password (default: empty) |
| `CLM_RECORDINGS__PROCESSING__DEEPFILTER_ATTEN_LIM` | DeepFilterNet attenuation limit (default: 35.0) |
| `CLM_RECORDINGS__PROCESSING__SAMPLE_RATE` | Audio sample rate (default: 48000) |
| `CLM_RECORDINGS__PROCESSING__LOUDNORM_TARGET` | Loudness target in LUFS (default: -16.0) |
| `CLM_RECORDINGS__AUPHONIC__API_KEY` | Auphonic API key (required when `processing_backend = "auphonic"`) |
| `CLM_RECORDINGS__AUPHONIC__PRESET` | Optional managed preset name (empty = inline algorithms) |
| `CLM_RECORDINGS__AUPHONIC__POLL_TIMEOUT_MINUTES` | Max minutes per Auphonic job (default: 120) |
| `CLM_RECORDINGS__AUPHONIC__REQUEST_CUT_LIST` | Request cut list on every production (default: `false`) |
| `CLM_RECORDINGS__AUPHONIC__BASE_URL` | API base URL override (default: `https://auphonic.com`) |
