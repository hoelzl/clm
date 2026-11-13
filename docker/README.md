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

So `clm` resolves it at run time: on **native Linux** the executor starts each
container with `--user <host-uid>:<host-gid>`. On Docker Desktop (Windows and
macOS) the mount is virtualized and maps ownership itself, so the image's own
`USER` stands there — note that macOS is POSIX, so the check is on the platform,
not on `os.name`. The images
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

- **lite** — base `ubuntu:26.04`; scientific stack, no ML/GPU libraries.
- **full** — ML stack. On amd64 the base is `nvidia/cuda:13.3.1-cudnn-runtime-ubuntu26.04`
  (CUDA 13 / PyTorch; GPU-accelerated when the container is started with
  GPUs, which needs a host NVIDIA driver that supports CUDA 13.3). On arm64 (Apple Silicon) the variant is CPU-only: it
  reuses the lite base and installs CPU PyTorch wheels; GPU-only packages with
  no aarch64 wheel (e.g. `fastembed-gpu`) are skipped.
- Both variants sit on the **same Ubuntu release**, and Python comes from
  micromamba in both — the base image contributes the system packages only.
- Package manager: uv (modern Python package installer)
- External deps: .NET SDK, Deno, IJava
- Python deps: PyTorch, FastAI, Jupyter, scientific stack (full variant)
- Jupyter kernels: Python, C++, C#, Java, TypeScript
- C++ toolchain: the system g++ (`build-essential`, **g++ 15** on Ubuntu 26.04)
  and `cmake`. It serves two purposes at once (#1228):

  - it is the **standard library of the xeus-cpp kernel**. The kernel embeds
    its own clang (21, via the pinned `cppinterop`), but takes its libstdc++
    headers from whichever `c++` is on `PATH` — the system g++. No conda
    libstdc++ headers are installed; they would not be used.
  - it **compiles the C++ code export**.

  So a notebook and its exported project always see the same library, and the
  base image's g++ decides which C++23 library features a deck can use: g++ 15
  provides `<print>`, `std::ranges::to`, `<generator>` and `<flat_map>` (not
  `<mdspan>`). g++ 14 is *not* a safe floor: it has no `<flat_map>`, and with
  the headers of the 14.2.0 release (Ubuntu 24.04's `g++-14`) pipe-form
  `r | std::ranges::to<…>()` does not compile under the kernel's clang 21 —
  later 14.x snapshots (Debian 13's) carry the fix. Pinned by
  `tests/infrastructure/workers/test_docker_image_cpp_toolchain.py`.

  With it the image can compile the C++ code export, so no host toolchain is
  needed:

  ```bash
  # <out> = a clm output tree; <dir> = e.g. "<course>/Slides/Cpp/Completed"
  docker run --rm -v "<out>:/w" -w /w mhoelzl/clm-notebook-processor:lite \
    sh -c 'cmake -S "<dir>" -B /tmp/b && cmake --build /tmp/b --parallel'
  ```

  `/opt/conda/bin` comes first on `PATH` but holds no `c++`, so CMake — and the
  kernel — pick the system g++. Keep it that way: a conda clang there compiles the export, but
  its binaries fail at run time against the conda `libstdc++` (the
  cam-notebook `cpp` image in PythonCourses pins `CXX` for exactly this).
