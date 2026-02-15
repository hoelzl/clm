# syntax=docker/dockerfile:1
#
# Notebook Processor Worker - Multi-variant build
#
# This Dockerfile supports two variants:
#   - lite: Smaller image using python:3.11-slim base (multi-arch: amd64, arm64)
#   - full: Larger image with CUDA/PyTorch using nvidia/cuda base (amd64 only)
#
# Build lite version (cross-platform, no GPU):
#   docker build --build-arg VARIANT=lite -t mhoelzl/clm-notebook-processor:lite .
#
# Build full version with CUDA/ML (default):
#   docker build -t mhoelzl/clm-notebook-processor:full .
#   docker build --build-arg VARIANT=full -t mhoelzl/clm-notebook-processor:full .
#
# Or use the build script:
#   ./build-services.sh notebook:lite
#   ./build-services.sh notebook:full
#   ./build-services.sh notebook        # builds both
#

ARG VARIANT=full

#=============================================================================
# BASE IMAGES - Different base for each variant
#=============================================================================

# Lite base: Python slim (multi-arch: amd64, arm64)
# Suitable for macOS (Apple Silicon), Linux, Windows without GPU
FROM python:3.11-slim AS base-lite

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

# Full base: NVIDIA CUDA (amd64 only, with GPU support)
# Note: Python will be installed via micromamba in the common stage
FROM nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04 AS base-full

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

#=============================================================================
# COMMON SETUP - Install tools and kernels (applied to selected base)
#=============================================================================

FROM base-${VARIANT} AS common

ARG DOCKER_PATH=docker/notebook

# Copy static files needed for installation
COPY ${DOCKER_PATH}/deno-x86_64-unknown-linux-gnu.zip .
COPY ${DOCKER_PATH}/ijava-1.3.0.zip .

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

# Install micromamba for Python environment management
# This provides a clean Python installation without PEP 668 restrictions
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C /usr/local bin/micromamba && \
    /usr/local/bin/micromamba shell init -s bash --root-prefix /opt/conda && \
    echo "export MAMBA_ROOT_PREFIX=/opt/conda" >> ~/.bashrc

# Install Python 3.12 and xeus-cpp (C++ kernel) via micromamba
# xeus-cpp cannot be installed via pip, so we use micromamba for it
# We also install Python here to have a clean, managed environment
RUN /usr/local/bin/micromamba install -y -n base -c conda-forge \
    python=3.12 \
    xeus-cpp>=0.8 \
    xtensor>=0.25 \
    xtensor-blas>=0.21 \
    libstdcxx-devel_linux-64 && \
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
RUN unzip ./deno-x86_64-unknown-linux-gnu.zip -d deno-bin && \
    rm ./deno-x86_64-unknown-linux-gnu.zip && \
    cp ./deno-bin/deno /usr/local/bin && \
    /usr/local/bin/deno jupyter --unstable --install

# Install IJava (Java kernel)
RUN unzip ./ijava-1.3.0.zip -d ijava-1.3.0 && \
    rm ./ijava-1.3.0.zip
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
        "newspaper4k>=0.2.8"

# Full variant: Everything from lite + ML/GPU libraries
FROM common AS packages-full

# Install Python packages using uv
# Split into multiple steps for better caching and to handle large downloads

# Step 1: PyTorch ecosystem (large downloads, needs longer timeout)
# CUDA 12.6 support from PyTorch index
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=600 uv pip install --python /opt/conda/bin/python --extra-index-url https://download.pytorch.org/whl/cu126 \
        'torch>=2.8.0' \
        'torchvision>=0.21.0' \
        'torchaudio>=2.8.0'

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
