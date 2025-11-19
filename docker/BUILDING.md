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
# Build all workers
./build-services.sh

# Build specific worker
./build-services.sh notebook
./build-services.sh plantuml
./build-services.sh drawio
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
- `plantuml-converter:latest`, `clx-plantuml-converter:latest`
- `mhoelzl/clx-plantuml-converter:0.4.0`

**Base Image:** `python:3.11-slim`

**Build:**
```bash
docker build -f docker/plantuml/Dockerfile -t clx-plantuml-converter .
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
- `drawio-converter:latest`, `clx-drawio-converter:latest`
- `mhoelzl/clx-drawio-converter:0.4.0`

**Base Image:** `python:3.11-slim`

**Build:**
```bash
docker build -f docker/drawio/Dockerfile -t clx-drawio-converter .
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

**Image Tags:**
- `notebook-processor:latest`, `clx-notebook-processor:latest`
- `mhoelzl/clx-notebook-processor:0.4.0`

**Base Image:** `nvidia/cuda:12.4.1-cudnn9-runtime-ubuntu22.04`

**Build:**
```bash
docker build -f docker/notebook/Dockerfile -t clx-notebook-processor .
```

**External Dependencies:**
- CUDA 12.4 + cuDNN 9 (from base image)
- .NET SDK 9.0 (C# and F# kernels)
- Deno (TypeScript/JavaScript kernel)
- Java JDK (Java kernel)
- IJava 1.3.0 (Java Jupyter kernel)

**Python Package Manager:**
- **uv** - Modern Python package installer (primary)
- **micromamba** - Only for xeus-cpp (not available on PyPI)

**Python Dependencies:**
- PyTorch 2.8+ with CUDA 12.4 support
- Scientific stack: NumPy, Pandas, Matplotlib, SciPy, scikit-learn
- Jupyter: JupyterLab, IPython, nbconvert, jupytext
- Fast.ai and ML tools
- See Dockerfile for complete list

**Jupyter Kernels:**
- Python 3.11
- C++ (xeus-cpp)
- C# and F# (.NET Interactive)
- Java (IJava)
- TypeScript/JavaScript (Deno)

## Build Process

### Build Arguments

All Dockerfiles accept a `DOCKER_PATH` build argument:

```bash
docker build \
  -f docker/plantuml/Dockerfile \
  --build-arg DOCKER_PATH=docker/plantuml \
  -t clx-plantuml-converter \
  .
```

The build scripts automatically set this argument.

### Image Tagging

Each service is tagged with multiple names for compatibility:

1. **Short name:** `plantuml-converter:latest`
2. **CLX prefix:** `clx-plantuml-converter:latest`
3. **Versioned:** `plantuml-converter:0.4.0`, `clx-plantuml-converter:0.4.0`
4. **Hub namespace:** `mhoelzl/clx-plantuml-converter:latest`, `mhoelzl/clx-plantuml-converter:0.4.0`

This ensures backward compatibility with existing deployments.

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

## Using Docker Compose

Build all services with docker-compose:

```bash
docker-compose build
```

Run services:

```bash
docker-compose up -d
```

View logs:

```bash
docker-compose logs -f
```

Stop services:

```bash
docker-compose down
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

1. **CUDA/PyTorch compatibility:**
   - Verify CUDA 12.4 is supported by PyTorch version
   - Check PyTorch index URL: `https://download.pytorch.org/whl/cu124`

2. **xeus-cpp installation:**
   - Requires micromamba (not available via pip)
   - Ensure conda-forge channel is accessible

3. **.NET SDK installation:**
   - Requires PPA repository
   - May need updated package list

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

## Advanced Topics

### Multi-Platform Builds

Build for multiple architectures:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/plantuml/Dockerfile \
  -t clx-plantuml-converter \
  .
```

**Note:** Notebook worker requires CUDA, which limits platform support.

### Custom Base Images

To use a different CUDA version for the notebook worker:

1. Change base image in `docker/notebook/Dockerfile`:
   ```dockerfile
   FROM nvidia/cuda:12.6.0-cudnn9-runtime-ubuntu22.04
   ```

2. Update PyTorch index URL to match CUDA version:
   ```bash
   --extra-index-url https://download.pytorch.org/whl/cu126
   ```

3. Verify PyTorch compatibility at: https://pytorch.org/get-started/locally/

### Development Builds

For faster iteration during development:

1. Use smaller base images where possible
2. Mount source code as volume instead of COPY
3. Use `docker-compose.override.yaml` for local overrides
4. Consider multi-stage builds for smaller final images

## Further Reading

- [Docker BuildKit Documentation](https://docs.docker.com/build/buildkit/)
- [Git LFS Documentation](https://git-lfs.github.com/)
- [PyTorch CUDA Installation Guide](https://pytorch.org/get-started/locally/)
- [uv Documentation](https://docs.astral.sh/uv/)
