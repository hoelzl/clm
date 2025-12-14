# syntax=docker/dockerfile:1
#
# Draw.io Converter Worker - Multi-stage build
#
# Build from project root with:
#   docker build -f docker/drawio/Dockerfile -t mhoelzl/clx-drawio-converter .
#
# For faster rebuilds during development, use cached stages:
#   clx docker build --cache-stages drawio
#   clx docker build-quick drawio  # After CLX code changes
#
# Or use the CLI:
#   clx docker build drawio
#

#=============================================================================
# DEPS STAGE - System dependencies and Draw.io
#=============================================================================
FROM python:3.11-slim AS deps

WORKDIR /app

ARG DOCKER_PATH=docker/drawio

# Copy files needed for system install to maximize cache hits
# The expensive apt/drawio install will only rebuild if these files change
COPY ${DOCKER_PATH}/drawio-amd64-24.7.5.deb .
COPY ${DOCKER_PATH}/ArchitectsDaughter-Regular.ttf /usr/local/share/fonts/

# Install system dependencies and drawio
# Using apt cache mounts to avoid re-downloading packages
# This is the most expensive step for this service
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        dbus \
        dbus-x11 \
        nodejs \
        npm \
        libasound2 \
        libgbm1 \
        xvfb \
        x11-xkb-utils \
        xfonts-75dpi \
        xfonts-100dpi \
        fonts-liberation \
        fonts-noto \
        fonts-noto-cjk && \
    apt-get -y -f install ./drawio-amd64-24.7.5.deb && \
    rm drawio-amd64-24.7.5.deb && \
    npm install -g svgo && \
    fc-cache -f -v

#=============================================================================
# FINAL STAGE - Install CLX and set up entrypoint
#=============================================================================
FROM deps AS final

ARG DOCKER_PATH=docker/drawio

# Copy clx repository (workers are now part of main package)
COPY . ./clx

# Install clx with drawio worker dependencies
# Workers are now at clx.workers.drawio
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clx[drawio]

# Copy and set up entrypoint script
COPY ${DOCKER_PATH}/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
