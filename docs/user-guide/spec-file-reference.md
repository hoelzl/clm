# Course Specification File Reference

This document describes the XML format for CLM course specification files.

## Overview

Course specification files define the structure and configuration of a CLM course. They use XML format and are typically named `course.xml` or similar.

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
</course>
```

## Elements Reference

### `<course>` (Root Element)

The root element containing all course configuration.

### `<name>` (Required)

Bilingual course name with `<de>` and `<en>` child elements.

```xml
<name>
    <de>Python Programmierung</de>
    <en>Python Programming</en>
</name>
```

### `<prog-lang>` (Required)

Programming language for the course notebooks.

**Valid values**: `python`, `cpp`, `csharp`, `java`, `typescript`

```xml
<prog-lang>python</prog-lang>
```

### `<description>` (Required)

Bilingual course description.

```xml
<description>
    <de>Ein umfassender Kurs über Python</de>
    <en>A comprehensive course about Python</en>
</description>
```

### `<certificate>` (Required)

Bilingual certificate text.

```xml
<certificate>
    <de>Zertifikatstext auf Deutsch</de>
    <en>Certificate text in English</en>
</certificate>
```

### `<project-slug>` (Recommended)

Base name used for output directories and repository names. Output directories are
named `{project-slug}-{lang}` (e.g., `ml-course-de`, `ml-course-en`).

```xml
<project-slug>ml-course</project-slug>
```

If omitted, CLM falls back to a sanitized version of the course name with a language
suffix (e.g., `Python Programming-de`). Using `<project-slug>` is recommended for
clean, predictable directory names.

> **Deprecation note**: In earlier versions, `<project-slug>` was placed inside the
> `<github>` element. That location still works but is deprecated and will log a
> warning. Move it to the top level of `<course>` for forward compatibility.

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

### `<github>` (Optional)

Git repository configuration for course output directories. Used by `clm git` commands
to manage git repositories in output directories.

```xml
<github>
    <repository-base>https://github.com/Coding-Academy-Munich</repository-base>
    <remote-path>Coding-Academy-Munich</remote-path>  <!-- Optional -->
    <remote-template>git@github.com-cam:Coding-Academy-Munich/{repo}.git</remote-template>
    <include-speaker>true</include-speaker>  <!-- Optional, default: false -->
</github>
```

This element configures repository URLs derived from the top-level `<project-slug>`,
language, and output target name:

| Element | Required | Description |
|---------|----------|-------------|
| `<repository-base>` | Yes | Git hosting base URL (e.g., `https://github.com/Org` or `https://gitlab.example.com`) |
| `<remote-path>` | No | Path between base URL and repo name (e.g., GitLab group/namespace). Supports nested paths like `group/subgroup`. |
| `<remote-template>` | No | URL template for git remotes (see below) |
| `<include-speaker>` | No | Whether to create repos for speaker targets (default: `false`) |

> **Deprecation note**: `<project-slug>` was previously placed inside `<github>`.
> That location still works but is deprecated. Use the top-level `<project-slug>`
> element instead.

**URL derivation** (requires both `<project-slug>` and `<repository-base>`):
- Without `<remote-path>`: `{repository-base}/{project-slug}-{lang}[-{suffix}]`
- With `<remote-path>`: `{repository-base}/{remote-path}/{project-slug}-{lang}[-{suffix}]`
- Public/first target: `https://github.com/Org/ml-course-de`
- Other explicit targets: `https://github.com/Org/ml-course-de-completed`
- Speaker targets (if enabled): `https://github.com/Org/ml-course-de-speaker`

**Per-target remote path**: Each `<output-target>` can include a `<remote-path>`
element to override the course-level `<remote-path>`. This enables pushing
different targets to different GitLab groups for access control:

```xml
<github>
    <repository-base>https://gitlab.example.com</repository-base>
    <remote-path>azav-editors</remote-path>
</github>
<output-targets>
    <output-target name="students">
        <path>./output/students</path>
        <kinds><kind>code-along</kind></kinds>
        <remote-path>azav-students</remote-path>
    </output-target>
    <output-target name="teachers">
        <path>./output/teachers</path>
        <kinds><kind>code-along</kind><kind>completed</kind></kinds>
        <remote-path>azav-teachers</remote-path>
    </output-target>
    <output-target name="editors">
        <path>./output/editors</path>
        <!-- Inherits remote-path "azav-editors" from <github> -->
    </output-target>
</output-targets>
```

When an output target has its own `<remote-path>` (different from the course-level
one), the target suffix is suppressed — the group already disambiguates:
- `https://gitlab.example.com/azav-students/ml-course-de` (no `-students` suffix)
- `https://gitlab.example.com/azav-teachers/ml-course-de` (no `-teachers` suffix)
- `https://gitlab.example.com/azav-editors/ml-course-de-editors` (inherits, keeps suffix)

**Remote URL template**: The `<remote-template>` element (or the `CLM_GIT__REMOTE_TEMPLATE`
environment variable) lets you override the URL pattern used for git remotes. This is useful
when you need SSH access, a custom host alias, or a different git hosting provider.

Available placeholders:

| Placeholder | Example | Description |
|-------------|---------|-------------|
| `{repository_base}` | `https://github.com/Org` | The `<repository-base>` value |
| `{remote_path}` | `azav-editors` | Effective remote path (per-target or course-level) |
| `{repo}` | `ml-course-de-completed` | Full derived repository name |
| `{slug}` | `ml-course` | Project slug only |
| `{lang}` | `de` | Language code |
| `{suffix}` | `-completed` | Target suffix (includes leading dash, empty for default) |

Examples:

```bash
# SSH with custom host alias (e.g., in .env file or environment)
CLM_GIT__REMOTE_TEMPLATE="git@github.com-cam:Coding-Academy-Munich/{repo}.git"

# SSH with GitLab and remote_path
CLM_GIT__REMOTE_TEMPLATE="git@gitlab.example.com:{remote_path}/{repo}.git"

# HTTPS with .git suffix
CLM_GIT__REMOTE_TEMPLATE="{repository_base}/{repo}.git"
```

The environment variable takes precedence over the XML `<remote-template>` element,
allowing per-machine overrides without modifying the shared course spec.

The `CLM_GIT__REMOTE_PATH` environment variable overrides the course-level
`<remote-path>` but does not override per-target `<remote-path>` values.

**Git commands**:

```bash
clm git init course.xml      # Initialize git repos in output directories
clm git status course.xml    # Show status of all repos
clm git sync course.xml -m "Update"  # Commit and push all repos
clm git reset course.xml     # Reset to remote (for conflict resolution)
```

### `<sections>` (Required)

Contains section definitions. Each section groups related topics.

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
    <section>
        <name>
            <de>Woche 2</de>
            <en>Week 2</en>
        </name>
        <topics>
            <topic>advanced_topics</topic>
        </topics>
    </section>
</sections>
```

#### `<section>`

Defines a course section (e.g., a week or module).

- `<name>`: Bilingual section name
- `<topics>`: List of topic IDs in this section

Optional `<section>` attributes:

| Attribute | Description |
|-----------|-------------|
| `enabled` | `"true"` (default) or `"false"`, case-insensitive. A disabled section is dropped from the parsed spec entirely, so `clm build`, `clm outline`, `clm validate`, and all MCP tools ignore it without needing code changes. Disabled sections may omit `<topics>` or reference topic IDs that do not yet exist — they are never built or validated, which lets a full roadmap spec live as a single file instead of carrying a separate `-build.xml` subset. |
| `id` | Optional stable identifier for the section (e.g. `id="w03"`). Recommended for courses that are frequently filtered with `clm build --only-sections`, because IDs are stable under reordering and renaming. |
| `module` | Optional module-directory binding (e.g. `module="module_545_ml_azav_cohort_2026_04"`). When set, every `<topic>` inside this section resolves only against that module — duplicate topic IDs in other modules are ignored. This is the supported mechanism for cohort archives or course variants that share topic IDs with a live module. The value is the literal directory name under `slides/`. Per-topic `module=` overrides the section default for individual topics. |
| `http-replay` | Default `http-replay` value applied to every `<topic>` in the section that does not itself carry an `http-replay` attribute. Same values as the topic-level attribute. |

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

Building such a spec (`clm build course.xml`) silently skips the
disabled section and all its unresolved topic references. Use
`clm outline course.xml --include-disabled` to see the full roadmap
including disabled sections, and `clm validate course.xml
--include-disabled` to validate disabled sections' topics with a
`(disabled)` suffix on each finding.

#### `<topic>`

References a topic by its ID. The ID corresponds to the topic directory name (without the numeric prefix).

For example, if your directory is `slides/module_001/topic_100_introduction/`, the topic ID is `introduction`.

Optional `<topic>` attributes:

| Attribute | Description |
|-----------|-------------|
| `id` | Topic ID as an attribute (e.g. `<topic id="introduction"/>`) — equivalent to the text-content form `<topic>introduction</topic>`. **Required form when the `<topic>` carries `<include>` or other child elements** — see "Topic ID forms" below. Specifying both the attribute *and* text content is a hard error. |
| `html` | If set, skip HTML generation for this topic |
| `evaluate` | `"true"` (default) or `"false"`. With `evaluate="no"`, the notebook is rendered to all configured output formats *without spawning a kernel* — cells appear with empty outputs. Useful for topics that depend on live services, GPU hardware, or long-running training runs. |
| `skip-errors` | `"true"` or `"false"` (default `false`). When set, cell execution errors do not abort HTML generation — cell outputs containing errors are cleared and a processing warning is emitted. Prefer fixing the root cause (e.g., recording an HTTP cassette) over leaving this on. |
| `http-replay` | `"true"` or `"false"` (default `false`). Opts the topic in to HTTP replay via `vcrpy`: live `requests` / `httpx` / `urllib3` / `aiohttp` calls are intercepted and recorded to a cassette next to the source, then replayed on subsequent builds. Mode is chosen at build time via `--http-replay=<replay\|once\|new-episodes\|refresh\|disabled>` or `CLM_HTTP_REPLAY_MODE`. Requires the `[replay]` extra. |
| `author` | Override the course-level author for this topic |
| `prog-lang` | Override the course-level programming language for this topic |
| `module` | Optional module-directory binding for this single topic, overriding the section's `module=` default. Use sparingly. |

The `prog-lang` attribute is useful for `.md` notebook files where the language cannot be
inferred from the file extension. For `.md` files, the programming language is resolved in
this order: topic `prog-lang` attribute → course `<prog-lang>` element → `python` (default).

Example:

```xml
<topic prog-lang="java">capstone_project/phase_01</topic>
```

#### Topic ID forms

A topic's ID can be supplied in two equivalent ways:

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
use the `id=` attribute. The text-content form is unsafe with children: XML
parsers assign text appearing *after* a child element to that child's tail
rather than to the topic, so an author who writes the ID after a child
would silently end up with an empty topic ID. CLM rejects this case with a
clear error. Specifying the ID via both the `id=` attribute *and* text
content is also a hard error — pick one form per topic.

### `<dir-groups>` (Optional)

Defines directory groups to copy additional files (like code examples) to output.

```xml
<dir-groups>
    <dir-group>
        <name>Code Examples</name>
        <path>code/examples</path>
        <subdirs>
            <subdir>example_1</subdir>
            <subdir>example_2</subdir>
        </subdirs>
    </dir-group>
</dir-groups>
```

#### `<dir-group>` Element

Each directory group specifies a set of directories to copy to the output.

| Element/Attribute | Required | Description |
|-------------------|----------|-------------|
| `<name>` | Yes | Bilingual or simple name for the output directory |
| `<path>` | Yes | Source path relative to course root |
| `<subdirs>` | No | List of subdirectories to copy (if omitted, copies entire path) |
| `include-root-files` | No | Attribute to include files from the base path (default: `false`) |
| `recursive` | No | Attribute to control recursive directory copying (default: `true`) |

#### Basic Usage

Copy an entire directory:

```xml
<dir-group>
    <name>Examples</name>
    <path>code/examples</path>
</dir-group>
```

#### Selective Subdirectories

Copy only specific subdirectories:

```xml
<dir-group>
    <name>Code/Solutions</name>
    <path>code/solutions</path>
    <subdirs>
        <subdir>Project_1</subdir>
        <subdir>Project_2</subdir>
    </subdirs>
</dir-group>
```

#### Including Root Files with Subdirectories

When using `<subdirs>`, files directly in the base path are not copied by default. Use the `include-root-files` attribute to also copy these files:

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

Given this directory structure:
```
code/completed/
├── CMakeLists.txt      # Root file
├── README.md           # Root file
├── Example_1/
│   └── main.cpp
├── Example_2/          # Not in subdirs, will be skipped
│   └── util.cpp
└── Example_3/
    └── helper.cpp
```

The output will contain:
```
Code/Completed/
├── CMakeLists.txt      # Copied because include-root-files="true"
├── README.md           # Copied because include-root-files="true"
├── Example_1/
│   └── main.cpp
└── Example_3/
    └── helper.cpp
```

**Note**: The `include-root-files` attribute only copies **files** from the base path, not directories. Subdirectories are controlled exclusively by the `<subdirs>` element.

#### Copying Only Root Files (Non-Recursive)

Use `recursive="false"` to copy only files from a directory without copying any subdirectories. Combined with `include-root-files="true"`, this allows copying only the root-level files:

```xml
<dir-group include-root-files="true" recursive="false">
    <name>Code</name>
    <path>code</path>
</dir-group>
```

Given this directory structure:
```
code/
├── CMakeLists.txt      # Root file
├── CMakePresets.json   # Root file
├── completed/          # Subdirectory (not copied)
│   └── main.cpp
├── external/           # Subdirectory (not copied)
│   └── lib.cpp
└── examples/           # Subdirectory (not copied)
    └── demo.cpp
```

The output will contain only:
```
Code/
├── CMakeLists.txt
└── CMakePresets.json
```

You can also combine root files with specific subdirectories, copying only files (not nested subdirectories) from each:

```xml
<dir-group include-root-files="true" recursive="false">
    <name>Code</name>
    <path>code</path>
    <subdirs>
        <subdir>examples</subdir>
    </subdirs>
</dir-group>
```

This copies:
- Root files from `code/` (CMakeLists.txt, CMakePresets.json)
- Only files directly in `code/examples/` (not nested subdirectories within examples)

#### Empty Name for Root Output

Use an empty `<name>` element to copy files directly into the course root output:

```xml
<dir-group>
    <name/>
    <path>root-files</path>
</dir-group>
```

---

## Shared-Source Includes (`<include>`)

Some topics share the same supporting code — typically a small Python
package that several notebooks import from (`from simple_chatbot import
BudgetGuard`). The straightforward way to handle this is to keep one
canonical copy of the source under `examples/` (or anywhere else in the
course root) and declare an `<include>` on each topic that needs it.
At build time CLM splices the source into the topic *virtually* — your
working tree is untouched, but the source files show up under
`<topic>/<as>` from the build's perspective, get copied/executed exactly
as if they had been there all along, and are written to the topic's
output directory.

`<include>` is allowed as a direct child of `<topic>` or `<section>`.
There is no top-level wrapper.

### Basic Usage

```xml
<topic id="gradio_intro">
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
</topic>
```

Use the `id=` attribute form on the `<topic>` whenever it carries an
`<include>` (or any other child element) — see [Topic ID forms](#topic-id-forms)
above for why the legacy text-before-children form is unsafe with children.

This splices the canonical `examples/SimpleChatbot/src/simple_chatbot/`
directory under the topic as `simple_chatbot/`, so the notebook's
`from simple_chatbot.budget_guard import BudgetGuard` resolves at build
time without keeping a physical copy in `slides/.../topic_040_gradio_intro/`.

### Section-Level Inheritance

When several topics in the same section need the same include, declare
it once on the section. Every direct child topic inherits it as a
default:

```xml
<section>
    <name><de>Gradio</de><en>Gradio</en></name>
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
    <topics>
        <topic>gradio_intro</topic>       <!-- inherits the include -->
        <topic>gradio_deep_dive</topic>   <!-- inherits the include -->
    </topics>
</section>
```

A topic can override an inherited include by declaring its own
`<include>` with the same `as` target:

```xml
<section>
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
    <topics>
        <topic>gradio_intro</topic>
        <topic id="custom">
            <!-- Same target, different source: overrides the section default. -->
            <include source="examples/CustomChatbot/src/simple_chatbot"
                     as="simple_chatbot"/>
        </topic>
    </topics>
</section>
```

Topics keep section-level includes whose `as` targets they don't touch
and may add their own additional includes alongside them.

### `<include>` Attributes

| Attribute | Required | Description |
|-----------|----------|-------------|
| `source` | Yes | Course-root-relative path to the file or directory to include. Both forward-slash and backslash separators are accepted; `..` segments and absolute paths are rejected. |
| `as` | No | Relative target path inside the topic directory. Defaults to the basename of `source`. Must be relative with no `..` segments. Acts as the per-topic dedup key. |
| `optional` | No | `"true"` to silently skip if the source is missing. Defaults to `"false"` — a missing source is a build/validation error. |

### Local Files Always Win

If a file at `<topic>/<as>` already exists on disk in your working tree,
that local file wins and the include is shadowed. The build emits an
`include_shadowed_by_local` warning and `clm validate` reports the
same as `include_shadowed`. Delete the local copy (or change `as`) to
let the include take effect.

This rule lets you iterate on a single topic without disturbing the
shared source: drop a local copy in for the duration of an edit, then
remove it when you're done.

**Exception — sync-includes materializations.** When the shadowing file
was created by `clm sync-includes` — i.e., the topic's `.clm-include`
ledger lists a matching `as_path` + `source` entry — the warning is
suppressed, since the on-disk copy *is* the include's authorized output
rather than an ad-hoc override. Unauthorized shadowings (no ledger or
stale entry pointing at a different source) still warn.

### Validation

Run `clm validate course.xml` to surface any include problems
before a build:

| Category | Severity | Meaning |
|----------|----------|---------|
| `include_source_missing` | Error | `source` doesn't exist and the include isn't `optional`. |
| `include_shadowed` | Warning | A local file at the target shadows the include. Suppressed when the topic's `.clm-include` ledger lists a matching entry (sync-includes-managed materialization). |
| `include_source_is_topic_dir` | Warning | `source` points into another `slides/.../topic_*` — allowed, but fragile. |
| `include_dependencies` | Info | Lists the source's `pyproject.toml` `[project] dependencies` so you can confirm the worker environment satisfies them. |
| `include_section_inheritance` | Info | Lists every topic that inherits each section-level include and any topic that overrides it. |

### Materializing Includes for Local Notebook Execution

A virtual splice is enough for `clm build`, but running a notebook
directly in VS Code or JupyterLab needs the included package to
physically sit next to the slide file (Python imports read from the
filesystem, not from the build's in-memory file map). Run
`clm sync-includes course.xml` to materialize every declared include on
disk:

```bash
clm sync-includes course.xml                       # default: copy
clm sync-includes course.xml --mode=symlink        # if you have admin / Developer Mode
clm sync-includes course.xml --remove              # undo (only paths we created)
clm sync-includes course.xml --print-gitignore     # print suggested .gitignore lines
clm sync-includes course.xml --dry-run             # preview without writing
```

Each topic that received a materialization gets a small JSON ledger
written at `<topic>/.clm-include`. `--remove` consults this ledger and
deletes only paths it created — untracked files in the topic
directory are never touched.

#### Keeping materialized includes out of git

`clm sync-includes` does not edit `.gitignore` files; that's the
author's file, not CLM's. To exclude materialized include targets and
ledgers from version control, run `--print-gitignore` once and append
the output to your course-root `.gitignore`:

```bash
clm sync-includes course.xml --print-gitignore >> .gitignore
```

The output is deterministic and paste-safe — re-running it produces
the same patterns, so an accidental double-append is harmless. The
universal `**/.clm-include` pattern is always emitted (so a fresh
checkout can bootstrap before the first materialization); each
declared `<include>` adds one `slides/**/<as>/` line anchored under
`slides/` so the canonical source under `examples/` stays tracked.

Modes:

- `copy` (default) — most portable; survives filesystem moves and works
  identically on every platform.
- `symlink` — fastest, no drift between canonical source and materialized
  copy, but requires admin or Developer Mode on Windows. Falls back to
  `copy` per-include on `OSError` so you're never blocked.
- `hardlink` — per-file links inside the same filesystem; falls back to
  `copy` per-file when the filesystem refuses.

If a target path already exists outside the ledger (e.g., the legacy
hand-maintained copy), `sync-includes` leaves it untouched and warns —
delete the legacy copy first to let the materialization take over.

### Migration Recipe (Replacing Hand-Copied Sources)

If your repo currently has byte-identical copies of a package under
several topic directories, migrate them like this:

1. Choose a canonical location (typically under `examples/`) and ensure
   the copies in `slides/.../topic_*/` are still byte-identical (use
   `diff -r`).
2. Declare `<include>` on each topic that needs the package, pointing
   at the canonical location.
3. Remove the physical copies from `slides/.../topic_*/`.
4. Run `clm sync-includes course.xml` so local notebook execution still
   works. Optionally `--mode=symlink` if you have admin / Developer
   Mode.
5. Run `clm build course.xml` and compare against a pre-migration build
   — output should be identical because the splice is transparent to
   the build pipeline.

---

## Output Targets (Multiple Output Directories)

**New in CLM 0.4.x**: Course specs can define multiple output targets, each with different content filters. This enables scenarios like:

- Releasing code-along materials immediately while withholding solutions
- Creating separate instructor packages with speaker notes
- Generating language-specific distributions

### Basic Usage

Add an `<output-targets>` element with one or more `<output-target>` children:

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

    <output-target name="instructor">
        <path>./output/instructor</path>
        <kinds>
            <kind>speaker</kind>
        </kinds>
        <languages>
            <language>en</language>
        </languages>
    </output-target>
</output-targets>
```

### `<output-target>` Element

Each output target specifies:

| Element | Required | Description |
|---------|----------|-------------|
| `name` (attribute) | Yes | Unique identifier for this target |
| `<path>` | Yes | Output directory (relative to course root or absolute) |
| `<kinds>` | No | Filter by output kind (omit for all) |
| `<formats>` | No | Filter by output format (omit for all) |
| `<languages>` | No | Filter by language (omit for all) |
| `<remote-path>` | No | Override course-level `<remote-path>` for this target (e.g., GitLab group) |

### Output Kinds

**Valid values for `<kind>`**:

| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared for student exercises |
| `completed` | Notebooks with all solutions included |
| `partial` | Completed outside workshop ranges, code-along inside them. A workshop range starts at a markdown cell tagged `workshop` and ends — exclusively — at the next `end-workshop` cell, the next `workshop` heading, or end-of-notebook. Intended as a student follow-along artifact: demonstrations remain worked out, workshops stay blank. |
| `trainer` | Notebooks for trainers teaching the course: keeps speaker `notes` cells but strips `voiceover` cells. The variant most trainers want. |
| `recording` | Notebooks for the trainer recording the course on video: keeps both `notes` and `voiceover` cells. The voiceover cells contain the polished narration read on camera. |

> **Deprecation note**: The previous single `speaker` kind has been split
> into `trainer` (notes only) and `recording` (notes + voiceover).
> `speaker` is still accepted as an input alias for one release and resolves
> to `recording`; spec parsing emits a deprecation warning and rewrites the
> kind internally. Update existing specs to use `trainer` and/or `recording`
> explicitly. Output paths for these kinds gain a kind subdir (e.g.
> `speaker/.../Recording/...` and `speaker/.../Trainer/...`); the legacy
> "no kind subdir under the speaker toplevel" layout is no longer produced.
> See `clm info migration` for the full migration guide.

### Output Formats

**Valid values for `<format>`**:

| Format | Description |
|--------|-------------|
| `html` | HTML slides (executed for `completed`/`trainer`/`recording`, cleared for `code-along`) |
| `notebook` | Jupyter notebook files (.ipynb) |
| `code` | Extracted source code files (e.g., .py for Python) — only generated for the `completed` kind |
| `jupyterlite` | Deployable in-browser JupyterLite site — **strictly opt-in**. Run `clm info jupyterlite` for the full guide. A target must list `<format>jupyterlite</format>` explicitly to opt in. |

**Default format set when `<formats>` is omitted**: `html`, `notebook`, `code`
only. `jupyterlite` is **never** included implicitly.

### Languages

**Valid values for `<language>`**:

| Language | Description |
|----------|-------------|
| `de` | German |
| `en` | English |

### Examples

#### Delayed Solution Release

Students get code-along materials immediately; solutions are released later:

```xml
<output-targets>
    <!-- Immediately available to students -->
    <output-target name="student-materials">
        <path>./dist/students</path>
        <kinds>
            <kind>code-along</kind>
        </kinds>
        <formats>
            <format>html</format>
            <format>notebook</format>
        </formats>
    </output-target>

    <!-- Released after assignment deadline -->
    <output-target name="solutions">
        <path>./dist/solutions</path>
        <kinds>
            <kind>completed</kind>
        </kinds>
        <formats>
            <format>html</format>
            <format>notebook</format>
            <format>code</format>
        </formats>
    </output-target>
</output-targets>
```

#### Language-Specific Distributions

Separate outputs per language:

```xml
<output-targets>
    <output-target name="german">
        <path>./output/de</path>
        <languages>
            <language>de</language>
        </languages>
    </output-target>

    <output-target name="english">
        <path>./output/en</path>
        <languages>
            <language>en</language>
        </languages>
    </output-target>
</output-targets>
```

#### Instructor Package

Private materials for instructors only:

```xml
<output-targets>
    <!-- Public student materials -->
    <output-target name="public">
        <path>./public</path>
        <kinds>
            <kind>code-along</kind>
            <kind>completed</kind>
        </kinds>
    </output-target>

    <!-- Private instructor materials -->
    <output-target name="instructor">
        <path>./private/instructor</path>
        <kinds>
            <kind>speaker</kind>
        </kinds>
    </output-target>
</output-targets>
```

### Default Behavior

If no `<output-targets>` element is present, CLM uses legacy behavior:
- All kinds, formats, and languages are generated
- Output goes to `--output-dir` (CLI) or `./output` (default)

### CLI Integration

**List defined targets**:
```bash
clm targets course.xml
```

Output:
```
Output Targets:

  student-materials
    Path:      ./dist/students
    Kinds:     code-along
    Formats:   html, notebook
    Languages: (all)

  solutions
    Path:      ./dist/solutions
    Kinds:     completed
    Formats:   html, notebook, code
    Languages: (all)
```

**Build specific targets**:
```bash
# Build only the 'students' target
clm build course.xml --targets students

# Build multiple targets
clm build course.xml --targets students,solutions
```

**Override targets with CLI**:
```bash
# Ignore spec targets, use single output directory
clm build course.xml --output-dir ./custom-output
```

When `--output-dir` is specified, it overrides all targets defined in the spec file.

---

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
        <de>Zertifikat für Python Programmierung</de>
        <en>Certificate for Python Programming</en>
    </certificate>
    <project-slug>python-course</project-slug>
    <github>
        <repository-base>https://github.com/example</repository-base>
        <include-speaker>true</include-speaker>
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
                <topic>control_flow</topic>
            </topics>
        </section>
        <section>
            <name>
                <de>Fortgeschritten</de>
                <en>Advanced</en>
            </name>
            <topics>
                <topic>functions</topic>
                <topic>classes</topic>
            </topics>
        </section>
    </sections>

    <dir-groups>
        <dir-group>
            <name>Examples</name>
            <path>code/examples</path>
        </dir-group>
        <dir-group include-root-files="true">
            <name>Solutions</name>
            <path>code/solutions</path>
            <subdirs>
                <subdir>project_1</subdir>
                <subdir>project_2</subdir>
            </subdirs>
        </dir-group>
    </dir-groups>

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
        <output-target name="instructor">
            <path>./output/instructor</path>
            <kinds>
                <kind>speaker</kind>
            </kinds>
        </output-target>
    </output-targets>
</course>
```

---

## Validation

CLM validates course spec files and reports errors:

- Missing required elements
- Duplicate target names
- Duplicate target paths
- Invalid kind/format/language values

Validation errors are reported before the build starts.

---

## See Also

- [Configuration Guide](configuration.md) - Application configuration
- [Quick Start Guide](quick-start.md) - Building your first course
- [Troubleshooting](troubleshooting.md) - Common issues
