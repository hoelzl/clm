#!/usr/bin/env python3
"""
CLX Development Environment Setup Script

This script:
1. Creates a virtual environment (if none exists)
2. Installs the clx package in development mode
3. Installs all service packages in development mode
4. Checks for external tool availability (PlantUML, DrawIO)
5. Provides guidance on missing dependencies
"""

import os
import sys
import subprocess
import platform
from pathlib import Path
from typing import Optional, Tuple


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

    @classmethod
    def disable(cls):
        """Disable colors for Windows or non-terminal output"""
        cls.GREEN = cls.YELLOW = cls.RED = cls.BLUE = cls.BOLD = cls.END = ''


# Disable colors on Windows unless in a modern terminal
if platform.system() == 'Windows' and not os.getenv('WT_SESSION'):
    Colors.disable()


def print_step(step: str):
    """Print a step header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{step}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}\n")


def print_success(message: str):
    """Print a success message"""
    print(f"{Colors.GREEN}✓ {message}{Colors.END}")


def print_warning(message: str):
    """Print a warning message"""
    print(f"{Colors.YELLOW}⚠ {message}{Colors.END}")


def print_error(message: str):
    """Print an error message"""
    print(f"{Colors.RED}✗ {message}{Colors.END}")


def print_info(message: str):
    """Print an info message"""
    print(f"{Colors.BLUE}ℹ {message}{Colors.END}")


def get_repo_root() -> Path:
    """Get the repository root directory"""
    return Path(__file__).parent.absolute()


def get_venv_path() -> Path:
    """Get the virtual environment path"""
    return get_repo_root() / ".venv"


def get_python_executable() -> str:
    """Get the appropriate Python executable name for this platform"""
    return "python" if platform.system() == "Windows" else "python3"


def is_venv_active() -> bool:
    """Check if a virtual environment is currently active"""
    return hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )


def get_venv_python() -> Path:
    """Get the path to the Python executable in the virtual environment"""
    venv_path = get_venv_path()
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "python.exe"
    else:
        return venv_path / "bin" / "python"


def get_venv_pip() -> Path:
    """Get the path to pip in the virtual environment"""
    venv_path = get_venv_path()
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "pip.exe"
    else:
        return venv_path / "bin" / "pip"


def create_virtual_environment() -> bool:
    """Create a virtual environment if it doesn't exist"""
    print_step("Step 1: Virtual Environment Setup")

    venv_path = get_venv_path()

    if venv_path.exists():
        print_success(f"Virtual environment already exists at: {venv_path}")
        return True

    print_info(f"Creating virtual environment at: {venv_path}")

    try:
        python_exec = get_python_executable()
        subprocess.run(
            [python_exec, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True
        )
        print_success(f"Virtual environment created successfully")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to create virtual environment: {e}")
        print_error(f"Error output: {e.stderr.decode()}")
        return False


def upgrade_pip() -> bool:
    """Upgrade pip in the virtual environment"""
    print_info("Upgrading pip to latest version...")

    try:
        pip_exec = get_venv_pip()
        subprocess.run(
            [str(pip_exec), "install", "--upgrade", "pip"],
            check=True,
            capture_output=True
        )
        print_success("pip upgraded successfully")
        return True
    except subprocess.CalledProcessError as e:
        print_warning(f"Failed to upgrade pip (continuing anyway): {e}")
        return False


def install_package(package_path: Path, package_name: str) -> bool:
    """Install a package in development mode"""
    print_info(f"Installing {package_name}...")

    try:
        pip_exec = get_venv_pip()
        subprocess.run(
            [str(pip_exec), "install", "-e", str(package_path)],
            check=True,
            capture_output=True
        )
        print_success(f"{package_name} installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to install {package_name}: {e}")
        print_error(f"Error output: {e.stderr.decode()}")
        return False


def install_clx_package() -> bool:
    """Install the main clx package"""
    print_step("Step 2: Install CLX Package")

    repo_root = get_repo_root()
    return install_package(repo_root, "clx")


def install_service_packages() -> bool:
    """Install all service packages"""
    print_step("Step 3: Install Service Packages")

    repo_root = get_repo_root()
    services_dir = repo_root / "services"

    if not services_dir.exists():
        print_error(f"Services directory not found: {services_dir}")
        return False

    services = [
        ("notebook-processor", "Notebook Processor"),
        ("plantuml-converter", "PlantUML Converter"),
        ("drawio-converter", "DrawIO Converter"),
    ]

    all_success = True
    for service_dir, service_name in services:
        service_path = services_dir / service_dir
        if service_path.exists():
            success = install_package(service_path, service_name)
            all_success = all_success and success
        else:
            print_warning(f"Service directory not found: {service_path}")
            all_success = False

    return all_success


def check_command(command: str) -> Tuple[bool, Optional[str]]:
    """Check if a command is available and return its version if possible"""
    try:
        # Try to get version
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            version = result.stdout.strip().split('\n')[0]
            return True, version
        return True, None
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        return False, None


def check_java() -> Tuple[bool, Optional[str]]:
    """Check if Java is available"""
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        # Java outputs version to stderr
        version_output = result.stderr.strip().split('\n')[0]
        return True, version_output
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        return False, None


def check_plantuml() -> bool:
    """Check for PlantUML availability"""
    print_step("Step 4: Check External Tools - PlantUML")

    # Check for PLANTUML_JAR environment variable
    plantuml_jar = os.getenv("PLANTUML_JAR")

    if plantuml_jar:
        plantuml_path = Path(plantuml_jar)
        if plantuml_path.exists():
            print_success(f"PlantUML JAR found via PLANTUML_JAR: {plantuml_path}")

            # Check Java
            java_available, java_version = check_java()
            if java_available:
                print_success(f"Java found: {java_version}")
                return True
            else:
                print_error("Java not found (required for PlantUML)")
                print_info("Install Java: https://www.oracle.com/java/technologies/downloads/")
                return False
        else:
            print_warning(f"PLANTUML_JAR set but file not found: {plantuml_path}")

    # Check repository location
    repo_root = get_repo_root()
    repo_jar = repo_root / "services" / "plantuml-converter" / "plantuml-1.2024.6.jar"

    if repo_jar.exists():
        print_success(f"PlantUML JAR found in repository: {repo_jar}")
        print_info(f"Set environment variable: PLANTUML_JAR={repo_jar}")

        # Check Java
        java_available, java_version = check_java()
        if java_available:
            print_success(f"Java found: {java_version}")
        else:
            print_error("Java not found (required for PlantUML)")
            print_info("Install Java: https://www.oracle.com/java/technologies/downloads/")

        return java_available

    # Check system plantuml command
    plantuml_available, plantuml_version = check_command("plantuml")
    if plantuml_available:
        print_success(f"PlantUML command found: {plantuml_version or 'version unknown'}")
        return True

    # Not found
    print_error("PlantUML not found")
    print_info("Download from: https://github.com/plantuml/plantuml/releases/download/v1.2024.6/plantuml-1.2024.6.jar")
    print_info(f"Or check: {repo_jar}")
    print_info("Set PLANTUML_JAR environment variable to JAR path")

    return False


def check_drawio() -> bool:
    """Check for DrawIO availability"""
    print_step("Step 5: Check External Tools - DrawIO")

    # Check for DRAWIO_EXECUTABLE environment variable
    drawio_exec = os.getenv("DRAWIO_EXECUTABLE")

    if drawio_exec:
        drawio_path = Path(drawio_exec)
        if drawio_path.exists():
            print_success(f"DrawIO found via DRAWIO_EXECUTABLE: {drawio_path}")
            return True
        else:
            print_warning(f"DRAWIO_EXECUTABLE set but file not found: {drawio_path}")

    # Check common locations
    system = platform.system()

    if system == "Linux":
        common_paths = [
            Path("/usr/local/bin/drawio"),
            Path("/usr/bin/drawio"),
            Path("/opt/drawio/drawio"),
        ]
    elif system == "Darwin":  # macOS
        common_paths = [
            Path("/Applications/draw.io.app/Contents/MacOS/draw.io"),
        ]
    elif system == "Windows":
        common_paths = [
            Path("C:/Program Files/draw.io/draw.io.exe"),
            Path(os.path.expanduser("~/AppData/Local/Programs/draw.io/draw.io.exe")),
        ]
    else:
        common_paths = []

    for path in common_paths:
        if path.exists():
            print_success(f"DrawIO found at: {path}")
            print_info(f"Set environment variable: DRAWIO_EXECUTABLE={path}")
            return True

    # Check drawio command
    drawio_available, drawio_version = check_command("drawio")
    if drawio_available:
        print_success(f"DrawIO command found: {drawio_version or 'version unknown'}")
        return True

    # Not found
    print_warning("DrawIO not found (optional)")
    print_info("DrawIO is required for converting Draw.io diagrams")

    if system == "Linux":
        print_info("Download .deb from: https://github.com/jgraph/drawio-desktop/releases/download/v24.7.5/drawio-amd64-24.7.5.deb")
        print_info("Or install via package manager")
    elif system == "Darwin":
        print_info("Install from: https://github.com/jgraph/drawio-desktop/releases")
        print_info("Or use: brew install --cask drawio")
    elif system == "Windows":
        print_info("Download from: https://github.com/jgraph/drawio-desktop/releases")

    print_info("Set DRAWIO_EXECUTABLE environment variable to executable path")

    return False


def check_xvfb() -> bool:
    """Check for Xvfb (Linux only, for headless DrawIO)"""
    if platform.system() != "Linux":
        return True  # Not needed on non-Linux

    print_info("Checking for Xvfb (required for headless DrawIO on Linux)...")

    xvfb_available, _ = check_command("Xvfb")
    if xvfb_available:
        print_success("Xvfb found")
        return True

    print_warning("Xvfb not found (required for headless DrawIO rendering)")
    print_info("Install: sudo apt-get install xvfb")
    print_info("Start: Xvfb :99 -screen 0 1024x768x24 -ac +extension GLX +render -noreset &")
    print_info("Set: export DISPLAY=:99")

    return False


def print_activation_instructions():
    """Print instructions for activating the virtual environment"""
    print_step("Activation Instructions")

    venv_path = get_venv_path()
    system = platform.system()

    print_info("To activate the virtual environment:")
    print()

    if system == "Windows":
        print(f"  {Colors.BOLD}PowerShell:{Colors.END}")
        print(f"    {venv_path}\\Scripts\\Activate.ps1")
        print()
        print(f"  {Colors.BOLD}Command Prompt:{Colors.END}")
        print(f"    {venv_path}\\Scripts\\activate.bat")
    else:
        print(f"  {Colors.BOLD}Bash/Zsh:{Colors.END}")
        print(f"    source {venv_path}/bin/activate")
        print()
        print(f"  {Colors.BOLD}Fish:{Colors.END}")
        print(f"    source {venv_path}/bin/activate.fish")

    print()
    print_info("After activation, you can run:")
    print(f"  {Colors.BOLD}clx --help{Colors.END}")
    print(f"  {Colors.BOLD}python -c \"from clx import Course; print('✓ CLX ready!');\"{Colors.END}")


def print_summary(venv_created: bool, clx_installed: bool, services_installed: bool,
                  plantuml_ok: bool, drawio_ok: bool):
    """Print a summary of the setup"""
    print_step("Setup Summary")

    print(f"Virtual Environment:    {Colors.GREEN if venv_created else Colors.RED}{'✓' if venv_created else '✗'}{Colors.END}")
    print(f"CLX Package:            {Colors.GREEN if clx_installed else Colors.RED}{'✓' if clx_installed else '✗'}{Colors.END}")
    print(f"Service Packages:       {Colors.GREEN if services_installed else Colors.RED}{'✓' if services_installed else '✗'}{Colors.END}")
    print(f"PlantUML:               {Colors.GREEN if plantuml_ok else Colors.YELLOW}{'✓' if plantuml_ok else '⚠'}{Colors.END}")
    print(f"DrawIO:                 {Colors.GREEN if drawio_ok else Colors.YELLOW}{'✓' if drawio_ok else '⚠ (optional)'}{Colors.END}")

    print()

    if venv_created and clx_installed and services_installed:
        print_success("Core setup completed successfully!")

        if not plantuml_ok or not drawio_ok:
            print()
            print_warning("Some external tools are missing:")
            if not plantuml_ok:
                print("  - PlantUML: Required for PlantUML diagram conversion")
            if not drawio_ok:
                print("  - DrawIO: Optional for Draw.io diagram conversion")
            print()
            print_info("See messages above for installation instructions")
    else:
        print_error("Setup incomplete. Please review errors above.")
        return False

    return True


def main():
    """Main setup function"""
    print(f"{Colors.BOLD}CLX Development Environment Setup{Colors.END}")
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.system()} {platform.release()}")

    # Check if running in venv
    if is_venv_active():
        print_warning("Running inside an active virtual environment")
        print_warning("This script will create/use the .venv directory in the repo")
        print_info("Consider deactivating first: deactivate")

        # Check for --yes flag or if stdin is not a tty
        if '--yes' in sys.argv or '-y' in sys.argv or not sys.stdin.isatty():
            print_info("Continuing automatically (non-interactive mode)")
        else:
            print()
            response = input("Continue anyway? [y/N]: ")
            if response.lower() not in ['y', 'yes']:
                print("Aborted.")
                return 1

    # Step 1: Create virtual environment
    venv_created = create_virtual_environment()
    if not venv_created:
        print_error("Cannot continue without virtual environment")
        return 1

    # Upgrade pip
    upgrade_pip()

    # Step 2: Install CLX package
    clx_installed = install_clx_package()

    # Step 3: Install service packages
    services_installed = install_service_packages()

    # Step 4-5: Check external tools
    plantuml_ok = check_plantuml()
    drawio_ok = check_drawio()

    # Check Xvfb on Linux
    if platform.system() == "Linux":
        check_xvfb()

    # Print activation instructions
    print_activation_instructions()

    # Print summary
    success = print_summary(venv_created, clx_installed, services_installed,
                           plantuml_ok, drawio_ok)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
