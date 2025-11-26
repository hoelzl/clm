# Course Specification File Reference

This document describes the XML format for CLX course specification files.

## Overview

Course specification files define the structure and configuration of a CLX course. They use XML format and are typically named `course.xml` or similar.

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
    <github>
        <de>https://github.com/user/course-de</de>
        <en>https://github.com/user/course-en</en>
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

### `<github>` (Required)

GitHub repository URLs for each language.

```xml
<github>
    <de>https://github.com/user/course-de</de>
    <en>https://github.com/user/course-en</en>
</github>
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

#### `<topic>`

References a topic by its ID. The ID corresponds to the topic directory name (without the numeric prefix).

For example, if your directory is `slides/module_001/topic_100_introduction/`, the topic ID is `introduction`.

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

---

## Output Targets (Multiple Output Directories)

**New in CLX 0.4.x**: Course specs can define multiple output targets, each with different content filters. This enables scenarios like:

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

### Output Kinds

**Valid values for `<kind>`**:

| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared for student exercises |
| `completed` | Notebooks with all solutions included |
| `speaker` | Notebooks with speaker notes and all content |

### Output Formats

**Valid values for `<format>`**:

| Format | Description |
|--------|-------------|
| `html` | HTML slides (executed for speaker/completed, cleared for code-along) |
| `notebook` | Jupyter notebook files (.ipynb) |
| `code` | Extracted source code files (e.g., .py for Python) |

**Note**: The `code` format is only generated for the `completed` kind.

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

If no `<output-targets>` element is present, CLX uses legacy behavior:
- All kinds, formats, and languages are generated
- Output goes to `--output-dir` (CLI) or `./output` (default)

### CLI Integration

**List defined targets**:
```bash
clx targets course.xml
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
clx build course.xml --targets students

# Build multiple targets
clx build course.xml --targets students,solutions
```

**Override targets with CLI**:
```bash
# Ignore spec targets, use single output directory
clx build course.xml --output-dir ./custom-output
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
    <github>
        <de>https://github.com/example/python-course-de</de>
        <en>https://github.com/example/python-course-en</en>
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

CLX validates course spec files and reports errors:

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
