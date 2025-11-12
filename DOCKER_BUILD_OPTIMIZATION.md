# Docker Build Optimization Guide

## Understanding the Caching Problem

When building Docker images for the CLX services (especially notebook-processor), you may encounter situations where the large conda/mamba package installation (5GB+) re-executes even though you recently built the image. This document explains why this happens and how to prevent it.

## Root Causes of Cache Invalidation

### 1. Layer Cache Invalidation

Docker builds images in layers. Each instruction in the Dockerfile creates a new layer. **If any layer changes, all subsequent layers must be rebuilt.**

For the notebook-processor Dockerfile, the layer ordering is:
```dockerfile
FROM mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0  # Line 9
WORKDIR /app                                        # Line 16
RUN mkdir -p /root/.jupyter /root/course           # Line 24
COPY packages.yaml /tmp/packages.yaml              # Line 27
COPY base-requirements.txt ./base-requirements.txt # Line 28
COPY deno-x86_64-unknown-linux-gnu.zip .          # Line 29
COPY packages-microsoft-prod.deb .                 # Line 30
COPY ijava-1.3.0.zip .                            # Line 31
RUN --mount=type=cache,target=/opt/conda/pkgs \
    micromamba install -y -n base -f /tmp/packages.yaml  # Line 35-36
# ... more layers ...
```

**The conda install (line 35) will be re-executed if:**
- Any of the COPY commands before it (lines 27-31) have different content or timestamps
- The FROM image changed
- Build arguments changed

### 2. File Timestamp Changes

Even if file *content* hasn't changed, if file *timestamps* change, Docker treats it as a new layer. This commonly happens when:
- Doing `git pull` (changes timestamps on all files)
- Running build scripts that touch files
- Switching git branches
- Cloning the repository fresh

### 3. Build Context Size

The build sends all files from the project root (except those in `.dockerignore`) to the Docker daemon. Large build contexts slow down the build even if layers are cached.

**Current build context** (from clx root): ~1.3MB (with optimized .dockerignore)

### 4. BuildKit Cache Mounts

The Dockerfile uses `RUN --mount=type=cache,target=/opt/conda/pkgs` to cache downloaded packages. However:
- Cache mounts are stored in Docker's BuildKit cache directory
- Running `docker builder prune` clears these caches
- Different Docker contexts may have different caches
- Cache mounts don't prevent layer re-execution; they only cache the downloaded files within that execution

## Solutions and Best Practices

### Quick Fix: Force Cache Reuse

If you just pulled changes and file timestamps changed, you can minimize rebuilds:

```bash
# Option 1: Touch only the files that actually need to change
# (This is manual and error-prone - not recommended)

# Option 2: Use --cache-from to reuse layers from existing images
docker build \
    -f services/notebook-processor/Dockerfile \
    --cache-from notebook-processor:latest \
    -t notebook-processor:0.2.2 \
    .
```

### Long-term Solution 1: Multi-Stage Build

Split the build into stages where the expensive conda install is isolated:

```dockerfile
# Stage 1: Base environment with all packages
FROM mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0 AS base-env
COPY services/notebook-processor/packages.yaml /tmp/packages.yaml
RUN --mount=type=cache,target=/opt/conda/pkgs \
    micromamba install -y -n base -f /tmp/packages.yaml

# Stage 2: Build on top of base
FROM base-env
# ... copy application code ...
```

This way, the conda layer is in a separate stage and only rebuilds if packages.yaml changes.

### Long-term Solution 2: Pre-built Base Images

Create and push a base image with all conda packages pre-installed:

```bash
# Build base image once
docker build -f services/notebook-processor/Dockerfile.base \
    -t clx-notebook-base:0.2.2 .

# Push to registry
docker tag clx-notebook-base:0.2.2 your-registry/clx-notebook-base:0.2.2
docker push your-registry/clx-notebook-base:0.2.2

# Update service Dockerfile to use the base
FROM your-registry/clx-notebook-base:0.2.2
```

### Solution 3: Minimize File Copies Before Expensive Operations

The current Dockerfile copies 5 files before the conda install. If any change, the conda layer rebuilds.

**Optimization**: Only copy `packages.yaml` before mamba install, and move other files after:

```dockerfile
# Only copy what's needed for mamba
COPY ${SERVICE_PATH}/packages.yaml /tmp/packages.yaml

# Install conda packages (this layer rarely needs rebuilding)
RUN --mount=type=cache,target=/opt/conda/pkgs \
    micromamba install -y -n base -f /tmp/packages.yaml

# Copy other large files after
COPY ${SERVICE_PATH}/base-requirements.txt ./base-requirements.txt
COPY ${SERVICE_PATH}/deno-x86_64-unknown-linux-gnu.zip .
# ... etc
```

### Solution 4: Use Docker BuildKit Properly

Ensure BuildKit is enabled and you're using cache mounts:

```bash
# Enable BuildKit
export DOCKER_BUILDKIT=1

# Build with progress output to see cache hits
docker build --progress=plain -f services/notebook-processor/Dockerfile .

# Check BuildKit cache usage
docker builder du
```

### Solution 5: Optimize .dockerignore

An improved `.dockerignore` has been added to reduce build context size:
- Excludes all test files
- Excludes .git, .venv, IDE files
- Excludes build scripts (except those in services/)
- Excludes database files and test workspaces

This reduces build context from 53MB to ~1.3MB.

## Checking What Changed

Before building, you can check what files Docker will send:

```bash
# See what's in the build context
docker build -f services/notebook-processor/Dockerfile --no-cache -t test-context . 2>&1 | grep "Sending build context"

# Or use a tool to inspect
git ls-files | grep -v -f .dockerignore | wc -l
```

## When to Expect Rebuilds

You **will** see the conda layer rebuild when:
- `packages.yaml` content changes (adding/removing/updating packages)
- Base image updates (`mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0`)
- Files copied before conda install change (even timestamp-only changes)

You **should not** see rebuilds when:
- Only Python source code changes (clx-common, service code)
- Build scripts change
- Documentation changes
- Test files change

## Performance Tips

1. **Keep your BuildKit cache**: Don't run `docker builder prune` unless necessary
2. **Build regularly**: Fresh builds are expensive; incremental builds are fast
3. **Use `latest` tag**: Build with `:latest` tag and then tag with versions, so cache is reused
4. **Build all services at once**: Shared layers (clx-common) will be cached across services

```bash
# Good: Build all services
./build-services.sh

# Better: Build with explicit cache
for service in notebook-processor drawio-converter plantuml-converter; do
    docker build -f services/$service/Dockerfile \
        --cache-from ${service}:latest \
        -t ${service}:0.2.2 \
        -t ${service}:latest \
        .
done
```

## Monitoring Build Performance

```bash
# See detailed layer cache info
docker build --progress=plain -f services/notebook-processor/Dockerfile . 2>&1 | grep -E "CACHED|DONE"

# Check BuildKit cache size
docker builder du

# See what layers are being reused
docker history notebook-processor:latest
```

## Future Optimizations (Not Yet Implemented)

1. **Multi-stage builds**: Separate conda environment from application code
2. **Pre-built base images**: Push base images to registry
3. **Layer ordering optimization**: Reorder Dockerfile to put rarely-changing operations first
4. **Dependency lock files**: Pin exact package versions to prevent unnecessary updates
