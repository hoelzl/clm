# syntax=docker/dockerfile:1
#
# Build from project root with:
#   docker build -f services/plantuml-converter/Dockerfile -t clx-plantuml-converter \
#     --build-arg SERVICE_PATH=services/plantuml-converter --build-arg COMMON_PATH=. .
#
# Or use the build script: ./build-services.sh plantuml-converter
#
FROM python:3.11-slim

WORKDIR /app

ARG SERVICE_PATH=services/plantuml-converter
ARG COMMON_PATH=.

# Copy requirements and PlantUML jar first (for better layer caching)
COPY ${SERVICE_PATH}/plantuml-1.2024.6.jar ./plantuml.jar
COPY ${SERVICE_PATH}/base-requirements.txt ./base-requirements.txt

# Install system dependencies (Java for PlantUML)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        default-jre \
        dbus \
        graphviz

# Install Python dependencies with cache mount
# This caches pip downloads on the build machine, avoiding re-downloads
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r base-requirements.txt

# Copy service code and install
COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./plantuml-converter

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clx-common && \
    pip install ./plantuml-converter

ENTRYPOINT ["python", "-m", "plantuml_converter.plantuml_converter"]
