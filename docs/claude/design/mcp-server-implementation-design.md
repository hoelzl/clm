# CLM MCP Server and Slide Tooling — Implementation Design

This document specifies how CLM implements the tools described in
`mcp-server-and-slide-tooling.md`. It focuses on code reuse, module
extraction, internal architecture, and integration with the existing
codebase.

**Reference**: `docs/claude/design/mcp-server-and-slide-tooling.md`
(the "spec document") defines the tool schemas, output formats, and
user-facing behavior. This document covers the *how*.

---

## 1. Reuse Analysis

The CLM codebase already contains significant infrastructure that the
new tools can build on. This section maps existing code to new
requirements.

### 1.1 Topic Resolution

**Existing code** (`core/course.py:447-515`):
- `Course._build_topic_map()` scans `slides/` and builds
  `dict[topic_id, Path]` via `simplify_ordered_name()`
- `Course._iterate_topic_paths()` yields `(topic_id, topic_path)` tuples
- Duplicate detection with warning logging

**Gap**: These are private methods on `Course`. The `resolve_topic` tool
needs a standalone function that works without building a full `Course`
object. It also needs glob support and module-name extraction.

**Action**: Extract topic resolution into a new module
`clm.core.topic_resolver` (see §2.1).

### 1.2 Course Outline

**Existing code** (`cli/commands/outline.py:18-47`):
- `generate_outline(course, language)` returns Markdown text
- Iterates `course.sections` → `section.notebooks` → `notebook.title`
- `Course.from_spec()` builds the full section/topic/file hierarchy

**Gap**: No JSON output mode. The Markdown generator discards structural
data (topic IDs, paths, directory info) that the JSON format needs.

**Action**: Add `generate_outline_json()` alongside the existing function.
Add `--format json` flag to the CLI command (see §4.2).

### 1.3 Slide Parsing

**Existing code** (`notebooks/slide_parser.py`):
- `CellMetadata` dataclass — cell type, lang, tags, raw_header
- `Cell` dataclass — line_number, header, content, metadata
- `parse_cell_header(header)` — regex-based header parsing
- `parse_cells(text)` — splits percent-format file into cells
- `group_slides(cells, lang)` → `list[SlideGroup]`

**Gap**: No `slide_id` or `for_slide` parsing. `parse_cell_header` only
extracts `lang` and `tags`. No validation of tag values.

**Action**: Extend `CellMetadata` with `slide_id` and `for_slide` fields.
Extend `parse_cell_header` to extract them. These changes are
backward-compatible — existing code that doesn't use these fields is
unaffected.

### 1.4 Tag System

**Existing code** (`workers/notebook/utils/jupyter_utils.py:55-64`):
```python
_SLIDE_TAGS = {"slide", "subslide", "notes", "voiceover"}
_PRIVATE_TAGS = {"notes", "voiceover", "private"}
_EXPECTED_GENERIC_TAGS = _SLIDE_TAGS | _PRIVATE_TAGS | {"alt", "del"}
_EXPECTED_CODE_TAGS = {"keep", "start"} | _EXPECTED_GENERIC_TAGS
_EXPECTED_MARKDOWN_TAGS = {"notes", "voiceover", "answer", "nodataurl"} | _EXPECTED_GENERIC_TAGS
```

Tag-based cell filtering in `output_spec.py`:
- `CodeAlongOutput.tags_to_delete_cell = {"alt", "del", "notes", "voiceover"}`
- `CompletedOutput.tags_to_delete_cell = {"del", "notes", "voiceover", "start"}`
- `SpeakerOutput.tags_to_delete_cell = {"del", "start"}`

**Gap**: No `completed` or `workshop` tag. The `alt` tag currently serves
double duty (solution variant after `start`, and standalone alternative
content).

**Action**: Add `completed` and `workshop` to tag constants. Process
`completed` identically to `alt` (deleted in code-along, kept in
completed/speaker output). `workshop` is metadata-only — no effect on
output processing.

### 1.5 Fuzzy Matching

**Existing code** (`voiceover/matcher.py`):
- Uses `rapidfuzz.fuzz.token_set_ratio()` for OCR-to-slide matching
- `match_frame_to_slides(ocr_text, slides)` returns scored matches

**Gap**: `rapidfuzz` is gated behind the `[voiceover]` extra. The
`search_slides` tool needs fuzzy matching as a core or lightly-gated
feature.

**Action**: Add `rapidfuzz` to a new `[mcp]` optional extra. The
`search_slides` tool falls back to substring matching if rapidfuzz is
not installed. The MCP server requires `[mcp]` extra.

### 1.6 Notebook Title Extraction

**Existing code** (`core/utils/notebook_utils.py:8-22`):
- `TITLE_REGEX` extracts bilingual titles from `{{ header("DE", "EN") }}`
- `find_notebook_titles(text, default)` → `Text(de=..., en=...)`

**Gap**: Works on individual files. For `search_slides` we need titles for
all slide files across the repository, indexed for search.

**Action**: Build a title index as part of the topic resolver's scan.

### 1.7 Git Integration

**Existing code**: `git_ops.py` CLI group for commit/push/sync. The
`suggest_sync` tool needs `git diff HEAD -- file` to detect changes.

**Gap**: No programmatic git diff API. The existing CLI wraps
`run_subprocess`.

**Action**: Use `subprocess` directly for `git diff` in the `suggest_sync`
tool. No need for a shared abstraction — this is a single call site.

---

## 2. Module Extraction and New Packages

### 2.1 New Module: `clm.core.topic_resolver`

Extracts and generalizes topic-resolution logic from
`Course._build_topic_map()` and `Course._iterate_topic_paths()`.

```python
# src/clm/core/topic_resolver.py

@dataclass
class TopicMatch:
    topic_id: str
    path: Path                  # absolute path to topic dir or file
    path_type: str              # "directory" or "file"
    module: str                 # module directory name
    slide_files: list[Path]     # all slides_*.py files within

@dataclass
class ResolutionResult:
    topic_id: str               # the query
    match: TopicMatch | None    # primary match (None if ambiguous)
    ambiguous: bool
    alternatives: list[TopicMatch]
    glob: bool                  # True if query contained wildcards

def build_topic_map(slides_dir: Path) -> dict[str, list[TopicMatch]]:
    """Scan slides/ and return topic_id -> list of TopicMatch.

    Unlike Course._build_topic_map which picks the first occurrence,
    this returns ALL occurrences so callers can detect ambiguity.
    """

def resolve_topic(
    topic_id: str,
    slides_dir: Path,
    *,
    course_spec: CourseSpec | None = None,
) -> ResolutionResult:
    """Resolve a topic ID (or glob pattern) to filesystem path(s).

    Matching semantics: exact suffix match of the part after
    topic_NNN_ in directory names. Glob wildcards (* and ?)
    activate multi-match mode.

    When course_spec is provided, only topics referenced by the
    course are searched.
    """

def find_slide_files(topic_path: Path) -> list[Path]:
    """Return all slides_*.py (and topic_*.py, project_*.py) files
    within a topic directory or return [topic_path] for file topics.
    """
```

**Refactoring `Course`**: After extraction, `Course._build_topic_map()`
delegates to `build_topic_map()`, keeping just the first-occurrence
logic and error tracking:

```python
def _build_topic_map(self, rebuild=False):
    if self._topic_path_map and not rebuild:
        return
    self._topic_path_map.clear()
    slides_dir = self.course_root / "slides"
    full_map = build_topic_map(slides_dir)
    for topic_id, matches in full_map.items():
        if len(matches) > 1:
            self.loading_warnings.append(...)
        self._topic_path_map[topic_id] = matches[0].path
```

### 2.2 New Module: `clm.slides.validator`

Validation engine for slide files. Consumes `Cell` objects from
`notebooks.slide_parser` and produces structured findings.

```python
# src/clm/slides/validator.py

@dataclass
class Finding:
    severity: str       # "error", "warning", "info"
    category: str       # "format", "pairing", "tags"
    file: str
    line: int
    message: str
    suggestion: str = ""

@dataclass
class ReviewMaterial:
    """Extracted data for LLM-dependent checks."""
    code_quality: dict | None = None
    voiceover_gaps: list[dict] | None = None
    completeness: dict | None = None

@dataclass
class ValidationResult:
    files_checked: int
    findings: list[Finding]
    review_material: ReviewMaterial | None = None

    @property
    def summary(self) -> str: ...

def validate_file(
    path: Path,
    checks: list[str] | None = None,
) -> ValidationResult:
    """Validate a single slide file."""

def validate_quick(path: Path) -> ValidationResult:
    """Fast syntax-only validation for PostToolUse hook.

    Checks: cell header syntax, valid tag names,
    unclosed start/completed pairs.
    """

def validate_directory(path: Path, ...) -> ValidationResult:
    """Validate all slide files in a topic directory."""

def validate_course(
    course_spec_path: Path,
    slides_dir: Path,
    checks: list[str] | None = None,
) -> ValidationResult:
    """Validate all slides referenced by a course spec."""
```

**Check implementations** (internal functions):

- `_check_format(cells)` — cell header syntax, valid `# %%` patterns
- `_check_tags(cells)` — valid tag names against known set, unclosed
  `start`/`completed` pairs, `start`/`completed` inside workshop sections
- `_check_pairing(cells)` — DE/EN cell count matching, position
  alignment, tag consistency between paired cells
- `_extract_code_quality(cells)` → `ReviewMaterial.code_quality`
- `_extract_voiceover_gaps(cells)` → `ReviewMaterial.voiceover_gaps`
- `_extract_completeness(cells)` → `ReviewMaterial.completeness`

**Tag constants** (shared between validator and worker):

Move the tag constant definitions from `jupyter_utils.py` into a shared
location that both the worker and the validator can import:

```python
# src/clm/slides/tags.py

SLIDE_TAGS = frozenset({"slide", "subslide"})
NARRATIVE_TAGS = frozenset({"notes", "voiceover"})
STRUCTURAL_TAGS = frozenset({"workshop"})
VISIBILITY_TAGS = frozenset({"private"})
CONTENT_TAGS = frozenset({"keep", "start", "completed", "alt", "answer"})
CONTROL_TAGS = frozenset({"del", "nodataurl"})

ALL_VALID_TAGS = (
    SLIDE_TAGS | NARRATIVE_TAGS | STRUCTURAL_TAGS
    | VISIBILITY_TAGS | CONTENT_TAGS | CONTROL_TAGS
)

# Per-cell-type valid tags
VALID_CODE_TAGS = ALL_VALID_TAGS - {"answer", "nodataurl", "workshop"}
VALID_MARKDOWN_TAGS = ALL_VALID_TAGS - {"keep", "start", "completed"}
```

Then `jupyter_utils.py` imports from `clm.slides.tags` instead of
defining its own constants. This avoids duplication and keeps the tag
definitions in one place.

### 2.3 New Module: `clm.slides.normalizer`

Normalization engine for mechanical fixes to slide files.

```python
# src/clm/slides/normalizer.py

@dataclass
class Change:
    file: str
    operation: str       # "tag_migration", "workshop_tags", etc.
    line: int | None
    lines: str | None    # e.g., "12-78" for range operations
    description: str

@dataclass
class ReviewItem:
    """Item that needs human/agent review before resolution."""
    issue: str           # "count_mismatch", "similarity_failure"
    # ... per the spec document

@dataclass
class NormalizationResult:
    files_modified: int
    changes: list[Change]
    needs_review: list[ReviewItem]

    @property
    def status(self) -> str:
        """'complete', 'partial', or 'blocked'."""

def normalize_file(
    path: Path,
    operations: list[str] | None = None,
    dry_run: bool = False,
) -> NormalizationResult:
    """Apply normalization operations to a single file."""

def normalize_directory(path: Path, ...) -> NormalizationResult:
    """Normalize all slide files in a directory."""

def normalize_course(
    course_spec_path: Path,
    slides_dir: Path,
    operations: list[str] | None = None,
    dry_run: bool = False,
) -> NormalizationResult:
    """Normalize all slides referenced by a course spec."""
```

**Operation implementations** (internal functions):

- `_migrate_tags(cells, text)` — `alt` → `completed` after `start`
- `_add_workshop_tags(cells, text)` — add `workshop` tag to
  `## Workshop:` / `## Mini-Workshop:` headings
- `_add_slide_ids(cells, text)` — auto-generate `slide_id` metadata
- `_normalize_interleaving(cells, text)` — three-tier reordering
  strategy (structural pre-check → positional pairing →
  report for review)

### 2.4 New Module: `clm.slides.language_tools`

Bilingual editing support tools.

```python
# src/clm/slides/language_tools.py

def get_language_view(
    path: Path,
    language: str,
    *,
    include_voiceover: bool = False,
    include_notes: bool = False,
) -> str:
    """Extract a single-language view of a slide file.

    Returns filtered file content with [original line N] annotations.
    Reuses parse_cells() from notebooks.slide_parser.
    """

@dataclass
class SyncSuggestion:
    type: str               # "modified", "added", "deleted"
    slide_id: str | None
    source_line: int | None
    target_line: int | None
    source_content: str
    target_content_current: str
    suggestion: str

@dataclass
class SyncResult:
    file: str
    source_language: str
    target_language: str
    pairing_method: str     # "slide_id" or "positional"
    suggestions: list[SyncSuggestion]
    unmodified_pairs: int
    sync_needed: bool

def suggest_sync(
    path: Path,
    *,
    source_language: str | None = None,
) -> SyncResult:
    """Compare current file against git HEAD and suggest sync updates.

    Uses slide_id for precise pairing when available, falls back
    to positional pairing.
    """
```

**Implementation notes**:
- `get_language_view` filters cells from `parse_cells()` by language,
  then reconstructs the file text with `[original line N]` annotations
- `suggest_sync` runs `git diff HEAD -- <file>` to get the previous
  version, parses both versions with `parse_cells()`, pairs cells by
  `slide_id` (or position), and identifies asymmetric changes

### 2.5 New Module: `clm.slides.voiceover_tools`

Voiceover extraction and inlining.

```python
# src/clm/slides/voiceover_tools.py

@dataclass
class ExtractionResult:
    source_file: str
    voiceover_file: str
    cells_extracted: int
    slide_ids_added: int
    source_lines_before: int
    source_lines_after: int
    dry_run: bool

def extract_voiceover(
    path: Path,
    *,
    dry_run: bool = False,
) -> ExtractionResult:
    """Move voiceover cells from slide file to companion file.

    Companion file: slides_X.py -> voiceover_X.py (same directory).
    Content cells without slide_id are assigned one automatically.
    """

@dataclass
class InlineResult:
    source_file: str
    voiceover_file: str
    cells_inlined: int
    unmatched: list[str]    # for_slide values with no match
    dry_run: bool

def inline_voiceover(
    path: Path,
    *,
    delete_companion: bool = False,
    dry_run: bool = False,
) -> InlineResult:
    """Merge voiceover from companion file back into slide file.

    Each voiceover cell's for_slide attribute is matched to a
    content cell's slide_id. Insertion follows the interleaving
    convention.
    """
```

### 2.6 New Module: `clm.slides.search`

Fuzzy search across topic names and slide titles.

```python
# src/clm/slides/search.py

@dataclass
class SearchResult:
    score: float
    topic_id: str
    directory: str
    slides: list[dict]      # {file, title_de, title_en}
    courses: list[str]       # which course specs reference this topic

def search_slides(
    query: str,
    slides_dir: Path,
    *,
    course_spec_path: Path | None = None,
    language: str | None = None,
    max_results: int = 10,
) -> list[SearchResult]:
    """Fuzzy search across topic names and slide file titles.

    Uses rapidfuzz if available, falls back to substring matching.
    Searches topic directory names, slide file names, and titles
    extracted from header macros.
    """
```

**Implementation**:
1. Build topic index via `build_topic_map()` from `topic_resolver`
2. For each topic, extract titles from slide files via
   `find_notebook_titles()` from `core/utils/notebook_utils.py`
3. Score query against topic_id, directory name, and titles using
   `rapidfuzz.fuzz.token_set_ratio()` (or substring containment
   as fallback)
4. If `course_spec` given, filter to topics referenced by that course
5. Sort by score descending, limit to `max_results`

### 2.7 New Module: `clm.slides.spec_validator`

Course spec validation, separate from slide validation.

```python
# src/clm/slides/spec_validator.py

@dataclass
class SpecFinding:
    severity: str       # "error", "warning", "info"
    type: str           # "unresolved_topic", "ambiguous_topic", etc.
    topic_id: str | None
    section: str | None
    message: str
    suggestion: str = ""
    matches: list[str] | None = None  # for ambiguous topics

@dataclass
class SpecValidationResult:
    course_spec: str
    topics_total: int
    findings: list[SpecFinding]

def validate_spec(
    course_spec_path: Path,
    slides_dir: Path,
) -> SpecValidationResult:
    """Validate a course spec XML file for consistency.

    Uses build_topic_map() for resolution checking and
    difflib.get_close_matches() for near-match suggestions.
    """
```

**Reuse**: Leverages `CourseSpec.from_file()` for XML parsing,
`CourseSpec.validate()` for basic structural validation, and
`build_topic_map()` from `topic_resolver` for filesystem checks.

### 2.8 New Package: `clm.mcp`

MCP server implementation using the `mcp` Python SDK.

```python
# src/clm/mcp/__init__.py
# src/clm/mcp/server.py     — MCP server setup and tool registration
# src/clm/mcp/tools.py      — Tool handler functions (thin wrappers)
```

```python
# src/clm/mcp/server.py

from mcp.server import Server
from mcp.server.stdio import stdio_server

def create_server(data_dir: Path) -> Server:
    """Create and configure the MCP server with all tools."""

async def run_server(data_dir: Path):
    """Run the MCP server on stdio transport."""
```

**Design principle**: The MCP tool handlers are thin wrappers that call
the library functions from `clm.slides.*` and `clm.core.topic_resolver`.
All business logic lives in the library modules — the MCP layer only
handles JSON serialization and MCP protocol framing.

Similarly, the CLI commands are thin wrappers around the same library
functions. This ensures CLI and MCP always produce identical results.

---

## 3. Extending Existing Modules

### 3.1 `CellMetadata` — Add `slide_id` and `for_slide`

In `src/clm/notebooks/slide_parser.py`:

```python
@dataclass
class CellMetadata:
    cell_type: str
    lang: str | None = None
    tags: list[str] = field(default_factory=list)
    is_j2: bool = False
    raw_header: str = ""
    slide_id: str | None = None      # NEW
    for_slide: str | None = None     # NEW
```

Extend `parse_cell_header`:

```python
def parse_cell_header(header: str) -> CellMetadata:
    # ... existing parsing ...

    # NEW: parse slide_id and for_slide
    slide_id_match = re.search(r'slide_id="([^"]*)"', header)
    slide_id = slide_id_match.group(1) if slide_id_match else None

    for_slide_match = re.search(r'for_slide="([^"]*)"', header)
    for_slide = for_slide_match.group(1) if for_slide_match else None

    return CellMetadata(
        cell_type=cell_type,
        lang=lang,
        tags=tags,
        is_j2=False,
        raw_header=header,
        slide_id=slide_id,
        for_slide=for_slide,
    )
```

### 3.2 Tag Constants — Add `completed` and `workshop`

In `workers/notebook/utils/jupyter_utils.py`, update to import from
`clm.slides.tags` (once that module exists):

```python
from clm.slides.tags import ALL_VALID_TAGS, VALID_CODE_TAGS, VALID_MARKDOWN_TAGS

# Replace local constants with imports
_EXPECTED_CODE_TAGS = VALID_CODE_TAGS
_EXPECTED_MARKDOWN_TAGS = VALID_MARKDOWN_TAGS
```

In `workers/notebook/output_spec.py`, update the tag sets:

```python
# CodeAlongOutput: completed behaves like alt
tags_to_delete_cell = {"alt", "completed", "del", "notes", "voiceover"}

# CompletedOutput: completed behaves like alt (kept)
tags_to_delete_cell = {"del", "notes", "voiceover", "start"}
# (no change needed — completed is already not in this set)

# SpeakerOutput: completed behaves like alt (kept)
tags_to_delete_cell = {"del", "start"}
# (no change needed)
```

### 3.3 Outline Command — Add JSON Format

Extend `cli/commands/outline.py`:

```python
def generate_outline_json(course: Course, language: str) -> dict:
    """Generate a structured JSON outline for a course."""
    sections = []
    for section in course.sections:
        topics = []
        for topic in section.topics:
            slides = []
            for f in topic.files:
                if isinstance(f, NotebookFile):
                    slides.append({
                        "file": f.path.name,
                        "title": f.title[language],
                    })
            topics.append({
                "topic_id": topic.id,
                "directory": str(topic.path),
                "slides": slides,
            })
        sections.append({
            "number": len(sections) + 1,
            "name": section.name[language],
            "topics": topics,
        })
    return {
        "course_name": course.name[language],
        "language": language,
        "sections": sections,
    }

# Add --format option to click command
@click.option("--format", "output_format",
    type=click.Choice(["markdown", "json"]), default="markdown")
```

### 3.4 Notebook Processing — Strip `slide_id` and `for_slide`

The build pipeline must strip `slide_id` and `for_slide` from output so
students never see these identifiers. In the notebook processor
(`workers/notebook/notebook_processor.py`), add a post-processing step
that removes these metadata fields from cell metadata before writing
output.

### 3.5 Build Pipeline — Companion Voiceover Merge

When processing a slide file, the build pipeline checks for a companion
`voiceover_*.py` file in the same directory. If found, voiceover cells
are merged in-memory (matched by `slide_id` + `lang`) before processing.
The merge happens transparently — neither file is modified on disk.

This logic belongs in the notebook worker's file loading step, not in
core. It is specific to the speaker output kind (voiceover cells are
only included in speaker output).

---

## 4. CLI Commands

All new CLI commands follow the existing pattern: Click commands in
`src/clm/cli/commands/`, registered in `main.py`.

### 4.1 `clm resolve-topic`

```bash
clm resolve-topic <topic_id> [--course-spec PATH]
```

Thin wrapper around `topic_resolver.resolve_topic()`. Prints path(s)
to stdout. JSON output for programmatic use.

### 4.2 `clm outline` (extended)

```bash
clm outline <spec-file> [--format json|markdown] [-L de|en]
```

Extends existing command with `--format json` option.

### 4.3 `clm search-slides`

```bash
clm search-slides <query> [--course-spec PATH] [-L de|en] [--max-results N]
```

Wrapper around `slides.search.search_slides()`.

### 4.4 `clm validate-slides`

```bash
clm validate-slides <path> [--checks LIST] [--llm-checks] [--quick]
```

Wrapper around `slides.validator.validate_file/directory/course()`.
`--quick` mode for PostToolUse hook.

### 4.5 `clm validate-spec`

```bash
clm validate-spec <spec-file>
```

Wrapper around `slides.spec_validator.validate_spec()`.

### 4.6 `clm normalize-slides`

```bash
clm normalize-slides <path> [--operations LIST] [--dry-run]
```

Wrapper around `slides.normalizer.normalize_file/directory/course()`.

### 4.7 `clm language-view`

```bash
clm language-view <file> <lang> [--include-voiceover] [--include-notes]
```

Wrapper around `slides.language_tools.get_language_view()`.

### 4.8 `clm suggest-sync`

```bash
clm suggest-sync <file> [--source-language de|en]
```

Wrapper around `slides.language_tools.suggest_sync()`.

### 4.9 `clm extract-voiceover`

```bash
clm extract-voiceover <path> [--dry-run]
```

Wrapper around `slides.voiceover_tools.extract_voiceover()`.

### 4.10 `clm inline-voiceover`

```bash
clm inline-voiceover <path> [--delete-companion] [--dry-run]
```

Wrapper around `slides.voiceover_tools.inline_voiceover()`.

### 4.11 `clm mcp`

```bash
clm mcp [--data-dir PATH] [--log-level LEVEL]
```

Starts the MCP server on stdio transport. Requires `[mcp]` extra.

---

## 5. MCP Server Architecture

### 5.1 Tool Registration

Each tool from the spec document (§1.2-1.11) is registered as an MCP
tool. The handler functions are thin async wrappers:

```python
@server.tool()
async def resolve_topic(topic_id: str, course_spec: str | None = None) -> str:
    slides_dir = data_dir / "slides"
    spec = CourseSpec.from_file(data_dir / course_spec) if course_spec else None
    result = topic_resolver.resolve_topic(topic_id, slides_dir, course_spec=spec)
    return json.dumps(asdict(result))
```

### 5.2 Data Directory Resolution

Priority order (matching spec document):
1. `--data-dir` argument to `clm mcp`
2. `CLM_DATA_DIR` environment variable
3. Current working directory (cwd)

The cwd approach works naturally with the MCP config in `.mcp.json`:
```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "clm", "mcp"],
      "cwd": "<path-to-pythoncourses>"
    }
  }
}
```

### 5.3 Performance

MCP tools should respond quickly. Expensive operations:
- Building the topic map — cache after first build (per-session)
- Parsing all slide files for titles — lazy, cache results
- Course loading — cache `Course` objects by spec file path

The MCP server maintains an in-memory cache of these results, keyed by
filesystem modification times for cache invalidation.

---

## 6. New Package: `clm.slides`

The `clm.slides` package collects all slide-authoring tools that are
not part of the core build pipeline or the voiceover video pipeline:

```
src/clm/slides/
├── __init__.py
├── tags.py              # Tag constants (shared with workers)
├── validator.py         # Slide validation engine
├── normalizer.py        # Normalization/migration operations
├── language_tools.py    # Language view and sync suggestions
├── voiceover_tools.py   # Extract/inline voiceover
├── search.py            # Fuzzy search across slides
└── spec_validator.py    # Course spec validation
```

The existing `clm.notebooks` package remains as-is (slide_parser,
slide_writer, polish). The new `clm.slides` package imports from
`clm.notebooks` — it is a higher-level consumer.

**Why a new package instead of extending `clm.notebooks`?**

`clm.notebooks` contains parsing and writing primitives. The new tools
are higher-level operations (validation, normalization, search) that
combine multiple primitives. Putting them together avoids circular
dependencies and keeps `clm.notebooks` focused on its current role.

---

## 7. Dependencies

### 7.1 New Optional Extra: `[mcp]`

```toml
[project.optional-dependencies]
mcp = [
    "mcp>=1.0.0",          # MCP Python SDK
    "rapidfuzz>=3.0.0",    # Fuzzy matching for search_slides
]
```

### 7.2 New Optional Extra: `[slides]`

For users who want CLI slide tools without the MCP server:

```toml
slides = [
    "rapidfuzz>=3.0.0",    # Fuzzy matching for search_slides
]
```

The MCP extra implies slides: `mcp = ["coding-academy-lecture-manager[slides]", "mcp>=1.0.0"]`

### 7.3 Core Dependencies (No Change)

The validation, normalization, and language tools use only the standard
library (+ existing dependencies like `click`, `pydantic`, `attrs`).
`rapidfuzz` is needed only for `search_slides`.

---

## 8. `slide_id` Metadata Lifecycle

### 8.1 Parsing

Extended `parse_cell_header()` extracts `slide_id` from cell headers
(§3.1). Backward-compatible — files without `slide_id` parse normally.

### 8.2 Auto-Generation

`normalize_slides --operations slide_ids` generates IDs for cells that
lack them, following the rules in the spec document Appendix C:
- Markdown with heading → heading text, lowercased, hyphenated
- Code with definitions → function/class name
- Fallback → file-stem-cell-N
- Paired DE/EN cells share the same ID (German heading as source)
- Collision resolution with `-2`, `-3` suffixes

### 8.3 Stripping from Output

The notebook processor removes `slide_id` and `for_slide` from cell
metadata before generating output (HTML, notebook, code). Students
never see these identifiers.

### 8.4 Use by Tools

- `suggest_sync` uses `slide_id` for precise DE/EN pairing
- `extract_voiceover` uses `slide_id` to link voiceover cells to content
- `validate_slides` uses `slide_id` to verify pairing consistency
- `get_language_view` annotates cells with their `slide_id`

---

## 9. Testing Strategy

### 9.1 Unit Tests

Each new module gets its own test file under `tests/slides/`:

```
tests/slides/
├── test_validator.py
├── test_normalizer.py
├── test_language_tools.py
├── test_voiceover_tools.py
├── test_search.py
├── test_spec_validator.py
└── test_tags.py
```

`tests/core/test_topic_resolver.py` for the extracted resolver.

`tests/mcp/test_tools.py` for MCP tool handlers (unit tests against
the library functions, not the MCP protocol).

### 9.2 Integration Tests

Integration tests use real slide files from a test fixture directory.
Mark with `@pytest.mark.integration`.

### 9.3 Test Fixtures

Create a small set of representative slide files for testing:
- A well-formed bilingual slide file
- A file with common errors (missing pair, bad tags)
- A file with voiceover cells
- A mini course spec with a few topics

---

## 10. Error Handling

All library functions return result dataclasses (not exceptions) for
expected conditions like "topic not found" or "validation error". These
map cleanly to both CLI error messages and MCP tool responses.

Unexpected errors (I/O failures, malformed files) raise exceptions that
the CLI/MCP layer catches and reports appropriately.

The pattern follows the existing `CourseSpec.validate()` → `list[str]`
approach: return structured error data, let the caller decide how to
present it.
