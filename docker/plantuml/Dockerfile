# syntax=docker/dockerfile:1
#
# PlantUML Converter Worker - Multi-stage build
#
# Build from project root with:
#   docker build -f docker/plantuml/Dockerfile -t mhoelzl/clm-plantuml-converter .
#
# For faster rebuilds during development, use cached stages:
#   clm docker build --cache-stages plantuml
#   clm docker build-quick plantuml  # After CLM code changes
#
# Or use the CLI:
#   clm docker build plantuml
#

#=============================================================================
# DEPS STAGE - System dependencies and PlantUML JAR
#=============================================================================
FROM python:3.11-slim AS deps

WORKDIR /app

ARG DOCKER_PATH=docker/plantuml

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

# Copy PlantUML JAR AFTER system install to avoid cache invalidation
COPY ${DOCKER_PATH}/plantuml-1.2024.6.jar ./plantuml.jar

# Set PlantUML environment variable
ENV PLANTUML_JAR=/app/plantuml.jar

#=============================================================================
# FINAL STAGE - Install CLM
#=============================================================================
FROM deps AS final

# Copy clm repository (workers are now part of main package)
COPY . ./clm

# Install clm with plantuml worker dependencies
# Workers are now at clm.workers.plantuml
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clm[plantuml]

# Run the PlantUML worker
CMD ["python", "-m", "clm.workers.plantuml"]
