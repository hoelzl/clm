# CLM {version} — Spec File Reference

Course specification files define the structure and configuration of a CLM course.
They use XML format and are typically named `course.xml`.

## Basic Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name>
        <de>Kursname</de>
        <en>Course Name</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Beschreibung</de>
        <en>Description</en>
    </description>
    <certificate>
        <de>Zertifikatstext</de>
        <en>Certificate text</en>
    </certificate>
    <project-slug>course-name</project-slug>
    <github>
        <repository-base>https://github.com/user</repository-base>
    </github>
    <sections>
        <!-- Section definitions -->
    </sections>
    <dir-groups>
        <!-- Optional directory groups -->
    </dir-groups>
    <output-targets>
        <!-- Optional output targets -->
    </output-targets>
</course>
```

## Required Elements

### `<name>`

Bilingual course name with `<de>` and `<en>` children.

### `<prog-lang>`

Programming language. Valid values: `python`, `cpp`, `csharp`, `java`, `typescript`.

### `<description>`

Bilingual course description with `<de>` and `<en>` children.

### `<certificate>`

Bilingual certificate text with `<de>` and `<en>` children.

### `<project-slug>` (Recommended)

Base name used for output directories and repository names. Output directories
are named `{project-slug}-{lang}` (e.g., `ml-course-de`, `ml-course-en`).

```xml
<project-slug>ml-course</project-slug>
```

If omitted, CLM falls back to a sanitized version of the course name with a
language suffix (e.g., `Python Programming-de`). Using `<project-slug>` is
recommended for clean, predictable directory names.

> **Deprecation note**: In earlier versions, `<project-slug>` was placed inside
> the `<github>` element. That location still works but is deprecated and will
> log a warning. Move it to the top level of `<course>` for forward compatibility.

### `<sections>`

Contains one or more `<section>` elements:

```xml
<sections>
    <section>
        <name>
            <de>Woche 1</de>
            <en>Week 1</en>
        </name>
        <topics>
            <topic>introduction</topic>
            <topic>basic_concepts</topic>
        </topics>
    </section>
</sections>
```

Each `<topic>` references a topic by directory name (without numeric prefix).
For `slides/module_001/topic_100_introduction/`, the ID is `introduction`.

Optional `<section>` attributes:

| Attribute | Description |
|-----------|-------------|
| `enabled` | `"true"` (default) or `"false"`, case-insensitive. A disabled section is dropped from the parsed spec entirely, so `clm build`, `clm outline`, `clm validate`, and all MCP tools ignore it without needing code changes. Disabled sections may omit `<topics>` or reference topic IDs that do not yet exist — they are never built or validated, which lets a full roadmap spec live as a single file instead of carrying a separate `-build.xml` subset. |
| `id` | Optional stable identifier for the section (e.g. `id="w03"`). Recommended for courses that are frequently filtered, because IDs are stable under reordering and renaming. |
| `module` | Optional module-directory binding (e.g. `module="module_545_ml_azav_cohort_2026_04"`). When set, every `<topic>` inside this section resolves only against that module — duplicate topic IDs in other modules are ignored. This is the supported mechanism for cohort archives or course variants that share topic IDs with the live module. The value is the literal directory name under `slides/`. Per-topic `module=` (see below) overrides the section default for individual topics. |

Example of a roadmap section deferred until its topics exist:

```xml
<section id="w17" enabled="false">
    <name>
        <de>Woche 17: Fortgeschrittene Themen</de>
        <en>Week 17: Advanced Topics</en>
    </name>
    <topics>
        <topic>not_yet_implemented</topic>
    </topics>
</section>
```

Example of a frozen-cohort archive sharing topic IDs with the live module:

```xml
<!-- New unified content for the next cohort, currently disabled -->
<section id="w01" enabled="false">
    <name><de>Woche 1</de><en>Week 1</en></name>
    <topics>
        <topic>introduction</topic>
        <topic>python_setup</topic>
    </topics>
</section>

<!-- Frozen materials shipped to the current cohort, bound to the
     archive module so they resolve regardless of duplicate topic IDs
     in the live module. -->
<section id="w01-cohort-2026-04" enabled="true"
         module="module_545_ml_azav_cohort_2026_04">
    <name>
        <de>Woche 1 (Kohorte 2026-04)</de>
        <en>Week 1 (cohort 2026-04)</en>
    </name>
    <topics>
        <topic>introduction</topic>
        <topic>python_setup</topic>
    </topics>
</section>
```

When the next cohort starts, flip `enabled="true"` on the live section and
`enabled="false"` on the frozen one — no topic-directory renames required.

A `<section>` may also contain one or more `<include>` elements (see the
[`<include>` element](#include) reference below). Section-level includes
are inherited as defaults by every child `<topic>` in the section; a topic
can override an inherited include by declaring its own `<include>` with
the same `as` target.

Optional `<topic>` attributes:

| Attribute | Description |
|-----------|-------------|
| `id` | Topic ID as an attribute (e.g. `<topic id="introduction"/>`). Equivalent to the text-content form `<topic>introduction</topic>`. **Required form when the `<topic>` carries `<include>` or other child elements** — see the note below. Specifying both the `id=` attribute and text content is a hard error. |
| `html` | If set, skip HTML generation for this topic |
| `evaluate` | `"true"`/`"yes"`/`"1"` (default) or `"false"`/`"no"`/`"0"` (case-insensitive). Set `evaluate="no"` to render the notebook to all configured output formats (HTML, `.ipynb`, code) **without spawning a kernel** — cells appear with empty outputs. Use this for slides that should ship as static decks (e.g., topics that depend on a live service, an interactive demo, GPU hardware, or a long-running training run that is too expensive to repeat on every build). Independent of `html=` (which skips HTML entirely) and `skip-errors` (which catches errors raised during execution). |
| `skip-errors` | `"true"`/`"yes"`/`"1"` or `"false"`/`"no"`/`"0"` (case-insensitive; default `false`). When set, cell execution errors do not abort HTML generation. Cells whose outputs contain an error are cleared, and a processing warning is emitted listing the affected cell indices. Useful for topics that rely on live services that may be temporarily unavailable, or as a short-lived escape hatch for flaky external tools. Prefer fixing the underlying cause (e.g., recording an HTTP cassette) over leaving this enabled permanently. |
| `http-replay` | `"true"`/`"yes"`/`"1"` or `"false"`/`"no"`/`"0"` (case-insensitive; default `false`). Opts the topic in to HTTP replay: live `requests` / `httpx` / `urllib3` / `aiohttp` calls are intercepted by `vcrpy` and recorded to a cassette file next to the source (or under a sibling `_cassettes/` directory), then replayed on subsequent builds. The replay record mode is chosen at build time via `--http-replay=<replay\|once\|new-episodes\|refresh\|disabled>` or `CLM_HTTP_REPLAY_MODE`; CI defaults to strict `replay` mode, local builds default to `new-episodes` (replay recorded requests and append any new ones to the cassette, so an edited notebook that issues additional requests does not fail the build). Use `--http-replay=once` to make a local build fail loudly on unrecorded requests. Cassettes are version-controlled alongside source but excluded from student output. Requires the `[replay]` extra (`pip install -e .[replay]`). |
| `author` | Override the course-level author for this topic |
| `prog-lang` | Override the course-level programming language for this topic |
| `module` | Optional module-directory binding for this single topic, overriding the section's `module=` default. Use sparingly — usually the section-level `module=` is enough. |

The `prog-lang` attribute is useful for `.md` notebook files where the language
cannot be inferred from the file extension. For `.md` files, the programming
language is resolved in this order: topic `prog-lang` attribute → course
`<prog-lang>` element → `python` (default).

A `<topic>` element may also contain one or more `<include>` elements (see
the [`<include>` element](#include) reference below) to splice a shared
source directory or file under the topic at build time.

**Topic ID forms.** A topic's ID can be supplied in two equivalent ways:

```xml
<!-- Attribute form (preferred when the topic has children) -->
<topic id="gradio_intro">
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
</topic>

<!-- Legacy text-content form (fine for childless topics) -->
<topic>introduction</topic>
```

If a `<topic>` carries `<include>` or any other child elements, you **must**
use the `id=` attribute. The text-content form is unsafe with children:
XML parsers assign text appearing *after* a child element to that child's
tail rather than to the topic, so an author who writes the ID after a child
will silently end up with an empty topic ID. CLM rejects this case with a
clear error. Specifying the ID via both the `id=` attribute *and* text
content is also a hard error — pick one form per topic.

### `<author>` (Optional)

Author name displayed in notebook slide headers. Defaults to `Dr. Matthias Hölzl`.

```xml
<author>Dr. Jane Smith</author>
```

Individual topics can override the course-level author with an `author` attribute:

```xml
<topic author="Prof. Bob Expert">special_topic</topic>
```

### `<organization>` (Optional)

Bilingual organization name displayed in notebook slide headers.
Defaults to `Coding-Akademie München` (de) / `Coding-Academy Munich` (en).

```xml
<organization>
    <de>Meine Akademie</de>
    <en>My Academy</en>
</organization>
```

## Optional Elements

### `<github>`

Git repository configuration for output directories. Used by `clm git` commands
to manage git repositories in output directories.

```xml
<github>
    <repository-base>https://github.com/Coding-Academy-Munich</repository-base>
    <remote-path>Coding-Academy-Munich</remote-path>  <!-- Optional -->
    <remote-template>git@github.com-cam:Coding-Academy-Munich/{repo}.git</remote-template>
    <include-speaker>true</include-speaker>  <!-- Optional, default: false -->
</github>
```

This element configures repository URLs derived from the top-level
`<project-slug>`, language, and output target name:

| Element | Required | Description |
|---------|----------|-------------|
| `<repository-base>` | Yes | Git hosting base URL (e.g., `https://github.com/Org` or `https://gitlab.example.com`) |
| `<remote-path>` | No | Path between base URL and repo name (e.g., GitLab group). Supports nested paths. |
| `<remote-template>` | No | URL template for git remotes (see below) |
| `<include-speaker>` | No | Whether to create repos for speaker targets (default: `false`) |

> **Deprecation note**: `<project-slug>` was previously placed inside `<github>`.
> That location still works but is deprecated. Use the top-level `<project-slug>`
> element instead.

URL derivation (requires both `<project-slug>` and `<repository-base>`):
- Without `<remote-path>`: `{repository-base}/{project-slug}-{lang}[-{suffix}]`
- With `<remote-path>`: `{repository-base}/{remote-path}/{project-slug}-{lang}[-{suffix}]`
- Public/first target: `https://github.com/Org/ml-course-de`
- Other targets: `https://github.com/Org/ml-course-de-completed`
- Speaker targets (if enabled): `https://github.com/Org/ml-course-de-speaker`

**Per-target remote path**: Each `<output-target>` can override `<remote-path>`
to push to a different group/namespace. When an output target has its own
`<remote-path>`, the target suffix is suppressed (the path already disambiguates):

```xml
<github>
    <repository-base>https://gitlab.example.com</repository-base>
    <remote-path>editors</remote-path>
</github>
<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <remote-path>students</remote-path>  <!-- Overrides course-level -->
    </output-target>
</output-targets>
```

This produces `https://gitlab.example.com/students/ml-course-de` (no `-students`
suffix).

**Remote URL template**: The `<remote-template>` element (or the `CLM_GIT__REMOTE_TEMPLATE`
environment variable) lets you override the URL pattern. Available placeholders:
`{repository_base}`, `{remote_path}`, `{repo}`, `{slug}`, `{lang}`, `{suffix}`.
Example:

```bash
CLM_GIT__REMOTE_TEMPLATE="git@github.com-cam:Coding-Academy-Munich/{repo}.git"
```

The environment variable takes precedence over the XML element.
The `CLM_GIT__REMOTE_PATH` environment variable overrides the course-level
`<remote-path>` (but not per-target overrides).

### `<dir-groups>`

Copy additional directories (e.g., code examples) to output.

```xml
<dir-groups>
    <dir-group>
        <name>Examples</name>
        <path>code/examples</path>
    </dir-group>
</dir-groups>
```

#### `<dir-group>` attributes and children

| Element/Attribute | Required | Description |
|-------------------|----------|-------------|
| `<name>` | Yes | Output directory name (bilingual or simple; empty = course root) |
| `<path>` | Yes | Source path relative to course root |
| `<subdirs>` | No | Specific subdirectories to copy (omit to copy all) |
| `include-root-files` | No | Also copy files from base path (default: `false`) |
| `recursive` | No | Recurse into subdirectories (default: `true`) |

#### Selective subdirectories with root files

```xml
<dir-group include-root-files="true">
    <name>Code/Completed</name>
    <path>code/completed</path>
    <subdirs>
        <subdir>Example_1</subdir>
        <subdir>Example_3</subdir>
    </subdirs>
</dir-group>
```

#### Non-recursive (root files only)

```xml
<dir-group include-root-files="true" recursive="false">
    <name>Code</name>
    <path>code</path>
</dir-group>
```

### `<include>`

Splice a shared source directory or file from elsewhere in the course root
into a topic at build time. Use it when several topics need the same
sibling-importable Python package (e.g., a shared `simple_chatbot/`
package next to each notebook that imports from it) and you don't want
to keep byte-identical physical copies in sync by hand.

Allowed as a child of `<topic>` or `<section>`. There is no top-level
`<includes>` wrapper — `<include>` elements are direct children of the
parent element.

```xml
<sections>
    <section>
        <name><de>Gradio</de><en>Gradio</en></name>
        <!-- Section-level default: every topic in this section
             inherits the include unless it overrides it. -->
        <include source="examples/SimpleChatbot/src/simple_chatbot"
                 as="simple_chatbot"/>
        <topics>
            <topic>gradio_intro</topic>
            <topic>gradio_deep_dive</topic>
            <topic>
                custom
                <!-- Topic-level override: same target path, different source.
                     The topic ID is text content before the child element. -->
                <include source="examples/CustomChatbot/src/simple_chatbot"
                         as="simple_chatbot"/>
            </topic>
        </topics>
    </section>
</sections>
```

#### Attributes

| Attribute | Required | Description |
|-----------|----------|-------------|
| `source` | Yes | Course-root-relative path to the file or directory to include. Forward-slash and backslash separators are both accepted and normalized internally. `..` segments and absolute paths are rejected. |
| `as` | No | Relative target path inside the topic directory. Defaults to the basename of `source`. Must be relative with no `..` segments. Acts as the per-topic deduplication key — two includes on the same parent cannot share the same `as` (parse-time error). |
| `optional` | No | `"true"` or `"false"` (default: `false`, case-insensitive). When `true`, a missing source is silently skipped instead of producing an `include_source_missing` error during validation/build. |

#### How includes are resolved

- **Virtual splice at build time.** The build pipeline does not modify
  your source tree. Each included file is read from its canonical
  location and presented to workers as if it lived under
  `<topic-dir>/<as>`. Outputs are written under the topic's output
  directory exactly as if the files had been copied there.
- **Section inheritance.** Every `<include>` on a `<section>` is
  inherited as a default by every child `<topic>`. A topic overrides an
  inherited include by declaring its own `<include>` with the same `as`
  target. Topics keep section-level includes whose `as` targets they do
  not touch, and may add new ones.
- **Local files win.** If a real file already exists at
  `<topic-dir>/<as>`, the local file wins and the included file is
  shadowed. The build emits an `include_shadowed_by_local` warning and
  `validate` surfaces the same condition as `include_shadowed`.
  *Exception* (CLM {version}+): when the shadowing file was materialized
  by `clm sync-includes` — i.e., the topic's `.clm-include` ledger lists
  a matching `as_path` + `source` entry — the warning is suppressed, since
  the on-disk copy *is* the include's authorized output rather than an
  ad-hoc override.
- **Collisions inside one parent.** Two `<include>` elements on the same
  `<topic>` or the same `<section>` with the same `as` target are a
  spec error reported at parse time (you cannot pick two sources for
  the same target).

#### Materializing includes for local notebook execution

Includes are virtual at build time, but running a notebook directly in
VS Code or JupyterLab needs the included package to physically sit next
to the notebook (Python's import system reads from the filesystem).
`clm sync-includes` materializes every include declared in a spec — as
a copy, symlink, or hardlink — and tracks what it created in a
per-topic `.clm-include` ledger so it can clean up safely later. See
`clm info commands` for the command reference.

#### Validation findings emitted for includes

| Category | Severity | When it fires |
|----------|----------|---------------|
| `include_source_missing` | Error | `source` path does not exist under the course root and the include is not `optional`. |
| `include_shadowed` | Warning | A real file/directory already occupies `<topic-dir>/<as>` — the local copy will be used, the include will not. Suppressed when the topic's `.clm-include` ledger lists a matching entry (sync-includes-managed materialization). |
| `include_source_is_topic_dir` | Warning | `source` resolves into another `slides/.../topic_*` directory. Allowed but fragile; prefer pulling from a stable location like `examples/`. |
| `include_dependencies` | Info | One per unique include source — lists the source's `pyproject.toml` `[project] dependencies` so authors can confirm the worker environment satisfies them. |
| `include_section_inheritance` | Info | One per section-level include — lists every topic that inherits it and any topic that overrides it with a different source. |

Run `clm validate` to surface all of the above; `include_source_missing`
also surfaces at build time.

### `<output-targets>`

Define multiple output directories with content filters.

```xml
<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <kinds>
            <kind>code-along</kind>
        </kinds>
        <formats>
            <format>html</format>
            <format>notebook</format>
        </formats>
    </output-target>
    <output-target name="solutions">
        <path>./output/solutions</path>
        <kinds>
            <kind>completed</kind>
        </kinds>
    </output-target>
</output-targets>
```

#### `<output-target>` children

| Element | Required | Description |
|---------|----------|-------------|
| `name` (attr) | Yes | Unique target identifier |
| `<path>` | Yes | Output directory (relative or absolute) |
| `<kinds>` | No | Filter by output kind (omit for all) |
| `<formats>` | No | Filter by output format (omit for all) |
| `<languages>` | No | Filter by language (omit for all) |
| `<remote-path>` | No | Override course-level remote path for this target (e.g., GitLab group) |

#### Output kinds

| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared |
| `completed` | Notebooks with all solutions |
| `trainer` | Notebooks for trainers teaching the course: keeps speaker `notes` cells but strips `voiceover` cells. This is the variant most trainers want. |
| `recording` | Notebooks for the trainer recording the course on video: keeps both `notes` and `voiceover` cells. The voiceover cells contain the polished narration read on camera. |
| `partial` | Completed outside workshop ranges, code-along inside them. A workshop range starts at a markdown cell tagged `workshop` **or** a slide-start (`slide`/`subslide`) markdown cell whose `slide_id` starts with `workshop-`, and ends — exclusively — at the next `end-workshop` markdown cell, the next workshop opener, or end-of-notebook. Intended as a student follow-along artifact: demonstrations remain worked out, workshops stay blank. Without an explicit `end-workshop` tag, the workshop runs to end-of-notebook (legacy behaviour). Use `end-workshop` (on the heading that starts the next non-workshop section) to mark the end of a workshop in the middle of a deck. `clm validate` (since {version}) warns when a `# Workshop` heading has no workshop scope covering it, so a missing opener doesn't silently render the exercise cells. |

> **Deprecation note**: The previous single `speaker` kind has been split into
> `trainer` (notes only) and `recording` (notes + voiceover). `speaker` is
> still accepted as an input alias for one release and resolves to
> `recording`; spec parsing emits a deprecation warning and rewrites the kind
> internally. Update existing course specs to use `trainer` and/or
> `recording` explicitly. Output paths for these kinds gain a kind subdir
> (`speaker/.../Recording/...` and `speaker/.../Trainer/...`); the legacy
> "no kind subdir under the speaker toplevel" layout is no longer produced.

#### Output formats

| Format | Description |
|--------|-------------|
| `html` | HTML slides |
| `notebook` | Jupyter notebook (.ipynb) |
| `code` | Extracted source code (only for `completed` kind) |
| `jupyterlite` | Deployable in-browser JupyterLite site — **strictly opt-in**, see `<jupyterlite>` below and run `clm info jupyterlite` for the full guide. Phase 1 recognizes the format but does not yet emit output; the site builder ships in a later release. |

**Default format set when `<formats>` is omitted**: `html`, `notebook`, `code`
only. `jupyterlite` is **never** included implicitly — a target must list
`<format>jupyterlite</format>` explicitly to opt in.

#### Languages

Valid values: `de` (German), `en` (English).

### Default behavior

If no `<output-targets>` element is present, all default kinds, default formats
(`html`, `notebook`, `code`), and languages are generated to `--output-dir`
(CLI) or `./output` (default). Opt-in formats like `jupyterlite` are **not**
enabled by the default target.

### `<jupyterlite>`

Configuration for the `jupyterlite` output format. May appear at course level
(child of `<course>`) as a default for every target that opts in, **and/or**
at target level (child of `<output-target>`) to override the course-level
block wholesale for that one target. See `clm info jupyterlite` for field
reference and authoring guidance.

```xml
<jupyterlite>
    <kernel>xeus-python</kernel>  <!-- or "pyodide" -->
    <wheels>
        <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
    </wheels>
    <environment>jupyterlite/environment.yml</environment>  <!-- xeus only, optional -->
    <launcher>true</launcher>      <!-- default: true -->
    <app-archive>offline</app-archive>  <!-- "offline" (default) or "cdn" -->
</jupyterlite>
```

**Merge semantics**: a target-level `<jupyterlite>` block replaces the
course-level block wholesale — fields are not merged. To reuse most
course-level settings, copy the full block into the target.

**Validation**: any target that lists `<format>jupyterlite</format>` must have
an effective `<jupyterlite>` block at either level; otherwise the build fails
with a pointer to this topic.

## Complete Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name>
        <de>Python Programmierung</de>
        <en>Python Programming</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Lernen Sie Python von Grund auf</de>
        <en>Learn Python from scratch</en>
    </description>
    <certificate>
        <de>Zertifikat fuer Python Programmierung</de>
        <en>Certificate for Python Programming</en>
    </certificate>
    <project-slug>python-course</project-slug>
    <github>
        <repository-base>https://github.com/example</repository-base>
    </github>
    <sections>
        <section>
            <name>
                <de>Grundlagen</de>
                <en>Fundamentals</en>
            </name>
            <topics>
                <topic>introduction</topic>
                <topic>variables</topic>
            </topics>
        </section>
    </sections>
    <dir-groups>
        <dir-group>
            <name>Examples</name>
            <path>code/examples</path>
        </dir-group>
    </dir-groups>
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
</course>
```

## Cross-references between notebooks (CLM {version}+)

Link from one notebook to another using a custom Markdown link scheme.
Because CLM renames notebooks at build time, you cannot hand-write a stable
relative link — instead reference the **topic id** (the same identifier you
use in `<topic>id</topic>`, i.e. the directory/file name with its
`topic_NNN_` / `slides_NNN_` / `project_NNN_` numeric prefix stripped):

```markdown
See the [Functions workshop](clm:functions_workshop) for exercises.
```

At build time CLM rewrites the `clm:` href to the correct relative path to
the **same variant** (language, kind, format) of the target notebook. The
link **text** is never touched. An unbuilt notebook opened directly in
VS Code / JupyterLab simply shows a dead `clm:` link rather than corrupt
Markdown.

### Reference grammar

```
clm:<topic-id>                  link to a topic (single-notebook topics)
clm:<topic-id>/<notebook-stem>  disambiguate a directory topic with several decks
```

- `<topic-id>` is the path-derived topic id.
- `<notebook-stem>` is a slide file's stem (e.g. `slides_part_b`), used only
  when a directory topic contains more than one slide notebook.

### Per-format behavior

| Format        | Behavior                                                    |
|---------------|-------------------------------------------------------------|
| `html`        | rewritten to the target `.html` (working hyperlink)         |
| `notebook`    | rewritten to the target `.ipynb` (works in Jupyter/VS Code) |
| `code`        | link **dropped** — only the link text is rendered           |
| `jupyterlite` | **deferred** — link text left verbatim (no rewrite yet)     |

### v1 limitations

- **No anchors / sub-section targets.** `clm:topic#heading` is accepted but
  the `#heading` part is ignored (resolves to the whole notebook).
- **Multi-notebook topics resolve deterministically.** If a directory topic
  contains several slide notebooks and you do not add a `/notebook-stem`
  disambiguator, CLM resolves to the first deck (lowest slot number) and
  emits a `cross_reference_ambiguous` warning. Add a disambiguator to be
  explicit.
- **No cross-course references.**

### Missing-target policy

A `clm:` reference whose target topic is not included in the build (wrong id,
or excluded by `--only-sections` / `enabled="false"`) is reported as
`cross_reference_target_missing`. By default this is a **hard error** under
`--http-replay=replay` (the CI-strict default) and a **warning + dropped
link** otherwise. Override with `clm build --fail-on-missing-xref /
--no-fail-on-missing-xref` or the `CLM_FAIL_ON_MISSING_XREF` environment
variable (mirrors `--fail-on-error`). `clm validate` also reports
missing and ambiguous cross-references without a full build.

## Validation

CLM validates spec files before building and reports:
- Missing required elements
- Duplicate target names or paths
- Invalid kind/format/language values
- Cross-reference targets that are missing (`cross_reference_target_missing`)
  or ambiguous (`cross_reference_ambiguous`)
