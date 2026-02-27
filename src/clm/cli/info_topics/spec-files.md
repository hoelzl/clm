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
    <github>
        <project-slug>course-name</project-slug>
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

## Optional Elements

### `<github>`

Git repository configuration for output directories.

```xml
<github>
    <project-slug>ml-course</project-slug>
    <repository-base>https://github.com/Coding-Academy-Munich</repository-base>
    <include-speaker>true</include-speaker>  <!-- Optional, default: false -->
</github>
```

URL derivation pattern: `{repository-base}/{project-slug}-{lang}[-{target-suffix}]`

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

#### Output kinds

| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared |
| `completed` | Notebooks with all solutions |
| `speaker` | Notebooks with speaker notes |

#### Output formats

| Format | Description |
|--------|-------------|
| `html` | HTML slides |
| `notebook` | Jupyter notebook (.ipynb) |
| `code` | Extracted source code (only for `completed` kind) |

#### Languages

Valid values: `de` (German), `en` (English).

### Default behavior

If no `<output-targets>` element is present, all kinds, formats, and languages
are generated to `--output-dir` (CLI) or `./output` (default).

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
    <github>
        <project-slug>python-course</project-slug>
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
