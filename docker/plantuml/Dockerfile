# syntax=docker/dockerfile:1
#
# PlantUML Converter Worker - Multi-stage build
#
# Build from project root with:
#   docker build -f docker/plantuml/Dockerfile -t docker.io/mhoelzl/clm-plantuml-converter .
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
FROM python:3.12-slim AS deps

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

#=============================================================================
# NON-ROOT RUNTIME (finding S10 + D7, #798)
#=============================================================================
# Renders repo-supplied .puml through a JVM; no reason to be uid 0. The
# image must tolerate an arbitrary uid — on native Linux the executor runs
# the container as the host user so the diagrams it writes into the mounted
# source tree are owned correctly (a GitHub runner is uid 1001).
ENV HOME=/home/clm \
    XDG_CACHE_HOME=/tmp/cache

RUN set -eu; \
    mkdir -p /home/clm; \
    chmod 0777 /home/clm; \
    chmod -R a+rX /app

# Strip every setuid/setgid bit: a worker needs none of them (su, mount,
# passwd), and a container rendering repo-supplied input should not ship a
# local privilege-escalation surface. Pinned by a docker-marked test, so an
# apt-get added below this line cannot silently re-arm it.
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

USER 1000:1000

# Run the PlantUML worker
CMD ["python", "-m", "clm.workers.plantuml"]
