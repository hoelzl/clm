#!/bin/bash
# CLX Test Environment Diagnostic Script
#
# This script checks the availability of external tools and reports which
# test categories can be run in the current environment.
#
# Usage:
#   ./.claude/diagnose-test-env.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_header() {
    echo ""
    echo -e "${BLUE}===================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===================================${NC}"
}

print_section() {
    echo ""
    echo -e "${CYAN}=== $1 ===${NC}"
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

# Track overall status
ALL_OK=true
PLANTUML_OK=false
DRAWIO_OK=false
XVFB_OK=false

print_header "CLX Test Environment Diagnostics"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Working directory: $(pwd)"

# Check if we're in CLX repository
print_section "Repository Check"
if [ -f "pyproject.toml" ] && [ -d "src/clx" ]; then
    print_success "Running from CLX repository root"
else
    print_error "Not in CLX repository root"
    echo ""
    echo "Please run this script from the CLX repository root directory."
    exit 1
fi

# Check Python environment
print_section "Python Environment"
if command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1)
    print_success "Python available: $PYTHON_VERSION"

    # Check if CLX is installed
    if python -c "import clx" 2>/dev/null; then
        CLX_VERSION=$(python -c "import clx; print(clx.__version__)")
        print_success "CLX package installed: v$CLX_VERSION"
    else
        print_error "CLX package not installed"
        print_info "Run: pip install -e ."
        ALL_OK=false
    fi

    # Check worker packages
    for pkg in "nb" "plantuml_converter" "drawio_converter"; do
        if python -c "import $pkg" 2>/dev/null; then
            print_success "$pkg package is installed"
        else
            print_warning "$pkg package not installed (may affect some tests)"
        fi
    done
else
    print_error "Python not found"
    ALL_OK=false
fi

# Check Java (required for PlantUML)
print_section "Java (PlantUML dependency)"
if java -version &> /dev/null; then
    JAVA_VERSION=$(java -version 2>&1 | head -n 1)
    print_success "Java available: $JAVA_VERSION"
else
    print_error "Java not available (required for PlantUML)"
    print_info "Install Java: apt-get install default-jre"
fi

# Check PlantUML
print_section "PlantUML"
PLANTUML_JAR_PATH="${PLANTUML_JAR:-/usr/local/share/plantuml-1.2024.6.jar}"

if [ -f "$PLANTUML_JAR_PATH" ]; then
    FILE_SIZE=$(stat -c%s "$PLANTUML_JAR_PATH")
    if [ $FILE_SIZE -gt 1000000 ]; then
        print_success "PlantUML JAR found: $PLANTUML_JAR_PATH ($(numfmt --to=iec-i $FILE_SIZE)B)"

        # Check if it's a Git LFS pointer
        if grep -q "git-lfs.github.com" "$PLANTUML_JAR_PATH" 2>/dev/null; then
            print_error "PlantUML JAR is a Git LFS pointer (not the actual file)"
            print_info "Run ./.claude/setup-test-env.sh to download it"
        elif java -version &> /dev/null; then
            print_success "PlantUML is functional"
            PLANTUML_OK=true

            # Check environment variable
            if [ -n "$PLANTUML_JAR" ]; then
                print_success "PLANTUML_JAR environment variable is set"
            else
                print_warning "PLANTUML_JAR environment variable not set"
                print_info "Export: export PLANTUML_JAR=\"$PLANTUML_JAR_PATH\""
            fi
        else
            print_error "PlantUML JAR exists but Java is not available"
        fi
    else
        print_error "PlantUML JAR is too small ($FILE_SIZE bytes) - likely a Git LFS pointer"
        print_info "Run ./.claude/setup-test-env.sh to download it"
    fi
else
    print_error "PlantUML JAR not found at: $PLANTUML_JAR_PATH"
    print_info "Run ./.claude/setup-test-env.sh to install it"
fi

# Check DrawIO
print_section "DrawIO"
if command -v drawio &> /dev/null; then
    DRAWIO_PATH=$(which drawio)
    print_success "DrawIO executable found: $DRAWIO_PATH"

    # Check if DRAWIO_EXECUTABLE is set
    if [ -n "$DRAWIO_EXECUTABLE" ]; then
        print_success "DRAWIO_EXECUTABLE environment variable is set"
    else
        print_info "DRAWIO_EXECUTABLE not set (using PATH: $DRAWIO_PATH)"
    fi

    DRAWIO_OK=true
else
    print_warning "DrawIO executable not found"
    print_info "DrawIO is optional - tests requiring it will be skipped"
    print_info "To install: Run ./.claude/setup-test-env.sh"
fi

# Check Xvfb (required for headless DrawIO)
print_section "Xvfb (for headless DrawIO rendering)"
if pgrep -x Xvfb > /dev/null; then
    XVFB_PID=$(pgrep -x Xvfb)
    print_success "Xvfb is running (PID: $XVFB_PID)"

    if [ -n "$DISPLAY" ]; then
        print_success "DISPLAY environment variable is set: $DISPLAY"
        XVFB_OK=true
    else
        print_error "DISPLAY environment variable not set"
        print_info "Export: export DISPLAY=:99"
    fi
else
    print_warning "Xvfb is not running"
    print_info "Xvfb is needed for headless DrawIO rendering"
    print_info "Start: Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &"
    print_info "Export: export DISPLAY=:99"
fi

# Summary: What tests can run?
print_header "Test Categories Available"

echo ""
echo -e "${CYAN}Test Category          Status       Requirements${NC}"
echo "──────────────────────────────────────────────────────────────────"

if [ "$ALL_OK" = true ]; then
    echo -e "${GREEN}✓ Unit Tests           Available    None${NC}"
else
    echo -e "${RED}✗ Unit Tests           Unavailable  Python, CLX package${NC}"
fi

if [ "$PLANTUML_OK" = true ]; then
    echo -e "${GREEN}✓ PlantUML Tests       Available    Java, PlantUML JAR${NC}"
else
    echo -e "${YELLOW}⊘ PlantUML Tests       Skipped      Java, PlantUML JAR${NC}"
fi

if [ "$DRAWIO_OK" = true ] && [ "$XVFB_OK" = true ]; then
    echo -e "${GREEN}✓ DrawIO Tests         Available    DrawIO, Xvfb, DISPLAY${NC}"
elif [ "$DRAWIO_OK" = true ]; then
    echo -e "${YELLOW}⊘ DrawIO Tests         Partial      Missing: Xvfb/DISPLAY${NC}"
else
    echo -e "${YELLOW}⊘ DrawIO Tests         Skipped      DrawIO, Xvfb, DISPLAY${NC}"
fi

if [ "$PLANTUML_OK" = true ] || [ "$DRAWIO_OK" = true ]; then
    echo -e "${GREEN}✓ Integration Tests    Available    At least one converter${NC}"
else
    echo -e "${YELLOW}⊘ Integration Tests    Partial      PlantUML or DrawIO${NC}"
fi

if [ "$PLANTUML_OK" = true ] && [ "$DRAWIO_OK" = true ]; then
    echo -e "${GREEN}✓ E2E Tests (Full)     Available    All tools${NC}"
elif [ "$PLANTUML_OK" = true ] || [ "$DRAWIO_OK" = true ]; then
    echo -e "${YELLOW}⊘ E2E Tests (Partial)  Available    Some tools${NC}"
else
    echo -e "${YELLOW}⊘ E2E Tests            Skipped      PlantUML and DrawIO${NC}"
fi

echo ""

# Recommended commands
print_header "Recommended Test Commands"

echo ""
echo "Based on your environment, you can run:"
echo ""

if [ "$ALL_OK" = true ]; then
    echo -e "  ${GREEN}pytest${NC}"
    echo "    → Run fast unit tests (always available)"
    echo ""
fi

if [ "$PLANTUML_OK" = true ] || [ "$DRAWIO_OK" = true ]; then
    echo -e "  ${GREEN}pytest -m integration${NC}"
    echo "    → Run integration tests with available converters"
    echo ""
fi

if [ "$PLANTUML_OK" = true ] || [ "$DRAWIO_OK" = true ]; then
    echo -e "  ${GREEN}pytest -m e2e${NC}"
    echo "    → Run end-to-end tests (some may be skipped)"
    echo ""
fi

if [ "$PLANTUML_OK" = true ] && [ "$DRAWIO_OK" = true ] && [ "$XVFB_OK" = true ]; then
    echo -e "  ${GREEN}pytest -m \"\"${NC}"
    echo "    → Run ALL tests (full test suite)"
    echo ""
fi

# Setup recommendations
if [ "$PLANTUML_OK" = false ] || [ "$DRAWIO_OK" = false ]; then
    print_header "Setup Recommendations"
    echo ""

    if [ "$PLANTUML_OK" = false ]; then
        echo "To enable PlantUML tests:"
        echo "  1. Run: ./.claude/setup-test-env.sh"
        echo "  2. Or manually install Java and download PlantUML JAR"
        echo ""
    fi

    if [ "$DRAWIO_OK" = false ]; then
        echo "To enable DrawIO tests:"
        echo "  1. Run: ./.claude/setup-test-env.sh"
        echo "  2. Start Xvfb: Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &"
        echo "  3. Set DISPLAY: export DISPLAY=:99"
        echo ""
    fi
fi

# Environment-specific notes
print_header "Environment Notes"
echo ""
echo "Claude Code Web:"
echo "  • PlantUML should work automatically (JAR in repository)"
echo "  • DrawIO downloads may timeout (can be skipped)"
echo "  • Tests automatically skip unavailable tools"
echo ""
echo "Local Development:"
echo "  • Full test suite available after setup"
echo "  • Run ./.claude/setup-test-env.sh for automated setup"
echo ""

print_header "Diagnostic Complete"
echo ""
