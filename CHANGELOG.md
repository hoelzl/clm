# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
- **`validate-slides --review` no longer reports missing voiceover for cells
  inside `workshop`/`end-workshop` ranges.** Workshops are narrated live by
  the trainer, so the authoring convention is to attach voiceover only to
  the workshop's opening heading (the `workshop`-tagged markdown cell) and
  leave subsequent exercise subslides and code cells silent. The validator
  now matches that convention: the workshop heading is still gap-checked,
  but every other cell inside the workshop range is suppressed. Files
  without any `workshop` heading are unaffected. Design:
  `docs/claude/design/validator-workshop-voiceover-suppression.md`.

## [1.4.1] - 2026-05-13

### Changed
- **`include_shadowed_by_local` / `include_shadowed` warnings now check the
  topic's `.clm-include` ledger before firing.** When a real file at
  `<topic-dir>/<as_path>` matches a ledger entry (same `as_path` *and*
  resolved `source`), the shadowing is `clm sync-includes`'s own
  materialization, not an ad-hoc local override — so the warning is
  suppressed. Without this, any course adopting `<include>` would see one
  HIGH warning per materialized file on every build (16 per build in the
  AZAV ML migration). Unauthorized shadowings — real local files with no
  matching ledger entry, or stale ledgers pointing at a different source —
  still warn as before. Both the build-time path (`Topic.apply_includes`)
  and `clm validate-spec` apply the same check. Ledger reader extracted to
  `clm.core.include_ledger` so build and sync-includes share one
  implementation.
- **Build summary always shows the output-write registry counts.** The
  `N duplicate output writes deduplicated; N output paths had conflicting
  writes` line now appears unconditionally alongside files-processed,
  errors, and warnings — previously it was suppressed when both counts
  were zero, which left users unable to confirm the registry had run.

### Changed
- **`clm sync-includes --gitignore` replaced with `--print-gitignore`.** The
  old flag wrote per-topic `.gitignore` files into every materialized topic
  directory, which would leak into student/trainer/speaker build output
  (same class of bug as the `.clm-include` ledger leak fixed earlier on
  master). The new flag prints suggested `.gitignore` patterns to stdout
  instead, so the author pastes them once into a course-root `.gitignore`
  and CLM never touches that file again. Output is paste-safe and idempotent:
  `clm sync-includes spec.xml --print-gitignore >> .gitignore`. Patterns are
  anchored under `slides/**/` so the canonical include source (typically
  under `examples/`) stays tracked. Breaking: scripts invoking `--gitignore`
  must switch to `--print-gitignore` and redirect. The flag is unreleased,
  so no migration path is provided.
- **`<include as="...">` rejects glob metacharacters.** `*`, `?`, `[`, `]`
  in the `as` attribute now produce a `CourseSpecError` at parse time. The
  `as` value flows into generated gitignore patterns and into a literal
  filesystem path; glob metacharacters in either context are confusing and
  almost always a typo. The `source` attribute is unchanged — it can still
  point at on-disk filenames containing these characters.

### Fixed
- **HTTP-replay cassettes survive forceful kernel termination.** Previously,
  if a build hit the wait-for-completion timeout while a notebook was
  recording HTTP interactions, the worker process was force-killed
  (`TerminateProcess` on Windows) before vcrpy could flush the cassette to
  disk. Every interaction recorded so far was discarded, and the next build
  re-ran the same long-running requests from scratch — for chained-request
  notebooks this caused the build to time out forever. The bootstrap cell
  now writes to a per-worker staging file at an absolute path under the
  source tree and patches `Cassette.append` to save eagerly after each
  recorded interaction, so the cassette on disk always reflects every
  interaction recorded up to the moment the kernel died. A post-execution
  merge step runs in a `finally` block to fold the worker's staging file
  (and any orphan staging files left by previously-killed workers) into the
  canonical cassette under a cross-process file lock, deduplicating by
  request fingerprint. This also makes concurrent builds of the same
  notebook in different languages safe — German and English workers each
  write to their own staging file and merge into the shared canonical
  cassette without races. Adds `filelock` to the `[replay]` extra.

### Removed
- **Validator: "start/completed inside workshop" warning.** The matching
  authoring guideline was retired, so the deterministic `tags`-category
  warning that flagged `start`/`completed` pairs nested in a `workshop`
  range no longer fires. The orphan `end-workshop` warning and the
  start/completed pairing checks are unchanged. Existing files that
  previously emitted this warning will validate cleanly without
  modification.

### Added
- **`<topic id="...">` attribute as an alternative to text-content topic IDs.**
  The legacy `<topic>foo</topic>` form continues to work for childless topics,
  but topics that carry `<include>` or any other child elements must now use
  the attribute form: `<topic id="foo"><include .../></topic>`. CLM hard-errors
  when a `<topic>` has children but no resolvable ID, and when the ID is
  specified via both attribute and text — closing the long-standing
  "text-after-child becomes the child's tail, so the topic ID is silently
  empty" footgun. See `clm info migration` for the migration guidance and
  `clm info spec-files` for the reference.
- **`<include>` element on `<topic>` and `<section>`.** Splice a shared
  source directory or file from elsewhere in the course root into a
  topic at build time, without keeping byte-identical physical copies
  in sync by hand. Attributes: `source` (required, course-root-relative,
  forward- or backward-slash, no `..`), `as` (optional, target path
  under the topic; defaults to source basename; per-topic dedup key),
  `optional` (default `false`). Section-level includes are inherited as
  defaults by every child topic; a topic overrides by declaring its own
  `<include>` with the same `as`. The build splice is virtual — your
  working tree is untouched, but workers see the source under
  `<topic>/<as>` and outputs land in the topic's output directory as if
  the files had been copied there. A real local file at `<topic>/<as>`
  shadows the include (warning `include_shadowed_by_local`).
- **`clm validate-spec` surfaces `<include>` problems.** New finding
  categories: `include_source_missing` (error), `include_shadowed`
  (warning), `include_source_is_topic_dir` (warning),
  `include_dependencies` (info; lists `pyproject.toml` `[project]
  dependencies` so authors can confirm the worker environment satisfies
  them), `include_section_inheritance` (info; lists topics inheriting
  each section-level include). Intra-parent target collisions are
  raised as `CourseSpecError` at parse time.
- **`clm sync-includes` command.** Materialize every `<include>`
  declared in a spec onto the filesystem so notebooks running directly
  in VS Code / `jupyter lab` find their sibling packages. Three modes:
  `copy` (default), `symlink` (falls back to `copy` per-include on
  `OSError`, so Windows-without-admin is not blocked), `hardlink`
  (falls back to per-file copy on cross-device errors). A per-topic
  `.clm-include` JSON ledger records every path the command created;
  `--remove` consults the ledger and deletes only those paths, leaving
  untracked files in place. Options: `--data-dir`, `--mode`, `--remove`,
  `--gitignore` (idempotent per-topic `.gitignore` rules for
  materialized includes), `--dry-run`. See `clm info commands` and
  `clm info spec-files` for the full reference.
- **Output-write deduplication and conflict warnings on `clm build`.**
  Builds that legitimately produce identical writes to the same output
  path (e.g. multiple topics that share an `<include>`-sourced file,
  or the C# course's repeated `NUnitTestRunner.cs`) now collapse to a
  single write, with the remaining writes reported as a dedup count
  in the build summary. Differing-content writes to the same output
  path still proceed with last-writer-wins (preserving previous
  behavior) but now surface a per-conflict `output_path_conflict`
  warning naming both source paths and a structured
  `output_conflicts` entry in the JSON summary, so authoring drift no
  longer hides silently. Image-path collisions continue to go through
  the existing `image_collision` channel — no double-warning. Tunable
  via `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB` (default 50 MB; files above
  this size skip hashing and are reported as a single summary
  collision count).
- **`output_dedup_count`, `output_conflicts`, and
  `output_large_file_collision_count` keys on the `clm build` JSON
  summary.** Emitted unconditionally so machine consumers don't have
  to special-case the absence of registry events on clean builds.
  `output_conflicts` is a list of
  `{output_path, first_writer, last_writer, first_hash, last_hash,
  conflict_count}` records.
- **`--http-replay=new-episodes` build mode.** Replays every request
  that is in the existing cassette and records only the genuinely new
  ones into the same file. Fixes the case where an edited notebook now
  issues additional requests on top of an otherwise-valid cassette and a
  strict mode would fail with `CannotOverwriteExistingCassetteException`.
  Maps to vcrpy's `new_episodes` record mode. Also accepted via the
  `CLM_HTTP_REPLAY_MODE` environment variable.

### Changed
- **Local-build default for `--http-replay` is now `new-episodes`.**
  Previously the local default was `once`, which failed builds whenever
  an edited notebook issued a request not in its cassette. Local builds
  now replay recorded requests and append new ones to the same cassette,
  so authors can iterate on a notebook without manually choosing a flag.
  CI default is unchanged at strict `replay`. Pass `--http-replay=once`
  if you want a local build to fail loudly on unrecorded requests.
- **`evaluate="no"` topic attribute in course specs.** Renders the
  notebook to all configured output formats (HTML, `.ipynb`, code) without
  spawning a kernel — cells appear with empty outputs. Useful for topics
  that depend on live services, GPUs, long training runs, or interactive
  demos that should ship as static decks. Independent of `html=` (which
  skips HTML entirely) and `skip-errors` (which catches in-cell
  exceptions). Implemented at the `NotebookProcessor` layer by forcing
  `evaluate_for_html=False` on the active output spec and bypassing the
  executed-notebook cache, so neither Recording (cache producer) nor
  Completed/Trainer/Partial (cache consumers) execute when a topic opts
  out. See `clm info spec-files` for the attribute reference.

### Fixed
- **`.clm-include` ledger files no longer leak into build output.** The
  per-topic JSON ledger written by `clm sync-includes` is a
  build-internal artifact, but `DirectoryTopic.build_file_map` was
  picking it up as a regular topic file and copying it into every
  output variant alongside the materialized includes. It is now
  filtered at the course-scanning layer (`SKIP_FILE_NAMES` in
  `path_utils`), so it never enters the worker payload, source mount,
  or output tree. Builds that already published a ledger alongside
  outputs will stop emitting it on the next build.

## [1.3.3] - 2026-05-03

### Added
- **Recordings web UI: per-part chip strip in the lectures page.** Each
  deck row's Status column now renders one chip per existing part (color-
  coded: amber `recorded`, green `processed`, purple `processing`, red
  `failed`) plus a trailing `+ N` chip for "record the next part". The
  chip strip doubles as the part selector — clicking a chip targets the
  Record/Arm/Process/Advance buttons at that part, and selecting an
  existing chip swaps Record/Arm labels to Retake/Re-arm with a ⚠ icon
  warning that the current take will be moved to `takes/`. A
  `Process all` button appears only when ≥2 unprocessed parts exist on
  the deck. Right-click a chip to reveal an inline take-history panel
  below the deck row, lazy-fetched from a new
  `GET /decks/{course}/{section}/{deck}/takes` route and refreshed on
  job SSE events. Selection state lives in client-side `sessionStorage`
  keyed by `(course, section, deck)` so swaps no longer wipe the user's
  choice — incidentally fixing the long-standing `part_number`
  snap-back bug as a side-effect of removing the input.
- **Restore-take UI in the recordings dashboard.** The inline take-
  history panel exposed by Phase C gains a Restore action behind a
  two-step morph button on each history row, plus a new
  `POST /decks/{course}/{section}/{deck}/takes/{take}/restore` route
  that performs the filesystem swap with planned-rename rollback. The
  active take now appears in the panel alongside history rows so a
  single Open affordance covers every take, and the Recorded column
  renders as a local datetime instead of a raw epoch. Open in
  Explorer goes through a new `POST /open-explorer` endpoint
  (`explorer /select,…` on Windows, `open -R` on macOS, `xdg-open` on
  Linux) so the action works from `http://` origins where browsers
  block `file:///` links.
- **Validator: DE/EN cell-adjacency check.** `validate-slides` now
  flags paired DE/EN cells separated by another lang-tagged or
  narrative cell (the
  `[de slide] [de voiceover] [en slide] [en voiceover]` anti-pattern).
  Runs in both `pairing` mode and `validate_quick` (PostToolUse hook),
  so authoring tools surface ordering violations at edit time. The
  cohesion layout `[DE_start, DE_completed, EN_start, EN_completed]`
  is permitted: a same-language `start` + immediately-following
  `completed` pair is collapsed into one logical unit before the
  ordering check runs.

### Changed
- **Spec consumers consistently honor `module=` bindings.**
  `validate-slides`, `normalize-slides`, `search-slides`,
  `resolve-topic --course-spec`, and
  `authoring-rules --slide-path` previously ignored the `module=`
  attribute on `<section>`/`<topic>` and processed every filesystem
  match for a topic ID, leaking across modules in cohort-archive
  setups. They now route through new shared helpers
  (`SectionSpec.module_for`, `CourseSpec.iter_topic_bindings`,
  `topic_resolver.matches_for_binding`,
  `resolve_topic(course_topic_bindings=…)`) so every consumer applies
  the same effective-module logic as `Course._build_topics` and
  `spec_validator`.

### Fixed
- **`validate-slides` now recurses into subdirectories for module
  and root paths.** `validate_directory` previously called the
  topic-scoped `find_slide_files`, which only inspected direct
  children — so passing `slides/` or a module directory silently
  returned zero findings even when nested topics had real issues.
  Promotes the recursion logic to the public
  `topic_resolver.find_slide_files_recursive` helper and routes both
  `validator.validate_directory` and `normalizer.normalize_directory`
  through it. Topic-directory semantics are preserved (a path with
  direct slide files returns those without descending).
- **Recordings retake/restore correctness.** Several edge cases in
  the manual-process + restore path were silently corrupting take
  state: `record_retake` could clobber an existing history take
  after a restore (now uses `max(takes[].take) + 1` for stable
  identity); `_preserve_active_take` used a filesystem-derived take
  number that diverged from `state.active_take` after a restore;
  `_swap_active_with_take` derived the processed-state of the target
  from `state.json`'s `processed_file` (which the manual `/process`
  route doesn't update), so restoring a processed take dumped the
  raw back into `to-process/`; `_scan_active_take_files` only
  recognised video extensions, leaving Auphonic `.edl` cut lists
  (and future sidecars) behind on every retake; and the chip
  strip's `data-deck-key` referenced an undefined `section_name`
  variable, producing right-click 404s. `scan_take_files` /
  `scan_section_takes` now `sanitize_file_name` before joining so
  section names containing characters stripped by the sanitizer
  (e.g. colons) match the on-disk subtree.

### Build
- **Pinned micromamba in `docker/notebook/Dockerfile`** with SHA-256
  verification and `curl --fail/--retry`. The previous
  `latest` redirect intermittently served HTML error pages that got
  piped into `tar`, surfacing as `bzip2: (stdin) is not a bzip2 file`
  and failing two consecutive CI runs during the 1.3.2 release.
- **Bumped `[tool.uv] exclude-newer` floor** to 2026-04-18 (~14 days
  back) and re-locked. Pairs with a PowerShell-profile change that
  mirrors this date into `$env:UV_EXCLUDE_NEWER`, so the env var no
  longer drifts on a 4-day rolling window and `uv` stops silently
  regenerating `uv.lock` mid-pre-commit-hook.

## [1.3.2] - 2026-05-02

### Added
- **Module-bound section/topic references** in course specs. `<section>` and
  `<topic>` accept an optional `module="module_directory_name"` attribute; when
  set, topic resolution is restricted to that specific module directory. This
  removes the long-standing first-occurrence-wins ambiguity when two modules
  share topic IDs, and is the supported mechanism for cohort archives or
  course variants. Per-topic `module=` overrides the section default. The
  `clm resolve-topic` CLI and the MCP `resolve_topic` tool gain a matching
  `--module` / `module` argument. `clm validate-spec` reports unknown module
  names and module-bound topics that don't exist in the named module. See
  `clm info spec-files` for the full pattern, including the cohort-archive
  recipe.
- **`trainer` and `recording` output kinds** split the previous `speaker`
  kind into two named variants that match how the decks are actually used:
  - `trainer` keeps `notes` cells but strips `voiceover` cells — the
    variant most trainers want when teaching live without recording.
  - `recording` keeps both `notes` and `voiceover` cells — the deck used
    by the trainer recording the course on video, where voiceover cells
    contain the polished narration read on camera.
  Both kinds land under the existing private (`speaker/`) toplevel
  output directory; their kind subdirs (`Trainer/`, `Recording/`) keep
  their files distinct. `recording` is now the canonical HTML cache
  producer; `trainer`, `completed`, and `partial` HTML all reuse its
  executed notebook by filtering the appropriate cell subset.

### Deprecated
- **`speaker` output kind**: still accepted as an input alias for one
  release and treated as `recording`. Spec parsing logs a deprecation
  warning and rewrites `<kind>speaker</kind>` to `<kind>recording</kind>`
  internally so downstream consumers only see the canonical kinds.
  `--speaker-only` continues to work and now selects both `trainer` and
  `recording`. See `clm info migration` for the spec-rewrite recipe.

### Changed
- **Duplicate-topic-id warning is now emitted only when resolution
  actually depended on first-occurrence-wins.** Previously the warning
  fired for every duplicate topic ID found on disk, even when every
  reference in the spec was bound to a specific module via the new
  `module=` attribute. Specs that disambiguate every duplicate via
  `module=` now produce no duplicate-id noise. Unbound references that
  hit a duplicate still warn exactly as before — strict improvement, no
  behaviour change for existing specs.
- **Output paths for private kinds always include a kind subdir.**
  Previously a `speaker` build wrote to
  `output/speaker/<course>/Slides/Html/<topic>.html` (no kind subdir).
  `recording` and `trainer` builds now write to
  `output/speaker/<course>/Slides/Html/Recording/<topic>.html` and
  `output/speaker/<course>/Slides/Html/Trainer/<topic>.html`. The
  deprecated `speaker` kind alias produces the same layout as
  `recording`. Tooling that reads from the old kind-subdir-less path
  needs to switch to one of the new locations.

## [1.3.1] - 2026-05-02

### Added
- **`end-workshop` tag**: marks the end of a workshop section that does not
  run to end-of-notebook. Until now, a workshop section was implicitly the
  trailing suffix of the slide deck — `partial` output kept demonstrations
  worked out before the first `workshop` heading and treated everything
  from that heading onwards as code-along. With `end-workshop`, trainers
  can now put workshops in the middle of a deck. The tag attaches to the
  markdown heading that starts the next non-workshop section (the cell
  carrying it is *outside* the workshop), and a deck may contain multiple
  workshops separated by regular content. Backward compatible: a workshop
  without an explicit `end-workshop` continues to extend to EOF, exactly
  like before. The validator warns on a stray `end-workshop` that appears
  before any `workshop` heading. Surfaced through `partial` output, the
  notebook processor's cached-partial filter, and the slide validator.

## [1.3.0] - 2026-04-26

### Added
- **HTTP replay for notebook execution (opt-in, per topic)**: topics that
  call live HTTP services can now record a cassette once and replay it
  deterministically on subsequent builds. Opt in by setting
  `http-replay="yes"` on the `<topic>` element; CLM injects a hidden
  `vcrpy` bootstrap cell at execution time and strips it before HTML
  rendering. Cassettes live next to the source as
  `<stem>.http-cassette.yaml` (or in a per-topic `_cassettes/` directory
  if that exists) and travel with the notebook into worker payloads and
  Docker source mounts, but are excluded from public and speaker output.
  Record mode is selected per build via `--http-replay=<replay|once|
  refresh|disabled>` or `CLM_HTTP_REPLAY_MODE`; CI (`CI=true`) defaults
  to strict `replay`, local builds default to `once`. The executed-
  notebook cache key folds in the cassette bytes so a refresh invalidates
  only that topic's cache entry. Requires the new `[replay]` extra
  (`pip install -e .[replay]`), also included in `[all]`. See
  `docs/user-guide/http-replay.md` for the author workflow.
- **`skip-errors` topic attribute**: cheap, generic escape hatch for
  topics whose cells may raise. Set `skip-errors="yes"` on the `<topic>`
  to build HTML even when cells fail; error-output cells are cleared and
  a processing warning lists the affected indices. Not a substitute for
  HTTP replay — a topic with a recorded cassette should rely on replay
  so legitimate regressions still surface.
- **`partial` output kind**: fourth kind alongside `code-along`, `completed`,
  and `speaker`. A `partial` notebook is completed up to the first `workshop`
  markdown heading and code-along from there to end-of-notebook — intended as
  a student follow-along artifact so demonstrations remain worked out while
  workshop exercises stay blank. Partial HTML executes independently (pre-
  workshop cells produce outputs; post-workshop cells are blanked before
  execution so they produce none). Request via `<kind>partial</kind>` in an
  `<output-target>`.
- **MCP exposure for the voiceover pipeline**: six new MCP tools surface
  read-mostly stages of the voiceover workflow so authoring sessions can
  drive them without shelling out — `voiceover_transcribe`,
  `voiceover_identify_rev`, `voiceover_compare`, `voiceover_backfill_dry`,
  `voiceover_cache_list`, and `voiceover_trace_show`. Mutating operations
  (`sync`, `sync-at-rev`, `port-voiceover`, `backfill --apply`) stay
  CLI-only on purpose. All handlers honor the existing artifact cache.
- **Inventory-aware compare wrapper**: new
  `clm voiceover compare-from-inventory SLIDE_FILE --inventory PATH --lang`
  looks up the recording video(s) for a slide in a
  `video_to_slide_mapping.json`-style inventory and composes
  `identify-rev` → `sync-at-rev` → `compare` automatically. Supports
  multi-part recordings (inventory order is preserved). Accepts the same
  `--rev / --auto / --force-rev / --format / -o` knobs as the underlying
  commands.
- **Markdown output for compare reports**: `clm voiceover compare` now
  accepts `--format {table,json,markdown}`; the existing `--json` flag is
  a shorthand for `--format json`. New `clm voiceover report REPORT.json`
  re-renders a saved JSON report in any format without rerunning the LLM
  judge. Markdown output has a summary-per-slide table plus per-bucket
  sections grouped by `dropped` / `added` / `rewritten` / `manual_review`.
- **`latest.patch` pointer for backfill**: every non-dry-run
  `clm voiceover backfill` invocation now also writes
  `.clm/voiceover-backfill/<topic>/latest.patch` (one level shallower
  than the timestamped scratch directory) so "just show me the most
  recent diff for this topic" is a predictable read. The full
  timestamped history under `<topic>-<ts>/port.patch` is retained.
- **JupyterLite output (experimental, opt-in)**: new `jupyterlite` output
  format produces a deployable JupyterLite static site from the already-built
  `notebook`-format output for one `(target, language, kind)` tuple. Opt-in
  via a `<jupyterlite>` config block (at course or target level, with
  per-target overriding wholesale) plus explicit `<format>jupyterlite</format>`
  per target; no course without both gates produces JupyterLite artifacts.
  Supports the `xeus-python` and `pyodide` kernels, pre-staged offline
  wheels, and optional `environment.yml`. Installing the
  `[jupyterlite]` extra (also in `[all]`) brings in `jupyterlite-core`, the
  two kernel addons, and `jupyter-server`; the build coordinator spawns one
  `jupyterlite-builder` worker on-demand only when a target requests the
  format. Each build writes a deterministic `jupyterlite-manifest.json`
  (cache-keyed on notebook-tree hash + wheel hashes + kernel +
  `jupyterlite-core` version). See `clm info jupyterlite` for the spec
  reference.
- **JupyterLite student launchers**: `<launcher>python</launcher>` (default)
  emits a `launch.py` with `ThreadingHTTPServer`, `.wasm` MIME fix for
  Windows, free-port selection, and browser auto-open.
  `<launcher>miniserve</launcher>` bundles prebuilt miniserve binaries for
  Windows, macOS (x64 + ARM), and Linux (~20 MB) — zero runtime dependencies;
  each binary is SHA-256 verified and cached under
  `~/.cache/clm/miniserve/<version>/`. Per-OS launcher scripts (`launch.bat`,
  `launch.command`, `launch.sh`) are emitted alongside the binaries. A
  `README-offline.md` is always emitted with launcher-appropriate instructions
  and IndexedDB persistence guidance.
- **JupyterLite branding**: optional `<branding>` block inside
  `<jupyterlite>` with `<theme>` (light/dark), `<logo>`, and `<site-name>`
  fields, mapped to JupyterLab's `overrides.json`.
- **`clm jupyterlite preview`**: CLI command that serves a previously built
  JupyterLite site locally for quick testing.
- **JupyterLite user guide**: `docs/user-guide/jupyterlite.md` — installation,
  configuration reference, launcher options, branding, troubleshooting.

### Changed
- **Validator: workshop scope now runs to end-of-notebook.** The scope used
  to exit at the next non-workshop slide heading; it now extends from the
  first `workshop` heading to EOF, matching real-world notebooks where
  workshops span multiple slides. A future `end-workshop` tag may be
  introduced if content after the workshop section is needed.
- **`run_compare` is now a library entry point**. Extracted from the CLI
  into `clm.voiceover.compare` (sync `run_compare` + async
  `run_compare_async`) so the MCP handler and any future callers share
  the same code path. Behavior unchanged for CLI users.
- **Shared fingerprint/identify-rev helper**: `clm.voiceover.identify`
  houses the fingerprint-build + rev-score composition that was
  previously duplicated between `identify-rev` and the backfill CLI
  entry point.

### Fixed
- **Spurious "Unknown tag '_post_workshop'" warnings during partial HTML
  builds**: the synthetic `_post_workshop` sentinel that
  `PartialOutput.annotate_cells` injects into cell metadata was being
  flagged by the per-cell tag validator before the strip-pass at the end
  of `_process_notebook_node` removed it, producing one warning per
  post-workshop cell on every slide file with a `workshop` heading.
  `get_invalid_code_tags` / `get_invalid_markdown_tags` now skip any tag
  with a leading underscore so internal CLM sentinels never reach the
  warning channel.

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
