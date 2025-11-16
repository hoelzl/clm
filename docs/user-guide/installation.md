# Installation Guide

This guide covers installing CLX and its optional dependencies.

## Quick Install

### Using pip

```bash
pip install clx
```

### Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install CLX
uv pip install clx
```

### Verify Installation

```bash
clx --help
```

You should see the CLX command-line interface help message.

## Development Install

If you want to contribute to CLX or use the latest development version:

```bash
# Clone the repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Install in editable mode (core dependencies only)
pip install -e .

# Or with uv
uv pip install -e .

# Install with optional features
pip install -e ".[tui]"      # TUI monitoring
pip install -e ".[web]"      # Web dashboard
pip install -e ".[dev]"      # Development tools
pip install -e ".[all]"      # Everything (required for testing)
```

### Python Optional Dependencies

CLX has several optional dependency groups for different features:

**[tui] - Terminal UI Monitoring**:
- Required for: `clx monitor` command
- Includes: textual, rich
- Install: `pip install -e ".[tui]"`

**[web] - Web Dashboard**:
- Required for: `clx serve` command
- Includes: fastapi, uvicorn, websockets
- Install: `pip install -e ".[web]"`

**[dev] - Development Tools**:
- Required for: Running tests, type checking, linting
- Includes: pytest, mypy, ruff, pytest-asyncio, pytest-cov, httpx
- Install: `pip install -e ".[dev]"`

**[all] - All Dependencies**:
- Required for: Full development and testing
- Includes: All of the above
- Install: `pip install -e ".[all]"`

**Note**: For development and testing, always install with `[all]` to ensure all features are available.

## External Tool Dependencies

CLX can process different types of content. Depending on what you need, you may want to install additional tools.

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

CLX requires Jupyter and the appropriate kernels for the programming languages you want to use.

**Python** (included with CLX):
```bash
# Already installed with CLX
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

## Docker Installation (Alternative)

If you prefer to use Docker for isolated execution:

```bash
# Clone repository
git clone https://github.com/hoelzl/clx.git
cd clx

# Build Docker services
./build-services.sh  # Linux/macOS
.\build-services.ps1 # Windows PowerShell

# Start services
docker-compose up -d
```

See [Building Guide](../developer-guide/building.md) for more details on Docker deployment.

## Upgrading

### Upgrade from PyPI

```bash
pip install --upgrade clx
```

### Upgrade Development Install

```bash
cd /path/to/clx
git pull origin main
pip install -e .
```

## Migrating from v0.2.x to v0.3.0

If you're upgrading from CLX v0.2.x, see [Migration Guide](../MIGRATION_GUIDE_V0.3.md) for important changes and migration steps.

**Key Changes in v0.3.0**:
- Single unified package (was 4 separate packages)
- Simpler imports: `from clx.core import Course`
- SQLite backend is now default (RabbitMQ optional)
- Faster installation and startup

## Troubleshooting Installation

### Import Errors

**Problem**: `ImportError: No module named 'clx'`

**Solution**:
```bash
# Verify installation
pip list | grep clx

# Reinstall if needed
pip uninstall clx
pip install clx
```

### Permission Errors

**Problem**: `Permission denied` when installing

**Solution**:
```bash
# Use --user flag
pip install --user clx

# Or use virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
pip install clx
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

- **Issues**: https://github.com/hoelzl/clx/issues
- **Documentation**: https://github.com/hoelzl/clx/tree/main/docs
