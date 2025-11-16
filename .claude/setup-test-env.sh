#!/bin/bash
# CLX Test Environment Setup Script
#
# This script automates the setup of a complete development and testing environment
# for the CLX project. It handles:
#   - Installing CLX package with all dependencies needed for testing
#   - Installing worker service packages
#   - Installing external tools (PlantUML, DrawIO)
#   - Setting up Xvfb for headless DrawIO rendering
#   - Setting required environment variables
#   - Verifying the environment is working
#
# Usage:
#   ./.claude/setup-test-env.sh
#
# Options:
#   --skip-verify    Skip environment verification at the end
#   --help           Show this help message
#
# Environment Variables:
#   CLX_SKIP_DOWNLOADS=1    Skip downloading external tools (useful in restricted environments)

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PLANTUML_VERSION="1.2024.6"
DRAWIO_VERSION="24.7.5"
SKIP_VERIFY=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-verify)
            SKIP_VERIFY=true
            shift
            ;;
        --help)
            head -n 20 "$0" | grep "^#" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run with --help for usage information"
            exit 1
            ;;
    esac
done

# Helper functions
print_header() {
    echo ""
    echo -e "${BLUE}===================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===================================${NC}"
}

print_step() {
    echo ""
    echo -e "${BLUE}=== $1 ===${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "  $1"
}

# Start
print_header "CLX Test Environment Setup"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Working directory: $(pwd)"
echo "User: $(whoami)"

# Verify we're in the CLX repository root
if [ ! -f "pyproject.toml" ] || [ ! -d "src/clx" ]; then
    print_error "This script must be run from the CLX repository root directory"
    exit 1
fi

print_success "Running from CLX repository root"

# Step 1: Install CLX package with all dependencies
print_step "Step 1/7: Installing CLX package with all dependencies"
print_info "This includes test dependencies, TUI, web, and development tools"

if python -m pip install -q -e ".[all]"; then
    print_success "CLX package installed with all extras"
else
    print_error "Failed to install CLX package"
    exit 1
fi

# Verify clx command is available
if command -v clx &> /dev/null; then
    print_success "clx command is available: $(which clx)"
else
    print_warning "clx command not found in PATH (this might be OK if using 'python -m clx.cli')"
fi

# Step 2: Install worker service packages
print_step "Step 2/7: Installing worker service packages"

services=("notebook-processor" "drawio-converter" "plantuml-converter")
for service in "${services[@]}"; do
    print_info "Installing $service..."
    if python -m pip install -q -e "./services/$service"; then
        print_success "$service installed"
    else
        print_error "Failed to install $service"
        exit 1
    fi
done

# Step 3: Install PlantUML
print_step "Step 3/7: Installing PlantUML"

PLANTUML_JAR="/usr/local/share/plantuml-${PLANTUML_VERSION}.jar"
REPO_PLANTUML_JAR="services/plantuml-converter/plantuml-${PLANTUML_VERSION}.jar"

if [ -f "$PLANTUML_JAR" ] && [ $(stat -c%s "$PLANTUML_JAR") -gt 1000000 ]; then
    print_success "PlantUML already installed at: $PLANTUML_JAR"
else
    # Check if repository file exists and is not a Git LFS pointer
    if [ -f "$REPO_PLANTUML_JAR" ] && ! grep -q "git-lfs.github.com" "$REPO_PLANTUML_JAR" 2>/dev/null; then
        print_info "Using PlantUML JAR from repository..."
        cp "$REPO_PLANTUML_JAR" "$PLANTUML_JAR"
        print_success "PlantUML installed from repository: $PLANTUML_JAR"
    elif [ -z "$CLX_SKIP_DOWNLOADS" ]; then
        print_info "Downloading PlantUML from GitHub releases..."
        print_info "(Repository file is a Git LFS pointer or missing)"

        if wget -q --show-progress --timeout=120 \
            "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar" \
            -O "$PLANTUML_JAR"; then

            # Verify the download
            if [ -f "$PLANTUML_JAR" ] && [ $(stat -c%s "$PLANTUML_JAR") -gt 1000000 ]; then
                print_success "PlantUML downloaded successfully: $PLANTUML_JAR ($(stat -c%s "$PLANTUML_JAR" | numfmt --to=iec-i)B)"
            else
                print_error "PlantUML download failed (file too small or missing)"
                rm -f "$PLANTUML_JAR"
                print_warning "PlantUML will not be available. Integration/E2E tests requiring PlantUML will fail."
            fi
        else
            print_error "Failed to download PlantUML"
            rm -f "$PLANTUML_JAR"
            print_warning "PlantUML will not be available. Integration/E2E tests requiring PlantUML will fail."
        fi
    else
        print_warning "Skipping PlantUML download (CLX_SKIP_DOWNLOADS is set)"
        print_warning "PlantUML will not be available. Integration/E2E tests requiring PlantUML will fail."
    fi
fi

# Create PlantUML wrapper script
if [ -f "$PLANTUML_JAR" ]; then
    if [ ! -f /usr/local/bin/plantuml ]; then
        print_info "Creating PlantUML wrapper script..."
        cat > /usr/local/bin/plantuml << 'EOF'
#!/bin/bash
PLANTUML_JAR="/usr/local/share/plantuml-1.2024.6.jar"
exec java -DPLANTUML_LIMIT_SIZE=8192 -jar "$PLANTUML_JAR" "$@"
EOF
        chmod +x /usr/local/bin/plantuml
        print_success "PlantUML wrapper script created at /usr/local/bin/plantuml"
    else
        print_success "PlantUML wrapper script already exists"
    fi

    # Test PlantUML
    if java -version &> /dev/null; then
        print_success "Java is available for PlantUML"
    else
        print_error "Java is not available - PlantUML will not work"
    fi
fi

# Step 4: Install DrawIO
print_step "Step 4/7: Installing DrawIO"

DRAWIO_DEB="/tmp/drawio-amd64-${DRAWIO_VERSION}.deb"
REPO_DRAWIO_DEB="services/drawio-converter/drawio-amd64-${DRAWIO_VERSION}.deb"

if command -v drawio &> /dev/null; then
    print_success "DrawIO already installed at: $(which drawio)"
else
    if [ -f "$DRAWIO_DEB" ] && [ $(stat -c%s "$DRAWIO_DEB") -gt 1000000 ]; then
        print_info "Using cached DrawIO .deb at $DRAWIO_DEB"
    else
        # Check if repository file exists and is not a Git LFS pointer
        if [ -f "$REPO_DRAWIO_DEB" ] && ! grep -q "git-lfs.github.com" "$REPO_DRAWIO_DEB" 2>/dev/null; then
            print_info "Using DrawIO .deb from repository..."
            cp "$REPO_DRAWIO_DEB" "$DRAWIO_DEB"
        elif [ -z "$CLX_SKIP_DOWNLOADS" ]; then
            print_info "Downloading DrawIO from GitHub releases..."
            print_info "(Repository file is a Git LFS pointer or missing)"

            if wget -q --show-progress --timeout=120 \
                "https://github.com/jgraph/drawio-desktop/releases/download/v${DRAWIO_VERSION}/drawio-amd64-${DRAWIO_VERSION}.deb" \
                -O "$DRAWIO_DEB"; then

                # Verify the download
                if [ -f "$DRAWIO_DEB" ] && [ $(stat -c%s "$DRAWIO_DEB") -lt 1000000 ]; then
                    print_error "DrawIO download failed (file too small: $(stat -c%s "$DRAWIO_DEB") bytes)"
                    rm -f "$DRAWIO_DEB"
                else
                    print_success "DrawIO downloaded successfully: $DRAWIO_DEB ($(stat -c%s "$DRAWIO_DEB" | numfmt --to=iec-i)B)"
                fi
            else
                print_error "Failed to download DrawIO"
                rm -f "$DRAWIO_DEB"
            fi
        else
            print_warning "Skipping DrawIO download (CLX_SKIP_DOWNLOADS is set)"
        fi
    fi

    # Extract DrawIO binary
    if [ -f "$DRAWIO_DEB" ] && [ $(stat -c%s "$DRAWIO_DEB") -gt 1000000 ]; then
        print_info "Extracting DrawIO binary..."
        dpkg -x "$DRAWIO_DEB" /tmp/drawio-extract
        ln -sf /tmp/drawio-extract/opt/drawio/drawio /usr/local/bin/drawio
        print_success "DrawIO installed at /usr/local/bin/drawio"
    else
        print_warning "DrawIO will not be available. Integration/E2E tests requiring DrawIO will fail."
    fi
fi

# Step 5: Setup Xvfb for headless DrawIO rendering
print_step "Step 5/7: Setting up Xvfb for headless rendering"

if pgrep -x Xvfb > /dev/null; then
    XVFB_PID=$(pgrep -x Xvfb)
    print_success "Xvfb is already running (PID: $XVFB_PID)"
else
    print_info "Starting Xvfb on display :99..."
    if Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &> /tmp/xvfb.log &
    then
        sleep 2  # Give Xvfb time to start
        if pgrep -x Xvfb > /dev/null; then
            XVFB_PID=$(pgrep -x Xvfb)
            print_success "Xvfb started successfully (PID: $XVFB_PID)"
        else
            print_error "Xvfb failed to start. Check /tmp/xvfb.log for details"
        fi
    else
        print_error "Failed to start Xvfb"
    fi
fi

# Step 6: Set environment variables
print_step "Step 6/7: Setting environment variables"

# Set PLANTUML_JAR if PlantUML is installed
if [ -f "$PLANTUML_JAR" ]; then
    export PLANTUML_JAR="$PLANTUML_JAR"
    print_success "PLANTUML_JAR=$PLANTUML_JAR"

    # Add to .bashrc for persistence
    if ! grep -q "PLANTUML_JAR" ~/.bashrc 2>/dev/null; then
        echo "export PLANTUML_JAR=\"$PLANTUML_JAR\"" >> ~/.bashrc
        print_info "Added PLANTUML_JAR to ~/.bashrc"
    fi
fi

# Set DISPLAY for Xvfb
export DISPLAY=:99
print_success "DISPLAY=$DISPLAY"

# Add to .bashrc for persistence
if ! grep -q "DISPLAY=:99" ~/.bashrc 2>/dev/null; then
    echo "export DISPLAY=:99" >> ~/.bashrc
    print_info "Added DISPLAY to ~/.bashrc"
fi

# Optional: Set DRAWIO_EXECUTABLE explicitly
if command -v drawio &> /dev/null; then
    export DRAWIO_EXECUTABLE=$(which drawio)
    print_success "DRAWIO_EXECUTABLE=$DRAWIO_EXECUTABLE"
fi

print_info ""
print_info "Environment variables are set for this session and added to ~/.bashrc"
print_info "To apply them to your current shell, run: source ~/.bashrc"

# Step 7: Verify environment
if [ "$SKIP_VERIFY" = false ]; then
    print_step "Step 7/7: Verifying environment"

    ALL_OK=true

    # Check Python packages
    print_info "Checking Python packages..."
    if python -c "import clx; print(f'  clx version: {clx.__version__}')" 2>/dev/null; then
        print_success "CLX package is importable"
    else
        print_error "CLX package is not importable"
        ALL_OK=false
    fi

    # Check worker packages
    # Note: notebook-processor package is imported as "nb"
    if python -c "import nb" 2>/dev/null; then
        print_success "nb (notebook processor) package is importable"
    else
        print_error "nb (notebook processor) package is not importable"
        ALL_OK=false
    fi

    for service in "drawio_converter" "plantuml_converter"; do
        if python -c "import $service" 2>/dev/null; then
            print_success "$service package is importable"
        else
            print_error "$service package is not importable"
            ALL_OK=false
        fi
    done

    # Check external tools
    print_info ""
    print_info "Checking external tools..."

    # Java (required for PlantUML)
    if java -version &> /dev/null; then
        JAVA_VERSION=$(java -version 2>&1 | head -n 1)
        print_success "Java is available: $JAVA_VERSION"
    else
        print_error "Java is not available (required for PlantUML)"
        ALL_OK=false
    fi

    # PlantUML
    if [ -f "$PLANTUML_JAR" ]; then
        print_success "PlantUML JAR exists: $PLANTUML_JAR"
        if command -v plantuml &> /dev/null; then
            print_success "plantuml command is available"
        else
            print_warning "plantuml command not found (wrapper script may be missing)"
        fi
    else
        print_warning "PlantUML JAR not found (PlantUML tests will fail)"
    fi

    # DrawIO
    if command -v drawio &> /dev/null; then
        print_success "DrawIO command is available: $(which drawio)"
    else
        print_warning "DrawIO not found (DrawIO tests will fail)"
    fi

    # Xvfb
    if pgrep -x Xvfb > /dev/null; then
        print_success "Xvfb is running (required for headless DrawIO)"
    else
        print_error "Xvfb is not running (required for DrawIO tests)"
        ALL_OK=false
    fi

    # DISPLAY variable
    if [ -n "$DISPLAY" ]; then
        print_success "DISPLAY environment variable is set: $DISPLAY"
    else
        print_error "DISPLAY environment variable is not set"
        ALL_OK=false
    fi

    # Summary
    print_info ""
    if [ "$ALL_OK" = true ]; then
        print_header "Environment Setup Complete ✓"
        echo -e "${GREEN}All checks passed! Your environment is ready for running CLX tests.${NC}"
        echo ""
        echo "You can now run tests with:"
        echo "  pytest                    # Unit tests only (fast)"
        echo "  pytest -m integration     # Integration tests"
        echo "  pytest -m e2e            # End-to-end tests"
        echo "  pytest -m \"\"              # All tests"
    else
        print_header "Environment Setup Complete (with warnings)"
        echo -e "${YELLOW}Some checks failed. See warnings above.${NC}"
        echo "You may still be able to run unit tests, but integration/e2e tests may fail."
    fi
else
    print_info "Skipping verification (--skip-verify flag set)"
    print_header "Environment Setup Complete"
fi

echo ""
echo "For more information, see CLAUDE.md"
echo ""
