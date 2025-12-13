"""Docker image build and push commands.

This module provides commands for building and pushing CLX worker Docker images.
"""

import re
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console

# Hub namespace for Docker images
HUB_NAMESPACE = "mhoelzl"

# Available services (short names matching docker/ subdirectories)
AVAILABLE_SERVICES = ["plantuml", "drawio", "notebook"]

# Map short names to full service names
SERVICE_NAME_MAP = {
    "plantuml": "plantuml-converter",
    "drawio": "drawio-converter",
    "notebook": "notebook-processor",
}

# Console for colored output
console = Console(file=sys.stderr)


def get_version() -> str:
    """Get version from pyproject.toml.

    Returns:
        Version string, or "0.5.0" as fallback.
    """
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        return "0.5.0"

    try:
        content = pyproject_path.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass

    return "0.5.0"


def get_project_root() -> Path | None:
    """Find the project root directory.

    Looks for a directory containing both 'docker/' and 'pyproject.toml'.

    Returns:
        Path to project root, or None if not found.
    """
    # Start from current directory and walk up
    current = Path.cwd()

    for path in [current, *current.parents]:
        if (path / "docker").is_dir() and (path / "pyproject.toml").is_file():
            return path

    return None


def run_docker_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a docker command.

    Args:
        args: Command arguments (without 'docker' prefix).
        check: If True, raise on non-zero exit code.

    Returns:
        CompletedProcess instance.
    """
    cmd = ["docker", *args]
    return subprocess.run(cmd, check=check, capture_output=False)


def build_service(service_name: str, version: str, docker_path: Path) -> bool:
    """Build a non-notebook service.

    Args:
        service_name: Short service name (plantuml, drawio).
        version: Version string for tagging.
        docker_path: Path to docker directory.

    Returns:
        True if build succeeded, False otherwise.
    """
    full_service_name = SERVICE_NAME_MAP.get(service_name, service_name)
    image_name = f"{HUB_NAMESPACE}/clx-{full_service_name}"

    dockerfile = docker_path / "Dockerfile"
    if not dockerfile.exists():
        console.print(f"[red]Error: Dockerfile not found in {docker_path}[/red]")
        return False

    console.print(f"[yellow]Building {service_name} (version {version})...[/yellow]")

    try:
        run_docker_command(
            [
                "buildx",
                "build",
                "-f",
                str(dockerfile),
                "-t",
                f"{image_name}:{version}",
                "-t",
                f"{image_name}:latest",
                "--build-arg",
                f"DOCKER_PATH=docker/{service_name}",
                ".",
            ]
        )

        console.print(f"[green]Successfully built {image_name}:{version}[/green]")
        console.print(f"[green]  Tagged as: {image_name}:{version}, {image_name}:latest[/green]")
        return True

    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to build {image_name}[/red]")
        return False


def build_notebook_variant(variant: str, version: str, docker_path: Path) -> bool:
    """Build a notebook variant.

    Args:
        variant: "lite" or "full".
        version: Version string for tagging.
        docker_path: Path to docker/notebook directory.

    Returns:
        True if build succeeded, False otherwise.
    """
    image_name = f"{HUB_NAMESPACE}/clx-notebook-processor"

    console.print(f"[yellow]Building notebook-processor:{variant} (version {version})...[/yellow]")

    try:
        if variant == "full":
            # Full variant: default tags point to full
            run_docker_command(
                [
                    "buildx",
                    "build",
                    "-f",
                    str(docker_path / "Dockerfile"),
                    "--build-arg",
                    "VARIANT=full",
                    "--build-arg",
                    "DOCKER_PATH=docker/notebook",
                    "-t",
                    f"{image_name}:{version}",
                    "-t",
                    f"{image_name}:{version}-full",
                    "-t",
                    f"{image_name}:latest",
                    "-t",
                    f"{image_name}:full",
                    ".",
                ]
            )
            console.print(f"[green]Successfully built {image_name}:{variant}[/green]")
            console.print(
                f"[green]  Tagged as: {image_name}:{version}, {image_name}:latest "
                f"(default = full)[/green]"
            )
            console.print(
                f"[green]  Tagged as: {image_name}:{version}-full, {image_name}:full[/green]"
            )
        else:
            # Lite variant
            run_docker_command(
                [
                    "buildx",
                    "build",
                    "-f",
                    str(docker_path / "Dockerfile"),
                    "--build-arg",
                    "VARIANT=lite",
                    "--build-arg",
                    "DOCKER_PATH=docker/notebook",
                    "-t",
                    f"{image_name}:{version}-lite",
                    "-t",
                    f"{image_name}:lite",
                    ".",
                ]
            )
            console.print(f"[green]Successfully built {image_name}:{variant}[/green]")
            console.print(
                f"[green]  Tagged as: {image_name}:{version}-lite, {image_name}:lite[/green]"
            )

        return True

    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to build {image_name}:{variant}[/red]")
        return False


def build_notebook(variant: str | None, version: str, docker_path: Path) -> bool:
    """Build notebook service (one or both variants).

    Args:
        variant: "lite", "full", or None for both.
        version: Version string for tagging.
        docker_path: Path to docker/notebook directory.

    Returns:
        True if all builds succeeded, False otherwise.
    """
    if variant is None:
        # Build both variants
        console.print("[yellow]Building both notebook variants...[/yellow]")
        console.print()
        lite_ok = build_notebook_variant("lite", version, docker_path)
        console.print()
        full_ok = build_notebook_variant("full", version, docker_path)
        return lite_ok and full_ok
    else:
        return build_notebook_variant(variant, version, docker_path)


def push_service(service_name: str, version: str) -> bool:
    """Push a service image to Docker Hub.

    Args:
        service_name: Full service name (e.g., "drawio-converter").
        version: Version string.

    Returns:
        True if push succeeded, False otherwise.
    """
    image_version = f"{HUB_NAMESPACE}/clx-{service_name}:{version}"
    image_latest = f"{HUB_NAMESPACE}/clx-{service_name}:latest"

    console.print(f"[yellow]Pushing {service_name}...[/yellow]")

    # Check if image exists
    result = subprocess.run(
        ["docker", "image", "inspect", image_version],
        capture_output=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Error: Image {image_version} not found[/red]")
        console.print("[blue]Run 'clx docker build' first[/blue]")
        return False

    try:
        # Push version tag
        console.print(f"[blue]Pushing {image_version}[/blue]")
        run_docker_command(["push", image_version])

        # Push latest tag
        console.print(f"[blue]Pushing {image_latest}[/blue]")
        run_docker_command(["push", image_latest])

        console.print(f"[green]Successfully pushed {service_name}[/green]")
        return True

    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to push {service_name}[/red]")
        return False


def check_docker_login() -> bool:
    """Check if user is logged in to Docker Hub.

    Returns:
        True if logged in, False otherwise.
    """
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
    )
    return "Username:" in result.stdout


@click.group(name="docker")
def docker_group():
    """Build and push CLX Docker images.

    These commands help manage Docker images for CLX workers.
    Images are pushed to the mhoelzl/ namespace on Docker Hub.
    """
    pass


@docker_group.command(name="build")
@click.argument("services", nargs=-1)
@click.option(
    "--all",
    "build_all",
    is_flag=True,
    help="Build all services (default if no services specified).",
)
def docker_build(services: tuple[str, ...], build_all: bool):
    """Build Docker images for CLX workers.

    SERVICES can be: plantuml, drawio, notebook, notebook:lite, notebook:full

    If no services are specified, all services are built.
    For the notebook service, both lite and full variants are built by default.

    Examples:

        clx docker build                    # Build all services
        clx docker build plantuml           # Build plantuml only
        clx docker build notebook           # Build both notebook variants
        clx docker build notebook:lite      # Build only lite variant
        clx docker build notebook:full      # Build only full variant
    """
    import os

    # Enable BuildKit
    os.environ["DOCKER_BUILDKIT"] = "1"

    # Find project root
    project_root = get_project_root()
    if project_root is None:
        console.print(
            "[red]Error: Could not find project root "
            "(directory with docker/ and pyproject.toml)[/red]"
        )
        console.print(f"[red]Current directory: {Path.cwd()}[/red]")
        raise SystemExit(1)

    # Change to project root for docker builds
    original_dir = Path.cwd()
    os.chdir(project_root)

    try:
        version = get_version()

        # If no services specified, build all
        if not services and not build_all:
            build_all = True

        if build_all:
            console.print("[yellow]Building all services...[/yellow]")
            services = tuple(AVAILABLE_SERVICES)

        all_succeeded = True

        for service_spec in services:
            # Parse service:variant format
            parts = service_spec.split(":", 1)
            service = parts[0]
            variant = parts[1] if len(parts) > 1 else None

            docker_path = project_root / "docker" / service

            if not docker_path.is_dir():
                console.print(f"[red]Error: Docker directory {docker_path} not found[/red]")
                console.print(
                    f"[yellow]Available services: {', '.join(AVAILABLE_SERVICES)}[/yellow]"
                )
                raise SystemExit(1)

            if service == "notebook":
                # Validate variant if specified
                if variant and variant not in ("lite", "full"):
                    console.print(f"[red]Error: Unknown notebook variant '{variant}'[/red]")
                    console.print("[yellow]Available variants: lite, full[/yellow]")
                    raise SystemExit(1)
                success = build_notebook(variant, version, docker_path)
            elif service in AVAILABLE_SERVICES:
                if variant:
                    console.print(
                        f"[red]Error: Service '{service}' does not support variants[/red]"
                    )
                    raise SystemExit(1)
                success = build_service(service, version, docker_path)
            else:
                console.print(f"[red]Error: Unknown service '{service}'[/red]")
                console.print(
                    f"[yellow]Available services: {', '.join(AVAILABLE_SERVICES)}[/yellow]"
                )
                console.print(
                    "[yellow]For notebook, you can specify variant: "
                    "notebook:lite, notebook:full[/yellow]"
                )
                raise SystemExit(1)

            if not success:
                all_succeeded = False
            console.print()

        if all_succeeded:
            console.print("[green]Done![/green]")
        else:
            console.print("[red]Some services failed to build[/red]")
            raise SystemExit(1)

    finally:
        os.chdir(original_dir)


@docker_group.command(name="push")
@click.argument("services", nargs=-1)
@click.option(
    "--all",
    "push_all",
    is_flag=True,
    help="Push all services (default if no services specified).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip Docker Hub login check.",
)
def docker_push(services: tuple[str, ...], push_all: bool, force: bool):
    """Push Docker images to Docker Hub.

    SERVICES should use full names: drawio-converter, notebook-processor, plantuml-converter

    If no services are specified, all services are pushed.

    Examples:

        clx docker push                         # Push all services
        clx docker push drawio-converter        # Push specific service
        clx docker push --force                 # Skip login check
    """
    # Find project root (needed for version)
    project_root = get_project_root()
    if project_root is None:
        console.print(
            "[red]Error: Could not find project root "
            "(directory with docker/ and pyproject.toml)[/red]"
        )
        raise SystemExit(1)

    import os

    original_dir = Path.cwd()
    os.chdir(project_root)

    try:
        version = get_version()

        # Available services for push (use full names)
        available_push_services = ["drawio-converter", "notebook-processor", "plantuml-converter"]

        # Check Docker Hub login
        if not force and not check_docker_login():
            console.print("[yellow]Warning: Not logged in to Docker Hub[/yellow]")
            console.print("[blue]Please login first:[/blue]")
            console.print("  docker login")
            console.print()
            if not click.confirm("Continue anyway?"):
                raise SystemExit(1)

        # If no services specified, push all
        if not services and not push_all:
            push_all = True

        if push_all:
            console.print(
                f"[yellow]Pushing all services to Docker Hub as "
                f"{HUB_NAMESPACE}/clx-*:{version}[/yellow]"
            )
            services = tuple(available_push_services)

        all_succeeded = True

        for service in services:
            if service not in available_push_services:
                console.print(f"[red]Error: Unknown service '{service}'[/red]")
                console.print(
                    f"[yellow]Available services: {', '.join(available_push_services)}[/yellow]"
                )
                raise SystemExit(1)

            success = push_service(service, version)
            if not success:
                all_succeeded = False
            console.print()

        if all_succeeded:
            console.print("[green]Done![/green]")
        else:
            console.print("[red]Some services failed to push[/red]")
            raise SystemExit(1)

    finally:
        os.chdir(original_dir)


@docker_group.command(name="list")
def docker_list():
    """List available services and their Docker images.

    Shows the services that can be built and their corresponding image names.
    """
    project_root = get_project_root()

    console.print("[bold]Available CLX Docker Services[/bold]")
    console.print("=" * 60)
    console.print()

    version = get_version() if project_root else "unknown"

    for short_name in AVAILABLE_SERVICES:
        full_name = SERVICE_NAME_MAP[short_name]
        image_name = f"{HUB_NAMESPACE}/clx-{full_name}"

        console.print(f"[cyan]{short_name}[/cyan]")
        console.print(f"  Image: {image_name}")
        console.print(f"  Tags:  {image_name}:{version}, {image_name}:latest")

        if short_name == "notebook":
            console.print(f"  Variants: lite ({image_name}:lite), full ({image_name}:full)")

        # Check if docker directory exists
        if project_root:
            docker_path = project_root / "docker" / short_name
            if docker_path.is_dir():
                console.print(f"  [green]Dockerfile: {docker_path / 'Dockerfile'}[/green]")
            else:
                console.print("  [red]Dockerfile: Not found[/red]")

        console.print()

    console.print("[bold]Usage:[/bold]")
    console.print("  clx docker build [services...]    # Build images")
    console.print("  clx docker push [services...]     # Push to Docker Hub")
