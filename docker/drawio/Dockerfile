# syntax=docker/dockerfile:1
#
# Draw.io Converter Worker - Multi-stage build
#
# Build from project root with:
#   docker build -f docker/drawio/Dockerfile -t docker.io/mhoelzl/clm-drawio-converter .
#
# For faster rebuilds during development, use cached stages:
#   clm docker build --cache-stages drawio
#   clm docker build-quick drawio  # After CLM code changes
#
# Or use the CLI:
#   clm docker build drawio
#

#=============================================================================
# DEPS STAGE - System dependencies and Draw.io
#=============================================================================
FROM python:3.12-slim AS deps

WORKDIR /app

ARG DOCKER_PATH=docker/drawio
# Automatic BuildKit arg: "amd64" on x86_64 hosts, "arm64" on Apple Silicon.
# Used to select the arch-correct Draw.io .deb and its checksum below.
ARG TARGETARCH

# The font is small (~38 KB) and stays vendored. The Draw.io .deb is fetched
# from the pinned GitHub release and SHA-256-verified inside the install step
# below (same pattern as the notebook Dockerfile) rather than vendored as a
# ~100 MB git LFS object. Bump by checking
# https://github.com/jgraph/drawio-desktop/releases and updating the version
# plus BOTH checksums below (one per architecture).
COPY ${DOCKER_PATH}/ArchitectsDaughter-Regular.ttf /usr/local/share/fonts/

ARG DRAWIO_VERSION=24.7.5
ARG DRAWIO_SHA256_AMD64=196fd079cf2625be122d2ab94342ddb9547ff500d7398daaf35432aee1674973
ARG DRAWIO_SHA256_ARM64=e573eb171f88a66cdd057c733b0308ed31d03c9c9d7cf280b2f25aceda95033d

# Install system dependencies and drawio
# Using apt cache mounts to avoid re-downloading packages
# This is the most expensive step for this service
#
# The Draw.io .deb is arch-specific: upstream publishes drawio-amd64-*.deb and
# drawio-arm64-*.deb as separate assets. We select the one matching TARGETARCH
# and verify it against the matching checksum so apt can resolve native
# dependencies on both x86_64 and Apple Silicon hosts.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    set -eu && \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache && \
    apt-get update && \
    apt-get install -y \
        ca-certificates \
        curl \
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
    if [ "${TARGETARCH}" = "arm64" ]; then \
        DRAWIO_SHA256="${DRAWIO_SHA256_ARM64}"; \
    else \
        DRAWIO_SHA256="${DRAWIO_SHA256_AMD64}"; \
    fi && \
    curl --fail --location --retry 5 --retry-delay 5 --retry-all-errors \
        --output drawio.deb \
        "https://github.com/jgraph/drawio-desktop/releases/download/v${DRAWIO_VERSION}/drawio-${TARGETARCH}-${DRAWIO_VERSION}.deb" && \
    echo "${DRAWIO_SHA256}  drawio.deb" | sha256sum -c - && \
    apt-get -y -f install ./drawio.deb && \
    rm drawio.deb && \
    npm install -g svgo && \
    fc-cache -f -v

#=============================================================================
# FINAL STAGE - Install CLM and set up entrypoint
#=============================================================================
FROM deps AS final

ARG DOCKER_PATH=docker/drawio

# Copy clm repository (workers are now part of main package)
COPY . ./clm

# Install clm with drawio worker dependencies
# Workers are now at clm.workers.drawio
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./clm[drawio]

# Copy and set up entrypoint script
COPY ${DOCKER_PATH}/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
