# Docker Build Guide

## Overview

The Docker containers for CLM services use **BuildKit cache mounts** to optimize build times and minimize package downloads. This approach caches downloaded packages (pip, conda, apt) on your build machine, so they only need to be downloaded once.

## Key Features

- **No separate base images needed**: All dependencies are built directly into service images
- **Persistent caching**: Packages are cached locally and reused across builds
- **Layer optimization**: Requirements are installed before service code, so code changes don't trigger dependency re-installs
- **Multi-layer caching**: Separate caches for pip, conda/mamba, and apt packages

## Prerequisites

You must use **Docker BuildKit** to build these containers. BuildKit is the default in Docker 20.10+, but you can ensure it's enabled:

**Linux/macOS:**
```bash
# Set environment variable for single build
export DOCKER_BUILDKIT=1
docker build ...
```

**Windows PowerShell:**
```powershell
# Set environment variable for single build
$env:DOCKER_BUILDKIT = "1"
docker build ...
```

**Permanent configuration** (all platforms):
```json
# Add to Docker daemon config:
# Linux/macOS: /etc/docker/daemon.json
# Windows: C:\ProgramData\docker\config\daemon.json
{
  "features": {
    "buildkit": true
  }
}
```

## Building Services

**IMPORTANT**: All builds must be run from the **root directory** of the project, as the Dockerfiles need access to both the service code and the shared `clm-common` directory.

### Using the Build Script (Recommended)

For convenience, use the provided build script:

**Linux/macOS:**
```bash
# Build all services
./build-services.sh

# Build specific service
./build-services.sh drawio-converter
./build-services.sh notebook-processor
./build-services.sh plantuml-converter
```

**Windows PowerShell:**
```powershell
# Build all services
.\build-services.ps1

# Build specific service
.\build-services.ps1 drawio-converter
.\build-services.ps1 notebook-processor
.\build-services.ps1 plantuml-converter
```

### Manual Build from Root Directory

If you prefer to build manually, you can run docker build directly from the project root:

**Linux/macOS:**
```bash
# From the root of the clm project
export DOCKER_BUILDKIT=1

# Build drawio-converter
docker build \
  -f docker/drawio/Dockerfile \
  -t clm-drawio-converter \
  .

# Build notebook-processor
docker build \
  -f docker/notebook/Dockerfile \
  -t clm-notebook-processor \
  .

# Build plantuml-converter
docker build \
  -f docker/plantuml/Dockerfile \
  -t clm-plantuml-converter \
  .
```

**Windows PowerShell:**
```powershell
# From the root of the clm project
$env:DOCKER_BUILDKIT = "1"

# Build drawio-converter
docker build `
  -f docker/drawio/Dockerfile `
  -t clm-drawio-converter `
  .

# Build notebook-processor
docker build `
  -f docker/notebook/Dockerfile `
  -t clm-notebook-processor `
  .

# Build plantuml-converter
docker build `
  -f docker/plantuml/Dockerfile `
  -t clm-plantuml-converter `
  .
```

## Pushing to Docker Hub

After building your images, you can push them to Docker Hub for sharing or deployment.

### Prerequisites

1. **Create a Docker Hub account** at https://hub.docker.com if you don't have one
2. **Login to Docker Hub** from the command line:
   ```bash
   docker login
   ```

### Using the Push Script (Recommended)

**Linux/macOS:**
```bash
# Push all services
./push-services.sh YOUR_DOCKERHUB_USERNAME

# Push specific service
./push-services.sh YOUR_DOCKERHUB_USERNAME drawio-converter
./push-services.sh YOUR_DOCKERHUB_USERNAME notebook-processor
./push-services.sh YOUR_DOCKERHUB_USERNAME plantuml-converter
```

**Windows PowerShell:**
```powershell
# Push all services
.\push-services.ps1 YOUR_DOCKERHUB_USERNAME

# Push specific service
.\push-services.ps1 YOUR_DOCKERHUB_USERNAME drawio-converter
.\push-services.ps1 YOUR_DOCKERHUB_USERNAME notebook-processor
.\push-services.ps1 YOUR_DOCKERHUB_USERNAME plantuml-converter
```

### What the Push Script Does

For each service, the script:
1. Tags the local image as `username/clm-service-name:0.3.0`
2. Tags the local image as `username/clm-service-name:latest`
3. Pushes both tags to Docker Hub

### Manual Push

You can also push manually:

**Linux/macOS:**
```bash
# Tag the image
docker tag clm-drawio-converter YOUR_USERNAME/clm-drawio-converter:0.3.0
docker tag clm-drawio-converter YOUR_USERNAME/clm-drawio-converter:latest

# Push to Docker Hub
docker push YOUR_USERNAME/clm-drawio-converter:0.3.0
docker push YOUR_USERNAME/clm-drawio-converter:latest
```

**Windows PowerShell:**
```powershell
# Tag the image
docker tag clm-drawio-converter YOUR_USERNAME/clm-drawio-converter:0.3.0
docker tag clm-drawio-converter YOUR_USERNAME/clm-drawio-converter:latest

# Push to Docker Hub
docker push YOUR_USERNAME/clm-drawio-converter:0.3.0
docker push YOUR_USERNAME/clm-drawio-converter:latest
```

## How Caching Works

### Pip Cache

Python packages are cached in `/root/.cache/pip`:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

When you rebuild, pip will:
1. Check the cache for already-downloaded packages
2. Only download new or updated packages
3. Install from the local cache when possible

### Conda/Mamba Cache

For the notebook-processor, conda packages are cached in `/opt/conda/pkgs`:

```dockerfile
RUN --mount=type=cache,target=/opt/conda/pkgs \
    micromamba install -y -n base -f packages.yaml
```

### Apt Cache

System packages are cached to avoid re-downloading on rebuilds:

```dockerfile
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y <packages>
```

## Cache Location

BuildKit stores caches in Docker's build cache storage. You can:

```bash
# View cache usage
docker buildx du

# Clear all build cache (including our package caches)
docker buildx prune

# Clear only old/unused cache
docker buildx prune --keep-storage 20GB
```

## Rebuild Scenarios

### Service Code Changes Only
- **Fast**: Only the final layers rebuild
- **No downloads**: All dependencies are cached

### Requirements File Changes
- **Medium**: Dependencies layer rebuilds
- **Partial downloads**: Only changed packages download, others use cache

### Base Image or System Dependencies Change
- **Slow**: Most layers rebuild
- **Partial downloads**: Package caches still help

## Migration from Base Images

The previous architecture used separate base images (`docker-base-images/*`). This directory and the base images are **no longer needed**. All necessary files have been moved to their respective service directories (`services/*/`), and the Dockerfiles now reference these local files.

If you have old base images from previous builds, you can remove them:

```bash
# Remove old base images (if they exist)
docker rmi mhoelzl/clm-drawio-converter-base:0.2.0
docker rmi mhoelzl/clm-notebook-processor-base:0.2.0
docker rmi mhoelzl/clm-plantuml-converter-base:0.2.0
```

## Troubleshooting

### Cache Not Working

If packages are re-downloading on every build:

1. **Check BuildKit is enabled**: `docker buildx version`
2. **Verify syntax directive**: Ensure Dockerfiles start with `# syntax=docker/dockerfile:1`
3. **Check cache mounts**: Look for `--mount=type=cache` in RUN commands

### Cache Taking Too Much Space

```bash
# Check cache size
docker system df -v

# Prune build cache older than 7 days
docker buildx prune --filter until=168h

# Set a size limit (keeps most recent)
docker buildx prune --keep-storage 10GB
```

### Permission Issues

If you see permission errors with cache mounts, ensure you're building as root in the Dockerfile (default for most base images).

## Performance Tips

1. **Keep requirements stable**: Changes to requirements.txt trigger re-installs
2. **Order matters**: Copy requirements before service code
3. **Combine RUN commands carefully**: Each RUN creates a layer, but too many cache mounts in one RUN can be slower
4. **Use .dockerignore**: Prevent unnecessary files from invalidating cache

## Additional Notes

- The `--no-cache-dir` flag has been **removed** from pip commands to enable caching
- Apt cache uses `sharing=locked` to allow parallel builds
- Large downloads (PyTorch, CUDA, etc.) benefit most from caching
- Cache is per-build-machine, not included in the image
