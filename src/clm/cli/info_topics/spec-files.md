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
| `enabled` | `"true"` (default) or `"false"`, case-insensitive. A disabled section is dropped from the parsed spec entirely, so `clm build`, `clm outline`, `clm validate-spec`, and all MCP tools ignore it without needing code changes. Disabled sections may omit `<topics>` or reference topic IDs that do not yet exist — they are never built or validated, which lets a full roadmap spec live as a single file instead of carrying a separate `-build.xml` subset. |
| `id` | Optional stable identifier for the section (e.g. `id="w03"`). Recommended for courses that are frequently filtered, because IDs are stable under reordering and renaming. |

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

Optional `<topic>` attributes:

| Attribute | Description |
|-----------|-------------|
| `html` | If set, skip HTML generation for this topic |
| `skip-errors` | `"true"`/`"yes"`/`"1"` or `"false"`/`"no"`/`"0"` (case-insensitive; default `false`). When set, cell execution errors do not abort HTML generation. Cells whose outputs contain an error are cleared, and a processing warning is emitted listing the affected cell indices. Useful for topics that rely on live services that may be temporarily unavailable, or as a short-lived escape hatch for flaky external tools. Prefer fixing the underlying cause (e.g., recording an HTTP cassette) over leaving this enabled permanently. |
| `http-replay` | `"true"`/`"yes"`/`"1"` or `"false"`/`"no"`/`"0"` (case-insensitive; default `false`). Opts the topic in to HTTP replay: live `requests` / `httpx` / `urllib3` / `aiohttp` calls are intercepted by `vcrpy` and recorded to a cassette file next to the source (or under a sibling `_cassettes/` directory), then replayed on subsequent builds. The replay record mode is chosen at build time via `--http-replay=<replay\|once\|refresh\|disabled>` or `CLM_HTTP_REPLAY_MODE`; CI defaults to strict `replay` mode, local builds default to `once`. Cassettes are version-controlled alongside source but excluded from student output. Requires the `[replay]` extra (`pip install -e .[replay]`). |
| `author` | Override the course-level author for this topic |
| `prog-lang` | Override the course-level programming language for this topic |

The `prog-lang` attribute is useful for `.md` notebook files where the language
cannot be inferred from the file extension. For `.md` files, the programming
language is resolved in this order: topic `prog-lang` attribute → course
`<prog-lang>` element → `python` (default).

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
| `speaker` | Notebooks with speaker notes |
| `partial` | Completed up to the first `workshop` heading, code-along from there to end-of-notebook. Intended as a student follow-along artifact: demonstrations remain worked out, workshops stay blank. |

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

## Validation

CLM validates spec files before building and reports:
- Missing required elements
- Duplicate target names or paths
- Invalid kind/format/language values
