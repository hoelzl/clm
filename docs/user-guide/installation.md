# Installation Guide

This guide covers installing CLM and its optional dependencies.

## Quick Install

### Using pip

```bash
pip install coding-academy-lecture-manager
```

### Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer:

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install CLM
uv pip install coding-academy-lecture-manager
```

### Verify Installation

```bash
clm --help
```

You should see the CLM command-line interface help message.

### Enable Shell Completion (Optional)

CLM ships tab completion for command names, options, and many argument
values. `clm completion <shell>` prints the activation script for your
shell. Bash, Zsh, and Fish use Click's native completion; **PowerShell**
(the primary shell on Windows) is supported by CLM directly.

**PowerShell** — enable for the current session, then make it permanent by
appending to your profile:

```powershell
# Current session only
clm completion powershell | Out-String | Invoke-Expression

# Permanent (appends to your $PROFILE; create it first if needed)
if (-not (Test-Path $PROFILE)) { New-Item -ItemType File -Path $PROFILE -Force }
clm completion powershell >> $PROFILE
```

**Bash** — add to `~/.bashrc`:

```bash
eval "$(clm completion bash)"
```

**Zsh** — add to `~/.zshrc`:

```bash
eval "$(clm completion zsh)"
```

**Fish** — install into the completions directory:

```bash
clm completion fish > ~/.config/fish/completions/clm.fish
```

Run `clm completion <shell> --install-hint` to print these instructions for
any shell. Restart your shell (or re-source the profile) after installing.

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
pip install -e ".[all]"      # Everything clm needs (no ML stack; see below)
```

### Python Optional Dependencies

CLM has several optional dependency groups for different features:

**Worker Dependencies**:
- **[notebook]**: ipython, ipykernel, ipywidgets, jinja2, jupytext, nbformat, nbconvert
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

> **The ML / data-science stack is no longer a clm extra.** As of CLM 1.19 the
> old `[ml]` extra (PyTorch, FastAI, transformers, pandas, the LangGraph
> deep-agents deck, the Postgres deployment decks, …) has been removed. It is
> *course-runtime* — clm never imports it — so it belongs in a **separate course
> venv** that the Direct-mode notebook kernel runs in, not in clm's own venv. It
> now ships as the self-contained `course-runtime-requirements.txt`. See
> [Running ML course decks in Direct mode](#running-ml-course-decks-in-direct-mode)
> below.

**UI Features**:
- **[tui]**: textual, rich
  - Required for: `clm monitor` command
  - Install: `pip install -e ".[tui]"`
- **[web]**: wsproto, httptools, watchfiles
  - Required for: `clm serve` command
  - Install: `pip install -e ".[web]"`

**LLM Features**:
- **[summarize]**: openai
  - Required for: `clm export summary` command (LLM-powered course summaries) and `clm slides polish` (LLM note cleanup)
  - Install: `pip install -e ".[summarize]"`

**Voiceover**:
- **[voiceover]**: faster-whisper, opencv-python, pytesseract, python-dateutil, rapidfuzz, Pillow, langfuse
  - Required for: `clm voiceover` commands (video-to-speaker-notes pipeline)
  - Install: `pip install -e ".[voiceover]"`
  - External tools: ffmpeg (audio extraction), Tesseract OCR (slide matching)
  - `[voiceover-cohere]` and `[voiceover-granite]` are alternative voiceover
    backends (heavier, require torch) and are **not** included in `[all]`

**Recordings**:
- **[recordings]**: jinja2, python-multipart, onnxruntime, soundfile, numpy, obsws-python
  - Required for: `clm recordings` commands (recording workflow and audio processing)
  - Install: `pip install -e ".[recordings]"`

**Slide Authoring**:
- **[slides]**: rapidfuzz
  - Required for: `clm slides` authoring tools (search, validation, normalization)
  - Install: `pip install -e ".[slides]"`
- **[mcp]**: mcp (plus `[slides]` dependencies)
  - MCP server for AI-assisted slide authoring
  - Install: `pip install -e ".[mcp]"`

**Cohort Calendars**:
- **[gcal]**: google-api-python-client, google-auth, google-auth-oauthlib
  - Required for: `clm calendar push` (mirror a cohort's viewing calendar into Google Calendar)
  - Install: `pip install -e ".[gcal]"`

**Output Bundling (JupyterLite)**:
- The `jupyterlite` output format (browser-based notebook site bundler) needs
  **no clm extra**. clm never imports jupyterlite-core — it only shells out to
  `jupyter lite build`, which runs in an **isolated `uvx` tool environment**
  pinned in `src/clm/workers/jupyterlite/builder.py`. The only requirement is
  that [`uv`](https://docs.astral.sh/uv/) is installed and `uvx` is on PATH; the
  first build provisions the tool env automatically. Keeping jupyterlite-core
  and its `empack` dependency (which caps `click<8.2`) out of clm's environment
  is deliberate — it makes the old Click collision structurally impossible. See
  `docs/claude/design/dependency-environment-isolation.md`.

**HTTP Replay**:
- **[replay]**: mitmproxy, pyyaml, filelock
  - HTTP request/response replay for notebooks that call live services
    (mitmproxy is the replay proxy; cassette serialization is CLM-owned and needs only PyYAML)
  - Install: `pip install -e ".[replay]"`

**Development Tools**:
- **[dev]**: pytest, pytest-asyncio, pytest-cov, pytest-mock, pytest-timeout, pytest-xdist, hypothesis, mypy, ruff, respx
  - Required for: Running tests, type checking, linting
  - Install: `pip install -e ".[dev]"`

**Everything (for clm development)**:
- **[all]**: all-workers, summarize, voiceover, recordings, slides, gcal, mcp,
  replay, dev, tui, web
  - Required for: Full clm development and testing
  - Install: `pip install -e ".[all]"`
  - **Carries no ML / data-science stack.** It is not imported by clm itself:
    the ML stack (PyTorch/pandas/transformers/…) is *course-runtime* — it exists
    only so Direct-mode notebook kernels can import it. Bundling it made every
    install multi-GB, so as of CLM 1.19 it is no longer a clm extra at all.
    Install it into a **separate course venv** when a course needs it (see
    [Running ML course decks in Direct mode](#running-ml-course-decks-in-direct-mode)),
    or run the workers in Docker mode (the notebook image bakes the ML stack in).
    (JupyterLite is likewise absent, and it too is not a clm extra — its build
    runs in an isolated `uvx` tool env.)

**Notes**:
- Core package works without worker dependencies (can use Docker mode)
- For direct execution mode, install worker-specific dependencies
- For clm development and testing, install with `[all]` (or just `uv sync`)
- To run **ML course decks** in Direct mode, install the course-runtime stack
  into a separate course venv — see
  [Running ML course decks in Direct mode](#running-ml-course-decks-in-direct-mode).
  (Do **not** install it into clm's own venv.)
- To build the **JupyterLite** output format, just make sure `uv` is installed
  (`uvx` on PATH); clm runs `jupyter lite build` in an isolated tool env — there
  is nothing to add to clm's own environment.
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

This includes: ipython, ipykernel, ipywidgets, jinja2, jupytext, nbformat, nbconvert.

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

## Running ML Course Decks in Direct Mode

Some course decks (deep-learning, RAG / LangGraph deep-agents, the Postgres
deployment decks, …) import a heavy machine-learning / data-science stack
(PyTorch, transformers, pandas, scikit-learn, …) *at notebook-execution time*.

**clm never imports any of that stack** — only the notebook *kernels* do. It is
therefore *course-runtime* (what the architecture docs call "Role B") and, as of
CLM 1.19, is **not a clm extra**. Installing it into clm's own venv would bloat
every clm install by multiple GB for no clm-side benefit. Instead, put it in a
**separate course venv** and point clm's Direct-mode notebook kernel at it.

The stack ships as the self-contained `course-runtime-requirements.txt` at the
repo root (it already includes `ipykernel`, which the kernel launcher needs).

```bash
# 1. Create a dedicated course venv (any Python 3.12–3.14).
uv venv /opt/course-venvs/ml          # or: python -m venv /opt/course-venvs/ml

# 2. Install the course-runtime stack into it (NOT into clm's venv).
/opt/course-venvs/ml/bin/python -m pip install -r course-runtime-requirements.txt

# 3. Point clm at it so Direct-mode notebook kernels run in that venv.
clm provision kernel-env --python /opt/course-venvs/ml/bin/python
```

`clm provision kernel-env` writes a `python3` kernelspec that clm prepends to
`JUPYTER_PATH` for notebook workers, so nbconvert (driven by clm's own venv,
"Role A") launches the kernel subprocess in the course venv. See
`clm info commands` (`provision kernel-env`) for the full resolution precedence
(`CLM_NOTEBOOK_KERNEL_PYTHON` env var → course-spec `<kernel-python>` element →
`clm.toml` `[jupyter] kernel_python`), and `clm info spec-files` for
`<kernel-python>`.

> **Prefer Docker for heavy ML decks.** The Docker notebook image already bakes
> in an equivalent course-runtime stack and is fully isolated, so Docker mode
> needs none of the above. The course-venv route is for running ML decks in
> Direct mode on a dev box.

For the architecture and rationale (the three dependency roles, why the ML stack
left clm's env), see
[`docs/claude/design/dependency-environment-isolation.md`](../claude/design/dependency-environment-isolation.md).

## Docker Workers (Optional)

CLM can use Docker containers for notebook processing, PlantUML, and Draw.io conversion.
Docker workers are started automatically by `clm build` when needed.

See [Building Guide](../developer-guide/building.md) for details on building Docker images.

## Upgrading

### Upgrade from PyPI

```bash
pip install --upgrade coding-academy-lecture-manager
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
- SQLite backend (RabbitMQ removed)
- Faster installation and startup

## Troubleshooting Installation

### Import Errors

**Problem**: `ImportError: No module named 'clm'`

**Solution**:
```bash
# Verify installation
pip list | grep clm

# Reinstall if needed
pip uninstall coding-academy-lecture-manager
pip install coding-academy-lecture-manager
```

### Permission Errors

**Problem**: `Permission denied` when installing

**Solution**:
```bash
# Use --user flag
pip install --user coding-academy-lecture-manager

# Or use virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
pip install coding-academy-lecture-manager
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
