# Configuration Guide

This guide covers all configuration options for CLX courses.

## Course Configuration File

The `course.yaml` file defines your course structure and settings.

### Basic Configuration

```yaml
name: "My Course"              # Course title
language: en                    # Content language (en, de)
prog_lang: python               # Default programming language
output_dir: "./output"          # Output directory
```

### Full Configuration Example

```yaml
# Course metadata
name: "Advanced Python Programming"
language: en
prog_lang: python

# Output configuration
output_dir: "./dist"

# Sections (optional, can also be auto-discovered)
sections:
  - name: "Introduction"
    dir: "section_001"
    topics:
      - name: "Getting Started"
        files:
          - "intro.py"
          - "diagram.puml"

  - name: "Advanced Topics"
    dir: "section_002"
    topics:
      - name: "Decorators"
        files:
          - "decorators.py"

# Output formats (optional)
outputs:
  speaker: true          # Generate speaker version
  participant: true      # Generate participant version

# Template directory (optional)
template_dir: "./templates"
```

## Configuration Options

### name

**Type**: String
**Required**: Yes
**Description**: The title of your course

```yaml
name: "Introduction to Machine Learning"
```

### language

**Type**: String
**Required**: Yes
**Default**: `en`
**Options**: `en` (English), `de` (German)
**Description**: The natural language for your course content

```yaml
language: de  # German course
```

This affects:
- Output filenames
- Template selection
- Language-specific text generation

### prog_lang

**Type**: String
**Required**: Yes
**Default**: `python`
**Options**: `python`, `cpp`, `csharp`, `java`, `typescript`
**Description**: The default programming language for notebooks

```yaml
prog_lang: cpp  # C++ course
```

**Supported Languages**:
- `python` - Python 3
- `cpp` - C++ (requires xeus-cling kernel)
- `csharp` - C# (requires .NET Interactive)
- `java` - Java (requires IJava kernel)
- `typescript` - TypeScript (requires tslab)

### output_dir

**Type**: String
**Required**: Yes
**Default**: `./output`
**Description**: Directory where generated files will be placed

```yaml
output_dir: "./dist"
```

**Output Structure**:
```
dist/
├── speaker/       # Full version with solutions
└── participant/   # Version without solutions (optional)
```

### sections

**Type**: List
**Required**: No (auto-discovered if not specified)
**Description**: Explicit section and topic definitions

```yaml
sections:
  - name: "Section 1"
    dir: "section_001"
    topics:
      - name: "Topic 1"
        files:
          - "topic_001.py"
          - "diagram.puml"

  - name: "Section 2"
    dir: "section_002"
    topics:
      - name: "Topic 2"
        files:
          - "topic_002.py"
```

**Auto-Discovery**: If not specified, CLX will automatically discover sections and topics based on directory structure:

```
course/
├── course.yaml
├── section_001/
│   ├── topic_001.py
│   └── topic_002.py
└── section_002/
    └── topic_003.py
```

### outputs

**Type**: Object
**Required**: No
**Default**: Both speaker and participant enabled
**Description**: Control which output versions are generated

```yaml
outputs:
  speaker: true       # Generate speaker version
  participant: false  # Skip participant version
```

### template_dir

**Type**: String
**Required**: No
**Default**: Built-in templates
**Description**: Directory containing custom templates

```yaml
template_dir: "./custom_templates"
```

Custom templates allow you to control the appearance of generated HTML, slides, etc.

## CLX Application Configuration

CLX can be configured using configuration files or environment variables. Configuration files provide a convenient way to manage settings without modifying environment variables.

### Configuration Files

CLX looks for configuration files in multiple locations (in priority order):

1. **Project config**: `.clx/config.toml` or `clx.toml` in current directory (highest priority)
2. **User config**: `~/.config/clx/config.toml` (Linux/macOS) or `%APPDATA%\clx\config.toml` (Windows)
3. **System config**: `/etc/clx/config.toml` (Linux/Unix only, lowest priority)

### Creating a Configuration File

Generate an example configuration file:

```bash
# Create user-level config (recommended)
clx config init

# Create project-level config
clx config init --location=project

# Overwrite existing config
clx config init --force
```

### Configuration File Format

CLX uses TOML format for configuration files. Here's a complete example:

```toml
# CLX Configuration File

[paths]
# Path to the SQLite database for job queue
db_path = "clx_cache.db"

# Workspace path for workers (optional, usually derived from output directory)
workspace_path = ""

[external_tools]
# Path to PlantUML JAR file
plantuml_jar = "/usr/local/share/plantuml-1.2024.6.jar"

# Path to Draw.io executable
drawio_executable = "/usr/local/bin/drawio"

[logging]
# Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL
log_level = "INFO"

# Enable logging for tests
enable_test_logging = false

[logging.testing]
# Progress update interval for E2E tests (seconds)
e2e_progress_interval = 10

# Long job warning threshold (seconds)
e2e_long_job_threshold = 60

# Show worker details in E2E tests
e2e_show_worker_details = false

[jupyter]
# Jinja2 line statement prefix for template processing
jinja_line_statement_prefix = "# j2"

# Jinja2 templates path
jinja_templates_path = "templates"

# Log cell processing in notebook processor
log_cell_processing = false

[workers]
# Worker configuration (usually not needed in config file)
worker_type = ""
worker_id = ""
use_sqlite_queue = true
```

### Managing Configuration

**View current configuration**:
```bash
clx config show
```

**Show configuration file locations**:
```bash
clx config locate
```

### Configuration Priority

Settings are loaded in this order (highest to lowest priority):

1. **Environment variables** (highest priority)
2. **Project configuration file** (`.clx/config.toml`)
3. **User configuration file** (`~/.config/clx/config.toml`)
4. **System configuration file** (`/etc/clx/config.toml`)
5. **Default values** (lowest priority)

Environment variables always override configuration files.

## Environment Variables

CLX settings can also be configured via environment variables. Environment variables have higher priority than configuration files.

### Environment Variable Naming

**CLX-prefixed variables** (for paths, logging, etc.):
- Format: `CLX_<SECTION>__<KEY>`
- Nested settings use double underscores (`__`)

**Examples**:
```bash
export CLX_PATHS__DB_PATH="/tmp/clx_cache.db"
export CLX_LOGGING__LOG_LEVEL="DEBUG"
export CLX_LOGGING__TESTING__E2E_PROGRESS_INTERVAL="5"
```

**Legacy variables** (for backward compatibility):
- No `CLX_` prefix
- Used for external tools and Jupyter settings

**Examples**:
```bash
export PLANTUML_JAR="/usr/local/share/plantuml-1.2024.6.jar"
export DRAWIO_EXECUTABLE="/usr/local/bin/drawio"
export JINJA_LINE_STATEMENT_PREFIX="# custom"
```

### Available Environment Variables

#### Paths Configuration

**CLX_PATHS__DB_PATH**
- **Description**: Path to SQLite job queue database
- **Default**: `clx_cache.db`
- **Example**: `export CLX_PATHS__DB_PATH="/tmp/clx_jobs.db"`

**CLX_PATHS__WORKSPACE_PATH**
- **Description**: Workspace path for workers
- **Default**: Derived from output directory
- **Example**: `export CLX_PATHS__WORKSPACE_PATH="/tmp/workspace"`

#### External Tools

**PLANTUML_JAR**
- **Description**: Path to PlantUML JAR file
- **Required**: For PlantUML diagram conversion
- **Example**: `export PLANTUML_JAR="/usr/local/share/plantuml-1.2024.6.jar"`

**DRAWIO_EXECUTABLE**
- **Description**: Path to Draw.io executable
- **Required**: For Draw.io diagram conversion
- **Examples**:
  ```bash
  # Linux
  export DRAWIO_EXECUTABLE="/usr/bin/drawio"

  # macOS
  export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"

  # Windows
  set DRAWIO_EXECUTABLE="C:\Program Files\draw.io\draw.io.exe"
  ```

#### Logging Configuration

**CLX_LOGGING__LOG_LEVEL**
- **Description**: Logging verbosity
- **Default**: `INFO`
- **Options**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Example**: `export CLX_LOGGING__LOG_LEVEL="DEBUG"`

**CLX_LOGGING__ENABLE_TEST_LOGGING**
- **Description**: Enable logging during tests
- **Default**: `false`
- **Example**: `export CLX_LOGGING__ENABLE_TEST_LOGGING="true"`

**CLX_LOGGING__TESTING__E2E_PROGRESS_INTERVAL**
- **Description**: Progress update interval for E2E tests (seconds)
- **Default**: `10`
- **Example**: `export CLX_LOGGING__TESTING__E2E_PROGRESS_INTERVAL="5"`

**CLX_LOGGING__TESTING__E2E_LONG_JOB_THRESHOLD**
- **Description**: Long job warning threshold (seconds)
- **Default**: `60`
- **Example**: `export CLX_LOGGING__TESTING__E2E_LONG_JOB_THRESHOLD="30"`

**CLX_LOGGING__TESTING__E2E_SHOW_WORKER_DETAILS**
- **Description**: Show worker details in E2E tests
- **Default**: `false`
- **Example**: `export CLX_LOGGING__TESTING__E2E_SHOW_WORKER_DETAILS="true"`

#### Jupyter Configuration

**JINJA_LINE_STATEMENT_PREFIX**
- **Description**: Jinja2 line statement prefix for template processing
- **Default**: `# j2`
- **Example**: `export JINJA_LINE_STATEMENT_PREFIX="## custom"`

**JINJA_TEMPLATES_PATH**
- **Description**: Jinja2 templates path
- **Default**: `templates`
- **Example**: `export JINJA_TEMPLATES_PATH="/custom/templates"`

**LOG_CELL_PROCESSING**
- **Description**: Log cell processing in notebook processor
- **Default**: `false`
- **Example**: `export LOG_CELL_PROCESSING="true"`

#### Worker Configuration

**WORKER_TYPE**
- **Description**: Worker type (notebook, plantuml, drawio)
- **Note**: Usually set automatically by worker executors
- **Example**: `export WORKER_TYPE="notebook"`

**WORKER_ID**
- **Description**: Unique worker identifier
- **Note**: Usually set automatically by worker executors
- **Example**: `export WORKER_ID="worker-1"`

**USE_SQLITE_QUEUE**
- **Description**: Use SQLite queue for job orchestration
- **Default**: `true`
- **Example**: `export USE_SQLITE_QUEUE="true"`

### Configuration Examples

#### Development Environment

Create `.clx/config.toml` in your project:

```toml
[logging]
log_level = "DEBUG"
enable_test_logging = true

[logging.testing]
e2e_progress_interval = 2
e2e_show_worker_details = true
```

#### Production Environment

Create `~/.config/clx/config.toml`:

```toml
[paths]
db_path = "/var/lib/clx/jobs.db"

[external_tools]
plantuml_jar = "/usr/local/share/plantuml-1.2024.6.jar"
drawio_executable = "/usr/local/bin/drawio"

[logging]
log_level = "WARNING"
```

#### Docker Environment

Use environment variables in `docker-compose.yaml`:

```yaml
services:
  clx:
    environment:
      - CLX_PATHS__DB_PATH=/data/jobs.db
      - CLX_LOGGING__LOG_LEVEL=INFO
      - PLANTUML_JAR=/usr/local/share/plantuml.jar
      - DRAWIO_EXECUTABLE=/usr/local/bin/drawio
```

### Troubleshooting Configuration

**Check current configuration**:
```bash
clx config show
```

**Find configuration files**:
```bash
clx config locate
```

**Test environment variable override**:
```bash
CLX_LOGGING__LOG_LEVEL=DEBUG clx config show
```

**Verify configuration priority**:
1. Create project config with one setting
2. Set environment variable with different value
3. Run `clx config show` to see environment variable takes priority

## File Naming Conventions

### Source Files

**Python Notebooks**:
- Format: `topic_NNN.py` (speaker version)
- Format: `topic_NNN_speaker.py` (explicit speaker version)
- Format: `topic_NNN_participant.py` (participant version)

**PlantUML Diagrams**:
- Format: `*.puml` or `*.plantuml`
- Output: `*.png` (or `*.svg` if configured)

**Draw.io Diagrams**:
- Format: `*.drawio`
- Output: `*.png` (or `*.svg`, `*.pdf` if configured)

### Output Files

**Notebooks**:
- `topic_NNN.ipynb` - Jupyter notebook
- `topic_NNN.html` - HTML version
- `topic_NNN.slides.html` - Reveal.js slides (if configured)
- `topic_NNN.pdf` - PDF version (if configured)

**Diagrams**:
- `diagram_name.png` - PNG image
- `diagram_name.svg` - SVG image (if configured)

## Directory Structure

### Recommended Structure

```
my-course/
├── course.yaml                  # Course configuration
├── section_001/                 # Section directory
│   ├── topic_001.py            # Python notebook source
│   ├── topic_002.py
│   ├── diagram.puml            # PlantUML diagram
│   └── flowchart.drawio        # Draw.io diagram
├── section_002/
│   └── topic_003.py
├── templates/                   # Custom templates (optional)
│   ├── notebook_template.html
│   └── slides_template.html
└── output/                      # Generated files (gitignored)
    ├── speaker/
    └── participant/
```

### Section Naming

**Convention**: `section_NNN` where NNN is a zero-padded number (001, 002, etc.)

**Why**: Ensures correct alphabetical sorting

### Topic Naming

**Convention**: `topic_NNN.py` where NNN is a zero-padded number

**Example**:
```
section_001/
├── topic_001.py
├── topic_002.py
└── topic_003.py
```

## Advanced Configuration

### Custom Output Formats

Specify which notebook formats to generate:

```yaml
notebook_formats:
  - notebook   # .ipynb
  - html       # .html
  - slides     # .slides.html
  - python     # .py (extracted code)
```

### Multiple Languages

Generate course in multiple languages:

```yaml
languages:
  - en
  - de
```

CLX will generate separate output directories for each language.

### Template Customization

Create custom templates in `templates/`:

```
templates/
├── notebook_template.html.j2
├── slides_template.html.j2
└── styles.css
```

Reference in `course.yaml`:

```yaml
template_dir: "./templates"
```

## Best Practices

### 1. Use Version Control

```bash
# .gitignore
output/
*.ipynb
clx_cache.db
clx_jobs.db
```

Commit source files (`.py`, `.puml`, `.drawio`), not generated files.

### 2. Organize Content

- One topic per file
- Keep sections focused
- Use descriptive names

### 3. Consistent Naming

- Zero-padded numbers (001, not 1)
- Lowercase filenames
- Underscores, not spaces

### 4. Templates in Version Control

If you customize templates, commit them:

```bash
git add templates/
git commit -m "Add custom course templates"
```

### 5. Document Dependencies

Create a `README.md` in your course repo documenting:
- Required CLX version
- Programming language versions
- Custom kernel requirements

## Troubleshooting Configuration

### Course Not Found

**Problem**: `Error: course.yaml not found`

**Solution**: Ensure you're in the directory with `course.yaml`, or specify path:

```bash
clx build /path/to/course.yaml
```

### Invalid YAML Syntax

**Problem**: `Error parsing course.yaml`

**Solution**: Check YAML syntax:
- Proper indentation (spaces, not tabs)
- Colons followed by space
- Quoted strings with special characters

**Validate YAML**:
```bash
python -c "import yaml; yaml.safe_load(open('course.yaml'))"
```

### Sections Not Discovered

**Problem**: CLX doesn't find your sections

**Solution**: Check directory naming:
- Directories should match pattern `section_NNN`
- Or explicitly define in `course.yaml`

### Templates Not Found

**Problem**: Custom templates not applied

**Solution**: Check `template_dir` path is correct and relative to `course.yaml`

## Examples

### Minimal Configuration

```yaml
name: "Quick Course"
language: en
prog_lang: python
output_dir: "./output"
```

### Full-Featured Configuration

```yaml
name: "Comprehensive Python Course"
language: en
prog_lang: python
output_dir: "./dist"

sections:
  - name: "Basics"
    dir: "section_001"
  - name: "Advanced"
    dir: "section_002"

outputs:
  speaker: true
  participant: true

template_dir: "./templates"

notebook_formats:
  - notebook
  - html
  - slides
```

### Multi-Language Course

```yaml
name: "Programming Fundamentals"
languages:
  - en
  - de
prog_lang: python
output_dir: "./output"
```

## See Also

- [Quick Start Guide](quick-start.md) - Building your first course
- [Troubleshooting](troubleshooting.md) - Common issues
- [Installation](installation.md) - Setup and dependencies
