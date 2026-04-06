# MCP Server and Slide Tooling — Handover

**Status**: Phase 1 + 2A + 2B [DONE]. Phase 2C [TODO] — slide validation.
**Branch**: `master`.
**Spec doc**: [`docs/claude/design/mcp-server-and-slide-tooling.md`](design/mcp-server-and-slide-tooling.md) — defines tool schemas, output formats, user-facing behavior.
**Implementation design**: [`docs/claude/design/mcp-server-implementation-design.md`](design/mcp-server-implementation-design.md) — covers code reuse, module extraction, internal architecture.

---

## 1. Feature Overview

**Name**: MCP Server and Slide Tooling for AI-Assisted Slide Authoring.

**One-paragraph description**: Add an MCP (Model Context Protocol) server to CLM that exposes course navigation, slide validation, normalization, bilingual editing support, and voiceover management as tools callable by Claude Code. These tools are also available as CLI commands. The implementation extracts and generalizes existing code (topic resolution, slide parsing, tag handling) into reusable modules, introduces a new `clm.slides` package for higher-level slide operations, and a `clm.mcp` package for the MCP server.

**Problem it solves**: Claude Code makes systematic errors when editing bilingual slide files in course repositories. Six root causes: (1) tag overloading (`alt` serves three purposes), (2) inconsistent DE/EN interleaving, (3) implicit workshop boundaries, (4) slow topic discovery (~559 topic dirs across 46 modules), (5) no real-time validation, (6) bilingual editing complexity. This feature addresses all six by providing instant topic resolution, structural validation, deterministic normalization, and bilingual editing aids.

**Related work**:
- Spec document: `docs/claude/design/mcp-server-and-slide-tooling.md`
- PythonCourses repository (consumer): hooks, slash commands, `.mcp.json` — these are *not* implemented in CLM
- Voiceover pipeline (predecessor, merged v1.1.3): `clm.notebooks.slide_parser`, `clm.notebooks.slide_writer` — reused heavily
- Existing outline command: `src/clm/cli/commands/outline.py` — extended with JSON format

---

## 2. Design Decisions

### Library-first architecture

**Decision**: All business logic lives in library modules (`clm.core.topic_resolver`, `clm.slides.*`). CLI commands and MCP tool handlers are thin wrappers. Both always produce identical results.

**Why**: Avoids duplication between CLI and MCP. Makes testing straightforward — test the library, not the protocol layer. Follows the existing pattern where `CourseSpec.from_file()` is a library call used by multiple CLI commands.

### New `clm.slides` package (not extending `clm.notebooks`)

**Decision**: Create `src/clm/slides/` for higher-level slide operations (validation, normalization, search, language tools, voiceover tools). Keep `clm.notebooks` (slide_parser, slide_writer, polish) as parsing/writing primitives.

**Why**: `clm.notebooks` is a leaf module imported by voiceover, polish, and the new slides tools. Putting higher-level operations into it would create circular dependencies (e.g., validator importing from notebooks while notebooks imports validator). Clean layering: `clm.notebooks` → `clm.slides` → `clm.mcp`.

**Rejected alternative**: Extending `clm.notebooks` with validation submodules. Rejected because it conflates parsing primitives with domain-specific tooling and risks import cycles.

### Extract topic resolution from `Course`

**Decision**: Create `clm.core.topic_resolver` with standalone functions (`build_topic_map()`, `resolve_topic()`). Refactor `Course._build_topic_map()` to delegate to the new module.

**Why**: The existing `Course._build_topic_map()` is a private method that requires constructing a full `Course` object. Topic resolution needs to work standalone (MCP tool, CLI command) without the overhead of loading all course files. The extraction also returns *all* occurrences of a topic ID (for ambiguity detection), while `Course` only keeps the first.

### Shared tag constants

**Decision**: Define tag constants once in `clm.slides.tags` and import from both the notebook worker (`jupyter_utils.py`) and the new validator. Add `completed` and `workshop` as new recognized tags.

**Why**: Tag definitions are currently duplicated conceptually between `jupyter_utils.py` (valid tag sets) and `output_spec.py` (per-output-kind delete sets). A single source of truth prevents drift. The new tags resolve tag overloading: `completed` replaces `alt`-after-`start`, and `workshop` marks workshop boundaries explicitly.

### `slide_id` as cell metadata

**Decision**: Add `slide_id` (and companion `for_slide`) as cell header metadata fields in Jupytext percent format. Paired DE/EN cells share the same `slide_id`. Strip from all output.

**Why**: Positional pairing (1st DE with 1st EN) is fragile — one missing cell shifts all subsequent pairs. `slide_id` makes pairing explicit and enables precise voiceover linking, sync suggestions, and language views. Stripping from output ensures students never see internal metadata.

### Optional dependencies for MCP and search

**Decision**: `rapidfuzz` goes into `[slides]` and `[mcp]` optional extras. `search_slides` falls back to substring matching without it. The MCP SDK (`mcp`) is in the `[mcp]` extra only.

**Why**: Core CLM (build pipeline) should not gain new dependencies. Course repositories that only build slides don't need a fuzzy matcher or MCP server. Gated imports follow the existing pattern (`[voiceover]`, `[recordings]`, `[summarize]`).

---

## 3. Phase Breakdown

### Phase 1: Foundation + Navigation [TODO]

**Goal**: Extract topic resolution, set up `clm.slides` package skeleton, implement navigation tools, stand up MCP server. After this phase, Claude Code can instantly find any slide file.

**Sub-phases for manageability**:

#### Phase 1A: Topic Resolver Extraction + `clm.slides` Skeleton [DONE]

**What it accomplishes**: Extracts topic resolution into a standalone, testable module. Creates the `clm.slides` package with `tags.py` as the first module (shared tag constants).

**Files created**:
- `src/clm/core/topic_resolver.py` — `TopicMatch`, `ResolutionResult`, `build_topic_map()`, `resolve_topic()`, `find_slide_files()`
- `src/clm/slides/__init__.py` — package init
- `src/clm/slides/tags.py` — `ALL_VALID_TAGS`, `VALID_CODE_TAGS`, `VALID_MARKDOWN_TAGS`, per-category tag sets
- `tests/core/test_topic_resolver.py`
- `tests/slides/__init__.py`
- `tests/slides/test_tags.py`
- Test fixtures: small fake `slides/` directory tree with modules and topics

**Files modified**:
- `src/clm/core/course.py` — refactor `_build_topic_map()` to delegate to `topic_resolver.build_topic_map()`
- `src/clm/workers/notebook/utils/jupyter_utils.py` — import tag constants from `clm.slides.tags`

**Acceptance criteria**:
- `build_topic_map()` produces identical results to `Course._build_topic_map()` for existing courses
- `resolve_topic()` handles exact match, glob patterns, ambiguity detection, and course-spec scoping
- `find_slide_files()` returns all `slides_*.py` / `topic_*.py` / `project_*.py` files
- All existing tests still pass (refactor is behavior-preserving)
- Tag constants in `clm.slides.tags` match the existing sets in `jupyter_utils.py` plus `completed` and `workshop`

#### Phase 1B: Navigation CLI Commands [DONE]

**What it accomplishes**: Adds `clm resolve-topic` and `clm search-slides` CLI commands. Extends `clm outline` with `--format json`.

**Files created**:
- `src/clm/cli/commands/resolve_topic.py` — `resolve-topic` Click command
- `src/clm/slides/search.py` — `SearchResult`, `search_slides()`
- `src/clm/cli/commands/search_slides.py` — `search-slides` Click command
- `tests/slides/test_search.py`
- `tests/cli/test_resolve_topic.py`
- `tests/cli/test_search_slides.py`

**Files modified**:
- `src/clm/cli/main.py` — register new commands
- `src/clm/cli/commands/outline.py` — add `generate_outline_json()`, `--format` option
- `src/clm/notebooks/slide_parser.py` — add `slide_id` and `for_slide` to `CellMetadata` and `parse_cell_header()` (needed by later phases but backward-compatible, so do it now)
- `tests/notebooks/test_slide_parser.py` — tests for new metadata fields

**Acceptance criteria**:
- `clm resolve-topic <id>` prints the path; `--course-spec` scopes to a course; glob patterns work
- `clm search-slides <query>` returns fuzzy-matched topics with scores
- `clm outline <spec> --format json` produces structured JSON matching the spec document schema
- `parse_cell_header` extracts `slide_id` and `for_slide` without breaking existing callers

#### Phase 1C: MCP Server Infrastructure [DONE]

**What it accomplishes**: Sets up the MCP server with stdio transport. Registers `resolve_topic`, `course_outline`, and `search_slides` as MCP tools. Adds `clm mcp` CLI command.

**Files created**:
- `src/clm/mcp/__init__.py`
- `src/clm/mcp/server.py` — `create_server()`, `run_server()`
- `src/clm/mcp/tools.py` — tool handler functions (thin wrappers)
- `src/clm/cli/commands/mcp_server.py` — `clm mcp` Click command
- `tests/mcp/__init__.py`
- `tests/mcp/test_tools.py` — unit tests for tool handlers

**Files modified**:
- `src/clm/cli/main.py` — register `mcp` command (gated import)
- `pyproject.toml` — add `[mcp]` and `[slides]` optional extras, add `mcp` and `rapidfuzz` dependencies

**Acceptance criteria**:
- `clm mcp` starts an MCP server on stdio; responds to `tools/list`
- `resolve_topic`, `course_outline`, `search_slides` tools work via MCP protocol
- Data directory resolution: `--data-dir` > `CLM_DATA_DIR` env var > cwd
- In-memory caching for topic map and course objects (keyed by mtime)

---

### Phase 2: Tag Changes + Validation [PARTIAL — 2A+2B done, 2C+2D TODO]

**Goal**: Introduce `completed` and `workshop` tags, build validation and normalization engines, add spec validation. After this phase, the tag system is unambiguous and errors are caught.

#### Phase 2A: Tag System Changes [DONE]

**What it accomplishes**: Adds `completed` and `workshop` as recognized tags. Updates the build pipeline so `completed` is processed identically to `alt` (deleted in code-along, kept in completed/speaker).

**Files modified**:
- `src/clm/slides/tags.py` — already has the new tags from Phase 1A (verify)
- `src/clm/workers/notebook/output_spec.py` — add `"completed"` to `CodeAlongOutput.tags_to_delete_cell`
- `src/clm/workers/notebook/utils/jupyter_utils.py` — verify imports from `clm.slides.tags`
- Existing tests — verify existing tag behavior is preserved; add tests for `completed` and `workshop`

**Acceptance criteria**:
- Notebooks with `completed` tag: cell deleted in code-along, kept in completed/speaker (same as `alt`)
- `workshop` tag is recognized (no warnings) but has no effect on output processing
- All existing notebook processing tests pass

#### Phase 2B: Spec Validation [DONE]

**What it accomplishes**: `validate_spec` tool checks course spec XML files for unresolved topics, ambiguous topics, duplicates, and missing dir-groups. Includes near-match suggestions.

**Files created**:
- `src/clm/slides/spec_validator.py` — `SpecFinding`, `SpecValidationResult`, `validate_spec()`
- `src/clm/cli/commands/validate_spec.py` — `validate-spec` Click command
- `tests/slides/test_spec_validator.py`

**Files modified**:
- `src/clm/cli/main.py` — register command
- `src/clm/mcp/tools.py` — add `validate_spec` MCP tool

**Acceptance criteria**:
- Detects unresolved topics with near-match suggestions (using `difflib.get_close_matches()`)
- Detects ambiguous topics (same ID in multiple modules)
- Detects duplicate topic references within a spec
- Detects missing dir-group paths
- CLI and MCP produce identical output

#### Phase 2C: Slide Validation [TODO]

**What it accomplishes**: `validate_slides` tool with deterministic checks (format, pairing, tags) and structured `review_material` extraction for LLM-dependent checks. Includes `--quick` mode.

**Files created**:
- `src/clm/slides/validator.py` — `Finding`, `ReviewMaterial`, `ValidationResult`, `validate_file()`, `validate_quick()`, `validate_directory()`, `validate_course()`
- `src/clm/cli/commands/validate_slides.py` — `validate-slides` Click command
- `tests/slides/test_validator.py`
- `tests/slides/fixtures/` — test slide files (well-formed, errors, voiceover)

**Files modified**:
- `src/clm/cli/main.py` — register command
- `src/clm/mcp/tools.py` — add `validate_slides` MCP tool

**Acceptance criteria**:
- Format checks: catches invalid cell headers, malformed tags
- Tag checks: catches unclosed `start`/`completed` pairs, `start`/`completed` inside workshops
- Pairing checks: catches DE/EN count mismatches, tag mismatches between pairs
- Review material extraction: identifies print() calls, missing voiceover, unpracticed concepts
- Quick mode: completes in <2s for a single file, checks only syntax/tags/unclosed pairs
- JSON output format matches the spec document

#### Phase 2D: Slide Normalization [TODO]

**What it accomplishes**: `normalize_slides` tool with tag migration (`alt`→`completed`), workshop tag insertion, and interleaving normalization (three-tier strategy).

**Files created**:
- `src/clm/slides/normalizer.py` — `Change`, `ReviewItem`, `NormalizationResult`, `normalize_file()`, `normalize_directory()`, `normalize_course()`
- `src/clm/cli/commands/normalize_slides.py` — `normalize-slides` Click command
- `tests/slides/test_normalizer.py`

**Files modified**:
- `src/clm/cli/main.py` — register command
- `src/clm/mcp/tools.py` — add `normalize_slides` MCP tool

**Acceptance criteria**:
- Tag migration: `alt`→`completed` only when immediately following `start`; standalone `alt` unchanged
- Workshop tags: adds `workshop` to `## Workshop:` / `## Mini-Workshop:` heading cells
- Interleaving: Tier 1 structural pre-check, Tier 2 positional pairing with similarity checks, Tier 3 report for uncertain pairs
- `--dry-run` previews all changes without modifying files
- Exit codes: 0 (clean), 1 (partial — review items remain), 2 (blocked)

---

### Phase 3: Bilingual Editing Support [TODO]

**Goal**: Tools for working with bilingual slide files. After this phase, editing one language and keeping the other in sync is streamlined.

#### Phase 3A: Language View [TODO]

**What it accomplishes**: `get_language_view` extracts a single-language view with line-number annotations mapping back to the original file.

**Files created**:
- `src/clm/slides/language_tools.py` — `get_language_view()`
- `src/clm/cli/commands/language_view.py` — `language-view` Click command
- `tests/slides/test_language_tools.py`

**Files modified**:
- `src/clm/cli/main.py` — register command
- `src/clm/mcp/tools.py` — add `get_language_view` MCP tool

**Acceptance criteria**:
- Filters cells by language; includes language-neutral cells (no `lang` attribute)
- `[original line N]` annotations before each cell
- Voiceover/notes included or excluded based on options

#### Phase 3B: Suggest Sync [TODO]

**What it accomplishes**: `suggest_sync` compares current file against git HEAD, identifies asymmetric language changes, and suggests updates.

**Files added to**:
- `src/clm/slides/language_tools.py` — `SyncSuggestion`, `SyncResult`, `suggest_sync()`
- `src/clm/cli/commands/suggest_sync.py` — `suggest-sync` Click command
- `tests/slides/test_language_tools.py` — additional tests

**Files modified**:
- `src/clm/cli/main.py` — register command
- `src/clm/mcp/tools.py` — add `suggest_sync` MCP tool

**Acceptance criteria**:
- Detects modified, added, and deleted cells in one language
- Suggests corresponding updates for the other language
- Uses `slide_id` for pairing when available; falls back to positional
- Auto-detects source language if not specified (picks the language with more changes)
- Handles untracked (new) files gracefully

---

### Phase 4: Slide IDs + Voiceover Separation [TODO]

**Goal**: Auto-generate `slide_id` metadata, extract voiceover to companion files, integrate companion files into the build pipeline. After this phase, voiceover lives permanently in companion files and slide files are 2-3x shorter.

#### Phase 4A: Slide ID Auto-Generation [TODO]

**What it accomplishes**: `slide_ids` operation in `normalize_slides` auto-generates `slide_id` metadata for cells that lack it.

**Files modified**:
- `src/clm/slides/normalizer.py` — add `_add_slide_ids()` operation
- `tests/slides/test_normalizer.py` — tests for ID generation rules

**Acceptance criteria**:
- Markdown with heading → heading text, lowercased, hyphenated
- Code with definitions → function/class name
- Fallback → file-stem-cell-N
- Paired DE/EN cells get the same ID (German heading as source)
- Collision resolution with `-2`, `-3` suffixes
- Cells that already have `slide_id` are unchanged

#### Phase 4B: Voiceover Extract/Inline [TODO]

**What it accomplishes**: `extract_voiceover` moves voiceover cells to companion files linked by `slide_id`. `inline_voiceover` reverses the operation.

**Files created**:
- `src/clm/slides/voiceover_tools.py` — `ExtractionResult`, `InlineResult`, `extract_voiceover()`, `inline_voiceover()`
- `src/clm/cli/commands/voiceover_tools.py` — `extract-voiceover`, `inline-voiceover` Click commands
- `tests/slides/test_voiceover_tools.py`

**Files modified**:
- `src/clm/cli/main.py` — register commands
- `src/clm/mcp/tools.py` — add MCP tools

**Acceptance criteria**:
- Companion file: `slides_X.py` → `voiceover_X.py` in same directory
- Voiceover cells linked via `for_slide` → `slide_id` matching
- Content cells without `slide_id` get auto-generated IDs before extraction
- Slide file after extraction has no voiceover cells and no marker comments
- `inline_voiceover` reconstructs the original file (round-trip)
- `--dry-run` on both commands

#### Phase 4C: Build Pipeline Integration [TODO]

**What it accomplishes**: The build pipeline automatically merges companion voiceover files during processing. Strips `slide_id` and `for_slide` from all output.

**Files modified**:
- `src/clm/workers/notebook/notebook_processor.py` — strip `slide_id`/`for_slide` from output cell metadata; merge companion voiceover cells for speaker output
- `src/clm/core/course_files/notebook_file.py` — detect companion voiceover files during file map construction
- Tests for build integration

**Acceptance criteria**:
- When `voiceover_X.py` exists alongside `slides_X.py`, voiceover cells are merged in-memory for speaker output
- `slide_id` and `for_slide` never appear in generated HTML, notebooks, or code
- Build works correctly with and without companion files
- Unmatched `for_slide` references produce build warnings

#### Phase 4D: Update `suggest_sync` for `slide_id` Pairing [TODO]

**What it accomplishes**: `suggest_sync` uses `slide_id` for precise pairing when cells have IDs, replacing the positional fallback.

**Files modified**:
- `src/clm/slides/language_tools.py` — enhance `suggest_sync()` to prefer `slide_id` pairing
- `tests/slides/test_language_tools.py` — tests for `slide_id`-based pairing

**Acceptance criteria**:
- Files with `slide_id`: uses `slide_id` for DE/EN pairing (reports `pairing_method: "slide_id"`)
- Files without `slide_id`: continues to use positional pairing
- Mixed files: uses `slide_id` where available, positional for remainder

---

## 4. Current Status

**Phase 1 + 2A + 2B complete** (2026-04-06).

**Commits:**
- `abe36d6` — Phase 1A: topic resolver, slides.tags, slide_id parsing
- `b93fd0a` — Phase 1B: resolve-topic, search-slides, outline --format json
- `ad56998` — Phase 1C: MCP server infrastructure
- *(pending)* — Phase 2A + 2B: tag system verification + spec validation

**What was built (Phase 1A+1B):**
- `src/clm/core/topic_resolver.py` — standalone topic resolution with `build_topic_map()`, `resolve_topic()`, `find_slide_files()`
- `src/clm/slides/tags.py` — canonical tag constants (adds `completed`, `workshop`)
- `src/clm/slides/search.py` — fuzzy search with rapidfuzz (substring fallback)
- `src/clm/cli/commands/resolve_topic.py` — `clm resolve-topic` command
- `src/clm/cli/commands/search_slides.py` — `clm search-slides` command
- `src/clm/cli/commands/outline.py` — extended with `--format json` and `generate_outline_json()`
- `src/clm/notebooks/slide_parser.py` — `slide_id` and `for_slide` in `CellMetadata`
- `src/clm/workers/notebook/output_spec.py` — `completed` added to code-along delete set
- `src/clm/workers/notebook/utils/jupyter_utils.py` — imports from `clm.slides.tags`, frozenset fix
- `src/clm/core/course.py` — refactored `_build_topic_map()` to delegate to topic_resolver

**What was built (Phase 1C):**
- `src/clm/mcp/__init__.py` — MCP package
- `src/clm/mcp/server.py` — `create_server()`, `run_server()` using FastMCP with stdio transport
- `src/clm/mcp/tools.py` — async tool handlers with result serialization and mtime-based course caching
- `src/clm/cli/commands/mcp_server.py` — `clm mcp` Click command (`--data-dir`, `--log-level`)
- `pyproject.toml` — `[slides]` extra (rapidfuzz), `[mcp]` extra (mcp SDK + slides), both in `[all]`
- `tests/mcp/test_tools.py` — 16 tests covering all 3 tools, caching, data dir resolution

**What was built (Phase 2A — tag verification):**
- Tag system already implemented in Phase 1A. Added 15 new tests verifying:
  - `completed` tag: deleted in code-along, kept in completed/speaker (6 tests in `test_output_spec.py`)
  - `workshop` tag: recognized without warnings, kept in all outputs (4+4 tests across `test_output_spec.py` and `test_jupyter_utils.py`)

**What was built (Phase 2B — spec validation):**
- `src/clm/slides/spec_validator.py` — `SpecFinding`, `SpecValidationResult`, `validate_spec()`
- `src/clm/cli/commands/validate_spec.py` — `clm validate-spec` Click command
- `src/clm/mcp/server.py` — `validate_spec` MCP tool registered
- `src/clm/mcp/tools.py` — `handle_validate_spec()` async handler
- `tests/slides/test_spec_validator.py` — 11 tests (all check types + combinations)
- `tests/cli/test_validate_spec.py` — 5 tests (clean/error/json/inferred-dir)
- `tests/mcp/test_tools.py` — 3 new tests (clean/unresolved/relative path)

**Blockers**: None.

---

## 5. Next Steps

**Continue with Phase 2C: Slide Validation.**

### Prerequisites
- Run `uv run pytest -m "not docker"` to confirm green baseline
- Phase 1 + 2A + 2B complete — all navigation, tag system, and spec validation tools work

---

## 6. Key Files & Architecture

### Existing files that are central to this feature

| File | Role |
|------|------|
| `src/clm/core/course.py` | `Course._build_topic_map()`, `_iterate_topic_paths()` — logic to extract |
| `src/clm/core/course_spec.py` | `CourseSpec.from_file()`, `SectionSpec`, `TopicSpec` — XML parsing |
| `src/clm/core/course_paths.py` | `resolve_course_paths()` — spec-file to course-root resolution |
| `src/clm/core/topic.py` | `Topic`, `DirectoryTopic`, `FileTopic` — topic representations |
| `src/clm/core/course_files/notebook_file.py` | `NotebookFile` — title extraction, file type |
| `src/clm/core/utils/notebook_utils.py` | `find_notebook_titles()`, `TITLE_REGEX` |
| `src/clm/core/utils/text_utils.py` | `Text(de, en)` — bilingual text type |
| `src/clm/infrastructure/utils/path_utils.py` | `simplify_ordered_name()`, `is_slides_file()`, `is_ignored_dir_for_course()` |
| `src/clm/notebooks/slide_parser.py` | `CellMetadata`, `Cell`, `SlideGroup`, `parse_cells()`, `parse_cell_header()`, `group_slides()` |
| `src/clm/notebooks/slide_writer.py` | `update_notes()`, `write_notes()`, `format_notes_cell()` |
| `src/clm/workers/notebook/utils/jupyter_utils.py` | Tag constants — to be centralized |
| `src/clm/workers/notebook/output_spec.py` | `CodeAlongOutput`, `CompletedOutput`, `SpeakerOutput` — tag-based cell filtering |
| `src/clm/cli/commands/outline.py` | `generate_outline()` — to extend with JSON |
| `src/clm/cli/main.py` | Command registration |

### Files to be created (full list across all phases)

| File | Phase | Role |
|------|-------|------|
| `src/clm/core/topic_resolver.py` | 1A | Standalone topic resolution |
| `src/clm/slides/__init__.py` | 1A | Package init |
| `src/clm/slides/tags.py` | 1A | Shared tag constants |
| `src/clm/slides/search.py` | 1B | Fuzzy search |
| `src/clm/slides/spec_validator.py` | 2B | Course spec validation |
| `src/clm/slides/validator.py` | 2C | Slide file validation |
| `src/clm/slides/normalizer.py` | 2D | Normalization/migration |
| `src/clm/slides/language_tools.py` | 3A | Language view + sync |
| `src/clm/slides/voiceover_tools.py` | 4B | Voiceover extract/inline |
| `src/clm/mcp/__init__.py` | 1C | Package init |
| `src/clm/mcp/server.py` | 1C | MCP server setup |
| `src/clm/mcp/tools.py` | 1C | Tool handler functions |
| `src/clm/cli/commands/resolve_topic.py` | 1B | CLI command |
| `src/clm/cli/commands/search_slides.py` | 1B | CLI command |
| `src/clm/cli/commands/mcp_server.py` | 1C | CLI command |
| `src/clm/cli/commands/validate_spec.py` | 2B | CLI command |
| `src/clm/cli/commands/validate_slides.py` | 2C | CLI command |
| `src/clm/cli/commands/normalize_slides.py` | 2D | CLI command |
| `src/clm/cli/commands/language_view.py` | 3A | CLI command |
| `src/clm/cli/commands/suggest_sync.py` | 3B | CLI command |
| `src/clm/cli/commands/voiceover_tools.py` | 4B | CLI command |

### Architecture diagram

```
                ┌──────────────────┐
                │   clm.mcp        │  MCP server (stdio transport)
                │   server.py      │  thin wrappers → library calls
                │   tools.py       │
                └────────┬─────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  clm.slides  │ │ clm.core     │ │  clm.cli     │
│  validator   │ │ topic_       │ │  commands/   │
│  normalizer  │ │ resolver     │ │  (wrappers)  │
│  search      │ │              │ │              │
│  lang_tools  │ └──────┬───────┘ └──────────────┘
│  vo_tools    │        │
│  spec_valid  │        ▼
│  tags        │ ┌──────────────┐
└──────┬───────┘ │ clm.core     │
       │         │ course.py    │
       ▼         │ course_spec  │
┌──────────────┐ │ topic.py     │
│ clm.notebooks│ └──────────────┘
│ slide_parser │
│ slide_writer │
│ polish       │
└──────────────┘
```

### Patterns and conventions

- **Result dataclasses**: All library functions return `@dataclass` result objects (`ValidationResult`, `NormalizationResult`, `SyncResult`, etc.), not raw dicts. The CLI/MCP layer converts to JSON.
- **Error reporting**: Expected conditions (topic not found, validation errors) are returned as structured data. Unexpected errors (I/O, malformed files) raise exceptions.
- **CLI registration**: Commands in `src/clm/cli/commands/<name>.py`, registered via `cli.add_command()` in `main.py`. Gated imports for optional dependencies.
- **Test fixtures**: Use `tmp_path` pytest fixture for filesystem tests. Create minimal fake slide files with `# %%` cell headers.
- **Naming**: CLI commands use kebab-case (`resolve-topic`), Python modules use snake_case (`topic_resolver`), MCP tools use snake_case (`resolve_topic`).

---

## 7. Testing Approach

### Strategy

- **Unit tests**: Each library module in `clm.slides.*` and `clm.core.topic_resolver` gets its own test file. Test against in-memory strings and temporary directories.
- **CLI tests**: Use Click's `CliRunner` to test command invocation, argument parsing, and output format.
- **MCP tests**: Test tool handlers as library function calls (not via MCP protocol). Protocol-level testing is optional/manual.
- **Integration tests**: Mark with `@pytest.mark.integration`. Use real slide files from a fixture directory. These tests verify cross-module behavior (e.g., validate → normalize → validate cycle).

### Test locations

```
tests/
├── core/
│   └── test_topic_resolver.py       # Phase 1A
├── slides/
│   ├── __init__.py
│   ├── test_tags.py                 # Phase 1A
│   ├── test_search.py              # Phase 1B
│   ├── test_spec_validator.py      # Phase 2B
│   ├── test_validator.py           # Phase 2C
│   ├── test_normalizer.py          # Phase 2D
│   ├── test_language_tools.py      # Phase 3A/3B
│   ├── test_voiceover_tools.py     # Phase 4B
│   └── fixtures/                   # Shared test slide files
│       ├── well_formed.py
│       ├── errors.py
│       ├── with_voiceover.py
│       └── mini_course_spec.xml
├── cli/
│   ├── test_resolve_topic.py       # Phase 1B
│   ├── test_search_slides.py       # Phase 1B
│   ├── test_validate_spec.py       # Phase 2B
│   ├── test_validate_slides.py     # Phase 2C
│   └── test_normalize_slides.py    # Phase 2D
└── mcp/
    └── test_tools.py               # Phase 1C
```

### Current state

- 2539 tests pass (full suite excluding docker)
- Feature tests: 58 from Phase 1A/1B, 16 MCP (Phase 1C), 15 tag verification (Phase 2A), 19 spec validation (Phase 2B) = 108 new tests
- Existing test coverage for `slide_parser.py` in `tests/notebooks/test_slide_parser.py` (307 lines)

### How to run

```bash
# All tests (non-docker)
uv run pytest -m "not docker"

# Just the new tests (once created)
uv run pytest tests/slides/ tests/core/test_topic_resolver.py tests/mcp/

# Quick validation during development
uv run pytest tests/slides/test_tags.py -v
```

---

## 8. Session Notes

### Codebase observations

- The `Course.from_spec()` factory is expensive — it builds file maps, collects images, etc. For navigation tools (resolve, outline, search), we need lightweight alternatives that don't load all files. The topic resolver extraction solves this.
- `simplify_ordered_name()` is simple but fragile: `"_".join(parts[2:])`. It assumes at least two underscore-separated prefix parts. Edge cases: directories named `"foo"` (no underscores) or `"topic_"` (empty after prefix). The resolver must handle these gracefully.
- The slide parser's `CellMetadata` uses a plain Python `@dataclass` (not attrs or Pydantic). The new `slide_id` and `for_slide` fields should follow the same style.
- Tag constants in `jupyter_utils.py` are plain sets (`{...}`), not frozen. The new `clm.slides.tags` module should use `frozenset` for immutability.
- The `[voiceover]` extra already depends on `rapidfuzz`. For the `[slides]` and `[mcp]` extras, we add `rapidfuzz` independently (no cross-extra dependency). The `search_slides` function should have a try/except fallback for when rapidfuzz is not installed.

### Phase 1C observations

- The `mcp` PyPI package (official MCP Python SDK) installed as v1.26.0 due to the project's `exclude-newer = "14 days"` supply-chain safety gate. The `FastMCP` decorator API works identically across 1.x versions. Dependency floor set to `mcp>=1.0.0`.
- The `mcp` SDK pulls in `cryptography`, `pyjwt`, and `sse-starlette` as transitive deps. No conflicts with CLM's existing dependencies.
- Course-object caching (keyed by spec file mtime) was implemented; topic-map caching was deferred since `build_topic_map()` is fast enough (~ms for filesystem scan).
- Never write to stdout in MCP stdio mode — it corrupts JSON-RPC. All logging goes to stderr.

### PythonCourses-side work (NOT in CLM)

The spec document (Part 2) describes changes to the PythonCourses repository:
- `.mcp.json` configuration
- PostToolUse validation hook
- `/edit-slides` slash command
- Tag migration execution
- Documentation updates (CLAUDE.md, slide-authoring.md)

These are **out of scope** for this handover. They should be done in the PythonCourses repo after the corresponding CLM phases are complete.
