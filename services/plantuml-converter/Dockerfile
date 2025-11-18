# syntax=docker/dockerfile:1
#
# Build from project root with:
#   docker build -f services/plantuml-converter/Dockerfile -t clx-plantuml-converter .
#
# Or use the build script: ./build-services.sh plantuml-converter
#
FROM python:3.11-slim

WORKDIR /app

ARG SERVICE_PATH=services/plantuml-converter

# IMPORTANT: No files needed for system install, so start with apt
# Install system dependencies (Java for PlantUML)
# This is the most expensive step for this service
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        default-jre \
        dbus \
        graphviz

# Copy files AFTER system install to avoid cache invalidation
COPY ${SERVICE_PATH}/plantuml-1.2024.6.jar ./plantuml.jar
COPY ${SERVICE_PATH}/base-requirements.txt ./base-requirements.txt

# Install Python dependencies with cache mount
# This caches pip downloads on the build machine, avoiding re-downloads
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r base-requirements.txt

# Copy clx repository (workers are now part of main package)
COPY . ./clx

# Install clx with plantuml worker extra
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clx[plantuml]

# Use SQLite-based worker (RabbitMQ support removed)
# Worker code is now at clx.workers.plantuml
CMD ["python", "-m", "clx.workers.plantuml"]
