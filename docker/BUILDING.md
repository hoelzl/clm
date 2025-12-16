# Building CLX Docker Images

This document describes how to build Docker images for CLX workers.

## Prerequisites

1. **Docker** with BuildKit enabled
2. **Git LFS** for large binary files
3. CLX repository cloned with LFS:
   ```bash
   git lfs install
   git lfs pull
   ```

## Quick Start

From the repository root:

```bash
# Build all workers (notebook builds both lite and full variants)
./build-services.sh

# Build specific worker
./build-services.sh plantuml
./build-services.sh drawio

# Build notebook variants
./build-services.sh notebook           # Both lite and full
./build-services.sh notebook:lite      # Lite only (cross-platform, no GPU)
./build-services.sh notebook:full      # Full only (CUDA/PyTorch, amd64 only)
```

On Windows (PowerShell):

```powershell
# Build all workers
.\build-services.ps1

# Build specific worker
.\build-services.ps1 notebook
.\build-services.ps1 plantuml
.\build-services.ps1 drawio
```

## Worker Images

### PlantUML Converter

**Image Tags:**
- `mhoelzl/clx-plantuml-converter:0.6.0`
- `mhoelzl/clx-plantuml-converter:latest`

**Base Image:** `python:3.11-slim`

**Build:**
```bash
docker build -f docker/plantuml/Dockerfile -t mhoelzl/clx-plantuml-converter .
```

**External Dependencies:**
- Java Runtime Environment
- PlantUML JAR (included in `docker/plantuml/`)

**Python Dependencies:**
- aiofiles
- tenacity
- pydantic
- SQLAlchemy

### Draw.io Converter

**Image Tags:**
- `mhoelzl/clx-drawio-converter:0.6.0`
- `mhoelzl/clx-drawio-converter:latest`

**Base Image:** `python:3.11-slim`

**Build:**
```bash
docker build -f docker/drawio/Dockerfile -t mhoelzl/clx-drawio-converter .
```

**External Dependencies:**
- Draw.io desktop application (from .deb package)
- Xvfb (X virtual framebuffer)
- Fonts (Noto, Liberation, Architects Daughter)

**Python Dependencies:**
- aiofiles
- tenacity
- pydantic
- SQLAlchemy

**Runtime:**
- Requires Xvfb and D-Bus to be running (handled by entrypoint.sh)
- Sets `DISPLAY=:99` environment variable

### Notebook Processor

The notebook processor has **two variants** to support different use cases:

#### Lite Variant (Cross-Platform)

**Best for:** Courses without deep learning, or running on Apple Silicon Macs.

**Image Tags:**
- `mhoelzl/clx-notebook-processor:0.6.0-lite`
- `mhoelzl/clx-notebook-processor:lite`

**Base Image:** `python:3.11-slim` (multi-arch: amd64, arm64)

**Build:**
```bash
docker build -f docker/notebook/Dockerfile \
  --build-arg VARIANT=lite \
  -t mhoelzl/clx-notebook-processor:lite .
```

**What's Included:**
- All Jupyter kernels (Python, C++, C#, F#, Java, TypeScript)
- Core scientific stack: NumPy, Pandas, Matplotlib, SciPy, scikit-learn
- `requests` library (commonly used in courses)
- Utility libraries: Jinja2, aiofiles, tqdm, SQLAlchemy, Pydantic

**What's NOT Included:**
- PyTorch and CUDA support
- FastAI, numba, skorch
- GPU acceleration

**Estimated Size:** ~3GB

**Platforms:** linux/amd64, linux/arm64 (Apple Silicon compatible)

#### Full Variant (GPU/ML)

**Best for:** Deep learning courses, CUDA-accelerated processing.

**Image Tags:**
- `mhoelzl/clx-notebook-processor:0.6.0` (default)
- `mhoelzl/clx-notebook-processor:0.6.0-full`
- `mhoelzl/clx-notebook-processor:latest`
- `mhoelzl/clx-notebook-processor:full`

**Base Image:** `nvidia/cuda:12.4.1-cudnn9-runtime-ubuntu22.04`

**Build:**
```bash
docker build -f docker/notebook/Dockerfile \
  --build-arg VARIANT=full \
  -t mhoelzl/clx-notebook-processor:full .

# Or simply (full is the default):
docker build -f docker/notebook/Dockerfile -t mhoelzl/clx-notebook-processor .
```

**What's Included:**
- Everything in lite, plus:
- PyTorch 2.8+ with CUDA 12.4 support
- FastAI, numba, skorch
- Full GPU acceleration

**Estimated Size:** ~10GB

**Platforms:** linux/amd64 only (NVIDIA CUDA requirement)

#### Common Features (Both Variants)

**External Dependencies:**
- .NET SDK 9.0 (C# and F# kernels)
- Deno (TypeScript/JavaScript kernel)
- Java JDK (Java kernel)
- IJava 1.3.0 (Java Jupyter kernel)
- micromamba + xeus-cpp (C++ kernel)

**Jupyter Kernels:**
- Python 3.11
- C++ (xeus-cpp)
- C# and F# (.NET Interactive)
- Java (IJava)
- TypeScript/JavaScript (Deno)

## Choosing a Variant

| Use Case | Recommended Variant |
|----------|---------------------|
| General programming courses | `lite` |
| Data science (no deep learning) | `lite` |
| Apple Silicon Mac | `lite` |
| PyTorch/FastAI courses | `full` |
| GPU-accelerated notebooks | `full` |
| Minimal image size | `lite` |

## Build Process

### Build Arguments

All Dockerfiles accept build arguments:

```bash
# PlantUML/DrawIO
docker build \
  -f docker/plantuml/Dockerfile \
  --build-arg DOCKER_PATH=docker/plantuml \
  -t mhoelzl/clx-plantuml-converter \
  .

# Notebook with variant
docker build \
  -f docker/notebook/Dockerfile \
  --build-arg DOCKER_PATH=docker/notebook \
  --build-arg VARIANT=lite \
  -t mhoelzl/clx-notebook-processor:lite \
  .
```

The build scripts automatically set these arguments.

### Image Tagging

All images use the Hub namespace (`mhoelzl/clx-*`) for consistency:

**PlantUML/DrawIO:**
- `mhoelzl/clx-plantuml-converter:0.6.0`
- `mhoelzl/clx-plantuml-converter:latest`

**Notebook (with variants):**
- **Full (default):** `mhoelzl/clx-notebook-processor:latest`, `:0.6.0`, `:full`, `:0.6.0-full`
- **Lite:** `mhoelzl/clx-notebook-processor:lite`, `:0.6.0-lite`

### BuildKit Cache Mounts

All Dockerfiles use BuildKit cache mounts to speed up builds:

```dockerfile
# APT cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get install ...

# Pip cache (for uv)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install ...

# Conda cache (for micromamba)
RUN --mount=type=cache,target=/opt/conda/pkgs,id=conda-pkgs,sharing=locked \
    micromamba install ...
```

**Important:** Do not use `--no-cache` with Docker build commands, as this disables cache mounts and makes builds significantly slower.

## Git LFS Files

Several large binary files are stored in Git LFS and must be present before building:

### PlantUML Worker
- `docker/plantuml/plantuml-1.2024.6.jar` (22MB) - **Committed directly (not LFS)**

### Draw.io Worker
- `docker/drawio/drawio-amd64-24.7.5.deb` (98MB) - **LFS**
- `docker/drawio/ArchitectsDaughter-Regular.ttf` (37KB) - **LFS**

### Notebook Worker
- `docker/notebook/deno-x86_64-unknown-linux-gnu.zip` (~40MB) - **LFS**
- `docker/notebook/ijava-1.3.0.zip` (~6MB) - **LFS**
- `docker/notebook/packages-microsoft-prod.deb` (~3KB) - **LFS**

To ensure all LFS files are downloaded:

```bash
git lfs pull
```

## Troubleshooting

### Build Fails: Missing LFS Files

**Error:** `COPY failed: file not found`

**Solution:**
```bash
git lfs install
git lfs pull
```

### Build Fails: BuildKit Not Enabled

**Error:** `unknown flag: --mount`

**Solution:**
```bash
export DOCKER_BUILDKIT=1
docker buildx version  # Verify BuildKit is available
```

### Slow Builds

**Symptom:** Builds re-download packages every time

**Possible Causes:**
1. BuildKit cache mounts not working (check `DOCKER_BUILDKIT=1`)
2. Using `--no-cache` flag (remove it)
3. Changing files too early in Dockerfile (review layer ordering)

**Best Practices:**
- Copy only files needed for expensive operations (apt, pip, conda)
- Copy application code last
- Use cache mounts for all package managers

### Notebook Image Build Fails

**Common Issues:**

1. **CUDA/PyTorch compatibility (full variant only):**
   - Verify CUDA 12.4 is supported by PyTorch version
   - Check PyTorch index URL: `https://download.pytorch.org/whl/cu124`

2. **xeus-cpp installation:**
   - Requires micromamba (not available via pip)
   - Ensure conda-forge channel is accessible

3. **.NET SDK installation:**
   - Full variant uses Ubuntu PPA
   - Lite variant uses Microsoft's Debian package

4. **ARM64 build fails for full variant:**
   - Full variant only supports amd64 (NVIDIA CUDA requirement)
   - Use lite variant for ARM64/Apple Silicon

### PlantUML Worker Issues

**Missing PlantUML JAR:**
- The JAR should be committed directly (not LFS)
- File: `docker/plantuml/plantuml-1.2024.6.jar` (22MB)

**Java not found:**
- Base image includes `default-jre` package
- Check Dockerfile apt-get install step

### Draw.io Worker Issues

**Xvfb fails to start:**
- Entrypoint script handles Xvfb startup
- Check D-Bus is running first
- Verify `DISPLAY=:99` is set

**Missing fonts:**
- Multiple font packages installed for compatibility
- Architects Daughter font included for diagrams

## Migration from Old Structure

If migrating from the old `services/` structure:

**Old paths:**
- `services/notebook-processor/Dockerfile`
- `services/plantuml-converter/Dockerfile`
- `services/drawio-converter/Dockerfile`

**New paths:**
- `docker/notebook/Dockerfile`
- `docker/plantuml/Dockerfile`
- `docker/drawio/Dockerfile`

**Build script changes:**
- Service names simplified: `notebook`, `plantuml`, `drawio`
- Build arg changed: `SERVICE_PATH` → `DOCKER_PATH`
- Image tags remain the same for backward compatibility
- **NEW:** Notebook now supports `:lite` and `:full` variant suffixes

## Advanced Topics

### Multi-Platform Builds

Build lite variant for multiple architectures:

```bash
# Lite variant supports multi-arch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/notebook/Dockerfile \
  --build-arg VARIANT=lite \
  -t mhoelzl/clx-notebook-processor:lite \
  --push \  # Required for multi-arch
  .

# PlantUML and DrawIO also support multi-arch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/plantuml/Dockerfile \
  -t mhoelzl/clx-plantuml-converter \
  .
```

**Note:** Full notebook variant requires CUDA, which limits it to amd64 only.

### Custom Base Images

To use a different CUDA version for the full notebook variant:

1. Change base image in `docker/notebook/Dockerfile`:
   ```dockerfile
   FROM nvidia/cuda:12.6.0-cudnn9-runtime-ubuntu22.04 AS base-full
   ```

2. Update PyTorch index URL to match CUDA version:
   ```bash
   --extra-index-url https://download.pytorch.org/whl/cu126
   ```

3. Verify PyTorch compatibility at: https://pytorch.org/get-started/locally/

### Development Builds

For faster iteration during development:

1. Use lite variant (smaller, faster builds)
2. Consider building only the variant you need
3. Use stage caching for quick rebuilds (see below)

## Stage Caching for Fast Rebuilds

All CLX Docker images use multi-stage builds. You can cache intermediate stages to dramatically speed up rebuilds when only CLX code changes (not the Dockerfile or system dependencies).

### How It Works

Each Dockerfile has stages:
- **plantuml/drawio**: `deps` stage (system dependencies) → `final` stage (CLX install)
- **notebook**: `common` stage (kernels, tools) → `packages` stage (Python packages) → `final` stage (CLX install)

When you use `--cache-stages`, the CLI builds and tags these intermediate stages. Subsequent builds can reuse these cached stages, skipping the expensive dependency installation.

### Recommended Workflow

```bash
# 1. Initial build with stage caching (slow, but caches stages)
clx docker build --cache-stages plantuml
clx docker build --cache-stages drawio
clx docker build --cache-stages notebook:full

# 2. After CLX code changes, quick rebuild (fast, reuses cached stages)
clx docker build-quick                 # Rebuild all services (default)
clx docker build-quick plantuml        # Or rebuild specific service
clx docker build-quick notebook:full
```

### CLI Commands

```bash
# Check cache status for all services
clx docker cache-info

# Build with stage caching
clx docker build --cache-stages              # All services
clx docker build --cache-stages <service>    # Specific service

# Quick rebuild using cached stages
clx docker build-quick              # All services (default)
clx docker build-quick <service>    # Specific service

# Build without cache (force full rebuild)
clx docker build --no-cache <service>
```

### Example: Notebook Development Cycle

```bash
# First time: Full build with caching (~20-30 min for full variant)
clx docker build --cache-stages notebook:full

# Check what's cached
clx docker cache-info

# After modifying CLX code: Quick rebuild (~1-2 min)
clx docker build-quick notebook:full

# If you modify the Dockerfile's earlier stages, rebuild cache:
clx docker build --cache-stages notebook:full
```

### Cache Image Tags

Cached stages are tagged as:
- `mhoelzl/clx-plantuml-converter:cache-deps`
- `mhoelzl/clx-drawio-converter:cache-deps`
- `mhoelzl/clx-notebook-processor:cache-common`
- `mhoelzl/clx-notebook-processor:cache-packages-lite`
- `mhoelzl/clx-notebook-processor:cache-packages-full`

These are local images used for caching; they don't need to be pushed to Docker Hub.

### Build Time Comparison

| Scenario | plantuml | drawio | notebook:full |
|----------|----------|--------|---------------|
| Full build (no cache) | ~2 min | ~5 min | ~25 min |
| Full build (with apt/pip cache) | ~1 min | ~3 min | ~15 min |
| Quick rebuild (cached stages) | ~30 sec | ~1 min | ~1-2 min |

*Times are approximate and depend on network speed and hardware.*

## Further Reading

- [Docker BuildKit Documentation](https://docs.docker.com/build/buildkit/)
- [Git LFS Documentation](https://git-lfs.github.com/)
- [PyTorch CUDA Installation Guide](https://pytorch.org/get-started/locally/)
- [uv Documentation](https://docs.astral.sh/uv/)
