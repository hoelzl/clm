#!/bin/bash

# Build script for CLX services
# Must be run from the root of the clx project

set -e

# Enable BuildKit
export DOCKER_BUILDKIT=1

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to get version from pyproject.toml
get_version() {
    if [ ! -f "clx-common/pyproject.toml" ]; then
        echo "0.2.2"  # fallback version
        return
    fi
    grep -m 1 '^version = ' clx-common/pyproject.toml | sed 's/version = "\(.*\)"/\1/'
}

# Function to build a service
build_service() {
    local service_name=$1
    local service_path="services/${service_name}"
    local version=$(get_version)

    # Image names without clx- prefix for pool manager
    local image_name="${service_name}"
    # Also tag with clx- prefix for backward compatibility
    local image_name_clx="clx-${service_name}"

    if [ ! -d "$service_path" ]; then
        echo -e "${RED}Error: Service directory $service_path not found${NC}"
        return 1
    fi

    if [ ! -f "$service_path/Dockerfile" ]; then
        echo -e "${RED}Error: Dockerfile not found in $service_path${NC}"
        return 1
    fi

    echo -e "${YELLOW}Building $service_name (version $version)...${NC}"

    docker build \
        -f "$service_path/Dockerfile" \
        -t "${image_name}:${version}" \
        -t "${image_name}:latest" \
        -t "${image_name_clx}:${version}" \
        -t "${image_name_clx}:latest" \
        --build-arg SERVICE_PATH="$service_path" \
        --build-arg COMMON_PATH=. \
        .

    echo -e "${GREEN}✓ Successfully built $image_name:$version${NC}"
    echo -e "${GREEN}  Tagged as: $image_name:$version, $image_name:latest${NC}"
    echo -e "${GREEN}  Tagged as: $image_name_clx:$version, $image_name_clx:latest${NC}"
}

# Check if we're in the right directory
if [ ! -d "services" ] || [ ! -d "clx-common" ]; then
    echo -e "${RED}Error: This script must be run from the root of the clx project${NC}"
    echo "Current directory: $(pwd)"
    echo "Expected to find: services/ and clx-common/ directories"
    exit 1
fi

# Available services
SERVICES=("drawio-converter" "notebook-processor" "plantuml-converter")

# If no arguments, build all services
if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Building all services...${NC}"
    for service in "${SERVICES[@]}"; do
        build_service "$service"
        echo ""
    done
    echo -e "${GREEN}✓ All services built successfully${NC}"
else
    # Build specified services
    for service in "$@"; do
        if [[ " ${SERVICES[@]} " =~ " ${service} " ]]; then
            build_service "$service"
            echo ""
        else
            echo -e "${RED}Error: Unknown service '$service'${NC}"
            echo "Available services: ${SERVICES[*]}"
            exit 1
        fi
    done
fi

echo -e "${GREEN}Done!${NC}"
