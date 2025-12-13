#!/bin/bash

# Build script for CLX services
# Must be run from the root of the clx project
#
# Usage:
#   ./build-services.sh                    # Build all services (notebook builds both variants)
#   ./build-services.sh plantuml           # Build plantuml only
#   ./build-services.sh notebook           # Build both notebook variants (lite + full)
#   ./build-services.sh notebook:lite      # Build only lite variant
#   ./build-services.sh notebook:full      # Build only full variant
#   ./build-services.sh --multiarch notebook:lite  # Build lite with multi-arch (amd64 + arm64)

set -e

# Enable BuildKit
export DOCKER_BUILDKIT=1

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse flags
MULTIARCH=false
while [[ "$1" == --* ]]; do
    case "$1" in
        --multiarch)
            MULTIARCH=true
            shift
            ;;
        *)
            echo -e "${RED}Unknown flag: $1${NC}"
            exit 1
            ;;
    esac
done

# Function to get version from pyproject.toml
get_version() {
    if [ ! -f "pyproject.toml" ]; then
        echo "0.5.0"  # fallback version
        return
    fi
    grep -m 1 '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
}

# Function to build a service (non-notebook)
build_service() {
    local service_name=$1
    local docker_path="docker/${service_name}"
    local version=$(get_version)

    # Map short names to full service names for image tags
    case "$service_name" in
        "plantuml")
            local full_service_name="plantuml-converter"
            ;;
        "drawio")
            local full_service_name="drawio-converter"
            ;;
        *)
            local full_service_name="${service_name}"
            ;;
    esac

    # Use only Hub namespace (works locally and on Docker Hub)
    local image_name="mhoelzl/clx-${full_service_name}"

    if [ ! -d "$docker_path" ]; then
        echo -e "${RED}Error: Docker directory $docker_path not found${NC}"
        return 1
    fi

    if [ ! -f "$docker_path/Dockerfile" ]; then
        echo -e "${RED}Error: Dockerfile not found in $docker_path${NC}"
        return 1
    fi

    echo -e "${YELLOW}Building $service_name (version $version)...${NC}"

    docker buildx build \
        -f "$docker_path/Dockerfile" \
        -t "${image_name}:${version}" \
        -t "${image_name}:latest" \
        --build-arg DOCKER_PATH="$docker_path" \
        .

    echo -e "${GREEN}✓ Successfully built ${image_name}:${version}${NC}"
    echo -e "${GREEN}  Tagged as: ${image_name}:${version}, ${image_name}:latest${NC}"
}

# Function to build notebook with a specific variant
build_notebook_variant() {
    local variant=$1  # "lite" or "full"
    local docker_path="docker/notebook"
    local version=$(get_version)

    # Use only Hub namespace (works locally and on Docker Hub)
    local image_name="mhoelzl/clx-notebook-processor"

    echo -e "${YELLOW}Building notebook-processor:${variant} (version $version)...${NC}"

    # Determine platform flags
    local platform_flags=""
    if [ "$MULTIARCH" = true ] && [ "$variant" = "lite" ]; then
        echo -e "${BLUE}  Building multi-arch (linux/amd64, linux/arm64)...${NC}"
        platform_flags="--platform linux/amd64,linux/arm64"
    fi

    # Build with variant
    if [ "$variant" = "full" ]; then
        # Full variant: default tags point to full
        docker buildx build \
            -f "$docker_path/Dockerfile" \
            --build-arg VARIANT=full \
            --build-arg DOCKER_PATH="$docker_path" \
            -t "${image_name}:${version}" \
            -t "${image_name}:${version}-full" \
            -t "${image_name}:latest" \
            -t "${image_name}:full" \
            .
    else
        # Lite variant
        if [ "$MULTIARCH" = true ]; then
            # Multi-arch build requires --push or --load with single platform
            # For now, just build for current platform with multi-arch disabled
            echo -e "${BLUE}  Note: Multi-arch build requires 'docker buildx create' setup.${NC}"
            echo -e "${BLUE}  Building for current platform only. Use 'docker buildx build --push' for multi-arch.${NC}"
        fi

        docker buildx build \
            -f "$docker_path/Dockerfile" \
            --build-arg VARIANT=lite \
            --build-arg DOCKER_PATH="$docker_path" \
            -t "${image_name}:${version}-lite" \
            -t "${image_name}:lite" \
            .
    fi

    echo -e "${GREEN}✓ Successfully built ${image_name}:${variant}${NC}"
    if [ "$variant" = "full" ]; then
        echo -e "${GREEN}  Tagged as: ${image_name}:${version}, ${image_name}:latest (default = full)${NC}"
        echo -e "${GREEN}  Tagged as: ${image_name}:${version}-full, ${image_name}:full${NC}"
    else
        echo -e "${GREEN}  Tagged as: ${image_name}:${version}-lite, ${image_name}:lite${NC}"
    fi
}

# Function to build notebook (both variants or specific)
build_notebook() {
    local variant=$1  # empty, "lite", or "full"

    if [ -z "$variant" ]; then
        # Build both variants
        echo -e "${YELLOW}Building both notebook variants...${NC}"
        echo ""
        build_notebook_variant "lite"
        echo ""
        build_notebook_variant "full"
    else
        build_notebook_variant "$variant"
    fi
}

# Check if we're in the right directory
if [ ! -d "docker" ] || [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}Error: This script must be run from the root of the clx project${NC}"
    echo "Current directory: $(pwd)"
    echo "Expected to find: docker/ directory and pyproject.toml file"
    exit 1
fi

# Available services (short names matching docker/ subdirectories)
SERVICES=("plantuml" "drawio" "notebook")

# If no arguments, build all services
if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Building all services...${NC}"
    for service in "${SERVICES[@]}"; do
        if [ "$service" = "notebook" ]; then
            build_notebook ""  # Build both variants
        else
            build_service "$service"
        fi
        echo ""
    done
    echo -e "${GREEN}✓ All services built successfully${NC}"
else
    # Build specified services
    for service_spec in "$@"; do
        # Parse service:variant format
        service="${service_spec%%:*}"
        variant="${service_spec#*:}"

        # If no colon, variant will equal service
        if [ "$variant" = "$service" ]; then
            variant=""
        fi

        if [ "$service" = "notebook" ]; then
            # Validate variant if specified
            if [ -n "$variant" ] && [ "$variant" != "lite" ] && [ "$variant" != "full" ]; then
                echo -e "${RED}Error: Unknown notebook variant '$variant'${NC}"
                echo "Available variants: lite, full"
                exit 1
            fi
            build_notebook "$variant"
            echo ""
        elif [[ " ${SERVICES[@]} " =~ " ${service} " ]]; then
            if [ -n "$variant" ]; then
                echo -e "${RED}Error: Service '$service' does not support variants${NC}"
                exit 1
            fi
            build_service "$service"
            echo ""
        else
            echo -e "${RED}Error: Unknown service '$service'${NC}"
            echo "Available services: ${SERVICES[*]}"
            echo "For notebook, you can specify variant: notebook:lite, notebook:full"
            exit 1
        fi
    done
fi

echo -e "${GREEN}Done!${NC}"
