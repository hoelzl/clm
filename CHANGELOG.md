# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [1.2.1] - 2026-04-12

### Added
- **Training data extraction**: new `clm voiceover extract-training-data`
  command reads JSONL trace logs produced by `clm voiceover sync` and
  correlates each entry with the current slide file state to produce training
  triples (`input.baseline`, `input.transcript`, `llm_output`, `human_final`,
  `delta_vs_llm`). Entries where the human final matches the LLM output are
  emitted with an empty delta as positive training examples. Entries with
  unreachable `git_head` commits are skipped with a warning. Supports
  `--base-dir`, `--tag`, `--no-check-git`, and `--output` options.
- **Langfuse tracing for all LLM calls**: when `LANGFUSE_HOST` (or
  `LANGFUSE_BASE_URL`), `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` are
  set, `_build_client` returns a Langfuse-observed `openai.AsyncOpenAI` that
  traces all LLM calls automatically. Benefits `clm voiceover sync` (merge),
  `clm polish`, and `clm summarize`. Env vars absent = no change. Langfuse
  unreachable = warning, pipeline continues. `langfuse>=3.0.0` added to the
  `[voiceover]` extra. Each voiceover merge invocation groups traces into a
  Langfuse session with per-batch trace IDs, tags, and metadata; the
  `langfuse_trace_id` is also written to the local JSONL trace log for
  correlation.

### Changed
- **Recordings dashboard: slide-deck-based lecture selection**: The
  `/lectures` page now lists individual slide decks (notebook files)
  instead of topics. This matches how recordings are actually made — one
  video per slide deck, not one per topic (a topic can contain multiple
  slide files).
  - The page builds a full `Course` object from the spec file at startup,
    reusing `Course.from_spec()` to resolve topics, find slide files,
    extract bilingual titles, and assign section numbers.
  - **Language toggle** (DE/EN): a cookie-based selector on the lectures
    page switches between German and English section names, slide deck
    titles, and course slugs. Default: German.
  - **Refresh button**: rebuilds the `Course` from disk without restarting
    the server, picking up title changes and new slides.
  - **Multi-part recording support**: the arm form now accepts a
    `part_number` field. When `part > 0`, filenames include a
    `(part N)` suffix (e.g. `03 Streaming (part 2)--RAW.mp4`).
  - `ArmedTopic` renamed to `ArmedDeck` with a `deck_name` field
    (replacing `topic_name`) and a `part_number` field.  Backward-compat
    aliases (`ArmedTopic`, `SessionSnapshot.armed_topic`,
    `RecordingSession.armed_topic`) are preserved.
  - Naming helpers (`raw_filename`, `final_filename`) accept `deck_name`
    (was `topic_name`) and a keyword-only `part` parameter.  New
    `parse_part()` function extracts the optional `(part N)` suffix from
    a base name.
  - New routes: `POST /set-lang`, `POST /lectures/refresh`.
  - JSON status API includes both `armed_deck` (new) and `armed_topic`
    (deprecated alias) for transition.
- **`clm voiceover sync` now accepts multiple video files** (breaking CLI
  change): argument order flipped from `sync VIDEO SLIDES` to
  `sync SLIDES VIDEO...`. Multiple video parts are processed independently
  (transcription + transition detection per part) and merged into a single
  logical timeline using running offsets — no on-disk concatenation. Each
  `TranscriptSegment` and `TransitionEvent` carries a `source_part_index`
  for downstream consumers. Single-video invocations work as before (just
  swap the argument order).
- **`clm voiceover sync` now merges into existing voiceover cells by
  default** instead of overwriting them. The merge uses a single-pass LLM
  call (Claude Sonnet 4.6 via OpenRouter by default) that preserves baseline
  content, integrates substantive transcript additions, and filters recording
  noise (greetings, self-corrections, code-typing dictation, operator
  asides). Use `--overwrite` to restore the old destructive behavior.
  - Factual contradictions in the transcript may rewrite baseline bullets;
    every rewrite is tracked in a structured `rewrites` field.
  - `--dry-run` now emits a colored unified diff with rewrite annotations.
  - `--mode verbatim` without `--overwrite` is now an error (verbatim has
    no noise filter, so merging raw transcript would be unsafe).
  - Every merge run writes a JSONL trace log to
    `.clm/voiceover-traces/` for future training data extraction.
  - LLM calls are batched across slides (20k char budget per batch) with
    automatic per-slide fallback on JSON parse failure.

### Fixed
- **`parse_dir_groups` now respects `<section enabled="false">`**: previously
  `CourseSpec.parse_dir_groups` used `root.iter("dir-group")` and walked the
  entire XML tree regardless of section enablement, so topic-scoped
  `<dir-group>` elements inside disabled sections silently leaked their
  directories into the build output. The traversal is now section-aware and
  mirrors `parse_sections`: topic-scoped dir-groups in disabled sections are
  dropped by default and retained when `keep_disabled=True`. Top-level
  `<dir-groups>` are unaffected. Document order of the returned dir-groups is
  preserved (topic-scoped before top-level). Fixes #29.
- `CourseSpec.from_file` now forwards its `keep_disabled` parameter to
  `parse_dir_groups` so full-roadmap enumeration (e.g.
  `clm outline --include-disabled`) sees the same dir-groups the sections do.

### Added
- **Section filtering**: Course spec `<section>` elements now accept
  `enabled` and `id` attributes, and `clm build` accepts an
  `--only-sections <selector>` flag for dev-time iteration on a subset
  of a course. Together these replace the common "`-build.xml` subset
  spec" pattern for courses with not-yet-implemented sections. See the
  proposal at `docs/proposals/SECTION_FILTERING.md` and the phased
  implementation plan at
  `docs/claude/design/section-filtering-plan.md`.
  - **`enabled="false"` on a `<section>`** drops it from the parsed spec
    entirely, so `clm build`, `clm outline`, `clm validate-spec`, MCP
    tools, and every other consumer of `CourseSpec.sections` ignores it
    without code changes. Default is `enabled="true"`.
  - Disabled sections may omit `<topics>` or reference topic IDs that do
    not yet exist on disk — they are never built or validated. This is
    the property that lets a full roadmap spec live as a single file
    (no more `-build.xml` companion specs).
  - `enabled` is case-insensitive (`true`/`True`/`TRUE`/`false`/`False`);
    any other value raises `CourseSpecError` with a clear message.
  - Optional `id` attribute on `<section>` (e.g. `id="w03"`) is stable
    under reordering and renaming; recommended for frequently filtered
    courses.
  - **`--include-disabled` flag** on `clm outline` and `clm validate-spec`
    (plus matching `include_disabled` parameters on the MCP
    `course_outline` and `validate_spec` tools) enumerates the full
    roadmap including disabled sections, with a `(disabled)` marker on
    each entry and a `(disabled)` suffix on each validation finding so
    users can tell which content is deferred.
  - `CourseSpec.parse_sections` and `CourseSpec.from_file` gain a
    keyword-only `keep_disabled: bool = False` parameter so tooling can
    enumerate the full roadmap.
  - **`clm build --only-sections <selector>`** rebuilds only the listed
    sections and leaves unselected section output directories untouched.
    Selector tokens are comma-separated; bare tokens try `id` → 1-based
    index → case-insensitive substring on the German or English name,
    stopping at the first hit. Prefixed tokens (`id:`, `idx:`, `name:`)
    force a single strategy. Section indices count disabled sections so
    toggling `enabled` does not renumber later sections.
  - Selector errors abort the build early: empty/whitespace tokens, zero
    matches (with a full section listing), ambiguous bare substring
    (with the matches listed), or an entirely-disabled selection. A
    mixed list containing disabled sections skips each disabled section
    with a warning and builds the rest.
  - `--only-sections` mode **skips `git_dir_mover`**, **skips dir-group
    processing**, and **rmtrees only the selected sections'
    subdirectories** per `(target, lang, kind)` tuple. Missing section
    dirs trigger a rename-hint warning rather than an error.
  - **`clm build --only-sections <selector> --watch`** reacts only to
    events under selected sections' source directories. Creation events
    outside the selected set are silently dropped; modification events
    rely on `course.find_course_file`, which naturally filters against
    the already-filtered `course.files` list. Restart the watcher if
    you change the section set in the spec.
  - New exports: `SectionSelection` and
    `CourseSpec.resolve_section_selectors` in `clm.core.course_spec`;
    `Course.from_spec` accepts a new `section_selection` parameter;
    `FileEventHandler` accepts a new `selected_section_source_dirs`
    constructor parameter.
  - Fully backward-compatible: existing spec files without the new
    attributes and existing `clm build` invocations without
    `--only-sections` behave exactly as before.
- **Environment-aware worker pool-size cap**: Spec-file worker counts are
  now clamped against the host machine's CPU, RAM, and an optional
  operator cap at pool start, so a spec tuned for a build farm (e.g.
  PythonCourses' 18 notebook workers) no longer saturates a developer
  laptop. See `docs/proposals/WORKER_CLEANUP_IMPLEMENTATION_PLAN.md`
  Fix 4 for the design rationale.
  - **`clm build --max-workers N`** — new CLI flag that caps the
    effective worker count for the invocation.
  - **`CLM_MAX_WORKERS`** — matching environment variable (empty,
    zero, negative, or non-integer values are tolerated and treated
    as "no cap").
  - **`WorkersManagementConfig.max_workers_cap: int | None`** — new
    config field (`ge=1, le=64`) surfaced through
    `config_loader.load_worker_config`.
  - Default caps are `cpu_cap = max(1, os.cpu_count() // 2)` and
    `mem_cap = max(1, floor(total_ram_gb / 2))`. `get_worker_config`
    logs a WARNING naming the worker type, requested count, and every
    individual cap value whenever clamping kicks in, so the diagnostic
    is visible in build logs.
  - New helper module `clm.infrastructure.workers.pool_size_cap`
    exposing `compute_pool_size_cap(requested, *, explicit_cap=None)`
    and a frozen `PoolSizeCapResult` dataclass with a
    `format_reason()` render for logs. The helper is pure so unit
    tests can pin CPU/RAM via `monkeypatch`.
- **`clm workers reap`**: New CLI subcommand that chains the full
  self-service recovery sequence for crashed or task-killed builds —
  orphan job-row reap, psutil-based scan for surviving
  `python -m clm.workers.*` processes, process-tree kill, and stale
  worker-row cleanup. Fix 5 of the worker cleanup reliability plan.
  - Options: `--jobs-db-path`, `--dry-run`, `--force`, `--all`.
  - Cross-worktree safety rail: by default only kills workers whose
    `DB_PATH` env var resolves to the same path as `--jobs-db-path`.
    Processes with unreadable env (common on Windows across sessions)
    or a different `DB_PATH` are listed but not killed. `--all` opts
    in to reaping them too, as an emergency escape hatch.
  - `--dry-run` prints what would be reaped without mutating the DB
    or touching any process. Without `--force`, the command prompts
    for confirmation before killing.
  - Uses `ctx.exit(1)` for the missing-DB error so CI scripts can
    reliably detect failures.
  - **Existing `clm workers cleanup` is unchanged** — it still only
    deletes DB rows and does not kill processes. The two commands
    now compose: `reap` does everything `cleanup` does plus the
    process-kill step.
  - New helper module `clm.infrastructure.workers.process_reaper`
    exposes `terminate_then_kill_procs`, `reap_process_tree`,
    `scan_worker_processes`, and the frozen `DiscoveredWorkerProcess`
    dataclass. Fix 2's `reap_kernel_descendants` is now a thin
    wrapper around the shared low-level helper.

### Fixed
- **Worker cleanup reliability on Windows** (resolves the incident
  documented in `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`:
  `clm build` previously leaked Jupyter kernel subprocesses any time
  a worker was killed mid-job, eventually wedging WMI and Windows
  Terminal with hundreds of orphaned `python.exe` processes):
  - **Windows `JobObject` owns every direct-mode worker** (Fix 1).
    `DirectWorkerExecutor` now creates a
    `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` job on init and assigns
    every worker subprocess to it immediately after `Popen`. When
    the job handle closes (explicit `cleanup()` or process exit),
    Windows itself terminates every process in the tree — even
    through `atexit`, `taskkill /F`, or a hard CLM crash.
    No-op on non-Windows. New helper module
    `clm.infrastructure.workers.windows_job_object` with a full
    ctypes wrapper around `CreateJobObjectW` /
    `SetInformationJobObject` / `AssignProcessToJobObject`.
  - **Kernel grandchild reap via `_ReapingKernelManager`** (Fix 2).
    `jupyter_client`'s `LocalProvisioner.kill` is `TerminateProcess`
    on Windows, which kills only the kernel pid — any
    `subprocess.Popen` / `multiprocessing` children that a cell
    spawned survive as orphan processes. A new
    `AsyncKernelManager` subclass now snapshots descendants before
    the kernel shuts down and reaps survivors afterward via
    `psutil`. Wired into `TrackingExecutePreprocessor` via the
    `kernel_manager_class` traitlet so every nbclient-managed
    kernel uses it automatically. Emits WARNING logs when anything
    had to be force-killed — the diagnostic signal the team had
    been missing. psutil is now a hard dependency
    (`psutil>=5.9.0` in `pyproject.toml`), replacing the conditional
    import + `/proc` fallback in `worker_executor.is_worker_running`.
  - **Orphan job rows marked failed at `pool_stopped`** (Fix 3).
    When a worker died mid-job, its `jobs` row was left with
    `started_at` set and `completed_at` null forever, causing
    `clm status` to silently under-report failures. New atomic
    `JobQueue.mark_orphaned_jobs_failed()` runs a single
    `BEGIN IMMEDIATE` SELECT+UPDATE over rows matching
    `started_at IS NOT NULL AND completed_at IS NULL AND
    cancelled_at IS NULL AND status IN ('processing', 'pending')`
    and stamps each with `status='failed'`,
    `error=JobQueue.ORPHAN_ERROR_MESSAGE`, and a
    `completed_at` timestamp.
    `WorkerLifecycleManager.stop_managed_workers` invokes this
    between `stop_pools()` and `log_pool_stopped()`, emits a
    WARNING naming each orphan, and passes `orphan_count` +
    `orphan_job_ids` into the `pool_stopped` event metadata.
    Wrapped in `try/except Exception` so a DB hiccup can never
    break pool teardown.
  - **Mock-based cleanup test replaced with real-kernel regression
    tests** (Fix 2). The old
    `test_cleanup_called_on_kernel_death` used `km=None, kc=None`
    and only asserted the finally block ran — giving false
    confidence. Replaced with two real-kernel tests that spawn a
    subprocess grandchild from a cell, run `preprocess` on a live
    kernel (both success and `CellExecutionError` paths), and
    assert the grandchild is dead via `psutil.pid_exists` after
    preprocess returns.

## [1.2.0] - 2026-04-08

### Added
- **MCP server for AI-assisted slide authoring**: New `clm.mcp` package providing a
  Model Context Protocol server via stdio transport with 12 tools for course navigation,
  validation, normalization, bilingual editing, and voiceover management.
  - `clm mcp` — start the MCP server (requires `[mcp]` extra).
  - `--data-dir` option and `CLM_DATA_DIR` env var for data directory resolution.
  - Tools: `resolve_topic`, `search_slides`, `course_outline`, `validate_spec`,
    `validate_slides`, `normalize_slides`, `get_language_view`, `suggest_sync`,
    `extract_voiceover`, `inline_voiceover`, `course_authoring_rules`.
  - In-memory caching for course objects (keyed by spec file mtime).
  - New optional extras: `[slides]` (rapidfuzz) and `[mcp]` (mcp SDK + slides).
- **Slide authoring tools** (`clm.slides`): New package for AI-assisted slide authoring
  with CLI commands and MCP tools.
  - `clm resolve-topic` — resolve a topic ID to its filesystem path, with exact match,
    glob patterns (`what_is_ml*`), course-spec scoping, and JSON output.
  - `clm search-slides` — fuzzy search across topic names and slide file titles using
    `rapidfuzz` (with substring fallback when not installed).
  - `clm outline --format json` — structured JSON course outline alongside existing
    Markdown format.
  - `clm validate-spec` — course spec validation: unresolved/ambiguous topics, duplicates,
    missing dir-groups, near-match suggestions. `--json` flag.
  - `clm validate-slides` — slide file validation: format, tags, DE/EN pairing checks,
    review material extraction. `--quick` mode for syntax-only.
  - `clm normalize-slides` — slide normalization: tag migration (`alt`→`completed`),
    workshop tag insertion, DE/EN interleaving, slide ID auto-generation. `--dry-run`
    and `--operations` filter.
  - `clm language-view` — single-language view of bilingual slide files with
    `[original line N]` annotations. `--include-voiceover`/`--include-notes` flags.
  - `clm suggest-sync` — detect asymmetric bilingual edits vs git HEAD with
    `slide_id`-aware pairing. `--json` and `--source-language` flags.
  - `clm extract-voiceover` / `clm inline-voiceover` — move voiceover cells to/from
    companion `voiceover_*.py` files linked by `slide_id`/`for_slide`. `--dry-run`.
  - `clm authoring-rules` — look up merged authoring rules (common + course-specific)
    by course spec or slide path. `--json` flag.
  - `clm.core.topic_resolver` — standalone topic resolution: `build_topic_map()`,
    `resolve_topic()`, `find_slide_files()`, `get_course_topic_ids()`.
  - `clm.slides.tags` — canonical tag definitions, single source of truth. Adds
    `completed` and `workshop` tags.
  - `slide_id` and `for_slide` metadata parsing in `CellMetadata` and
    `parse_cell_header()` (backward-compatible).
- **Build pipeline integration for voiceover companion files**: Companion voiceover files
  are automatically merged during notebook processing, and internal metadata is stripped
  from all output.
  - When `voiceover_X.py` exists alongside `slides_X.py`, voiceover cells are merged
    in-memory for speaker output. Other output kinds filter them via tag-based deletion.
  - `slide_id` and `for_slide` metadata are stripped from all output cell metadata.
  - Companion files are excluded from the `other_files` payload to avoid duplication.
  - Unmatched `for_slide` references produce build warnings.
- **Recording management module** (`clm recordings`): New optional module for managing
  the video recording workflow for educational courses.
  - `clm recordings check` — verify recording dependencies (ffmpeg, onnxruntime)
  - `clm recordings process` — process a single recording through the 5-step audio
    pipeline (extract → DeepFilterNet3 ONNX noise reduction → FFmpeg filters → AAC → mux)
  - `clm recordings batch` — batch-process all recordings in a directory
  - `clm recordings status` — show per-lecture recording status for a course
  - `clm recordings compare` — generate A/B audio comparison HTML with blind test mode
  - `clm recordings assemble` — scan for paired raw video + processed audio, mux final
    output via FFmpeg, and archive originals
  - `clm recordings serve` — HTMX-based web dashboard with SSE, lecture selection,
    watcher controls, OBS connection indicator, and processing jobs panel
  - Recording workflow automation: naming conventions, three-tier directory structure
    (`to-process/`, `final/`, `archive/`), session state machine, OBS WebSocket integration
  - Per-course recording state stored as JSON with auto-assignment and status tracking
  - Git commit capture at recording assignment time
  - File watcher with stability detection and backend-aware behavior
- **Pluggable recording processing backends**: Architecture refactored from monolithic
  to Protocol-based with three implementations:
  - `OnnxAudioFirstBackend` — local DeepFilterNet3 ONNX inference (default)
  - `ExternalAudioFirstBackend` — iZotope RX 11 or similar external tool workflows
  - `AuphonicBackend` — cloud video-in/video-out with speech-aware denoising, leveling,
    loudness normalization, and optional cut lists
  - `make_backend()` factory for backend selection via config
  - `JobManager` with lazy async poller, `JsonFileJobStore` with atomic writes,
    `EventBus` for lifecycle events
  - 6 new CLI subcommands: `clm recordings backends`, `clm recordings submit`,
    `clm recordings jobs list/cancel`, `clm recordings auphonic preset list/sync`
  - Web dashboard "Processing Jobs" panel with progress bars and cancel buttons
- **Per-target remote-path for GitLab group support**: Each `<output-target>` can
  now override `<remote-path>` to push to a different GitLab group. When a target has
  its own `<remote-path>`, the target suffix is suppressed.
- **Voiceover backends and device control**: Pluggable transcription backends with
  Granite model support and configurable device selection.
- **`--remove-missing` flag for `clm db prune/clean`**: Remove jobs for files that
  no longer exist on disk.
- **Default to keeping completed/failed jobs indefinitely** in the job queue.
- 367 new tests for MCP/slide tooling, 355 tests for recordings module.

### Changed
- **`clm git init` is now idempotent**: Running on already-initialized repos adds the
  remote origin if the remote exists but wasn't configured locally.
- **Default processing backend changed to `onnx`**: Fresh installs work offline without
  cloud credentials; users opt into Auphonic or external backends explicitly.
- **Replaced DeepFilterNet CLI with ONNX inference**: Removes the dependency on the
  unmaintained `deepfilternet` package. Dependencies: `onnxruntime`, `soundfile`, `numpy`.
- **Renamed config field**: `deepfilter_atten_lim` → `denoise_atten_lim` in both
  `PipelineConfig` and `RecordingsProcessingConfig`.
- `jupyter_utils.py` tag constants now imported from `clm.slides.tags` instead of
  defined locally. Tag sets are `frozenset` (immutable).
- `Course._build_topic_map()` delegates to `clm.core.topic_resolver.build_topic_map()`.
- `completed` tag added to `CodeAlongOutput.tags_to_delete_cell` (processed identically
  to `alt`: deleted in code-along, kept in completed/speaker).
- Test suite runs in parallel by default via `pytest-xdist` (`-n auto`), reducing fast
  suite time to ~30 seconds.

### Removed
- **Legacy backend module**: Deleted `backends_legacy.py` and its companion test file.
  All legacy functionality superseded by the new backend package.

### Fixed
- **Voiceover: CUDA crash on Windows**: Transcription now runs in an isolated subprocess
  to prevent CUDA memory conflicts when the parent process also uses GPU resources.
- **Voiceover: slide 0 bug**: Fixed off-by-one error in slide matching that could assign
  content to a non-existent slide index.
- **Orphaned worker processes on Windows**: Worker subprocesses are now properly terminated
  when the parent process exits.
- **Tornado SelectorThread atexit race on Windows**: Fixed spurious exception during
  interpreter shutdown.
- **Git init misclassifying empty remote repos**: Empty remote repositories are no longer
  misidentified as nonexistent.
- **Flaky mock worker discovery tests**: Replaced timing-dependent assertions with
  event-based synchronization.
- **SSE bridge thread safety**: Cross-thread events now marshal via
  `loop.call_soon_threadsafe` instead of non-thread-safe `put_nowait`.

## [1.1.9] - 2026-03-25

### Changed
- **Replaced litellm with openai SDK**: The `[summarize]` extra now uses the `openai`
  package directly instead of `litellm`, reducing the dependency footprint. The LLM
  client, polish module, and summarize pipeline all use the OpenAI SDK natively.
- **Added langfuse dependency**: Added `langfuse` to the `[summarize]` optional
  dependency group for LLM observability and tracing.

### Fixed
- **mypy type annotation**: Fixed `cv2.cvtColor` return type annotation in
  `voiceover/keyframes.py`.

## [1.1.8] - 2026-03-17

### Added
- **bm25s dependency**: Added `bm25s[core]>=0.3.2.post1` as a core dependency for BM25
  sparse retrieval support in notebooks.
- **Docker notebook image**: Added `bm25s[core]` to both lite and full variants of the
  notebook-processor Docker image.

## [1.1.7] - 2026-03-17

### Added
- **`voiceover` cell tag**: New tag that behaves identically to `notes` (private,
  deleted from completed/code-along output, kept in speaker output) but renders with
  a light amber background (`#FFEEBA`) instead of yellow, to visually distinguish
  voiceover-originated content from hand-written speaker notes.

### Changed
- **Renamed `is_notes` → `is_narrative`** in `slide_parser` and `slide_writer`: The
  property now returns `True` for both `notes` and `voiceover` tags, reflecting that
  both are speaker-facing narrative content attached to slides.

## [1.1.6] - 2026-03-10

### Added
- **ipywidgets dependency**: Added `ipywidgets>=8.1.0` to the `[notebook]` optional
  dependency group to fix tqdm "IProgress not found" warning in Jupyter notebooks.

## [1.1.5] - 2026-03-09

### Added
- **`project_` file prefix**: Files named `project_*.py`, `project_*.md`, etc. are now
  recognized as notebook files and processed through the full notebook pipeline (jupytext →
  nbconvert → HTML/ipynb), alongside the existing `slides_` and `topic_` prefixes. This
  enables markdown-based project documents to be converted to notebooks and HTML slides.
- **`prog-lang` attribute on `<topic>`**: Individual topics can now override the course-level
  programming language with `<topic prog-lang="java">my_topic</topic>`. This is especially
  useful for `.md` notebook files where the language cannot be inferred from the file extension.

### Changed
- **`.md` default language changed from Rust to Python**: Markdown notebook files (`.md`) now
  default to Python instead of Rust when no course-level or topic-level `prog-lang` is set.
  The programming language for `.md` files follows a priority chain:
  topic `prog-lang` attribute → course `<prog-lang>` element → Python (default).

### Fixed
- **Markdown notebook parsing**: `.md` files are now correctly parsed using jupytext's `"md"`
  format, which auto-detects both standard markdown (fenced code blocks) and MyST
  (`{code-cell}`) variants. Previously, `.md` files were incorrectly parsed using the
  programming language's format (e.g., `"py:percent"`), causing the entire file content to be
  treated as a single code cell.
- Forbid Markdown headings in trainer summaries to preserve heading hierarchy in
  generated summary documents.

## [1.1.3] - 2026-03-05

### Added
- **Voiceover pipeline** (`clm voiceover`): Synchronize video recordings with slide files.
  Extracts audio, transcribes with Whisper, detects slide transitions via frame differencing,
  matches transitions to slides using OCR + fuzzy matching, and inserts speaker notes into
  percent-format `.py` slide files. Requires the `[voiceover]` extra.
  - `clm voiceover sync` — Full pipeline: video + slides → speaker notes
  - `clm voiceover transcribe` — Extract transcript from video
  - `clm voiceover detect` — Detect slide transitions in video
  - `clm voiceover identify` — Match video frames to slides via OCR
- **LLM polish** (`clm polish`): Clean up existing speaker notes using an LLM. Removes filler
  words, fixes grammar, and preserves technical terms. Works standalone or as part of the
  voiceover pipeline (`--mode polished`). Requires the `[summarize]` extra.
- **`clm.notebooks` module**: Shared slide file utilities for parsing, writing, and polishing
  percent-format `.py` slide files (`slide_parser`, `slide_writer`, `polish`).
- **`clm.voiceover` module**: Video processing pipeline with pluggable transcription backend,
  frame-based transition detection, OCR + fuzzy slide matching, and transcript-to-slide alignment.
- **`[voiceover]` optional dependency group**: `faster-whisper`, `opencv-python`, `pytesseract`,
  `rapidfuzz`, `Pillow`.
- 129 new tests across voiceover, notebooks, and CLI modules.

### Changed
- Voiceover optional dependencies use lazy imports so CI works without the `[voiceover]` extra.

## [1.1.2] - 2026-03-05

### Added
- **`clm summarize` command**: Generate LLM-powered markdown summaries of course content.
  Supports `--audience client|trainer`, `--style prose|bullets`, `--granularity notebook|section`,
  per-notebook caching, and configurable LLM models via the openai SDK. Requires the `[summarize]` extra.
- **`--amend` flag for `clm git commit` and `clm git sync`**: Amend the previous commit
  instead of creating a new one. When used without `-m`, reuses the previous commit message
  (`--no-edit`). When used with `-m`, replaces the commit message.
- **`--force-with-lease` flag for `clm git push` and `clm git sync`**: Safe force push
  for rewritten history. `--amend` on `sync` implies `--force-with-lease` automatically.
  When force-pushing, the "remote is ahead" safety check is skipped.

### Fixed
- Bullet-style client output formatting in summarize command.

## [1.1.1] - 2026-03-05

### Added
- Automatic `.env` file loading: The `build` command now walks up the directory tree to
  find a `.env` file and loads it before spawning workers.

### Changed
- Reorganized optional dependencies: moved data-science packages from `[notebook]` to
  `[ml]` extra, organized by category.

### Fixed
- CLI help text formatting for multi-line examples.
- Suppressed `RequestsDependencyWarning` from the requests library.

## [1.1.0] - 2026-02-27

### Added
- **Remote URL template for git operations**: Trainers can now override the git remote
  URL pattern via a configurable template with placeholders (`{repository_base}`, `{repo}`,
  `{slug}`, `{lang}`, `{suffix}`). Set via `CLM_GIT__REMOTE_TEMPLATE` environment variable,
  `[git] remote_template` in TOML config, or `<remote-template>` in the course spec XML.
  Enables SSH access with custom host aliases (e.g., `git@github.com-cam:Org/{repo}.git`).

### Changed
- **Flatten speaker kind subfolder**: Speaker output no longer creates a redundant `Speaker/`
  subfolder in the output path. Paths are now `.../Html/Section/` instead of
  `.../Html/Speaker/Section/`, since speaker output has only one variant.

## [1.0.9] - 2025-11-29

### Added
- `clm info <topic>` command for version-accurate documentation that downstream agents
  can query at runtime. Topics: `spec-files`, `commands`, `migration`.

### Changed
- `<project-slug>` promoted to top-level course spec element (previously inside `<github>`).
  The old location still works but is deprecated and logs a warning.

## [1.0.8] - 2025-11-28

### Added
- `docker.io/` registry prefix for Podman compatibility.
- `.python-version` file for Arch Linux compatibility.

### Fixed
- `sanitize_path` no longer strips leading dots from path components.

## [1.0.7] - 2025-11-27

### Added
- LangSmith and Ragas to ML optional dependencies.
