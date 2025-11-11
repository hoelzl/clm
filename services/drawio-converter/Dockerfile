# syntax=docker/dockerfile:1
#
# Build from project root with:
#   docker build -f services/drawio-converter/Dockerfile -t clx-drawio-converter \
#     --build-arg SERVICE_PATH=services/drawio-converter --build-arg COMMON_PATH=. .
#
# Or use the build script: ./build-services.sh drawio-converter
#
FROM python:3.11-slim

WORKDIR /app

ARG SERVICE_PATH=services/drawio-converter
ARG COMMON_PATH=.

# Copy binary dependencies and requirements first (for better layer caching)
COPY ${COMMON_PATH}/docker-base-images/drawio-converter-base/drawio-amd64-24.7.5.deb .
COPY ${COMMON_PATH}/docker-base-images/drawio-converter-base/ArchitectsDaughter-Regular.ttf /usr/local/share/fonts/
COPY ${COMMON_PATH}/docker-base-images/drawio-converter-base/requirements.txt ./base-requirements.txt

# Install system dependencies and drawio
# Using apt cache mounts to avoid re-downloading packages
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

# Install Python dependencies with cache mount
# This caches pip downloads on the build machine, avoiding re-downloads
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r base-requirements.txt

# Copy service code and install
COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./drawio-converter

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clx-common && \
    pip install ./drawio-converter && \
    chmod +x /app/drawio-converter/entrypoint.sh

ENTRYPOINT ["/app/drawio-converter/entrypoint.sh"]
