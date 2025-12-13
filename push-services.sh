#!/bin/bash

# Push script for CLX service images to Docker Hub
# Usage: ./push-services.sh [service1 service2 ...]
#
# Images are pushed to the mhoelzl/ namespace (matching build-services.sh)

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Hub namespace (must match build-services.sh)
HUB_NAMESPACE="mhoelzl"

# Function to push a service
push_service() {
    local service_name=$1
    local version=$2
    local image_version="${HUB_NAMESPACE}/clx-${service_name}:${version}"
    local image_latest="${HUB_NAMESPACE}/clx-${service_name}:latest"

    echo -e "${YELLOW}Pushing $service_name...${NC}"

    # Check if image exists (built with Hub namespace)
    if ! docker image inspect "$image_version" &> /dev/null; then
        echo -e "${RED}Error: Image $image_version not found${NC}"
        echo -e "${BLUE}Run ./build-services.sh first${NC}"
        return 1
    fi

    # Push version tag
    echo -e "${BLUE}Pushing $image_version${NC}"
    docker push "$image_version"

    # Push latest tag
    echo -e "${BLUE}Pushing $image_latest${NC}"
    docker push "$image_latest"

    echo -e "${GREEN}✓ Successfully pushed $service_name${NC}"
}

# Available services
SERVICES=("drawio-converter" "notebook-processor" "plantuml-converter")

# Function to get version from pyproject.toml (same as build-services.sh)
get_version() {
    if [ ! -f "pyproject.toml" ]; then
        echo "0.5.0"  # fallback version
        return
    fi
    grep -m 1 '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'
}

# Get version dynamically
VERSION=$(get_version)

# Show usage
show_usage() {
    echo "Usage: $0 [service1 service2 ...]"
    echo ""
    echo "Pushes CLX Docker images to Docker Hub (${HUB_NAMESPACE}/clx-*)"
    echo ""
    echo "Examples:"
    echo "  $0                       # Push all services"
    echo "  $0 drawio-converter      # Push specific service"
    echo ""
    echo "Available services: ${SERVICES[*]}"
    echo ""
    echo "Note: You must be logged in to Docker Hub first:"
    echo "  docker login"
}

# Check for help flag
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    show_usage
    exit 0
fi

# Verify logged in to Docker Hub
if ! docker info 2>/dev/null | grep -q "Username:"; then
    echo -e "${YELLOW}Warning: Not logged in to Docker Hub${NC}"
    echo -e "${BLUE}Please login first:${NC}"
    echo "  docker login"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# If no services specified, push all
if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Pushing all services to Docker Hub as ${HUB_NAMESPACE}/clx-*:${VERSION}${NC}"
    echo ""
    FAILED=0
    for service in "${SERVICES[@]}"; do
        if ! push_service "$service" "$VERSION"; then
            FAILED=1
        fi
        echo ""
    done

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ All services pushed successfully${NC}"
    else
        echo -e "${RED}✗ Some services failed to push${NC}"
        exit 1
    fi
else
    # Push specified services
    FAILED=0
    for service in "$@"; do
        if [[ " ${SERVICES[@]} " =~ " ${service} " ]]; then
            if ! push_service "$service" "$VERSION"; then
                FAILED=1
            fi
            echo ""
        else
            echo -e "${RED}Error: Unknown service '$service'${NC}"
            echo "Available services: ${SERVICES[*]}"
            exit 1
        fi
    done

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}Done!${NC}"
    else
        exit 1
    fi
fi
