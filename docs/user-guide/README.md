# CLM User Guide

Welcome to the CLM user guide! This documentation is for users who want to use CLM to process course content.

## What is CLM?

CLM (Coding-Academy Lecture Manager) is a course content processing system that converts educational materials into multiple output formats.

**What CLM Can Do**:
- ✅ Execute Jupyter notebooks and convert them to HTML slides and Jupyter notebooks
- ✅ Support multiple programming languages (Python, C++, C#, Java, TypeScript)
- ✅ Convert PlantUML diagrams to images (PNG, SVG)
- ✅ Convert Draw.io diagrams to images (PNG, SVG, PDF)
- ✅ Generate both speaker and participant versions of content
- ✅ Support multiple languages (English, German)
- ✅ Watch for file changes and auto-rebuild

## Quick Links

- **[Installation](installation.md)** - Install CLM
- **[Quick Start](quick-start.md)** - Get started in 5 minutes
- **[Spec File Reference](spec-file-reference.md)** - Course specification XML format
- **[Configuration](configuration.md)** - Configure courses and options
- **[Troubleshooting](troubleshooting.md)** - Common issues and solutions

## Typical Workflow

1. **Create course structure**:
   ```
   my-course/
   ├── course.xml           # Course specification
   ├── section_001/
   │   ├── topic_001.py     # Python notebook source
   │   └── diagram.puml     # PlantUML diagram
   └── section_002/
       └── topic_002.py
   ```

2. **Configure course** (`course.xml`):
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <course>
       <name>
           <de>Mein Kurs</de>
           <en>My Programming Course</en>
       </name>
       <prog-lang>python</prog-lang>
       <description>
           <de>Beschreibung</de>
           <en>Description</en>
       </description>
       <sections>
           <!-- Section definitions -->
       </sections>
   </course>
   ```

3. **Build course**:
   ```bash
   clm build course.xml
   ```

4. **View outputs**:
   ```
   output/
   ├── speaker/
   │   └── section_001/
   │       ├── topic_001.html
   │       ├── topic_001.ipynb
   │       └── diagram.png
   └── participant/
       └── section_001/
           ├── topic_001.html
           └── diagram.png
   ```

## Key Features

### Multi-Language Support

Execute and convert notebooks in multiple programming languages:
- **Python** - Standard Jupyter Python kernel
- **C++** - Using xeus-cling kernel
- **C#** - Using .NET Interactive kernel
- **Java** - Using IJava kernel
- **TypeScript** - Using tslab kernel

### Output Formats

Convert notebooks to various formats:
- **HTML** - HTML slides (Reveal.js presentations)
- **Jupyter Notebook** (`.ipynb`) - Interactive notebooks
- **Code** - Extracted source code files (e.g., `.py` for Python)

### Diagram Support

Convert diagrams to images:
- **PlantUML** - UML diagrams, sequence diagrams, etc.
- **Draw.io** - Visual diagrams and flowcharts

### Incremental Builds

CLM intelligently caches results:
- Only processes files that have changed
- Content-based hashing detects changes
- Significantly faster incremental builds

### Watch Mode

Automatically rebuild when files change:
```bash
clm build course.xml --watch
```

Perfect for iterative content development!

## System Requirements

### Minimum Requirements

- **Python**: 3.11, 3.12, 3.13, or 3.14
- **Operating System**: Linux, macOS, or Windows
- **Disk Space**: ~500 MB (for Python packages)
- **Memory**: 2 GB RAM minimum, 4 GB recommended

### Optional Requirements

- **Docker**: For containerized worker mode (recommended for production)
- **PlantUML**: For PlantUML diagram conversion
  - Requires Java Runtime Environment
- **Draw.io**: For Draw.io diagram conversion
  - Linux: Draw.io desktop application
  - Requires Xvfb for headless operation

## Installation Methods

### Quick Install (Recommended)

```bash
pip install coding-academy-lecture-manager
```

### Development Install

```bash
git clone https://github.com/hoelzl/clm.git
cd clm
pip install -e .
```

See [Installation](installation.md) for detailed instructions.

## Getting Help

- **Documentation**: You're reading it!
- **Troubleshooting**: See [Troubleshooting Guide](troubleshooting.md)
- **Issues**: https://github.com/hoelzl/clm/issues
- **Repository**: https://github.com/hoelzl/clm/

## License

MIT License - see [LICENSE](../../LICENSE) for details.
