# CLX Docker Images

This directory contains Dockerfiles and supporting files for building CLX worker containers.

## Directory Structure

- `plantuml/` - PlantUML converter worker
- `drawio/` - Draw.io converter worker
- `notebook/` - Jupyter notebook processor worker

## Git LFS Files

Several large binary files are stored in Git LFS:

**DrawIO Worker:**
- `drawio/drawio-amd64-24.7.5.deb` (98MB) - Draw.io desktop application
- `drawio/ArchitectsDaughter-Regular.ttf` (37KB) - Font file

**Notebook Worker:**
- `notebook/deno-x86_64-unknown-linux-gnu.zip` (~40MB) - Deno runtime
- `notebook/ijava-1.3.0.zip` (~6MB) - IJava kernel
- `notebook/packages-microsoft-prod.deb` (~3KB) - Microsoft package repository config

**Note**: PlantUML JAR (`plantuml/plantuml-1.2024.6.jar`, 22MB) is committed directly (not LFS).

## Building Images

From the repository root:

```bash
# Build all services
./build-services.sh

# Build specific service
./build-services.sh plantuml
./build-services.sh drawio
./build-services.sh notebook
```

## Requirements

- Docker with BuildKit enabled
- Git LFS (to checkout large binary files)
- Repository must be cloned with Git LFS:
  ```bash
  git lfs install
  git lfs pull
  ```

## Image Details

### PlantUML Worker

- Base: `python:3.11-slim`
- External deps: Java Runtime, PlantUML JAR
- Python deps: Minimal (aiofiles, tenacity)

### DrawIO Worker

- Base: `python:3.11-slim`
- External deps: Draw.io desktop, Xvfb, fonts
- Python deps: Minimal (aiofiles, tenacity)
- Requires: D-Bus, Xvfb virtual display

### Notebook Worker

- Base: CUDA-enabled image (nvidia/cuda:13.1.0-cudnn8-runtime-ubuntu22.04)
- Package manager: uv (modern Python package installer)
- External deps: .NET SDK, Deno, IJava
- Python deps: PyTorch, FastAI, Jupyter, scientific stack
- Jupyter kernels: Python, C++, C#, Java, TypeScript
