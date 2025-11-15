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

## Environment Variables

Some CLX behavior can be configured via environment variables.

### PLANTUML_JAR

**Description**: Path to PlantUML JAR file
**Required**: For PlantUML diagram conversion
**Example**:

```bash
export PLANTUML_JAR="/usr/local/share/plantuml-1.2024.6.jar"
```

### DRAWIO_EXECUTABLE

**Description**: Path to Draw.io executable
**Required**: For Draw.io diagram conversion
**Example**:

```bash
# Linux
export DRAWIO_EXECUTABLE="/usr/bin/drawio"

# macOS
export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"

# Windows
set DRAWIO_EXECUTABLE="C:\Program Files\draw.io\draw.io.exe"
```

### LOG_LEVEL

**Description**: Logging verbosity
**Default**: `INFO`
**Options**: `DEBUG`, `INFO`, `WARNING`, `ERROR`
**Example**:

```bash
export LOG_LEVEL=DEBUG
clx build course.yaml
```

### DB_PATH

**Description**: Path to SQLite job queue database
**Default**: `clx_jobs.db`
**Example**:

```bash
export DB_PATH="/tmp/clx_jobs.db"
```

### CLX_SKIP_DOWNLOADS

**Description**: Skip downloads in sessionStart hook (for restricted environments)
**Default**: Not set (downloads enabled)
**Example**:

```bash
export CLX_SKIP_DOWNLOADS=1
```

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
