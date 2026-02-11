# Configuration Guide

This guide covers configuration options for CLM courses and the CLM application.

## Course Specification Files

CLM uses XML-based course specification files. For complete documentation of the course spec format, see the **[Spec File Reference](spec-file-reference.md)**.

### Quick Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name>
        <de>Python Kurs</de>
        <en>Python Course</en>
    </name>
    <prog-lang>python</prog-lang>
    <description>
        <de>Beschreibung</de>
        <en>Description</en>
    </description>
    <sections>
        <section>
            <name>
                <de>Woche 1</de>
                <en>Week 1</en>
            </name>
            <topics>
                <topic>introduction</topic>
            </topics>
        </section>
    </sections>
</course>
```

### Key Course Elements

| Element | Required | Description |
|---------|----------|-------------|
| `<name>` | Yes | Bilingual course name (`<de>`, `<en>`) |
| `<prog-lang>` | Yes | Programming language (python, cpp, csharp, java, typescript) |
| `<description>` | Yes | Bilingual description |
| `<sections>` | Yes | Section and topic definitions |
| `<output-targets>` | No | Multiple output directories (see below) |
| `<dir-groups>` | No | Additional directories to copy |

### Multiple Output Targets (New in v0.4.x)

Define separate output directories with selective content:

```xml
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
```

**Output Kinds**:
| Kind | Description |
|------|-------------|
| `code-along` | Notebooks with code cells cleared |
| `completed` | Notebooks with all solutions |
| `speaker` | Notebooks with speaker notes |

**Output Formats**:
| Format | Description |
|--------|-------------|
| `html` | HTML slides |
| `notebook` | Jupyter notebook (.ipynb) |
| `code` | Extracted source code (only for `completed`) |

For complete XML reference, see **[Spec File Reference](spec-file-reference.md)**.

---

## CLM Application Configuration

CLM can be configured using configuration files or environment variables.

### Configuration Files

CLM looks for configuration files in these locations (priority order):

1. **Project config**: `.clm/config.toml` or `clm.toml` (current directory)
2. **User config**: `~/.config/clm/config.toml` (Linux/macOS) or `%APPDATA%\clm\config.toml` (Windows)
3. **System config**: `/etc/clm/config.toml` (Linux/Unix only)

### Creating a Configuration File

```bash
# Create user-level config
clm config init

# Create project-level config
clm config init --location=project

# View current configuration
clm config show

# Find configuration files
clm config locate
```

### Configuration File Format

```toml
# CLM Configuration File (config.toml)

[paths]
cache_db_path = "clm_cache.db"
jobs_db_path = "clm_jobs.db"

[external_tools]
plantuml_jar = "/usr/local/share/plantuml-1.2024.6.jar"
drawio_executable = "/usr/local/bin/drawio"

[logging]
log_level = "INFO"
enable_test_logging = false

[logging.testing]
e2e_progress_interval = 10
e2e_long_job_threshold = 60
```

---

## Environment Variables

Environment variables override configuration files.

### Paths

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_PATHS__CACHE_DB_PATH` | Cache database path | `clm_cache.db` |
| `CLM_PATHS__JOBS_DB_PATH` | Job queue database path | `clm_jobs.db` |

### External Tools

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |

**Platform-specific Draw.io paths**:
```bash
# Linux
export DRAWIO_EXECUTABLE="/usr/bin/drawio"

# macOS
export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"

# Windows
set DRAWIO_EXECUTABLE="C:\Program Files\draw.io\draw.io.exe"
```

### Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_LOGGING__LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `CLM_LOGGING__ENABLE_TEST_LOGGING` | Enable logging during tests | `false` |

### Performance

| Variable | Description | Default |
|----------|-------------|---------|
| `CLM_MAX_CONCURRENCY` | Max concurrent operations | `50` |
| `CLM_MAX_WORKER_STARTUP_CONCURRENCY` | Max concurrent worker starts | `10` |

---

## Directory Structure

### Recommended Course Structure

```
my-course/
├── course.xml                  # Course specification
├── slides/
│   └── module_001/
│       └── topic_001_intro/
│           ├── slides.py       # Notebook source
│           └── diagram.puml    # PlantUML diagram
├── code/
│   └── examples/               # Code examples (dir-group)
└── output/                     # Generated files (gitignored)
    ├── students/
    └── solutions/
```

### File Naming Conventions

**Source Files**:
- Python notebooks: `*.py` (converted to .ipynb)
- PlantUML: `*.puml` or `*.plantuml`
- Draw.io: `*.drawio`

**Output Files**:
- Notebooks: `*.ipynb`
- HTML slides: `*.html`
- Extracted code: `*.py`, `*.cpp`, etc.
- Images: `*.png`, `*.svg`

---

## Best Practices

### Version Control

```bash
# .gitignore
output/
*.ipynb
clm_cache.db
clm_jobs.db
```

Commit source files (`.py`, `.puml`, `.drawio`), not generated files.

### Configuration Priority

Settings are loaded in this order (highest to lowest):

1. Environment variables
2. Project configuration file
3. User configuration file
4. System configuration file
5. Default values

---

## See Also

- **[Spec File Reference](spec-file-reference.md)** - Complete course XML format
- **[Quick Start Guide](quick-start.md)** - Building your first course
- **[Troubleshooting](troubleshooting.md)** - Common issues
- **[Installation](installation.md)** - Setup and dependencies
