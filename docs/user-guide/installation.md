# Installation Guide

This guide covers installing CLM and its optional dependencies.

## Quick Install

### Using pip

```bash
pip install clm
```

### Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install CLM
uv pip install clm
```

### Verify Installation

```bash
clm --help
```

You should see the CLM command-line interface help message.

## Development Install

If you want to contribute to CLM or use the latest development version:

```bash
# Clone the repository
git clone https://github.com/hoelzl/clm.git
cd clm

# Install in editable mode (core dependencies only - minimal)
pip install -e .

# Or with uv (recommended)
uv pip install -e .

# Install with worker dependencies (for direct execution mode)
pip install -e ".[all-workers]"  # All workers (notebook, plantuml, drawio)
pip install -e ".[notebook]"     # Just notebook processing
pip install -e ".[plantuml]"     # Just PlantUML conversion
pip install -e ".[drawio]"       # Just Draw.io conversion

# Install with optional UI features
pip install -e ".[tui]"      # TUI monitoring
pip install -e ".[web]"      # Web dashboard

# Install for development
pip install -e ".[dev]"      # Development tools
pip install -e ".[all]"      # Everything (required for full testing)
```

### Python Optional Dependencies

CLM has several optional dependency groups for different features:

**Worker Dependencies** (NEW in v0.4.0):
- **[notebook]**: IPython, nbconvert, jupytext, matplotlib, pandas, scikit-learn
  - Required for notebook processing in direct execution mode
  - Install: `pip install -e ".[notebook]"`
- **[plantuml]**: aiofiles, tenacity
  - Required for PlantUML conversion in direct execution mode
  - Install: `pip install -e ".[plantuml]"`
- **[drawio]**: aiofiles, tenacity
  - Required for Draw.io conversion in direct execution mode
  - Install: `pip install -e ".[drawio]"`
- **[all-workers]**: All worker dependencies combined
  - Install: `pip install -e ".[all-workers]"`
- **[ml]**: PyTorch, FastAI, transformers
  - Optional for advanced ML notebooks
  - Install: `pip install -e ".[ml]"`

**UI Features**:
- **[tui]**: textual, rich
  - Required for: `clm monitor` command
  - Install: `pip install -e ".[tui]"`
- **[web]**: fastapi, uvicorn, websockets
  - Required for: `clm serve` command
  - Install: `pip install -e ".[web]"`

**Development Tools**:
- **[dev]**: pytest, mypy, ruff, pytest-asyncio, pytest-cov, httpx
  - Required for: Running tests, type checking, linting
  - Install: `pip install -e ".[dev]"`

**Everything**:
- **[all]**: All of the above (workers + ml + tui + web + dev)
  - Required for: Full development and testing
  - Install: `pip install -e ".[all]"`

**Notes**:
- Core package works without worker dependencies (can use Docker mode)
- For direct execution mode, install worker-specific dependencies
- For development and testing, install with `[all]`
- External tools (PlantUML JAR, Draw.io app) still required for those workers

## External Tool Dependencies

CLM can process different types of content. Depending on what you need, you may want to install additional tools.

### For PlantUML Diagrams

**Requirements**:
- Java Runtime Environment (JRE) 8 or higher
- PlantUML JAR file

**Installation**:

1. **Install Java**:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install default-jre

   # macOS (with Homebrew)
   brew install openjdk

   # Windows
   # Download from https://www.java.com/
   ```

2. **Download PlantUML**:
   ```bash
   # Linux/macOS
   wget https://github.com/plantuml/plantuml/releases/download/v1.2024.6/plantuml-1.2024.6.jar \
     -O /usr/local/share/plantuml.jar

   # Or place the JAR file anywhere you prefer
   ```

3. **Set Environment Variable**:
   ```bash
   export PLANTUML_JAR="/usr/local/share/plantuml.jar"

   # Add to ~/.bashrc or ~/.zshrc to make it permanent
   echo 'export PLANTUML_JAR="/usr/local/share/plantuml.jar"' >> ~/.bashrc
   ```

### For Draw.io Diagrams

**Requirements**:
- Draw.io desktop application
- Xvfb (for headless operation on Linux servers)

**Installation**:

**Ubuntu/Debian**:
```bash
# Download Draw.io .deb package
wget https://github.com/jgraph/drawio-desktop/releases/download/v24.7.5/drawio-amd64-24.7.5.deb

# Install
sudo dpkg -i drawio-amd64-24.7.5.deb
sudo apt-get install -f  # Install dependencies

# For headless operation, install Xvfb
sudo apt-get install xvfb

# Start Xvfb
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
```

**macOS**:
```bash
# With Homebrew
brew install --cask drawio

# Set environment variable
export DRAWIO_EXECUTABLE="/Applications/draw.io.app/Contents/MacOS/draw.io"
```

**Windows**:
```powershell
# Download and install from https://github.com/jgraph/drawio-desktop/releases

# Set environment variable
$env:DRAWIO_EXECUTABLE = "C:\Program Files\draw.io\draw.io.exe"
```

### For Notebook Execution

**Step 1: Install Notebook Worker Python Dependencies**

```bash
# Install notebook worker dependencies
pip install -e ".[notebook]"

# Or install everything
pip install -e ".[all-workers]"
```

This includes: IPython, nbconvert, jupytext, matplotlib, pandas, scikit-learn, and more.

**Step 2: Install Jupyter Kernels for Additional Languages**

CLM requires Jupyter and the appropriate kernels for the programming languages you want to use.

**Python** (included with notebook worker dependencies):
```bash
# Already installed with [notebook] extra
```

**C++** (xeus-cling kernel):
```bash
conda install xeus-cling -c conda-forge
```

**C#** (.NET Interactive):
```bash
dotnet tool install -g Microsoft.dotnet-interactive
dotnet interactive jupyter install
```

**Java** (IJava kernel):
```bash
# Download and install IJava
wget https://github.com/SpencerPark/IJava/releases/download/v1.3.0/ijava-1.3.0.zip
unzip ijava-1.3.0.zip
python install.py --sys-prefix
```

**TypeScript** (tslab):
```bash
npm install -g tslab
tslab install
```

**Machine Learning Libraries** (optional):
```bash
# Install ML packages for advanced notebooks
pip install -e ".[ml]"
```

This includes: PyTorch, torchvision, torchaudio, FastAI, transformers, numba.

## Docker Workers (Optional)

CLM can use Docker containers for notebook processing, PlantUML, and Draw.io conversion.
Docker workers are started automatically by `clm build` when needed.

To use Docker workers, build the images first:

```bash
# Clone repository
git clone https://github.com/hoelzl/clm.git
cd clm

# Build Docker images
./build-services.sh  # Linux/macOS
.\build-services.ps1 # Windows PowerShell
```

See [Building Guide](../developer-guide/building.md) for more details on Docker image building.

## Upgrading

### Upgrade from PyPI

```bash
pip install --upgrade clm
```

### Upgrade Development Install

```bash
cd /path/to/clm
git pull origin main
pip install -e .
```

## Migrating from Previous Versions

### From v0.3.x to v0.4.0

**Key Changes in v0.4.0**:
- Workers integrated into main package (`clm.workers`)
- New optional dependency groups: `[notebook]`, `[plantuml]`, `[drawio]`, `[all-workers]`, `[ml]`
- No separate worker package installation needed for direct execution mode
- Core package remains minimal (works with Docker mode without worker deps)

**Migration Steps**:
```bash
# Old way (v0.3.x) - No longer needed
# pip install -e ./services/notebook-processor
# pip install -e ./services/plantuml-converter
# pip install -e ./services/drawio-converter

# New way (v0.4.0)
pip install -e ".[all-workers]"  # Install all worker dependencies

# Or specific workers
pip install -e ".[notebook]"     # Just notebook worker
pip install -e ".[plantuml]"     # Just PlantUML worker
pip install -e ".[drawio]"       # Just Draw.io worker
```

### From v0.2.x to v0.3.0+

If you're upgrading from CLM v0.2.x, see [Migration Guide](../MIGRATION_GUIDE_V0.3.md) for important changes and migration steps.

**Key Changes in v0.3.0**:
- Single unified package (was 4 separate packages)
- Simpler imports: `from clm.core import Course`
- SQLite backend is now default (RabbitMQ optional)
- Faster installation and startup

## Troubleshooting Installation

### Import Errors

**Problem**: `ImportError: No module named 'clm'`

**Solution**:
```bash
# Verify installation
pip list | grep clm

# Reinstall if needed
pip uninstall clm
pip install clm
```

### Permission Errors

**Problem**: `Permission denied` when installing

**Solution**:
```bash
# Use --user flag
pip install --user clm

# Or use virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
pip install clm
```

### PlantUML Not Found

**Problem**: `PlantUML JAR not found`

**Solution**:
```bash
# Set PLANTUML_JAR environment variable
export PLANTUML_JAR="/path/to/plantuml.jar"

# Or place plantuml.jar in a standard location
sudo cp plantuml.jar /usr/local/share/
```

### Draw.io Not Found

**Problem**: `DrawIO executable not found`

**Solution**:
```bash
# Set DRAWIO_EXECUTABLE environment variable
export DRAWIO_EXECUTABLE="/path/to/drawio"

# Or ensure draw.io is in PATH
which drawio
```

## Next Steps

- **[Quick Start Guide](quick-start.md)** - Build your first course
- **[Configuration Guide](configuration.md)** - Configure course options
- **[Troubleshooting](troubleshooting.md)** - Common issues and solutions

## Getting Help

- **Issues**: https://github.com/hoelzl/clm/issues
- **Documentation**: https://github.com/hoelzl/clm/tree/main/docs
