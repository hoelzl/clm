# syntax=docker/dockerfile:1
#
# Build from project root with:
#   docker build -f services/notebook-processor/Dockerfile -t clx-notebook-processor .
#
# Or use the build script: ./build-services.sh notebook-processor
#
FROM mambaorg/micromamba:1.5.8-jammy-cuda-12.5.0

USER root
ARG MAMBA_DOCKERFILE_ACTIVATE=1
ARG SERVICE_PATH=services/notebook-processor

WORKDIR /app

# Environment variables
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV PYTHONPATH=/app
ENV PATH=$PATH:/root/.dotnet/tools

# Create directories
RUN mkdir -p /root/.jupyter /root/course

# IMPORTANT: Only copy packages.yaml first to maximize cache hits
# The expensive conda install will only rebuild if packages.yaml changes
COPY ${SERVICE_PATH}/packages.yaml /tmp/packages.yaml

# Configure micromamba for Docker cache compatibility
RUN echo "pkgs_dirs:" > /root/.condarc && \
    echo "  - /opt/conda/pkgs" >> /root/.condarc && \
    echo "always_copy: true" >> /root/.condarc  # ✅ Changed to true

# Copy other large files AFTER conda install to avoid invalidating the conda cache
COPY ${SERVICE_PATH}/base-requirements.txt ./base-requirements.txt
COPY ${SERVICE_PATH}/deno-x86_64-unknown-linux-gnu.zip .
COPY ${SERVICE_PATH}/packages-microsoft-prod.deb .
COPY ${SERVICE_PATH}/ijava-1.3.0.zip .

# Install system dependencies with apt cache
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        ca-certificates \
        default-jdk \
        default-jre \
        dbus \
        graphviz \
        unzip \
        software-properties-common

# Add .NET repository
RUN add-apt-repository ppa:dotnet/backports

# Install .NET SDK
# Note: Not using apt cache mounts here because we're adding new repositories
# and the cache can cause issues with repository metadata
RUN dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get install -y dotnet-sdk-9.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install python packages with correct cache configuration
RUN --mount=type=cache,target=/opt/conda/pkgs,id=conda-pkgs,sharing=locked \
    echo "=== BEFORE INSTALL ===" && \
    ls -lh /opt/conda/pkgs/ 2>/dev/null || echo "Cache dir empty" && \
    du -sh /opt/conda/pkgs 2>/dev/null || echo "Cache dir doesn't exist" && \
    micromamba info && \
    micromamba config list --sources && \
    cat /root/.condarc && \
    echo "Installing packages..." && \
    micromamba install -y -n base -f /tmp/packages.yaml

# Install .NET Interactive
RUN dotnet tool install -g --no-cache Microsoft.dotnet-interactive && \
    mkdir -p /root/.local/share/jupyter/kernels && \
    dotnet interactive jupyter install

# Install Deno
RUN unzip ./deno-x86_64-unknown-linux-gnu.zip -d deno-bin && \
    rm ./deno-x86_64-unknown-linux-gnu.zip && \
    cp ./deno-bin/deno /usr/local/bin && \
    /usr/local/bin/deno jupyter --unstable --install

# Install IJava
RUN unzip ./ijava-1.3.0.zip -d ijava-1.3.0 && \
    rm ./ijava-1.3.0.zip
WORKDIR /app/ijava-1.3.0
RUN python install.py --sys-prefix

WORKDIR /app

# Copy clx repository (workers are now part of main package)
COPY . ./clx

# Install clx with notebook worker extra (still use conda for ML packages)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps ./clx[notebook]

EXPOSE 8888

# Use SQLite-based worker (RabbitMQ support removed)
# Worker code is now at clx.workers.notebook
CMD ["python", "-m", "clx.workers.notebook"]
