# Building CLM Docker Images

This document describes how to build Docker images for CLM workers.

## Prerequisites

1. **Docker** with BuildKit enabled
2. **Git LFS** — only needed for the Draw.io image (the small bundled font).
   The notebook and PlantUML images fetch their build inputs at build time and
   need no LFS objects. For the Draw.io image:
   ```bash
   git lfs install
   git lfs pull
   ```
3. **Network access during the build** — the Dockerfiles fetch Deno, IJava, and
   the Draw.io `.deb` from their pinned upstream releases (SHA-256-verified).

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
- `docker.io/mhoelzl/clm-plantuml-converter:1.22.0`
- `docker.io/mhoelzl/clm-plantuml-converter:latest`

**Base Image:** `python:3.12-slim`

**Build:**
```bash
docker build -f docker/plantuml/Dockerfile -t docker.io/mhoelzl/clm-plantuml-converter .
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
- `docker.io/mhoelzl/clm-drawio-converter:1.22.0`
- `docker.io/mhoelzl/clm-drawio-converter:latest`

**Base Image:** `python:3.12-slim`

**Build:**
```bash
docker build -f docker/drawio/Dockerfile -t docker.io/mhoelzl/clm-drawio-converter .
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
- `docker.io/mhoelzl/clm-notebook-processor:1.22.0-lite`
- `docker.io/mhoelzl/clm-notebook-processor:lite`

**Base Image:** `python:3.12-slim` (multi-arch: amd64, arm64)

**Build:**
```bash
docker build -f docker/notebook/Dockerfile \
  --build-arg VARIANT=lite \
  -t docker.io/mhoelzl/clm-notebook-processor:lite .
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

**Best for:** Deep learning courses, CUDA-accelerated processing (amd64); ML
courses on Apple Silicon (arm64, CPU-only).

**Image Tags:**
- `docker.io/mhoelzl/clm-notebook-processor:1.22.0` (default)
- `docker.io/mhoelzl/clm-notebook-processor:1.22.0-full`
- `docker.io/mhoelzl/clm-notebook-processor:latest`
- `docker.io/mhoelzl/clm-notebook-processor:full`

**Base Image:**
- amd64: `nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04` (CUDA, GPU)
- arm64: `python:3.12-slim` (no CUDA base image exists for arm64; CPU PyTorch)

**Build:**
```bash
docker build -f docker/notebook/Dockerfile \
  --build-arg VARIANT=full \
  -t docker.io/mhoelzl/clm-notebook-processor:full .

# Or simply (full is the default):
docker build -f docker/notebook/Dockerfile -t docker.io/mhoelzl/clm-notebook-processor .
```

**What's Included:**
- Everything in lite, plus:
- PyTorch 2.8+ (CUDA 12.6 wheels on amd64; CPU wheels on arm64)
- FastAI, numba, skorch
- GPU acceleration on amd64

**What's NOT included on arm64:**
- `fastembed-gpu` (no aarch64 `onnxruntime-gpu` wheel)

**Estimated Size:** ~10GB (amd64)

**Platforms:** linux/amd64 (CUDA/GPU) and linux/arm64 (CPU-only ML)

#### Common Features (Both Variants)

**External Dependencies:**
- .NET SDK 10.0 (C# and F# kernels)
- Deno (TypeScript/JavaScript kernel)
- Java JDK (Java kernel)
- IJava 1.22.0 (Java Jupyter kernel)
- micromamba + xeus-cpp (C++ kernel)

**Jupyter Kernels:**
- Python 3.12
- C++ (xeus-cpp)
- C# and F# (.NET Interactive)
- Java (IJava)
- TypeScript/JavaScript (Deno)

## Choosing a Variant

| Use Case | Recommended Variant |
|----------|---------------------|
| General programming courses | `lite` |
| Data science (no deep learning) | `lite` |
| Apple Silicon Mac (no GPU needed) | `lite` |
| PyTorch/FastAI courses (amd64 with GPU) | `full` |
| PyTorch/FastAI courses on Apple Silicon (CPU) | `full` |
| GPU-accelerated notebooks (amd64 only) | `full` |
| Minimal image size | `lite` |

## Build Process

### Build Arguments

All Dockerfiles accept build arguments:

```bash
# PlantUML/DrawIO
docker build \
  -f docker/plantuml/Dockerfile \
  --build-arg DOCKER_PATH=docker/plantuml \
  -t docker.io/mhoelzl/clm-plantuml-converter \
  .

# Notebook with variant
docker build \
  -f docker/notebook/Dockerfile \
  --build-arg DOCKER_PATH=docker/notebook \
  --build-arg VARIANT=lite \
  -t docker.io/mhoelzl/clm-notebook-processor:lite \
  .
```

The build scripts automatically set these arguments.

### Image Tagging

All images use the Hub namespace (`docker.io/mhoelzl/clm-*`) for consistency:

**PlantUML/DrawIO:**
- `docker.io/mhoelzl/clm-plantuml-converter:1.22.0`
- `docker.io/mhoelzl/clm-plantuml-converter:latest`

**Notebook (with variants):**
- **Full (default):** `docker.io/mhoelzl/clm-notebook-processor:latest`, `:1.22.0`, `:full`, `:1.22.0-full`
- **Lite:** `docker.io/mhoelzl/clm-notebook-processor:lite`, `:1.22.0-lite`

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

## Build inputs (fetched vs vendored)

The large third-party build inputs are **fetched from their pinned upstream
releases and SHA-256-verified inside the Dockerfiles** rather than vendored in
git, to keep them out of Git LFS. Bump a version by editing the `ARG …_VERSION`
/ `ARG …_SHA256` pair in the relevant Dockerfile.

| Worker | Input | Source |
|--------|-------|--------|
| Notebook | Deno (`ARG DENO_VERSION`) | fetched from `denoland/deno` releases |
| Notebook | IJava (`ARG IJAVA_VERSION`) | fetched from `SpencerPark/IJava` releases |
| Draw.io | Draw.io `.deb` (`ARG DRAWIO_VERSION`) | fetched from `jgraph/drawio-desktop` releases |
| PlantUML | `plantuml-1.2024.6.jar` (22 MB) | committed directly (not LFS) |
| Draw.io | `ArchitectsDaughter-Regular.ttf` (~38 KB) | vendored in Git LFS (small) |

Only the small Draw.io font remains in LFS, so a plain `git lfs pull` (or a
checkout with `lfs: true`) before building the Draw.io image is enough. The
notebook and PlantUML images need no LFS objects.

## Troubleshooting

### Build Fails: Checksum Mismatch on a Fetched Input

**Error:** `sha256sum: WARNING: 1 computed checksum did NOT match`

**Cause:** the pinned `ARG …_SHA256` no longer matches what the upstream URL
serves (a re-published asset, or a version bump that updated the URL but not the
SHA).

**Solution:** download the asset, recompute `sha256sum <file>`, and update the
matching `ARG …_SHA256` (and `ARG …_VERSION` if you intended a bump) in the
Dockerfile.

### Build Fails: Missing Draw.io Font

**Error:** `COPY failed: file not found` for `ArchitectsDaughter-Regular.ttf`

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

1. **CUDA/PyTorch compatibility (full variant, amd64 only):**
   - Verify CUDA 12.6 is supported by PyTorch version
   - Check PyTorch index URL: `https://download.pytorch.org/whl/cu126`
   - On arm64 there is no CUDA: the full variant installs CPU PyTorch from PyPI

2. **xeus-cpp installation:**
   - Requires micromamba (not available via pip)
   - Ensure conda-forge channel is accessible

3. **.NET SDK installation:**
   - Full variant uses Ubuntu PPA
   - Lite variant uses Microsoft's Debian package

4. **ARM64 / Apple Silicon builds:**
   - All three images now build on linux/arm64.
   - The notebook `full` variant is CPU-only on arm64 (no `nvidia/cuda` arm64
     image); GPU acceleration requires amd64. `fastembed-gpu` is skipped on
     arm64 (no aarch64 wheel).

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
  -t docker.io/mhoelzl/clm-notebook-processor:lite \
  --push \  # Required for multi-arch
  .

# PlantUML and DrawIO also support multi-arch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/plantuml/Dockerfile \
  -t docker.io/mhoelzl/clm-plantuml-converter \
  .
```

**Note:** All three images build on both linux/amd64 and linux/arm64. The
notebook `full` variant is GPU-accelerated on amd64 (CUDA/PyTorch) and
CPU-only on arm64 (no CUDA base image exists for arm64).

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

All CLM Docker images use multi-stage builds. You can cache intermediate stages to dramatically speed up rebuilds when only CLM code changes (not the Dockerfile or system dependencies).

### How It Works

Each Dockerfile has stages:
- **plantuml/drawio**: `deps` stage (system dependencies) → `final` stage (CLM install)
- **notebook**: `common` stage (kernels, tools) → `packages` stage (Python packages) → `final` stage (CLM install)

When you use `--cache-stages`, the CLI builds and tags these intermediate stages. Subsequent builds can reuse these cached stages, skipping the expensive dependency installation.

### Recommended Workflow

```bash
# 1. Initial build with stage caching (slow, but caches stages)
clm docker build --cache-stages plantuml
clm docker build --cache-stages drawio
clm docker build --cache-stages notebook:full

# 2. After CLM code changes, quick rebuild (fast, reuses cached stages)
clm docker build-quick                 # Rebuild all services (default)
clm docker build-quick plantuml        # Or rebuild specific service
clm docker build-quick notebook:full
```

### CLI Commands

```bash
# Check cache status for all services
clm docker cache-info

# Build with stage caching
clm docker build --cache-stages              # All services
clm docker build --cache-stages <service>    # Specific service

# Quick rebuild using cached stages
clm docker build-quick              # All services (default)
clm docker build-quick <service>    # Specific service

# Build without cache (force full rebuild)
clm docker build --no-cache <service>
```

### Example: Notebook Development Cycle

```bash
# First time: Full build with caching (~20-30 min for full variant)
clm docker build --cache-stages notebook:full

# Check what's cached
clm docker cache-info

# After modifying CLM code: Quick rebuild (~1-2 min)
clm docker build-quick notebook:full

# If you modify the Dockerfile's earlier stages, rebuild cache:
clm docker build --cache-stages notebook:full
```

### Cache Image Tags

Cached stages are tagged as:
- `docker.io/mhoelzl/clm-plantuml-converter:cache-deps`
- `docker.io/mhoelzl/clm-drawio-converter:cache-deps`
- `docker.io/mhoelzl/clm-notebook-processor:cache-common`
- `docker.io/mhoelzl/clm-notebook-processor:cache-packages-lite`
- `docker.io/mhoelzl/clm-notebook-processor:cache-packages-full`

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
