# CLM Unified Package Architecture Design

## Executive Summary

This document analyzes options for consolidating the CLM package and its worker services (currently 4 separate packages) into a unified packaging structure. The goal is to simplify installation and maintenance while maintaining flexibility for different use cases (Docker-only, direct execution, minimal vs. full installations).

**Recommended Approach**: **Option 2 - Core Package with Worker Extras** (see details below)

---

## Current State Analysis

### Current Package Structure

The CLM project currently consists of:

1. **Main `clm` package** (`/pyproject.toml`)
   - Location: Repository root `src/clm/`
   - Contains: Core logic, infrastructure, CLI
   - Dependencies: Minimal (pydantic, click, watchdog, docker, etc.)
   - Optional extras: `dev`, `tui`, `web`, `all`

2. **Three worker service packages** (`services/*/pyproject.toml`)
   - **`notebook-processor`** (`services/notebook-processor/`)
     - Python package name: `nb`
     - Heavy dependencies: PyTorch, FastAI, scikit-learn, pandas, IPython, etc.
     - Templates: Jupyter templates for 5 languages (Python, C++, C#, Java, TypeScript)
     - Docker image: Uses conda for dependency management

   - **`plantuml-converter`** (`services/plantuml-converter/`)
     - Python package name: `plantuml_converter`
     - Minimal Python dependencies: just `clm` + `aiofiles` + `tenacity`
     - External dependency: Java + PlantUML JAR

   - **`drawio-converter`** (`services/drawio-converter/`)
     - Python package name: `drawio_converter`
     - Minimal Python dependencies: just `clm` + `aiofiles` + `tenacity`
     - External dependency: DrawIO desktop app + Xvfb

### Current Installation Process

**For Direct Execution Mode (default)**:
```bash
# Install main package
pip install -e .

# Install each worker service separately
pip install -e ./services/notebook-processor
pip install -e ./services/plantuml-converter
pip install -e ./services/drawio-converter
```

**For Docker Mode**:
```bash
# Install main package only
pip install -e .

# Build Docker images (currently broken - references non-existent clm-common)
./build-services.sh
```

### Current Problems

1. **Installation complexity**: Users must manually install 3 separate service packages for direct execution mode
2. **Error-prone**: Easy to forget to install a service, leading to runtime errors
3. **Broken builds**: Docker build scripts reference removed `clm-common` package
4. **Version synchronization**: Hard to keep 4 packages in sync
5. **Dependency bloat**: Installing all services pulls in heavy ML dependencies even if not needed
6. **Testing complexity**: Test environment setup requires installing all 4 packages

### How Workers Are Invoked

**Direct Execution Mode** (default):
- `DirectWorkerExecutor` starts workers using `python -m <module>`
- Module mapping:
  - `notebook` worker → `python -m nb`
  - `plantuml` worker → `python -m plantuml_converter`
  - `drawio` worker → `python -m drawio_converter`
- Requires: Python packages must be installed and importable

**Docker Mode**:
- `DockerWorkerExecutor` starts Docker containers
- Uses pre-built images: `mhoelzl/clm-notebook-processor:0.3.1`, etc.
- No local Python package installation needed (but CLI still needs `clm` package)

---

## Design Options

### Option 1: Fully Unified Package (Monolithic)

**Structure**:
```
clm/
├── pyproject.toml                     # Single package definition
├── src/clm/
│   ├── __version__.py
│   ├── __init__.py
│   ├── core/                          # Core course processing
│   ├── infrastructure/                # Job queue, workers, backends
│   ├── cli/                           # CLI commands
│   └── workers/                       # NEW: All worker code
│       ├── __init__.py
│       ├── notebook/                  # From notebook-processor
│       │   ├── __init__.py
│       │   ├── __main__.py
│       │   ├── notebook_worker.py
│       │   ├── notebook_processor.py
│       │   ├── output_spec.py
│       │   ├── templates_python/
│       │   ├── templates_cpp/
│       │   ├── templates_csharp/
│       │   ├── templates_java/
│       │   └── templates_typescript/
│       ├── plantuml/                  # From plantuml-converter
│       │   ├── __init__.py
│       │   ├── __main__.py
│       │   ├── plantuml_worker.py
│       │   └── plantuml_converter.py
│       └── drawio/                    # From drawio-converter
│           ├── __init__.py
│           ├── __main__.py
│           ├── drawio_worker.py
│           └── drawio_converter.py
```

**Installation**:
```bash
# Default: Everything included
pip install clm

# With all extras
pip install clm[all]
```

**Dependencies in pyproject.toml**:
```toml
dependencies = [
    # Core deps (always installed)
    "pydantic>=2.8.2",
    "click>=8.1.0",
    # ... other core deps

    # Minimal worker deps (always installed for direct execution)
    "aiofiles>=24.1.0",
    "tenacity>=9.0.0",

    # Notebook worker deps (always installed - PROBLEM!)
    "ipython>=8.26.0",
    "ipykernel>=6.29.5",
    "nbconvert>=7.16.4",
    "matplotlib>=3.9.2",
    "numpy>=2.0.1",
    "pandas>=2.2.2",
    # ... many more heavy dependencies
]

[project.optional-dependencies]
ml = [
    # ML-specific deps from conda packages.yaml
    "torch>=2.8.0",
    "torchvision>=0.20.0",
    "fastai>=2.7",
    "transformers",
]
```

**Module Entry Points** (update `DirectWorkerExecutor.MODULE_MAP`):
```python
MODULE_MAP = {
    'notebook': 'clm.workers.notebook',
    'drawio': 'clm.workers.drawio',
    'plantuml': 'clm.workers.plantuml'
}
```

**Docker Image Build**:
```dockerfile
# services/notebook-processor/Dockerfile
FROM mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0

# Install clm package with notebook worker extra
COPY . /app/clm
RUN pip install /app/clm[notebook,ml]

CMD ["python", "-m", "clm.workers.notebook"]
```

**Pros**:
- ✅ **Simplest for users**: Single `pip install clm`
- ✅ **No version sync issues**: Everything versioned together
- ✅ **Easier testing**: One package to install
- ✅ **Clearer code organization**: All in one place
- ✅ **Works out-of-box**: Direct execution mode works immediately

**Cons**:
- ❌ **Dependency bloat**: Default install pulls in heavy notebook deps even if you only want plantuml/drawio
- ❌ **Large package size**: Includes all worker code even if you only use Docker mode
- ❌ **Slow installation**: Installing heavy ML deps takes time
- ❌ **No granular control**: Can't install just the notebook worker

**Use Cases**:
- ✅ Good for: Development, testing, full-featured installations
- ❌ Bad for: Docker-only users, minimal installations, CI/CD

---

### Option 2: Core Package with Worker Extras ⭐ **RECOMMENDED**

**Structure**: Same as Option 1, but with different dependency management

**Installation**:
```bash
# Minimal: Core + CLI (Docker mode only, no direct execution)
pip install clm

# With specific worker (for direct execution)
pip install clm[notebook]           # Notebook worker (Python notebooks only)
pip install clm[plantuml]           # PlantUML worker
pip install clm[drawio]             # DrawIO worker

# With all workers (for direct execution of all file types)
pip install clm[all-workers]

# With ML dependencies (for advanced notebook features)
pip install clm[notebook,ml]

# Everything (development)
pip install clm[all]
```

**Dependencies in pyproject.toml**:
```toml
dependencies = [
    # Core deps only (CLI, infrastructure, job queue)
    "pydantic>=2.8.2",
    "pydantic-settings>=2.0.0",
    "click>=8.1.0",
    "watchdog>=6.0.0",
    "attrs>=25.4.0",
    "loguru>=0.7.0",
    "docker>=6.0.0",           # For Docker mode
    "tabulate>=0.9.0",
]

[project.optional-dependencies]
# Individual workers
notebook = [
    "ipython~=8.26.0",
    "ipykernel~=6.29.5",
    "jinja2~=3.1.4",
    "jupytext>=1.16.4",
    "nbformat~=5.10.4",
    "nbconvert~=7.16.4",
    "matplotlib>=3.9.2",
    "numpy>=2.0.1",
    "pandas>=2.2.2",
    "scikit-learn>=1.5.1",
    "seaborn>=0.13.2",
    # ... other notebook deps
]

plantuml = [
    "aiofiles~=24.1.0",
    "tenacity~=9.0.0",
]

drawio = [
    "aiofiles~=24.1.0",
    "tenacity~=9.0.0",
]

# Convenience groups
all-workers = [
    "clm[notebook,plantuml,drawio]",
]

# ML packages (from Docker conda environment)
# Note: Some conda packages need pip equivalents or version adjustments
ml = [
    "torch>=2.8.0",
    "torchvision>=0.20.0",
    "torchaudio>=2.8.0",
    "fastai>=2.7",
    "transformers",
    "skorch>=1.0.0",
    "numba>=0.60.0",
    "cython>=3.0.11",
]

# Development and testing
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
    "mypy>=1.0",
    "ruff>=0.1.0",
]

# TUI and Web
tui = ["textual>=0.50.0", "rich>=13.7.0"]
web = ["fastapi>=0.104.0", "uvicorn[standard]>=0.24.0", "websockets>=12.0"]

# Everything
all = [
    "clm[all-workers,ml,dev,tui,web]",
]
```

**Smart Default Behavior**:
```python
# In DirectWorkerExecutor, check if worker module is available
def start_worker(self, worker_type: str, index: int, config: WorkerConfig):
    if worker_type not in self.MODULE_MAP:
        raise ValueError(f"Unknown worker type: {worker_type}")

    module = self.MODULE_MAP[worker_type]

    # Check if module is importable
    try:
        import importlib.util
        spec = importlib.util.find_spec(module)
        if spec is None:
            raise ImportError(
                f"Worker module '{module}' not found. "
                f"Install with: pip install clm[{worker_type}]"
            )
    except ImportError as e:
        logger.error(
            f"Cannot start {worker_type} worker in direct mode: {e}\n"
            f"Either install the worker: pip install clm[{worker_type}]\n"
            f"Or use Docker mode instead."
        )
        return None

    # ... continue with worker startup
```

**Docker Image Build**:
```dockerfile
# services/notebook-processor/Dockerfile
FROM mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y default-jdk graphviz

# Copy clm package
COPY . /app/clm

# Install clm with notebook and ml extras
RUN pip install /app/clm[notebook,ml]

# The worker code is now at clm.workers.notebook
CMD ["python", "-m", "clm.workers.notebook"]
```

**Migration Path**:

1. **Phase 1**: Move service code into `src/clm/workers/`
   ```bash
   mkdir -p src/clm/workers
   mv services/notebook-processor/src/nb src/clm/workers/notebook
   mv services/plantuml-converter/src/plantuml_converter src/clm/workers/plantuml
   mv services/drawio-converter/src/drawio_converter src/clm/workers/drawio
   ```

2. **Phase 2**: Update module entry points
   ```python
   # src/clm/workers/notebook/__main__.py
   from clm.workers.notebook.notebook_worker import main
   if __name__ == "__main__":
       main()
   ```

3. **Phase 3**: Update `DirectWorkerExecutor.MODULE_MAP`
   ```python
   MODULE_MAP = {
       'notebook': 'clm.workers.notebook',
       'drawio': 'clm.workers.drawio',
       'plantuml': 'clm.workers.plantuml'
   }
   ```

4. **Phase 4**: Update pyproject.toml with new extras

5. **Phase 5**: Update Dockerfiles

6. **Phase 6**: Update documentation and test setup

**Pros**:
- ✅ **Flexible installation**: Choose what you need
- ✅ **Reasonable defaults**: Core package is lightweight
- ✅ **Docker-only mode**: Can skip worker extras if using Docker
- ✅ **Direct execution mode**: Install specific workers as needed
- ✅ **Good error messages**: Can detect missing workers and suggest fix
- ✅ **Single source tree**: All code in one repo
- ✅ **No version sync**: Everything versioned together

**Cons**:
- ⚠️ **More complex pyproject.toml**: Many extras to maintain
- ⚠️ **Users must know what to install**: Need clear documentation
- ⚠️ **Default doesn't work for direct mode**: Must install extras

**Use Cases**:
- ✅ **Docker-only users**: `pip install clm` (minimal)
- ✅ **Direct mode Python notebooks**: `pip install clm[notebook]`
- ✅ **Direct mode all file types**: `pip install clm[all-workers]`
- ✅ **ML development**: `pip install clm[notebook,ml]`
- ✅ **Development/Testing**: `pip install clm[all]`

---

### Option 3: Optional Worker Installation with Explicit Flags

**Structure**: Same as Option 2

**Installation**:
```bash
# Explicit Docker-only mode (no worker code installed)
pip install clm[docker-only]

# Explicit direct execution mode (all workers installed)
pip install clm[direct-mode]

# Or individual workers
pip install clm[notebook-worker]
pip install clm[plantuml-worker]
pip install clm[drawio-worker]
```

**Dependencies**:
```toml
[project.optional-dependencies]
# Explicit modes
docker-only = []  # Empty, just for clarity
direct-mode = ["clm[notebook-worker,plantuml-worker,drawio-worker]"]

# Individual workers (more verbose names)
notebook-worker = ["ipython~=8.26.0", ...]
plantuml-worker = ["aiofiles~=24.1.0", ...]
drawio-worker = ["aiofiles~=24.1.0", ...]
```

**Pros**:
- ✅ **Very explicit**: Clear what each mode does
- ✅ **Self-documenting**: Names explain purpose

**Cons**:
- ❌ **Verbose**: Extra typing for common cases
- ❌ **Redundant**: `docker-only` is just the default
- ⚠️ **More extras to maintain**: Even more than Option 2

**Verdict**: This is a variation of Option 2 with more verbose naming. Not significantly better.

---

### Option 4: Keep Separate Packages with Namespace

**Structure**:
```
clm/                                   # Core package
├── pyproject.toml
└── src/clm/
    ├── core/
    ├── infrastructure/
    └── cli/

clm-workers/                           # Worker packages (namespace)
├── notebook/
│   └── pyproject.toml                 # Package: clm-workers-notebook
│       └── src/clm_workers/
│           └── notebook/
├── plantuml/
│   └── pyproject.toml                 # Package: clm-workers-plantuml
│       └── src/clm_workers/
│           └── plantuml/
└── drawio/
    └── pyproject.toml                 # Package: clm-workers-drawio
        └── src/clm_workers/
            └── drawio/
```

**Installation**:
```bash
pip install clm                        # Core only
pip install clm-workers-notebook       # Notebook worker
pip install clm-workers-plantuml       # PlantUML worker
pip install clm-workers-drawio         # DrawIO worker
```

**Pros**:
- ✅ **Maximum flexibility**: Independent versioning possible
- ✅ **Very granular**: Install exactly what you need
- ✅ **Clear separation**: Each worker is a distinct package

**Cons**:
- ❌ **Multiple packages**: Same problem we're trying to solve!
- ❌ **Installation complexity**: Must install 4 packages for full setup
- ❌ **Version synchronization**: Hard to keep in sync
- ❌ **More maintenance**: Multiple pyproject.toml files
- ❌ **Confusing for users**: What's the difference between `clm` and `clm-workers-*`?

**Verdict**: This doesn't solve the original problem and adds complexity.

---

### Option 5: Plugins with Entry Points

**Structure**: Same as Option 2, but workers register via entry points

**pyproject.toml**:
```toml
[project.entry-points."clm.workers"]
notebook = "clm.workers.notebook:NotebookWorker"
plantuml = "clm.workers.plantuml:PlantUMLWorker"
drawio = "clm.workers.drawio:DrawIOWorker"
```

**Worker Discovery**:
```python
# In pool_manager.py
from importlib.metadata import entry_points

def discover_workers():
    """Discover available workers via entry points."""
    workers = {}
    for ep in entry_points(group='clm.workers'):
        try:
            worker_class = ep.load()
            workers[ep.name] = worker_class
        except ImportError:
            # Worker not installed
            pass
    return workers
```

**Pros**:
- ✅ **Extensible**: Third-party workers could register
- ✅ **Auto-discovery**: No need to hardcode worker types
- ✅ **Graceful degradation**: Missing workers silently ignored

**Cons**:
- ⚠️ **More complex**: Entry point system adds indirection
- ⚠️ **Silent failures**: Might hide installation issues
- ⚠️ **Not needed**: CLM workers are internal, not plugins

**Verdict**: Over-engineering for this use case. Good for plugin architectures, but CLM workers are built-in components.

---

## Comparison Matrix

| Criterion | Option 1<br/>(Monolithic) | Option 2<br/>(Extras) ⭐ | Option 3<br/>(Explicit Flags) | Option 4<br/>(Namespace) | Option 5<br/>(Plugins) |
|-----------|---------------------------|---------------------------|-------------------------------|--------------------------|------------------------|
| **Installation Simplicity** | ⭐⭐⭐ Best | ⭐⭐ Good | ⭐⭐ Good | ❌ Poor | ⭐⭐ Good |
| **Dependency Control** | ❌ Poor | ⭐⭐⭐ Best | ⭐⭐⭐ Best | ⭐⭐⭐ Best | ⭐⭐ Good |
| **Default Experience** | ⭐⭐⭐ Works everywhere | ⭐ Docker only | ⭐ Docker only | ❌ Core only | ⭐ Docker only |
| **Docker-Only Users** | ⭐ OK (bloated) | ⭐⭐⭐ Perfect | ⭐⭐⭐ Perfect | ⭐⭐⭐ Perfect | ⭐⭐⭐ Perfect |
| **Direct Mode Users** | ⭐⭐⭐ Perfect | ⭐⭐ Good | ⭐⭐ Good | ❌ Complex | ⭐⭐ Good |
| **Maintenance** | ⭐⭐⭐ Simple | ⭐⭐ Moderate | ⭐ Complex | ❌ Complex | ⭐ Complex |
| **Version Management** | ⭐⭐⭐ Unified | ⭐⭐⭐ Unified | ⭐⭐⭐ Unified | ❌ Fragmented | ⭐⭐⭐ Unified |
| **Testing Setup** | ⭐⭐⭐ Simple | ⭐⭐ Moderate | ⭐⭐ Moderate | ❌ Complex | ⭐⭐ Moderate |
| **Error Messages** | ⭐⭐ OK | ⭐⭐⭐ Excellent | ⭐⭐⭐ Excellent | ⭐ Poor | ⭐⭐ OK |
| **Future Extensibility** | ⭐⭐ Limited | ⭐⭐⭐ Flexible | ⭐⭐ Moderate | ⭐⭐⭐ Flexible | ⭐⭐⭐ Very flexible |

---

## Recommended Solution: Option 2 (Core Package with Worker Extras)

**Rationale**:

1. **Balances all concerns**: Good defaults, granular control, reasonable complexity
2. **Clear upgrade path**: Users can start minimal and add workers as needed
3. **Docker compatibility**: Docker-only users get lightweight install
4. **Direct mode support**: Full functionality available with extras
5. **Industry standard**: Many packages use this pattern (e.g., `pip install requests[socks]`)

### Recommended Default Configuration

**For most users** (Python notebooks via direct execution):
```bash
pip install clm[notebook]
```

**For development/testing**:
```bash
pip install clm[all]
```

**For Docker-only deployments**:
```bash
pip install clm
```

### Implementation Approach

#### Default Installation Behavior

Make `notebook` extra install by default with a smart mechanism:

**Option A: Include minimal notebook support in core**
```toml
dependencies = [
    # ... core deps
    "ipython~=8.26.0",      # Basic notebook execution
    "ipykernel~=6.29.5",
    "nbconvert~=7.16.4",
    "nbformat~=5.10.4",
]

[project.optional-dependencies]
notebook-full = [
    "matplotlib>=3.9.2",
    "numpy>=2.0.1",
    "pandas>=2.2.2",
    "scikit-learn>=1.5.1",
    # ... other data science libs
]
```

Pros: Default install works for basic Python notebooks
Cons: Still adds ~50MB to core package

**Option B: Recommend `notebook` extra in docs** (RECOMMENDED)
```toml
# Keep core minimal
dependencies = ["click", "pydantic", ...]

# Document that most users want:
# pip install clm[notebook]
```

Pros: Clean separation, users choose what they need
Cons: Requires users to read docs

**Decision**: Go with Option B and make the error message very helpful:

```python
# In DirectWorkerExecutor
def start_worker(self, worker_type: str, ...):
    try:
        module = self.MODULE_MAP[worker_type]
        import importlib.util
        if importlib.util.find_spec(module) is None:
            raise ImportError(
                f"\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Worker '{worker_type}' not available in direct mode\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"\n"
                f"To use {worker_type} worker in direct execution mode:\n"
                f"\n"
                f"  pip install clm[{worker_type}]\n"
                f"\n"
                f"Or install all workers:\n"
                f"\n"
                f"  pip install clm[all-workers]\n"
                f"\n"
                f"Or use Docker mode instead (no extra installation needed):\n"
                f"\n"
                f"  clm build --execution-mode docker <course.yaml>\n"
                f"\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )
    except ImportError as e:
        logger.error(e)
        return None
```

---

## Special Considerations

### ML Dependencies (conda → pip)

The Docker notebook-processor image uses conda for ML packages. When moving to pip, some packages need adjustments:

**Conda packages → pip equivalents**:
```yaml
# packages.yaml (conda)
cuda~=12.4.0                → # System dependency, not pip installable
pytorch>=2.8                → torch>=2.8.0
pytorch-cuda=12.4           → # Installed via torch[cuda] or separate torch install
libstdcxx-devel_linux-64    → # System dependency
xeus-cpp>=0.8               → # Not on PyPI, skip or find alternative
xtensor-blas>=0.21          → # Not on PyPI, skip or find alternative
xtensor>=0.25               → # Not on PyPI, skip or find alternative
faststream[rabbit]==0.5.23  → # Remove, no longer using RabbitMQ
```

**Resulting pip `ml` extra**:
```toml
ml = [
    "torch>=2.8.0",              # From pytorch
    "torchvision>=0.20.0",       # From torchvision
    "torchaudio>=2.8.0",         # From torchaudio
    "fastai>=2.7",               # From fastai
    "transformers",              # From transformers
    "scikit-learn>=1.5.1",       # From scikit-learn
    "scipy>=1.14.0",             # From scipy
    "seaborn>=0.13.2",           # From seaborn
    "skorch>=1.0.0",             # From skorch
    "numba>=0.60.0",             # From numba
    "cython>=3.0.11",            # From cython
    "statsmodels",               # From statsmodels
    "joblib>=1.4.2",             # From joblib
]
```

**CUDA Support**:
For PyTorch with CUDA, users need to install from PyTorch's index:
```bash
pip install clm[notebook]
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

Or in pyproject.toml, use `ml-cuda` extra:
```toml
ml-cuda = [
    # Regular ML packages
    "clm[ml]",
    # Note: Users must install PyTorch separately with CUDA from:
    # pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
]
```

Document this in README and error messages.

### Docker Image Optimization

**Current Dockerfiles**: Copy `clm-common` separately, then install workers

**New Dockerfiles**: Single clm package with extras

**notebook-processor Dockerfile**:
```dockerfile
FROM mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0

WORKDIR /app

# Install system dependencies (Java, .NET, Deno, etc.)
# ... same as before ...

# Copy entire clm repository
COPY . /app/clm

# Install clm with notebook worker and ML extras
# Note: Still use conda for PyTorch/CUDA due to better CUDA integration
RUN micromamba install -y -n base -f /app/clm/services/notebook-processor/packages.yaml && \
    micromamba clean -a -y

# Install clm package (this adds CLM infrastructure)
RUN pip install --no-deps /app/clm

# The worker code is now available as clm.workers.notebook
CMD ["python", "-m", "clm.workers.notebook"]
```

**plantuml-converter Dockerfile**:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (Java)
RUN apt-get update && \
    apt-get install -y default-jre graphviz && \
    rm -rf /var/lib/apt/lists/*

# Copy PlantUML JAR
COPY services/plantuml-converter/plantuml-1.2024.6.jar /app/plantuml.jar

# Copy and install clm with plantuml worker
COPY . /app/clm
RUN pip install /app/clm[plantuml]

CMD ["python", "-m", "clm.workers.plantuml"]
```

**Build Script Updates**:
No need for `clm-common` anymore:
```bash
# build-services.sh
docker buildx build \
    -f services/notebook-processor/Dockerfile \
    -t mhoelzl/clm-notebook-processor:0.3.1 \
    .  # Build context is project root
```

### Package Data (Templates)

The notebook-processor includes Jupyter templates for multiple languages. These need to be included in the package:

**pyproject.toml**:
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/clm"]

[tool.hatch.build.targets.wheel.force-include]
"src/clm/workers/notebook/templates_python" = "clm/workers/notebook/templates_python"
"src/clm/workers/notebook/templates_cpp" = "clm/workers/notebook/templates_cpp"
"src/clm/workers/notebook/templates_csharp" = "clm/workers/notebook/templates_csharp"
"src/clm/workers/notebook/templates_java" = "clm/workers/notebook/templates_java"
"src/clm/workers/notebook/templates_typescript" = "clm/workers/notebook/templates_typescript"
```

Or use package data:
```toml
[tool.setuptools.package-data]
"clm.workers.notebook" = [
    "templates_python/**/*",
    "templates_cpp/**/*",
    "templates_csharp/**/*",
    "templates_java/**/*",
    "templates_typescript/**/*",
]
```

---

## Migration Plan

### Phase 1: Code Reorganization (1-2 days)

1. Create `src/clm/workers/` directory structure
2. Move worker code:
   - `services/notebook-processor/src/nb/` → `src/clm/workers/notebook/`
   - `services/plantuml-converter/src/plantuml_converter/` → `src/clm/workers/plantuml/`
   - `services/drawio-converter/src/drawio_converter/` → `src/clm/workers/drawio/`
3. Update `__init__.py` files for proper module structure
4. Update import statements in worker code
5. Ensure `__main__.py` entry points work correctly

### Phase 2: Dependency Management (1 day)

1. Merge dependencies from service pyproject.toml files into main pyproject.toml
2. Create worker-specific extras: `notebook`, `plantuml`, `drawio`
3. Create convenience extras: `all-workers`, `ml`
4. Convert conda packages to pip equivalents for `ml` extra
5. Test that all extras install correctly

### Phase 3: Code Updates (1 day)

1. Update `DirectWorkerExecutor.MODULE_MAP`:
   ```python
   MODULE_MAP = {
       'notebook': 'clm.workers.notebook',
       'plantuml': 'clm.workers.plantuml',
       'drawio': 'clm.workers.drawio',
   }
   ```
2. Add worker availability checking with helpful error messages
3. Update any hardcoded import paths

### Phase 4: Docker Updates (1 day)

1. Update Dockerfiles to install `clm[<worker>]` instead of separate packages
2. Update build-services.sh to remove clm-common references
3. Test Docker builds locally
4. Update docker-compose.yaml if needed

### Phase 5: Testing Infrastructure (1 day)

1. Update `.claude/setup-test-env.sh` to install `clm[all]` instead of separate packages
2. Update test fixtures that import worker modules
3. Run full test suite (unit + integration + e2e)
4. Fix any import or module path issues

### Phase 6: Documentation (1 day)

1. Update CLAUDE.md:
   - New package structure
   - New installation instructions
   - Remove references to separate worker packages
2. Update README.md:
   - Installation examples with extras
   - Explain Docker vs. Direct mode
3. Update CONTRIBUTING.md:
   - Development setup with `[all]` extra
4. Create migration guide for existing users

### Phase 7: Cleanup (1 day)

1. Remove `services/*/pyproject.toml` files
2. Remove `services/*/base-requirements.txt` files
3. Keep Dockerfiles and service-specific files (packages.yaml, .deb, .jar, etc.)
4. Update .gitignore if needed
5. Archive old documentation

### Testing Checklist

After migration, verify:

- [ ] `pip install clm` works (minimal install)
- [ ] `pip install clm[notebook]` works
- [ ] `pip install clm[plantuml]` works
- [ ] `pip install clm[drawio]` works
- [ ] `pip install clm[all-workers]` works
- [ ] `pip install clm[all]` works
- [ ] CLI commands work: `clm --help`
- [ ] Direct execution mode works for all worker types
- [ ] Docker images build successfully
- [ ] Docker containers start and process jobs
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Documentation is updated and accurate

---

## Alternative Considerations

### Should We Keep Services Separate?

**Arguments FOR separation**:
- Different lifecycle: Services might evolve independently
- Clear boundaries: Physical separation enforces modularity
- Deployment options: Could deploy workers separately from CLI

**Arguments AGAINST separation** (stronger):
- Same repository: Already in same repo, why separate packages?
- Same version: Always released together (0.3.1 for all)
- Tight coupling: Workers are integral to CLM, not plugins
- Maintenance burden: Synchronizing 4 packages is tedious
- User confusion: "Do I need clm or notebook-processor?"
- Testing complexity: Setting up test env requires all packages

**Conclusion**: Consolidation is the right choice.

### Should Default Include Notebook Worker?

**Arguments FOR including**:
- Most common use case is Python notebooks
- Better "out of box" experience
- Less confusing for beginners

**Arguments AGAINST including**:
- Adds ~50-100MB dependencies to core
- Docker-only users don't need it
- Goes against "minimal by default" principle

**Recommended Compromise**:
- Keep core minimal
- Make error messages extremely helpful
- Prominently document `pip install clm[notebook]` as recommended
- Consider adding a setup wizard: `clm init` that asks what you need

---

## Summary

**Recommended Architecture**: **Option 2 - Core Package with Worker Extras**

**Key Benefits**:
1. Single unified package: `clm`
2. Flexible installation via extras
3. Clear, helpful error messages
4. Works for all use cases (Docker, direct, minimal, full)
5. Industry-standard pattern

**Installation Examples**:
```bash
# Docker-only users (minimal)
pip install clm

# Most users (Python notebooks, direct mode)
pip install clm[notebook]

# All file types (direct mode)
pip install clm[all-workers]

# ML development
pip install clm[notebook,ml]

# Full development environment
pip install clm[all]
```

**Migration Effort**: ~7 days of focused work

**Risk Level**: Low (mostly mechanical refactoring, comprehensive test suite exists)

**User Impact**: Positive (simpler installation, clearer documentation)

---

## Questions for Discussion

1. **Default behavior**: Should we include basic notebook support in core dependencies, or keep it purely in extras?

2. **ML dependencies**: Should we try to make `ml` extra work with pure pip, or keep Docker images using conda for better CUDA integration?

3. **Migration timing**: Should we do this in one PR or multiple incremental PRs?

4. **Backward compatibility**: Do we need to provide shim packages (`notebook-processor`, etc.) that just install `clm[notebook]` for a transition period?

5. **Version bump**: Should this be a minor version bump (0.3.x → 0.4.0) or major (0.3.x → 1.0.0)?

---

**Document Version**: 1.0
**Date**: 2025-11-18
**Author**: Claude (Anthropic)
**Status**: Draft for Review
