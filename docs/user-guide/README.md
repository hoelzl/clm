# CLX User Guide

Welcome to the CLX user guide! This documentation is for users who want to use CLX to process course content.

## What is CLX?

CLX (Coding-Academy Lecture Manager eXperimental) is a course content processing system that converts educational materials into multiple output formats.

**What CLX Can Do**:
- ✅ Execute Jupyter notebooks and convert them to HTML, PDF, slides
- ✅ Support multiple programming languages (Python, C++, C#, Java, TypeScript)
- ✅ Convert PlantUML diagrams to images (PNG, SVG)
- ✅ Convert Draw.io diagrams to images (PNG, SVG, PDF)
- ✅ Generate both speaker and participant versions of content
- ✅ Support multiple languages (English, German)
- ✅ Watch for file changes and auto-rebuild

## Quick Links

- **[Installation](installation.md)** - Install CLX
- **[Quick Start](quick-start.md)** - Get started in 5 minutes
- **[Configuration](configuration.md)** - Configure courses and options
- **[Troubleshooting](troubleshooting.md)** - Common issues and solutions

## Typical Workflow

1. **Create course structure**:
   ```
   my-course/
   ├── course.yaml          # Course configuration
   ├── section_001/
   │   ├── topic_001.py     # Python notebook source
   │   └── diagram.puml     # PlantUML diagram
   └── section_002/
       └── topic_002.py
   ```

2. **Configure course** (`course.yaml`):
   ```yaml
   name: "My Programming Course"
   language: en
   prog_lang: python
   output_dir: "./output"
   ```

3. **Build course**:
   ```bash
   clx build course.yaml
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
- **Jupyter Notebook** (`.ipynb`) - Interactive notebooks
- **HTML** - Standalone HTML pages
- **Slides** - Reveal.js presentations
- **PDF** - Printable documents (requires additional setup)
- **Python Script** (`.py`) - Plain Python code

### Diagram Support

Convert diagrams to images:
- **PlantUML** - UML diagrams, sequence diagrams, etc.
- **Draw.io** - Visual diagrams and flowcharts

### Incremental Builds

CLX intelligently caches results:
- Only processes files that have changed
- Content-based hashing detects changes
- Significantly faster incremental builds

### Watch Mode

Automatically rebuild when files change:
```bash
clx build course.yaml --watch
```

Perfect for iterative content development!

## System Requirements

### Minimum Requirements

- **Python**: 3.10, 3.11, or 3.12
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
pip install clx
```

### Development Install

```bash
git clone https://github.com/hoelzl/clx.git
cd clx
pip install -e .
```

See [Installation](installation.md) for detailed instructions.

## Getting Help

- **Documentation**: You're reading it!
- **Troubleshooting**: See [Troubleshooting Guide](troubleshooting.md)
- **Issues**: https://github.com/hoelzl/clx/issues
- **Repository**: https://github.com/hoelzl/clx/

## License

MIT License - see [LICENSE](../../LICENSE) for details.
