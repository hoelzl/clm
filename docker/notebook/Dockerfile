# syntax=docker/dockerfile:1
#
# Notebook Processor Worker - Multi-variant build
#
# This Dockerfile supports two variants:
#   - lite: Smaller image using python:3.12-slim base (multi-arch: amd64, arm64)
#   - full: ML/GPU stack. On amd64 this uses an nvidia/cuda base (CUDA/PyTorch,
#     GPU-accelerated). On arm64 (e.g. Apple Silicon) there is no CUDA base, so
#     it reuses the python:3.12-slim base and installs the CPU PyTorch wheels;
#     GPU-only packages with no aarch64 wheel (e.g. fastembed-gpu) are skipped.
#
# Build lite version (cross-platform, no GPU):
#   docker build --build-arg VARIANT=lite -t docker.io/mhoelzl/clm-notebook-processor:lite .
#
# Build full version with CUDA/ML (default):
#   docker build -t docker.io/mhoelzl/clm-notebook-processor:full .
#   docker build --build-arg VARIANT=full -t docker.io/mhoelzl/clm-notebook-processor:full .
#
# Or use the build script:
#   ./build-services.sh notebook:lite
#   ./build-services.sh notebook:full
#   ./build-services.sh notebook        # builds both
#

ARG VARIANT=full
# Automatic BuildKit arg: "amd64" on x86_64 hosts, "arm64" on Apple Silicon.
# Declared at global scope so it is visible in the base-full FROM selector
# below (a stage-scope ARG cannot be used in a FROM line).
ARG TARGETARCH

#=============================================================================
# BASE IMAGES - Different base for each variant
#=============================================================================

# Lite base: Python slim (multi-arch: amd64, arm64)
# Suitable for macOS (Apple Silicon), Linux, Windows without GPU
FROM python:3.12-slim AS base-lite

WORKDIR /app

# Environment variables for lite
ENV DEBIAN_FRONTEND=noninteractive
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV PYTHONPATH=/app
ENV DOTNET_ROOT=/root/.dotnet
ENV PATH=/root/.local/bin:/root/.dotnet:/root/.dotnet/tools:$PATH
ENV MAMBA_ROOT_PREFIX=/opt/conda

# Create directories
RUN mkdir -p /root/.jupyter /root/course

# Install system dependencies for lite
# Note: software-properties-common is not needed on Debian (no PPA support)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        ca-certificates \
        curl \
        wget \
        default-jdk \
        default-jre \
        dbus \
        graphviz \
        unzip \
        git \
        build-essential \
        # Required for .NET on Debian
        libicu-dev

# Full base (amd64): NVIDIA CUDA (with GPU support).
# Note: Python will be installed via micromamba in the common stage.
# nvidia/cuda publishes no arm64 manifest, so this stage is amd64-only; the
# arm64 full variant is routed to base-full-arm64 below.
FROM nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04 AS base-full-amd64

WORKDIR /app

# Environment variables for full
ENV DEBIAN_FRONTEND=noninteractive
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV PYTHONPATH=/app
ENV DOTNET_ROOT=/root/.dotnet
ENV PATH=/root/.local/bin:/root/.dotnet:/root/.dotnet/tools:$PATH
ENV MAMBA_ROOT_PREFIX=/opt/conda

# Create directories
RUN mkdir -p /root/.jupyter /root/course

# Install system dependencies for full (includes CUDA-specific setup)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        ca-certificates \
        curl \
        wget \
        software-properties-common \
        default-jdk \
        default-jre \
        dbus \
        graphviz \
        unzip \
        git \
        build-essential \
    && add-apt-repository ppa:dotnet/backports

# Full base (arm64): there is no nvidia/cuda arm64 image. For Apple Silicon
# (and other arm64 hosts) the full variant reuses the multi-arch base-lite
# (python:3.12-slim + its apt stack); the ML stack is added in packages-full
# using CPU PyTorch wheels. This keeps a single, known-good arm64 base and
# avoids duplicating the base-lite apt layer.
FROM base-lite AS base-full-arm64

# Select the full base by build platform: amd64 -> CUDA, arm64 -> python slim.
# TARGETARCH is declared at global scope (see top of file).
FROM base-full-${TARGETARCH} AS base-full

#=============================================================================
# COMMON SETUP - Install tools and kernels (applied to selected base)
#=============================================================================

FROM base-${VARIANT} AS common

ARG DOCKER_PATH=docker/notebook
# Re-declare TARGETARCH so the arch-aware fetches below resolve per platform.
ARG TARGETARCH

# Install .NET SDK 10.0 using the official install script
# Note: .NET 9 has a known issue with dotnet-interactive installation
# (DotnetToolSettings.xml not found error), fixed in .NET 10
# See: https://github.com/dotnet/interactive/issues/3951
# This works across different Linux distributions without libicu version conflicts
# Note: DOTNET_ROOT and PATH are set in the base images
RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh && \
    chmod +x dotnet-install.sh && \
    ./dotnet-install.sh --channel 10.0 --install-dir /root/.dotnet && \
    rm dotnet-install.sh

# Install uv (modern Python package installer)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Install micromamba for Python environment management.
# This provides a clean Python installation without PEP 668 restrictions.
#
# Pinned to a specific GitHub release (not the micro.mamba.pm "latest"
# redirect) and SHA-256-verified so a flaky CDN cannot poison the build:
# the previous `curl … | tar -xj` pipeline silently piped HTML error pages
# into tar when the redirect served them, causing two consecutive CI
# failures during the 1.3.2 release. Bump by checking
# https://github.com/mamba-org/micromamba-releases/releases and updating
# the version tag plus BOTH checksums below (one per architecture).
ARG MICROMAMBA_VERSION=2.5.0-2
ARG MICROMAMBA_SHA256_AMD64=c04571cfb0750e5432d530a3068b8fcd232ebed3133358e056e59a90b9852b00
ARG MICROMAMBA_SHA256_ARM64=a64db0d7a82107c8d64357cf035fb8f9dbbe2fc48f48b302cbc8ba1590974e20
RUN set -eu && \
    if [ "${TARGETARCH}" = "arm64" ]; then \
        MICROMAMBA_SHA256="${MICROMAMBA_SHA256_ARM64}"; MICROMAMBA_ARCH="linux-aarch64"; \
    else \
        MICROMAMBA_SHA256="${MICROMAMBA_SHA256_AMD64}"; MICROMAMBA_ARCH="linux-64"; \
    fi && \
    curl --fail --location --retry 5 --retry-delay 5 --retry-all-errors \
        --output /usr/local/bin/micromamba \
        "https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_VERSION}/micromamba-${MICROMAMBA_ARCH}" && \
    echo "${MICROMAMBA_SHA256}  /usr/local/bin/micromamba" | sha256sum -c - && \
    chmod +x /usr/local/bin/micromamba && \
    /usr/local/bin/micromamba shell init -s bash --root-prefix /opt/conda && \
    echo "export MAMBA_ROOT_PREFIX=/opt/conda" >> ~/.bashrc

# Install Python 3.12 and xeus-cpp (C++ kernel) via micromamba
# xeus-cpp cannot be installed via pip, so we use micromamba for it
# We also install Python here to have a clean, managed environment
#
# The libstdcxx-devel sysroot package name encodes the target arch
# (libstdcxx-devel_linux-64 for x86_64, libstdcxx-devel_linux-aarch64 for
# arm64); install the one matching TARGETARCH. xeus-cpp / xtensor /
# xtensor-blas are arch-neutral names that conda resolves per-platform.
RUN set -eu && \
    if [ "${TARGETARCH}" = "arm64" ]; then LIBSTDCXX_DEVEL="libstdcxx-devel_linux-aarch64"; \
    else LIBSTDCXX_DEVEL="libstdcxx-devel_linux-64"; fi && \
    /usr/local/bin/micromamba install -y -n base -c conda-forge \
        python=3.12 \
        xeus-cpp>=0.8 \
        xtensor>=0.25 \
        xtensor-blas>=0.21 \
        "${LIBSTDCXX_DEVEL}" && \
    /usr/local/bin/micromamba clean -a -y

# Set up PATH to use micromamba's Python
ENV PATH=/opt/conda/bin:$PATH

# Install minimal Jupyter packages needed for kernel installations
# IJava and other kernel installers require jupyter_client to be present
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'jupyter_client>=8.0' \
        'jupyter_core>=5.0'

# Install .NET Interactive (C# and F# kernels)
RUN dotnet tool install -g --no-cache Microsoft.dotnet-interactive && \
    mkdir -p /root/.local/share/jupyter/kernels && \
    dotnet interactive jupyter install

# Install Deno (TypeScript/JavaScript kernel)
# Fetched from the pinned GitHub release and SHA-256-verified rather than
# vendored in-tree, so the 50 MB archive stays out of git LFS (see the
# micromamba block above for the same pattern and rationale). Bump by
# checking https://github.com/denoland/deno/releases and updating the version
# tag plus BOTH checksums below (one per architecture).
ARG DENO_VERSION=2.1.1
ARG DENO_SHA256_AMD64=cbc1b6728040f715e879a3a0c36e462bd09c37b634eb79d04df0bc8bdb07d6c6
ARG DENO_SHA256_ARM64=51beb65156a8a8e0d26b64a619e57e9fdf4d14741cf246abe8a79de39b5c18c5
RUN set -eu && \
    if [ "${TARGETARCH}" = "arm64" ]; then \
        DENO_SHA256="${DENO_SHA256_ARM64}"; DENO_ARCH="aarch64-unknown-linux-gnu"; \
    else \
        DENO_SHA256="${DENO_SHA256_AMD64}"; DENO_ARCH="x86_64-unknown-linux-gnu"; \
    fi && \
    curl --fail --location --retry 5 --retry-delay 5 --retry-all-errors \
        --output deno.zip \
        "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${DENO_ARCH}.zip" && \
    echo "${DENO_SHA256}  deno.zip" | sha256sum -c - && \
    unzip deno.zip -d deno-bin && \
    rm deno.zip && \
    cp ./deno-bin/deno /usr/local/bin && \
    /usr/local/bin/deno jupyter --unstable --install

# Install IJava (Java kernel)
# Same fetch-and-verify approach (was a vendored LFS zip).
ARG IJAVA_VERSION=1.3.0
ARG IJAVA_SHA256=484cc62579cb77510b0f0c78283f043794396d8ae9d76a8f2ab5d7368ec246b2
RUN set -eu && \
    curl --fail --location --retry 5 --retry-delay 5 --retry-all-errors \
        --output ijava.zip \
        "https://github.com/SpencerPark/IJava/releases/download/v${IJAVA_VERSION}/ijava-${IJAVA_VERSION}.zip" && \
    echo "${IJAVA_SHA256}  ijava.zip" | sha256sum -c - && \
    unzip ijava.zip -d ijava-1.3.0 && \
    rm ijava.zip
WORKDIR /app/ijava-1.3.0
RUN python3 install.py --sys-prefix

WORKDIR /app

#=============================================================================
# PYTHON PACKAGES - Different packages per variant
#=============================================================================

# Lite variant: Core Jupyter + Scientific stack (no ML/GPU libraries)
FROM common AS packages-lite

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        # Jupyter and notebook processing
        'ipython~=8.26.0' \
        'ipykernel~=6.29.5' \
        'ipywidgets~=8.0.2' \
        'jupyterlab~=4.0' \
        'notebook~=7.0' \
        'nbformat~=5.10.4' \
        'nbconvert~=7.16.4' \
        'jupytext~=1.16.4' \
        'rise' \
        # Scientific stack
        'numpy~=2.0.1' \
        'pandas~=2.2.2' \
        'matplotlib~=3.9.2' \
        'seaborn~=0.13.2' \
        'scipy~=1.14.0' \
        'scikit-learn~=1.5.1' \
        # Utilities (commonly used in courses)
        'requests' \
        'jinja2~=3.1.4' \
        'aiofiles~=24.1.0' \
        'tqdm~=4.66.5' \
        'toolz~=0.12.1' \
        'sqlalchemy~=2.0.32' \
        'pydantic~=2.8.2' \
        # HTTP client for REST API communication with CLM host
        'httpx' \
        # Text cleaning libraries (for data preprocessing in notebooks)
        "beautifulsoup4>=4.12.2" \
        "inscriptis>=1.0.0" \
        "ftfy>=6.1.1" \
        "clean-text>=0.6.0" \
        "trafilatura>=1.6.0" \
        "newspaper4k>=0.2.8" \
        # BM25 sparse retrieval (used in notebooks)
        'bm25s[core]>=0.3.2.post1'

# Full variant: Everything from lite + ML/GPU libraries
FROM common AS packages-full

# Re-declare TARGETARCH so the arch-gated installs below resolve per platform.
ARG TARGETARCH

# Install Python packages using uv
# Split into multiple steps for better caching and to handle large downloads

# Step 1: PyTorch ecosystem (large downloads, needs longer timeout)
# On amd64 we pull CUDA 12.6 wheels from the PyTorch index (GPU-accelerated).
# On arm64 there is no CUDA wheel, so we install the CPU wheels from PyPI
# (torch 2.8+ publishes aarch64 manylinux wheels). Same package names, no
# extra index URL on arm64.
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu && \
    if [ "${TARGETARCH}" = "arm64" ]; then \
        UV_HTTP_TIMEOUT=600 uv pip install --python /opt/conda/bin/python \
            'torch>=2.8.0' \
            'torchvision>=0.21.0' \
            'torchaudio>=2.8.0'; \
    else \
        UV_HTTP_TIMEOUT=600 uv pip install --python /opt/conda/bin/python --extra-index-url https://download.pytorch.org/whl/cu126 \
            'torch>=2.8.0' \
            'torchvision>=0.21.0' \
            'torchaudio>=2.8.0'; \
    fi

# Step 2: Jupyter and notebook processing
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'ipython~=8.26.0' \
        'ipykernel~=6.29.5' \
        'ipywidgets~=8.0.2' \
        'jupyterlab~=4.0' \
        'notebook~=7.0' \
        'nbformat~=5.10.4' \
        'nbconvert~=7.16.4' \
        'jupytext~=1.16.4' \
        'rise'

# Step 3: Scientific stack
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'numpy~=2.0.1' \
        'pandas~=2.2.2' \
        'matplotlib~=3.9.2' \
        'seaborn~=0.13.2' \
        'scipy~=1.14.0' \
        'scikit-learn~=1.5.1' \
        'numba~=0.60.0'

# Step 4: Fast.ai and ML tools
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'fastai>=2.8.0' \
        'skorch~=1.0.0'

# Step 5: Utilities
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'requests' \
        'jinja2~=3.1.4' \
        'aiofiles~=24.1.0' \
        'tqdm~=4.66.5' \
        'toolz~=0.12.1' \
        'sqlalchemy~=2.0.32' \
        'pydantic~=2.8.2' \
        'httpx'

# Step 6: LLM/RAG
# fastembed-gpu (which depends on onnxruntime-gpu) has no aarch64 wheel, so it
# is installed in the arch-gated step below rather than here.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'langchain>=1.2.7' \
        'langsmith' \
        'langchain-anthropic' \
        'langchain-community>=0.3.0' \
        'langchain-core' \
        'langchain-openai' \
        'langchain-text-splitters' \
        'langgraph>=0.2.0' \
        'qdrant-client>=1.13.0' \
        'langchain-qdrant' \
        'openai>=2.8.1' \
        'tiktoken>=0.9.0' \
        'ragas' \
        'pypdf>=6.3.2' \
        'pymediawiki>=0.7.5' \
        'bm25s[core]>=0.3.2.post1'

# Step 6b: GPU-only embedding backend (amd64 only).
# fastembed-gpu -> onnxruntime-gpu, neither of which publishes aarch64 wheels.
# Skipped on arm64; the CPU embedding stack from Step 6 remains available.
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "${TARGETARCH}" != "arm64" ]; then \
        uv pip install --python /opt/conda/bin/python 'fastembed-gpu>=0.6.0'; \
    fi

# Step 7: Additional ML packages (image generation, transformers, visualization)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'transformers' \
        'diffusers>=0.35.1' \
        'accelerate>=1.11.0' \
        'sentencepiece>=0.2.1' \
        'timm>=1.0.15' \
        'hf-xet>=1.1.10' \
        'protobuf>=6.32.1' \
        'gradio>=6.5.1' \
        'plotly>=6.4.0' \
        'pillow>=11.2.1'

# Step 8: Text cleaning libraries (for data preprocessing in notebooks)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python \
        'beautifulsoup4>=4.12.2' \
        'inscriptis>=1.0.0' \
        'ftfy>=6.1.1' \
        'clean-text>=0.6.0' \
        'trafilatura>=1.6.0' \
        'newspaper4k>=0.2.8'

#=============================================================================
# FINAL IMAGE - Select variant and install clm
#=============================================================================

FROM packages-${VARIANT} AS final

# Copy clm repository (workers are now part of main package)
COPY . ./clm

# Install clm with notebook worker dependencies
# Note: We don't use [notebook] extra here because we've already installed
# the dependencies above with specific versions via uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/conda/bin/python --no-deps ./clm

EXPOSE 8888

# Run the notebook worker
CMD ["python3", "-m", "clm.workers.notebook"]
