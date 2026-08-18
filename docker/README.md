# CLM Docker Images

This directory contains Dockerfiles and supporting files for building CLM worker containers.

## Directory Structure

- `plantuml/` - PlantUML converter worker
- `drawio/` - Draw.io converter worker
- `notebook/` - Jupyter notebook processor worker

## Build inputs

Large third-party inputs are **fetched from pinned upstream releases and
SHA-256-verified at build time** (see the `ARG …_VERSION` / `ARG …_SHA256` pairs
in the Dockerfiles), so they are no longer vendored in Git LFS:

- `notebook/` — Deno runtime, IJava kernel (fetched)
- `drawio/` — Draw.io desktop `.deb` (fetched)

Still in-tree:

- `drawio/ArchitectsDaughter-Regular.ttf` (~38 KB) — small font, vendored in Git LFS
- `plantuml/plantuml-1.2024.6.jar` (22 MB) — committed directly (not LFS)

So only the Draw.io image needs `git lfs pull`; the others need none. See
`BUILDING.md` for version-bump and checksum-mismatch notes.

## Containment: non-root workers and read-only sources

All three images run as **uid 1000 by default** (`USER 1000:1000`) rather than
root, and the notebook worker gets the course sources mounted **read-only** at
`/source`. PlantUML and Draw.io keep `/source` writable — they render diagrams
into the source tree, which is their job. `/workspace` (the output tree) is
writable for every worker.

The notebook worker is the one that executes course-authored code, so it is the
one worth containing: a notebook cell no longer runs as uid 0 and can no longer
write into the course repository.

### The uid caveat on native Linux

A bind mount keeps **host** ownership. A container running as uid 1000 can only
write into the mount if the host directory is writable by uid 1000 — and a
GitHub Actions runner, for instance, is uid 1001. Baking a build-time uid into
the image cannot solve this, because the right uid is a property of the machine
running the build, not of the image.

So `clm` resolves it at run time: on POSIX hosts the executor starts each
container with `--user <host-uid>:<host-gid>`, and the image's own `USER` stands
on Docker Desktop (Windows/macOS), where the mount is virtualized. The images
are therefore written to work under **any** uid — installs are world-readable,
`$HOME` is world-writable, and caches/runtime dirs point at `/tmp`. Keep it that
way when editing them: nothing may depend on the process being uid 1000.

Running these images by hand follows the same rule:

```bash
# Ordinary use: the image's own non-root user
docker run --rm clm-notebook-processor:lite

# Writing into a bind mount on native Linux
docker run --rm --user "$(id -u):$(id -g)"     -v "$PWD/output:/workspace" clm-notebook-processor:lite
```

If `clm` itself runs as root on Linux, containers inherit uid 0 (otherwise
their writes would fail) and a warning says so.

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
- Network access during the build (Dockerfiles fetch pinned, checksummed inputs)
- Git LFS only for the Draw.io image (its bundled font):
  ```bash
  git lfs install
  git lfs pull
  ```

## Image Details

### PlantUML Worker

- Base: `python:3.12-slim`
- External deps: Java Runtime, PlantUML JAR
- Python deps: Minimal (aiofiles, tenacity)

### DrawIO Worker

- Base: `python:3.12-slim`
- External deps: Draw.io desktop, Xvfb, fonts
- Python deps: Minimal (aiofiles, tenacity)
- Requires: D-Bus, Xvfb virtual display

### Notebook Worker

Two variants, each multi-architecture (linux/amd64 and linux/arm64):

- **lite** — base `python:3.12-slim`; scientific stack, no ML/GPU libraries.
- **full** — ML stack. On amd64 the base is `nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04`
  (CUDA/PyTorch, GPU-accelerated). On arm64 (Apple Silicon) there is no CUDA
  base image, so it reuses `python:3.12-slim` and installs CPU PyTorch wheels;
  GPU-only packages with no aarch64 wheel (e.g. `fastembed-gpu`) are skipped.
- Package manager: uv (modern Python package installer)
- External deps: .NET SDK, Deno, IJava
- Python deps: PyTorch, FastAI, Jupyter, scientific stack (full variant)
- Jupyter kernels: Python, C++, C#, Java, TypeScript
