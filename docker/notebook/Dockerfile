# syntax=docker/dockerfile:1
#
# Notebook Processor Worker
#
# Build from project root with:
#   docker build -f docker/notebook/Dockerfile -t clx-notebook-processor .
#
# Or use the build script:
#   ./build-services.sh notebook
#
# This image uses:
# - nvidia/cuda as base (CUDA 12.4 + cuDNN 9)
# - uv for Python package management (PyTorch, scientific stack)
# - micromamba for xeus-cpp only (not available on PyPI)
#
FROM nvidia/cuda:12.4.1-cudnn9-runtime-ubuntu22.04

WORKDIR /app

ARG DOCKER_PATH=docker/notebook

# Environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV PYTHONPATH=/app
ENV PATH=/root/.local/bin:/root/.dotnet/tools:$PATH
ENV MAMBA_ROOT_PREFIX=/opt/conda

# Create directories
RUN mkdir -p /root/.jupyter /root/course

# Install system dependencies
# This is one of the expensive steps - use apt cache to speed up rebuilds
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

# Copy static files needed for installation
COPY ${DOCKER_PATH}/packages-microsoft-prod.deb .
COPY ${DOCKER_PATH}/deno-x86_64-unknown-linux-gnu.zip .
COPY ${DOCKER_PATH}/ijava-1.3.0.zip .

# Install .NET SDK (required for C# kernel)
RUN dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update && \
    apt-get install -y dotnet-sdk-9.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv (modern Python package installer)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Install micromamba (needed for xeus-cpp only)
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C /usr/local bin/micromamba && \
    /usr/local/bin/micromamba shell init -s bash -p /opt/conda && \
    echo "export MAMBA_ROOT_PREFIX=/opt/conda" >> ~/.bashrc

# Install xeus-cpp and its dependencies via micromamba
# This cannot be installed via pip, so we use micromamba
RUN /usr/local/bin/micromamba install -y -n base -c conda-forge \
    python=3.11 \
    xeus-cpp>=0.8 \
    xtensor>=0.25 \
    xtensor-blas>=0.21 \
    libstdcxx-devel_linux-64 && \
    /usr/local/bin/micromamba clean -a -y

# Set up Python environment with micromamba's Python
ENV PATH=/opt/conda/bin:$PATH

# Install Python packages using uv
# PyTorch with CUDA 12.4 support from PyTorch index
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --extra-index-url https://download.pytorch.org/whl/cu124 \
        # PyTorch ecosystem
        'torch>=2.8.0' \
        'torchvision>=0.20.0' \
        'torchaudio>=2.8.0' \
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
        'numba~=0.60.0' \
        # Fast.ai and ML tools
        'fastai~=2.7.16' \
        'skorch~=1.0.0' \
        # Utilities
        'jinja2~=3.1.4' \
        'aiofiles~=24.1.0' \
        'tqdm~=4.66.5' \
        'toolz~=0.12.1' \
        'sqlalchemy~=2.0.32' \
        'pydantic~=2.8.2'

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
RUN python install.py --sys-prefix

WORKDIR /app

# Copy clx repository (workers are now part of main package)
COPY . ./clx

# Install clx with notebook worker dependencies
# Note: We don't use [notebook] extra here because we've already installed
# the dependencies above with specific versions via uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps ./clx

EXPOSE 8888

# Run the notebook worker
CMD ["python", "-m", "clx.workers.notebook"]
