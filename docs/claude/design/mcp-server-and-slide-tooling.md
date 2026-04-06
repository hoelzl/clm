# CLM Tooling Specification for AI-Assisted Slide Authoring

## Motivation

Claude Code makes systematic errors when editing slide files in this
repository. Root causes identified:

1. **Tag overloading**: `alt` serves three distinct purposes (start/alt pairs,
   workshop discussion, multi-cell solution hiding)
2. **Inconsistent DE/EN interleaving**: no fixed cell-ordering convention
3. **Implicit workshop boundaries**: rules change at a `## Workshop:` heading
   with no structural marker
4. **Slow topic discovery**: 559 topic directories across 46 modules, suffix-
   matching is ambiguous for ~30-40 topics
5. **No real-time validation**: errors caught only after the fact
6. **Bilingual editing complexity**: editing one language while tracking the
   other is error-prone

This document specifies tools to address all six causes. The tools split into
two groups:

- **CLM-side tools**: MCP server + CLI commands, implemented in the CLM
  project (`coding-academy-lecture-manager`)
- **PythonCourses-side tools**: Claude Code hooks, slash commands, and one-time
  migrations, implemented in this repository

---

## Part 1: CLM MCP Server and CLI Commands

### 1.1 MCP Server Overview

The MCP server exposes CLM functionality to Claude Code via the Model Context
Protocol. It runs as a stdio-based MCP server process launched by Claude Code.

**Configuration** (in `.mcp.json` or user MCP settings):

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

The `clm mcp` subcommand starts the MCP server. It needs access to the course
data directory (the `slides/` tree and `course-specs/` directory).

**Data directory resolution**: The server needs to know where the slide files
and course specs live. Options (in priority order):

1. `--data-dir` argument to `clm mcp`
2. `CLM_DATA_DIR` environment variable
3. Current working directory

---

### 1.2 Tool: `resolve_topic`

Resolves a topic ID to its filesystem path(s).

**Why**: Claude Code currently needs 3-5 glob/grep rounds to find the right
directory for a topic. This tool makes it instant.

#### Topic Resolution Semantics

A topic can be either:
- A **directory** `slides/module_*/topic_NNN_<topic_id>/` containing one or
  more `slides_*.py` files
- A **single file** `slides/module_*/topic_NNN_<topic_id>.py` (legacy
  format)

Resolution is by **exact suffix match**: the topic ID must match exactly
the portion of the directory/file name after `topic_NNN_`. For example:

- `what_is_ml` matches `topic_120_what_is_ml` but **NOT**
  `topic_120_what_is_ml_azav`
- `what_is_ml_azav` matches `topic_120_what_is_ml_azav` but **NOT**
  `topic_120_what_is_ml`

Ambiguity arises only when the same topic ID exists in **different
modules**:
- `module_100_xxx/topic_200_decorators/` and
  `module_230_yyy/topic_100_decorators/` are ambiguous for topic ID
  `decorators`

To find **related variants** (e.g., check whether an AZAV version of a
topic exists), use glob syntax: `what_is_ml*` matches both `what_is_ml`
and `what_is_ml_azav`.

#### MCP Tool Schema

```
name: resolve_topic
description: >
  Resolve a topic ID to its filesystem path. Returns the matching topic
  directory (or file, for single-file topics) and all slide files.
  Matching is by exact suffix: "what_is_ml" matches topic_120_what_is_ml
  but NOT topic_120_what_is_ml_azav. Use glob wildcards (e.g.,
  "what_is_ml*") to find related variants. Optionally scope to a course
  spec to disambiguate topics that exist in multiple modules.

inputSchema:
  type: object
  properties:
    topic_id:
      type: string
      description: >
        Topic identifier, e.g., "what_is_ml_azav" or "linear_regression".
        Matched by exact suffix against topic directory/file names (the
        part after "topic_NNN_"). Supports glob wildcards: "what_is_ml*"
        finds both "what_is_ml" and "what_is_ml_azav".
    course_spec:
      type: string
      description: >
        Optional path to a course spec XML file. When provided, only
        topics referenced by this course are searched, eliminating
        cross-module ambiguity. Path relative to data directory.
  required: [topic_id]
```

**Output**: JSON object:

```json
{
  "topic_id": "what_is_ml_azav",
  "path": "slides/module_540_ml_basics/topic_120_what_is_ml_azav",
  "path_type": "directory",
  "slide_files": [
    "slides/module_540_ml_basics/topic_120_what_is_ml_azav/slides_what_is_ml.py"
  ],
  "ambiguous": false,
  "alternatives": []
}
```

Single-file topic:

```json
{
  "topic_id": "some_legacy_topic",
  "path": "slides/module_100_basics/topic_050_some_legacy_topic.py",
  "path_type": "file",
  "slide_files": [
    "slides/module_100_basics/topic_050_some_legacy_topic.py"
  ],
  "ambiguous": false,
  "alternatives": []
}
```

When ambiguous (same topic ID in different modules). Note: course specs
reference topics by ID only (not by module), so CLM cannot determine which
module was intended — it picks one arbitrarily. Ambiguous topics are always
an error that should be resolved by renaming one variant.

```json
{
  "topic_id": "decorators",
  "path": null,
  "path_type": null,
  "slide_files": [],
  "ambiguous": true,
  "referencing_courses": ["python-prog.xml", "python-best-practice.xml"],
  "alternatives": [
    {
      "path": "slides/module_100_xxx/topic_200_decorators",
      "path_type": "directory",
      "module": "module_100_xxx"
    },
    {
      "path": "slides/module_230_yyy/topic_100_decorators",
      "path_type": "directory",
      "module": "module_230_yyy"
    }
  ]
}
```

Glob result (multiple matches are expected, not an error):

```json
{
  "topic_id": "what_is_ml*",
  "path": null,
  "path_type": null,
  "slide_files": [],
  "ambiguous": false,
  "glob": true,
  "matches": [
    {
      "topic_id": "what_is_ml",
      "path": "slides/module_540_ml_basics/topic_120_what_is_ml",
      "path_type": "directory",
      "courses": ["ml-intro.xml"]
    },
    {
      "topic_id": "what_is_ml_azav",
      "path": "slides/module_540_ml_basics/topic_120_what_is_ml_azav",
      "path_type": "directory",
      "courses": ["machine-learning-azav.xml"]
    }
  ]
}
```

#### CLI Equivalent

```bash
clm resolve-topic what_is_ml_azav
clm resolve-topic decorators --course-spec course-specs/python-prog.xml
clm resolve-topic "what_is_ml*"    # glob: find all variants
```

Output: prints the path (or lists alternatives/matches).

---

### 1.3 Tool: `course_outline`

Returns the structured outline of a course.

**Why**: Claude Code needs to understand which topics exist, what order they
appear in, and what section they belong to. The existing `clm outline`
command produces Markdown; this tool returns structured data.

#### MCP Tool Schema

```
name: course_outline
description: >
  Returns the full structure of a course: sections, topics, and slide files
  with their titles. Use this to understand the course structure, find where
  a topic fits, or check what comes before/after a given topic.

inputSchema:
  type: object
  properties:
    course_spec:
      type: string
      description: >
        Path to the course spec XML file, relative to data directory.
        E.g., "course-specs/machine-learning-azav.xml".
    language:
      type: string
      enum: [de, en]
      default: en
      description: Language for section and slide titles.
    include_paths:
      type: boolean
      default: true
      description: Include filesystem paths for each topic and slide file.
  required: [course_spec]
```

**Output**: JSON object:

```json
{
  "course_name": "Machine Learning (AZAV)",
  "language": "en",
  "sections": [
    {
      "number": 1,
      "name": "01 Introduction",
      "topics": [
        {
          "topic_id": "introduction_ml_course_azav",
          "directory": "slides/module_540_ml_basics/topic_100_introduction_ml_course_azav",
          "slides": [
            {
              "file": "slides_introduction_ml_course.py",
              "title": "Introduction to the ML Course"
            }
          ]
        }
      ]
    }
  ]
}
```

#### CLI Equivalent

The existing `clm outline` command produces Markdown output. It should be
extended (or a new `clm outline --format json` flag added) to produce the
structured JSON format above. The Markdown output remains the default for
human consumption.

```bash
clm outline course-specs/machine-learning-azav.xml                    # existing: Markdown
clm outline course-specs/machine-learning-azav.xml --format json      # new: structured JSON
clm outline course-specs/machine-learning-azav.xml -L de              # existing: German
clm outline course-specs/machine-learning-azav.xml --format json -L de  # new combo
```

---

### 1.4 Tool: `search_slides`

Fuzzy search across topic names and slide file titles.

**Why**: Users often describe topics informally ("the slides about
decorators", "the RAG introduction"). This tool handles fuzzy matching.

#### MCP Tool Schema

```
name: search_slides
description: >
  Search for slide files by topic name, slide title, or keywords. Supports
  fuzzy matching. Returns matching topics with their paths and slide files.
  Optionally scoped to a specific course.

inputSchema:
  type: object
  properties:
    query:
      type: string
      description: >
        Search query. Matched against topic directory names, slide file
        names, and slide titles (from the header macro). Examples:
        "decorators", "RAG introduction", "linear regression deep dive".
    course_spec:
      type: string
      description: >
        Optional course spec path to limit search scope.
    language:
      type: string
      enum: [de, en]
      description: >
        Language for title matching. When provided, searches titles in that
        language. When omitted, searches both.
    max_results:
      type: integer
      default: 10
      description: Maximum number of results to return.
  required: [query]
```

**Output**: JSON array of matches, ranked by relevance:

```json
{
  "results": [
    {
      "score": 0.95,
      "topic_id": "decorators",
      "directory": "slides/module_230_decorators/topic_100_decorators",
      "slides": [
        {
          "file": "slides_decorators.py",
          "title_de": "Dekoratoren",
          "title_en": "Decorators"
        }
      ],
      "courses": ["python-prog.xml", "python-best-practice.xml"]
    }
  ]
}
```

#### CLI Equivalent

```bash
clm search-slides "decorators"
clm search-slides "RAG" --course-spec course-specs/machine-learning-azav.xml
clm search-slides "Dekoratoren" -L de
```

---

### 1.5 Tool: `validate_slides`

Full validation of slide files against authoring rules.

**Why**: Claude Code needs to verify its edits are correct after completing
a set of changes. This replaces the current `/validate-slides` slash
command with a faster, more precise tool.

#### Check Categories: Deterministic vs. LLM-Powered

Some checks can be performed mechanically; others require judgment that
only an LLM can provide:

| Check | Type | What it covers |
|-------|------|----------------|
| `format` | deterministic | Jupytext cell headers, valid tags, file structure |
| `pairing` | deterministic | DE/EN cell pairing (count, position, tag match) |
| `tags` | deterministic | start/completed rules, workshop constraints, tag validity |
| `code_quality` | **LLM-powered** | Unnecessary print(), explanatory comments, cell granularity |
| `voiceover` | **LLM-powered** | Content coverage, engaging style, completeness |
| `completeness` | **LLM-powered** | Workshop coverage of concepts, pedagogical gaps |

The LLM-powered checks cannot be done mechanically because the rules have
contextual exceptions (some `print()` is fine, some comments are allowed,
voiceover can omit trivial cells, etc.). Deciding whether an exception
applies requires understanding the content.

#### MCP vs. CLI: Different Strategies for LLM Checks

**MCP tool (called by Claude Code)**: Only runs deterministic checks
(`format`, `pairing`, `tags`). For the LLM-dependent categories, instead
of calling another LLM internally, the tool performs **structured
extraction** — it mechanically identifies the cells that *might* have
issues and returns them as `review_material`. Claude Code then applies its
own judgment to this data, which is faster, cheaper, and avoids redundant
nested LLM calls.

**CLI (run by a human)**: Supports all checks, including LLM-powered ones
via a `--llm-checks` flag. The CLI calls an LLM (configurable model,
defaults to a fast/cheap option) to evaluate the extracted material and
returns findings in the same structured format as deterministic checks.

#### MCP Tool Schema

```
name: validate_slides
description: >
  Validate slide files for format correctness, tag consistency, and
  language pairing (deterministic checks). For content-quality checks
  that require judgment (code_quality, voiceover, completeness), returns
  structured review_material with the relevant cells extracted — the
  calling agent evaluates these directly rather than nesting LLM calls.
  Call this after completing edits to a slide file.

inputSchema:
  type: object
  properties:
    path:
      type: string
      description: >
        Path to validate. Can be a single slide file, a topic directory,
        or a course spec XML file (validates all slides in the course).
    checks:
      type: array
      items:
        type: string
        enum:
          - format
          - pairing
          - tags
          - code_quality
          - voiceover
          - completeness
      description: >
        Which checks to run. Default: all. Deterministic checks (format,
        pairing, tags) produce findings directly. LLM-dependent checks
        (code_quality, voiceover, completeness) produce review_material
        for the caller to evaluate.
    course_spec:
      type: string
      description: >
        Course spec for cross-topic sequencing checks (concepts used before
        introduced). Only relevant when validating multiple topics.
  required: [path]
```

**Output**: JSON object with two sections — `findings` (deterministic
results) and `review_material` (extracted data for caller evaluation):

```json
{
  "files_checked": 1,
  "summary": "2 errors, 3 warnings, 3 categories for review",
  "findings": [
    {
      "severity": "error",
      "category": "pairing",
      "file": "slides/module_540_ml_basics/topic_120_.../slides_what_is_ml.py",
      "line": 45,
      "message": "German markdown cell at line 45 has no corresponding English cell",
      "suggestion": "Add a matching English cell after line 52"
    },
    {
      "severity": "warning",
      "category": "tags",
      "file": "...",
      "line": 120,
      "message": "start/completed pair found inside workshop section (workshop begins at line 98)",
      "suggestion": "Use plain # %% for workshop solutions, not start/completed"
    }
  ],
  "review_material": {
    "code_quality": {
      "print_calls": [
        {"line": 45, "code": "print(len(my_list))", "context": "standalone expression cell; simple value — likely unnecessary print()"},
        {"line": 89, "code": "print(output)", "context": "output is a multiline string built above"}
      ],
      "leading_comments": [
        {"line": 112, "code": "# Calculate the result\nresult = x + y", "context": "comment at start of code cell"}
      ]
    },
    "voiceover_gaps": [
      {"line": 23, "type": "markdown", "lang": "de", "heading": "## Methoden", "has_voiceover": false},
      {"line": 67, "type": "code", "lang": null, "preview": "p.move(3, 5)...", "has_voiceover": false}
    ],
    "completeness": {
      "slide_concepts": ["methods", "dot notation", "self parameter", "default arguments"],
      "workshop_exercises": ["add a method to a class"],
      "potentially_unpracticed": ["dot notation", "self parameter", "default arguments"]
    }
  }
}
```

When no LLM-dependent checks are requested, `review_material` is omitted.

#### CLI Equivalent

```bash
# Deterministic checks only (fast)
clm validate-slides slides/.../topic_120_what_is_ml_azav/
clm validate-slides slides/.../topic_120_what_is_ml_azav/ --checks format,pairing

# All checks including LLM-powered (slower, requires API key)
clm validate-slides slides/.../topic_120_what_is_ml_azav/ --llm-checks
clm validate-slides slides/.../topic_120_what_is_ml_azav/ --llm-checks --llm-model claude-haiku-4-5

# Full course validation
clm validate-slides course-specs/machine-learning-azav.xml --llm-checks
```

When `--llm-checks` is used, the CLI evaluates the `review_material`
through an LLM and converts the results into `findings` entries with the
same severity/category/message structure. The raw `review_material` is
not printed — only the resulting findings.

LLM configuration:
- `--llm-model`: Model to use (default: a fast/cheap model, e.g., Haiku)
- API key: via `ANTHROPIC_API_KEY` environment variable or CLM config

#### Quick-Check Mode

A fast subset for the PostToolUse hook (see Part 2). Deterministic only,
checks:
- Cell header syntax
- Valid tag names
- Unclosed start/completed pairs (start without completed immediately after)

```bash
clm validate-slides --quick path/to/file.py
```

Returns exit code 0 if clean, exit code 1 with JSON findings if issues found.
Must complete in <2 seconds for a single file.

---

### 1.6 Tool: `validate_spec`

Validate a course specification XML file for consistency.

**Why**: Course specs reference topics by ID. Typos, stale references, and
cross-module ambiguity cause silent build failures or wrong topic inclusion.
This tool catches these problems early.

#### MCP Tool Schema

```
name: validate_spec
description: >
  Validate a course specification XML file. Checks that all referenced
  topic IDs resolve to exactly one existing topic directory or file, that
  there are no duplicate topic references, and that referenced dir-groups
  exist. Returns structured findings.

inputSchema:
  type: object
  properties:
    course_spec:
      type: string
      description: Path to the course spec XML file.
  required: [course_spec]
```

**Output**: JSON object:

```json
{
  "course_spec": "course-specs/machine-learning-azav.xml",
  "topics_total": 42,
  "findings": [
    {
      "severity": "error",
      "type": "unresolved_topic",
      "topic_id": "linar_regression",
      "section": "03 Supervised Learning",
      "message": "Topic 'linar_regression' does not match any topic directory or file",
      "suggestion": "Did you mean 'linear_regression'? Found: slides/module_550_.../topic_200_linear_regression/"
    },
    {
      "severity": "error",
      "type": "ambiguous_topic",
      "topic_id": "decorators",
      "section": "05 Advanced Python",
      "message": "Topic 'decorators' matches multiple directories across modules",
      "matches": [
        "slides/module_100_xxx/topic_200_decorators/",
        "slides/module_230_yyy/topic_100_decorators/"
      ],
      "suggestion": "Qualify the topic ID to make it unique, or move one variant to a different name"
    },
    {
      "severity": "warning",
      "type": "duplicate_topic",
      "topic_id": "what_is_ml_azav",
      "sections": ["01 Introduction", "03 Supervised Learning"],
      "message": "Topic 'what_is_ml_azav' is referenced in multiple sections"
    },
    {
      "severity": "warning",
      "type": "missing_dir_group",
      "path": "div/toplevel/nonexistent-course",
      "message": "Dir-group path does not exist"
    }
  ]
}
```

#### Checks Performed

| Check | Severity | Description |
|-------|----------|-------------|
| Unresolved topic | error | Topic ID matches no directory or file |
| Ambiguous topic | error | Topic ID matches multiple directories across different modules |
| Duplicate reference | warning | Same topic ID appears in multiple sections |
| Missing dir-group | warning | Referenced dir-group path does not exist |
| Empty section | warning | Section contains no topics |
| Near-match suggestion | info | For unresolved topics, suggest similar existing topic IDs |

#### CLI Equivalent

```bash
clm validate-spec course-specs/machine-learning-azav.xml
clm validate-spec course-specs/*.xml                      # validate all specs
```

---

### 1.7 Tool: `normalize_slides`

Automatically fix formatting and tag issues in slide files.

**Why**: Rather than asking Claude Code to manually fix issues, a tool can
apply mechanical fixes reliably. Also serves as the one-time migration tool
for tag changes.

#### MCP Tool Schema

```
name: normalize_slides
description: >
  Normalize slide files by applying mechanical fixes: consistent cell
  ordering, tag migration (alt->completed after start cells), workshop tag
  insertion, and interleaving normalization. Returns a summary of changes
  made. Use --dry-run to preview changes without modifying files.

inputSchema:
  type: object
  properties:
    path:
      type: string
      description: >
        Path to normalize. Can be a single file, a topic directory, or a
        course spec XML file.
    operations:
      type: array
      items:
        type: string
        enum:
          - interleaving   # Normalize DE/EN cell ordering
          - tag_migration  # Rename alt->completed after start cells
          - workshop_tags  # Add workshop tag to workshop heading cells
          - slide_ids      # Add slide_id metadata to content cells
          - all            # All of the above
      default: [all]
      description: Which normalization operations to apply.
    dry_run:
      type: boolean
      default: false
      description: Preview changes without modifying files.
  required: [path]
```

**Output**: JSON object:

```json
{
  "files_modified": 3,
  "changes": [
    {
      "file": "slides/module_210_oop/topic_130_methods/slides_methods.py",
      "operation": "tag_migration",
      "line": 145,
      "description": "Renamed tags=[\"alt\"] -> tags=[\"completed\"] (follows start cell at line 138)"
    },
    {
      "file": "...",
      "operation": "workshop_tags",
      "line": 172,
      "description": "Added 'workshop' to tags on workshop heading cell"
    },
    {
      "file": "...",
      "operation": "interleaving",
      "lines": "12-78",
      "description": "Reordered cells: moved EN markdown block (was at lines 51-78) to pair with DE block"
    }
  ]
}
```

#### CLI Equivalent

```bash
clm normalize-slides slides/module_210_oop/ --dry-run
clm normalize-slides slides/module_210_oop/
clm normalize-slides slides/ --operations tag_migration    # one-time migration
clm normalize-slides course-specs/machine-learning-azav.xml --operations workshop_tags
clm normalize-slides slides/ --operations slide_ids        # add IDs to all content cells
```

#### Normalization Rules

**Tag migration** (`alt` -> `completed`): For every cell tagged `alt` that
immediately follows a cell tagged `start`, rename the tag to `completed`.
Standalone `alt` cells (not preceded by `start`) are left unchanged. This
operation is always safe and requires no pairing analysis.

**Workshop tags**: For every markdown cell whose content matches the pattern
`## Workshop:` or `## Mini-Workshop:` (in either language), add `workshop`
to its tags if not already present. This operation is always safe.

**Slide IDs** (`slide_id` metadata): Add `slide_id` metadata to content
cells that don't already have one. See Appendix C for the full `slide_id`
specification. Auto-generation rules:

- Markdown cells with headings: derive from heading text, e.g.,
  `## Methoden und Attribute` -> `slide_id="methoden-und-attribute"`
- Code cells: derive from defined names, e.g., a cell defining
  `class Point` -> `slide_id="point-class-def"`
- Fallback: `slide_id="<file-stem>-cell-<N>"` where N is the cell's
  ordinal position
- Paired DE/EN cells get the **same** `slide_id`
- Collisions within a file get a numeric suffix (`-2`, `-3`, etc.)
- Cells that already have a `slide_id` are left unchanged

This operation is safe (additive only). It should run after interleaving
normalization so that DE/EN pairs are already adjacent and can be assigned
the same ID.

**Interleaving normalization**: Reorder cells so that paired DE/EN cells are
always adjacent. This is the most complex operation and uses a three-tier
strategy to handle files that may have structural inconsistencies.

Target interleaving pattern (no exceptions):

```
DE-markdown (with slide/subslide tag)
EN-markdown (with slide/subslide tag)
  [DE-voiceover]
  [EN-voiceover]
  [shared code cell(s)]
  [DE-markdown continuation]
  [EN-markdown continuation]
  [DE-voiceover]
  [EN-voiceover]
  [shared code cell(s)]
...
```

For language-specific code cells:

```
DE-markdown
EN-markdown
  DE-code (lang="de")
  EN-code (lang="en")
  [DE-voiceover]
  [EN-voiceover]
```

The key invariant: **paired DE/EN cells are always adjacent**. Language-
independent cells (shared code, language-independent images) follow the
pair they belong to. This convention applies uniformly to all sections
including workshops — there is no structural exception for workshops with
language-specific code.

#### Interleaving: Three-Tier Pairing Strategy

The normalizer must pair up DE and EN cells before it can interleave them.
Existing files may have inconsistent structure (one language having more
cells than the other, different ordering, etc.). The strategy uses three
tiers, each acting as a gate:

**Tier 1: Structural pre-check (fast, deterministic)**

Parse the file into cells and count by (language, cell_type) where:
- language: `de`, `en`, or `shared` (no lang attribute)
- cell_type: `markdown`, `code`, `voiceover`, `notes`

Voiceover and notes are counted separately from regular markdown because
they follow different pairing rules.

```
Category           DE    EN    Status
─────────────────────────────────────
markdown           14    14    MATCH
code (lang)         3     3    MATCH
voiceover           8     8    MATCH
notes               2     2    MATCH
shared code        12     -    (no pairing needed)
```

If all categories match → proceed to Tier 2.
If any category has a count mismatch → **report the mismatch and proceed
to Tier 3** for the mismatched categories. Matched categories can still
be paired via Tier 2.

**Tier 2: Positional pairing with structural verification**

For each cell category where counts match, pair by ordinal position (1st
DE markdown with 1st EN markdown, 2nd with 2nd, etc.).

For each candidate pair, verify structural similarity using these
heuristics (all must pass):

| Check | Criterion |
|-------|-----------|
| Tags | Same tag set (ignoring order) |
| Heading level | If one cell starts with `## X`, the other must start with `## Y` (same `#` count) |
| Bullet count | Number of top-level list items within ±2 |
| Code structure | For code cells: same function/class names present (extracted via regex: `def \w+`, `class \w+`) |
| Line count | Within ±50% of each other (generous threshold to account for German being more verbose) |

If all pairs in a category pass → **reorder automatically**. The
normalizer interleaves cells in the target pattern and writes the file.

If some pairs fail similarity → those specific pairs are **flagged as
uncertain** and included in the Tier 3 report. Pairs that passed can still
be reordered (the uncertain pairs stay in their original positions).

**Tier 3: Report for external resolution**

When the normalizer cannot confidently pair cells (count mismatch or
similarity failure), it does NOT modify those cells. Instead, it returns
structured findings in the output:

```json
{
  "file": "slides/.../slides_string_interpolation.py",
  "status": "partial",
  "auto_applied": [
    {"operation": "tag_migration", "count": 2},
    {"operation": "interleaving", "pairs_reordered": 10}
  ],
  "needs_review": [
    {
      "issue": "count_mismatch",
      "category": "code",
      "de_count": 4,
      "en_count": 3,
      "de_cells": [
        {"line": 22, "preview": "spieler_name = \"Hans\"..."},
        {"line": 30, "preview": "ausgabe = f\"\"\"\\..."},
        {"line": 38, "preview": "ausgabe = (\\n    f\"Hallo..."},
        {"line": 94, "preview": "def drucke_begrüßung(name):..."}
      ],
      "en_cells": [
        {"line": 56, "preview": "player_name = \"John\"..."},
        {"line": 64, "preview": "output = (\\n    f\"Hello..."},
        {"line": 120, "preview": "def print_greeting(name):..."}
      ],
      "suggestion": "DE has 4 lang-specific code cells but EN has 3. The DE cell at line 30 (triple-quoted f-string) may be an extra example without an EN equivalent. Review whether it should be translated or removed."
    },
    {
      "issue": "similarity_failure",
      "pair_index": 5,
      "de_cell": {"line": 81, "tags": ["subslide"], "heading": "## Mini-Workshop: Begrüßung", "bullets": 4},
      "en_cell": {"line": 107, "tags": [], "heading": "## Mini workshop: Greeting", "bullets": 3},
      "failed_checks": ["tags"],
      "suggestion": "Tags differ: DE has ['subslide'] but EN has []. Likely the EN cell is missing the 'subslide' tag."
    }
  ]
}
```

The `needs_review` items give the calling agent (Claude Code) or the human
user enough context to resolve the issue. The normalizer's job is to
**identify** problems precisely, not to guess at solutions.

**Design principle**: The normalizer is purely deterministic — no LLM calls
inside the tool. When Claude Code calls `normalize_slides` via MCP and
receives `needs_review` items, it can use its own judgment to resolve them
(or ask the user). This keeps the tool predictable and testable.

#### CLI Behavior for Tier 3

In CLI mode, `clm normalize-slides` with `--dry-run` shows all changes
(including what would need review). Without `--dry-run`, it applies safe
changes and prints the review items to stderr:

```bash
$ clm normalize-slides slides/.../slides_string_interpolation.py
Applied: 2 tag migrations, 10 pairs reordered
Review needed: 1 count mismatch (code cells), 1 tag mismatch
Run with --dry-run for details, or fix manually and re-run.
```

Exit codes:
- 0: all changes applied, no review needed
- 1: some changes applied, review items remain
- 2: no changes could be applied (all uncertain)

---

### 1.8 Tool: `get_language_view`

Extract a single-language view of a slide file.

**Why**: When editing bilingual slides, seeing only one language at a time
makes the logical flow much clearer. This is a read-only reference view.

#### MCP Tool Schema

```
name: get_language_view
description: >
  Extract a single-language view of a slide file. Returns the file content
  with only cells for the specified language (plus language-independent
  cells). This is a read-only preview — edit the original bilingual file,
  then use suggest_sync to update the other language.

inputSchema:
  type: object
  properties:
    file:
      type: string
      description: Path to the slide file.
    language:
      type: string
      enum: [de, en]
      description: Which language to extract.
    include_voiceover:
      type: boolean
      default: false
      description: >
        Whether to include voiceover cells. Default false to keep the
        view compact.
    include_notes:
      type: boolean
      default: false
      description: Whether to include speaker notes cells.
  required: [file, language]
```

**Output**: The filtered file content as text, with line-number annotations
showing where each cell appears in the original file:

```python
# j2 from 'macros.j2' import header
# {{ header("Methoden", "Methods") }}

# [original line 23]
# %% [markdown] lang="de" tags=["subslide"]
# ## Methoden
#
# - Klassen können Methoden enthalten.
# - Methoden sind Funktionen, die "zu einem Objekt gehören".

# [original line 39]
# %% tags=["keep"]
class MyClass:
    def method(self):
        print(f"Called method on {self}")

# [original line 45]
# %% tags=["keep"]
my_object = MyClass()
my_object.method()
```

The `[original line N]` comments let Claude Code map back to the bilateral
file when making edits.

#### CLI Equivalent

```bash
clm language-view slides/.../slides_methods.py de
clm language-view slides/.../slides_methods.py en --include-voiceover
```

Prints the filtered content to stdout.

---

### 1.9 Tool: `suggest_sync`

After editing cells in one language, suggest corresponding changes for the
other language.

**Why**: The most common mistake is editing DE cells and forgetting to update
EN cells (or vice versa). This tool detects which cells changed and suggests
updates.

**Pairing strategy**: When cells have `slide_id` metadata, pairing is
exact — DE and EN cells with the same `slide_id` are paired definitively.
This eliminates the positional ambiguity that the interleaving normalizer's
three-tier strategy works around. For cells without `slide_id`, the tool
falls back to positional pairing (same ordinal position within each
language sequence).

#### MCP Tool Schema

```
name: suggest_sync
description: >
  Compare the current state of a slide file against its last committed
  version (git HEAD) and identify cells in one language that changed without
  corresponding changes in the other language. Returns suggestions for
  what to update. Uses slide_id metadata for precise DE/EN pairing when
  available; falls back to positional pairing otherwise. Does NOT modify
  the file.

inputSchema:
  type: object
  properties:
    file:
      type: string
      description: Path to the slide file.
    source_language:
      type: string
      enum: [de, en]
      description: >
        The language that was edited. The tool identifies changes in this
        language and suggests corresponding updates for the other language.
        If omitted, the tool auto-detects which language has more changes.
  required: [file]
```

**Output**: JSON object:

```json
{
  "file": "slides/.../slides_methods.py",
  "source_language": "de",
  "target_language": "en",
  "pairing_method": "slide_id",
  "suggestions": [
    {
      "type": "modified",
      "slide_id": "methods-intro",
      "source_line": 23,
      "source_content": "# ## Methoden und Attribute\n# ...",
      "target_line": 31,
      "target_content_current": "# ## Methods\n# ...",
      "suggestion": "Title changed from 'Methoden' to 'Methoden und Attribute'. Update English title to 'Methods and Attributes'."
    },
    {
      "type": "added",
      "slide_id": "new-concept",
      "source_line": 55,
      "source_content": "# %% [markdown] lang=\"de\" slide_id=\"new-concept\"\n# Ein neuer Absatz...",
      "target_line": null,
      "suggestion": "New German cell 'new-concept' added at line 55. Add corresponding English cell with slide_id=\"new-concept\" after line 60."
    },
    {
      "type": "deleted",
      "slide_id": "old-paragraph",
      "source_line": null,
      "target_line": 72,
      "target_content_current": "# %% [markdown] lang=\"en\" slide_id=\"old-paragraph\"\n# Old paragraph...",
      "suggestion": "German cell 'old-paragraph' was deleted. Consider deleting the English cell at line 72 too."
    }
  ],
  "unmodified_pairs": 12,
  "sync_needed": true
}
```

**Dependency**: Requires git to be available for diffing against HEAD. If
the file is new (untracked), all cells are treated as "added" and the tool
suggests creating the full set of paired cells.

#### CLI Equivalent

```bash
clm suggest-sync slides/.../slides_methods.py
clm suggest-sync slides/.../slides_methods.py --source-language de
```

---

### 1.10 Tool: `extract_voiceover`

Extract voiceover cells from a slide file into a companion file.

**Why**: Voiceover cells make slide files 2-3x longer. Separating them
into permanent companion files reduces cognitive load when editing content
and keeps slide files focused on the material itself.

**Design**: Voiceover is stored permanently in companion files. The slide
file and voiceover file are linked via `slide_id` metadata — no marker
comments or anchors are left in the slide file. See Appendix C for the
full `slide_id` specification.

#### MCP Tool Schema

```
name: extract_voiceover
description: >
  Move voiceover cells from a slide file into a companion voiceover file.
  The companion file is named by replacing the "slides_" prefix with
  "voiceover_" (e.g., slides_methods.py -> voiceover_methods.py).
  Voiceover cells are matched to content cells via slide_id metadata.
  Content cells without slide_id are assigned one automatically before
  extraction. The slide file is left clean — no marker comments.

inputSchema:
  type: object
  properties:
    file:
      type: string
      description: Path to the slide file.
    dry_run:
      type: boolean
      default: false
      description: Preview changes without modifying files.
  required: [file]
```

**Output**: JSON object:

```json
{
  "source_file": "slides/.../slides_methods.py",
  "voiceover_file": "slides/.../voiceover_methods.py",
  "cells_extracted": 8,
  "slide_ids_added": 3,
  "source_lines_before": 450,
  "source_lines_after": 280,
  "dry_run": false
}
```

**Companion file format**: The voiceover file uses Jupytext percent format.
Each voiceover cell has a `for_slide` attribute referencing the `slide_id`
of the content cell it belongs to:

```python
# j2 from 'macros.j2' import header
# {{ header("Methoden", "Methods") }}

# %% [markdown] lang="de" tags=["voiceover"] for_slide="methods-intro"
#
# - Herzlich Willkommen!
# - In diesem Video lernen wir, wie wir Methoden definieren...

# %% [markdown] lang="en" tags=["voiceover"] for_slide="methods-intro"
#
# - Welcome!
# - In this video we'll learn how to define methods...

# %% [markdown] lang="de" tags=["voiceover"] for_slide="methods-demo"
#
# - Hier sehen wir ein Beispiel für eine einfache Methode...

# %% [markdown] lang="en" tags=["voiceover"] for_slide="methods-demo"
#
# - Here we see an example of a simple method...
```

**Slide file after extraction** (clean, no markers):

```python
# j2 from 'macros.j2' import header
# {{ header("Methoden", "Methods") }}

# %% [markdown] lang="de" tags=["subslide"] slide_id="methods-intro"
# ## Methoden
#
# - Klassen können Methoden enthalten.
# - Methoden sind Funktionen, die "zu einem Objekt gehören".

# %% [markdown] lang="en" tags=["subslide"] slide_id="methods-intro"
# ## Methods
#
# - Classes can contain methods.
# - Methods are functions that "belong to an object".

# %% tags=["keep"] slide_id="methods-demo"
class MyClass:
    def method(self):
        print(f"Called method on {self}")
```

**Pre-requisite**: Content cells must have `slide_id` metadata. If they
don't, `extract_voiceover` adds IDs automatically (using the same
auto-generation rules as `normalize_slides --operations slide_ids`) before
extracting. The count of added IDs is reported in `slide_ids_added`.

#### CLI Equivalent

```bash
clm extract-voiceover slides/.../slides_methods.py --dry-run
clm extract-voiceover slides/.../slides_methods.py
clm extract-voiceover slides/module_210_oop/     # all files in directory
```

---

### 1.11 Tool: `inline_voiceover`

Merge voiceover from a companion file back into the slide file.

**Why**: The reverse of `extract_voiceover`. Useful for reviewing the
complete slide deck in one file, or as a migration step if reverting to
inline voiceover.

#### MCP Tool Schema

```
name: inline_voiceover
description: >
  Merge voiceover cells from a companion voiceover file back into the main
  slide file. Each voiceover cell is inserted after the content cell with
  the matching slide_id and lang. The companion file is kept by default
  (permanent storage); use delete_companion to remove it.

inputSchema:
  type: object
  properties:
    file:
      type: string
      description: >
        Path to the slide file (not the voiceover file). The companion
        file is located automatically by name convention.
    delete_companion:
      type: boolean
      default: false
      description: >
        Delete the companion file after inlining. Default false because
        companion files are the permanent storage location.
    dry_run:
      type: boolean
      default: false
      description: Preview changes without modifying files.
  required: [file]
```

**Matching**: Each voiceover cell's `for_slide` attribute is matched to
a content cell's `slide_id` in the main file. The voiceover cell is
inserted immediately after the matching content cell (respecting the
interleaving convention: DE voiceover after DE content, EN voiceover after
EN content). Unmatched `for_slide` references are reported as warnings.

#### CLI Equivalent

```bash
clm inline-voiceover slides/.../slides_methods.py --dry-run
clm inline-voiceover slides/.../slides_methods.py
clm inline-voiceover slides/module_210_oop/     # all files in directory
```

---

### 1.12 New CLM Subcommand: `clm mcp`

Starts the MCP server.

```bash
clm mcp [--data-dir PATH] [--log-level LEVEL]
```

The server exposes all tools defined in sections 1.2-1.11 via the MCP
stdio transport. It should use the standard MCP Python SDK
(`mcp` package).

---

## Part 2: PythonCourses-Side Integration

These changes are made in the PythonCourses repository (this repo), not in
CLM.

### 2.1 MCP Server Configuration

Add `.mcp.json` to the repository root:

```json
{
  "mcpServers": {
    "clm": {
      "command": "uv",
      "args": ["run", "clm", "mcp"]
    }
  }
}
```

This makes the CLM MCP server available to all Claude Code sessions in this
project.

### 2.2 PostToolUse Validation Hook

Add to `.claude/settings.json` under `hooks`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // .tool_response.filePath // empty' | { read -r f; case \"$f\" in */slides_*.py) uv run clm validate-slides --quick \"$f\" 2>/dev/null ;; *) true ;; esac; }",
            "timeout": 10,
            "statusMessage": "Validating slide format..."
          }
        ]
      }
    ]
  }
}
```

**Behavior**:
- Triggers only on Write/Edit of `slides_*.py` files
- Runs `clm validate-slides --quick` (fast syntax-only checks)
- Returns JSON output with `additionalContext` if issues are found, so Claude
  Code sees the problem immediately
- Does NOT check pairing or completeness (avoids false positives during
  multi-step edits)
- Timeout: 10 seconds (should complete in <2s for a single file)

**Quick-check output format** (from CLM):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Slide validation: 1 error in slides_methods.py: line 145 has invalid tag 'slides' (did you mean 'slide'?)"
  }
}
```

If no issues, the command exits silently (exit code 0, no output).

### 2.3 Slash Command: `/edit-slides`

Create `.claude/commands/edit-slides.md`:

```markdown
You are about to edit slides in this course repository. Follow this
process to ensure correct, high-quality edits.

## Target

$ARGUMENTS

Interpret the argument as a topic ID, file path, topic directory, or
informal description (e.g., "the decorators slides").

## Setup Steps

1. Use the `resolve_topic` or `search_slides` MCP tool to find the exact
   file(s) to edit.

2. Use `course_outline` to understand what comes before and after this
   topic in the course. This helps maintain pedagogical flow.

3. Read the slide file(s) to understand the current content.

4. If the file is long (>300 lines), use `get_language_view` to get a
   single-language overview for easier comprehension.

## Editing Rules Reminder

- Tag reference: `slide`, `subslide`, `keep`, `completed`, `start`,
  `alt`, `answer`, `voiceover`, `notes`, `workshop`
- `start` is always followed by `completed` (never in workshops)
- `alt` is for workshop discussion/alternatives only (completed variant)
- Workshop headings have the `workshop` tag
- No `start`/`completed` inside workshop sections
- Use plain `# %%` for workshop solutions, `keep` for assertions/data

## After Editing

1. Call `validate_slides` on the modified file.
2. If you edited only one language, call `suggest_sync` to check whether
   the other language needs updating.
3. Fix any issues found.
```

### 2.4 Tag Changes

The following tag changes require coordinated updates to CLM and this
repository:

#### New tag: `completed`

**CLM change**: Add `completed` to the list of recognized tags. Process it
identically to `alt` (omit cell in code-along variants).

**Migration**: Run `clm normalize-slides slides/ --operations tag_migration`
to rename `alt` -> `completed` on cells that follow `start` cells.

**Documentation**: Update CLAUDE.md, slide-authoring.md, and
validate-slides.md to reflect the new tag.

#### New tag: `workshop`

**CLM change**: Add `workshop` to the list of recognized tags. It has no
effect on output processing (purely structural metadata for tooling and
validation).

**Migration**: Run `clm normalize-slides slides/ --operations workshop_tags`
to add `workshop` to heading cells matching the workshop title pattern.

**Documentation**: Update CLAUDE.md and slide-authoring.md.

### 2.5 Documentation Updates

After implementing the tools, update these files:

- **CLAUDE.md**: Add MCP tools to "Essential Commands" section. Update tag
  reference to include `completed` and `workshop`.
- **.claude/docs/slide-authoring.md**: Update tag definitions, add
  `completed` and `workshop` tags, update start/alt section to use
  start/completed terminology.
- **.claude/commands/validate-slides.md**: Update tag list, add references
  to MCP validation tool as an alternative.

---

## Part 3: Implementation Sequence

Recommended order of implementation, designed so each step delivers
standalone value:

### Phase 1: Navigation (highest immediate impact)

1. `resolve_topic` — CLI + MCP tool
2. `course_outline` — extend existing `clm outline` with `--format json`,
   add MCP tool
3. `search_slides` — CLI + MCP tool
4. `clm mcp` subcommand — MCP server infrastructure
5. `.mcp.json` in PythonCourses

After Phase 1, Claude Code can instantly find any slide file.

### Phase 2: Tag Changes + Validation

6. Add `completed` and `workshop` to CLM's recognized tags
7. `validate_spec` — CLI + MCP tool
8. `normalize_slides` — CLI + MCP tool (includes tag migration)
9. Run tag migration on the PythonCourses repository
10. `validate_slides` — CLI + MCP tool
11. PostToolUse hook in PythonCourses
12. Update documentation (CLAUDE.md, slide-authoring.md)

After Phase 2, the tag system is unambiguous, course specs are verified,
and errors are caught.

### Phase 3: Bilingual Editing Support

13. `get_language_view` — CLI + MCP tool
14. `suggest_sync` — CLI + MCP tool
15. `/edit-slides` slash command in PythonCourses

After Phase 3, bilingual editing is streamlined.

### Phase 4: Slide IDs + Voiceover Separation

16. Add `slide_id` metadata support to CLM (parse, preserve, strip from
    output)
17. Add `slide_ids` operation to `normalize_slides` (auto-generate IDs)
18. Run slide_id migration on the PythonCourses repository
19. `extract_voiceover` — CLI + MCP tool
20. `inline_voiceover` — CLI + MCP tool
21. CLM build integration: when building, if a companion voiceover file
    exists alongside a slide file, automatically merge voiceover cells
    into the processing pipeline (matched by `slide_id` + `lang`). The
    slide file itself is not modified — merging is done in memory during
    build. This makes companion files the permanent storage format.
22. Run voiceover extraction across the PythonCourses repository
23. Update `suggest_sync` to use `slide_id` for precise pairing (replaces
    positional fallback for files that have IDs)

After Phase 4, voiceover lives permanently in companion files, slide
files are 2-3x shorter, and DE/EN pairing is precise via `slide_id`.

---

## Appendix A: Tag Reference (Post-Migration)

| Tag | Cell Type | Purpose | Code-Along | Completed | Speaker |
|-----|-----------|---------|------------|-----------|---------|
| `slide` | md/code | Starts a new slide | yes | yes | yes |
| `subslide` | md/code | Starts a subslide | yes | yes | yes |
| `keep` | code | Include code in all variants | yes (with code) | yes | yes |
| `start` | code | Starter code for live coding | yes (with code) | omitted | omitted |
| `completed` | code | Full version after start | omitted | yes | yes |
| `alt` | any | Discussion/extras for completed only | omitted | yes | yes |
| `answer` | md | Solution text | placeholder | yes | yes |
| `voiceover` | md | Text to read aloud | omitted | omitted | yes |
| `notes` | md | Brief speaker hints | omitted | omitted | yes |
| `workshop` | md | Marks workshop boundary | yes | yes | yes |

**Rules**:
- `start` must be immediately followed by `completed` (same cell type, same
  lang attribute if any)
- `start`/`completed` never appear inside workshop sections (after a
  `workshop`-tagged cell)
- `alt` is independent of `start`; it stands alone
- `workshop` appears only on the heading cell of a workshop section

## Appendix B: Interleaving Convention (Post-Normalization)

After Phase 4 (voiceover separation), voiceover cells live in companion
files and the slide file contains only content, notes, and structural
cells. The interleaving convention for slide files is:

```
1. DE markdown (slide/subslide tag)   slide_id="intro"
2. EN markdown (slide/subslide tag)   slide_id="intro"
3. [shared code cell(s)]              slide_id="demo-1"
4. [DE markdown continuation]         slide_id="detail"
5. [EN markdown continuation]         slide_id="detail"
6. [shared code cell(s)]              slide_id="demo-2"
...
```

For language-specific code:

```
1. DE markdown                        slide_id="concept"
2. EN markdown                        slide_id="concept"
3. DE code (lang="de")                slide_id="example"
4. EN code (lang="en")                slide_id="example"
```

Key invariant: **paired DE/EN cells are always adjacent and share the
same `slide_id`**. A German cell is always immediately followed by its
English counterpart. Language-independent cells (shared code, images)
follow the pair they belong to.

**No exceptions.** This convention applies uniformly to all sections,
including workshops with language-specific code (e.g., different class/
variable names per language). The DE code cell is followed immediately by
the EN code cell, even though they define different identifiers. This
keeps the pairing mechanical and verifiable.

During Phases 2-3 (before voiceover extraction), voiceover cells appear
inline in the slide file, placed immediately after the content cell pair
they describe:

```
1. DE markdown   slide_id="intro"
2. EN markdown   slide_id="intro"
3. [DE voiceover for "intro"]
4. [EN voiceover for "intro"]
5. [shared code]
```

After Phase 4, the voiceover cells move to the companion file and the
slide file becomes more compact.

## Appendix C: `slide_id` Specification

### Purpose

`slide_id` is a cell metadata field that serves as a stable, human-readable
identifier for content cells. It enables:

1. **Voiceover linking**: Companion voiceover files reference content cells
   by `slide_id` via the `for_slide` attribute, without requiring marker
   comments or positional anchoring in the slide file.
2. **DE/EN pairing**: Paired German and English cells share the same
   `slide_id`, making the pairing explicit and verifiable.
3. **Precise sync suggestions**: `suggest_sync` uses `slide_id` to match
   cells definitively rather than relying on positional heuristics.
4. **Stable cross-references**: `get_language_view` annotates cells with
   their `slide_id` instead of line numbers, so references remain valid
   after edits.

### Syntax

In Jupytext percent format, `slide_id` appears on the cell header line:

```python
# %% [markdown] lang="de" tags=["slide"] slide_id="methods-intro"
# ## Methoden

# %% [markdown] lang="en" tags=["slide"] slide_id="methods-intro"
# ## Methods

# %% tags=["keep"] slide_id="methods-demo"
class MyClass:
    def method(self):
        print(f"Called method on {self}")
```

In the voiceover companion file, `for_slide` references the `slide_id`:

```python
# %% [markdown] lang="de" tags=["voiceover"] for_slide="methods-intro"
# - Herzlich Willkommen! In diesem Video...

# %% [markdown] lang="en" tags=["voiceover"] for_slide="methods-intro"
# - Welcome! In this video...
```

### Field Name Choice

The field is named `slide_id` (not `id`) to avoid conflict with Jupyter
Notebook format 4.5+, which reserves the `id` field for internal cell
tracking (UUID-like identifiers). Jupytext passes arbitrary metadata
through, so `slide_id` is stored as `{"slide_id": "..."}` in the cell
metadata when converted to `.ipynb`.

### Rules

1. **Uniqueness**: `slide_id` must be unique within a file per language.
   That is, there can be at most one `lang="de"` cell and one `lang="en"`
   cell with the same `slide_id`, plus optionally one shared cell (no
   `lang`) with that ID.

2. **Paired cells share IDs**: A German cell and its English translation
   must have the same `slide_id`. This is the primary mechanism for
   explicit DE/EN pairing.

3. **Human-readable**: IDs are short, lowercase, hyphenated strings
   derived from content. They should be meaningful when read in isolation
   (e.g., in a voiceover file's `for_slide` attribute).

4. **Stable across edits**: Once assigned, a `slide_id` should not change
   unless the cell's purpose fundamentally changes. Minor content edits
   (rewording, adding bullet points) do not require a new ID.

5. **Not all cells need IDs**: Voiceover cells, notes cells, and trivial
   cells (e.g., a bare `# %%` with a single expression) may omit
   `slide_id`. IDs are required on cells that:
   - Have or should have voiceover (so the companion file can reference
     them)
   - Are part of a DE/EN pair that `suggest_sync` should track

6. **Stripped from output**: CLM removes `slide_id` (and `for_slide`)
   metadata from generated output (notebooks, HTML, code). Students never
   see these identifiers.

### Auto-Generation Rules

The `normalize_slides --operations slide_ids` command generates IDs for
cells that don't have one:

| Cell type | Source | Example |
|-----------|--------|---------|
| Markdown with heading | Heading text, lowercased, hyphenated | `## Methoden und Attribute` -> `methoden-und-attribute` |
| Markdown without heading | First significant words | `# - Klassen können Methoden...` -> `klassen-methoden` |
| Code cell with definitions | Function/class name | `class Point:` -> `point-class-def` |
| Code cell without definitions | File stem + ordinal | `slides_methods.py` cell 5 -> `methods-cell-5` |

**Collision resolution**: If the generated ID already exists in the file,
append `-2`, `-3`, etc.

**Paired cell handling**: When generating IDs during interleaving
normalization, adjacent DE/EN cells (identified by Tier 2 pairing) are
assigned the same ID. The German cell's content is used as the generation
source (since German headings are often longer and more descriptive, but
either language would work).

### Voiceover Companion File Format

The companion file is named by replacing `slides_` with `voiceover_` in
the filename: `slides_methods.py` -> `voiceover_methods.py`. It lives in
the same directory as the slide file.

Structure:
- Same Jinja2 header as the slide file (for consistent title rendering)
- Voiceover cells ordered to match the slide file's cell order
- Each cell has `tags=["voiceover"]`, a `lang` attribute, and a
  `for_slide` attribute referencing the content cell's `slide_id`
- Multiple voiceover cells can reference the same `slide_id` (e.g., a
  long explanation split across multiple voiceover cells for the same
  slide)

CLM build behavior:
- When processing a slide file, CLM checks for a companion voiceover file
- If found, voiceover cells are merged into the processing pipeline at
  the positions determined by `slide_id` + `lang` matching
- The merge happens in memory — neither file is modified on disk
- Unmatched `for_slide` references produce build warnings
