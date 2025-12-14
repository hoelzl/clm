# syntax=docker/dockerfile:1
#
# PlantUML Converter Worker - Multi-stage build
#
# Build from project root with:
#   docker build -f docker/plantuml/Dockerfile -t mhoelzl/clx-plantuml-converter .
#
# For faster rebuilds during development, use cached stages:
#   clx docker build --cache-stages plantuml
#   clx docker build-quick plantuml  # After CLX code changes
#
# Or use the CLI:
#   clx docker build plantuml
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
# FINAL STAGE - Install CLX
#=============================================================================
FROM deps AS final

# Copy clx repository (workers are now part of main package)
COPY . ./clx

# Install clx with plantuml worker dependencies
# Workers are now at clx.workers.plantuml
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clx[plantuml]

# Run the PlantUML worker
CMD ["python", "-m", "clx.workers.plantuml"]
