"""Docker image build and push commands.

This module provides commands for building and pushing CLM worker Docker images.
"""

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

# Build stages that can be cached for each service
# These correspond to the stage names in the respective Dockerfiles
SERVICE_CACHE_STAGES = {
    "plantuml": ["deps"],  # deps stage contains Java + PlantUML JAR
    "drawio": ["deps"],  # deps stage contains Draw.io + system deps
    "notebook": ["common", "packages"],  # common + packages stages
}

# Console for colored output
console = Console(file=sys.stderr)

# Local cache directory name (relative to project root)
CACHE_DIR_NAME = ".docker-cache"


def get_version() -> str:
    """Get CLM version from the package.

    Returns:
        Version string.
    """
    from clm import __version__

    return __version__


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


def image_exists_locally(image_name: str) -> bool:
    """Check if a Docker image exists locally.

    Args:
        image_name: Full image name with tag.

    Returns:
        True if image exists locally, False otherwise.
    """
    result = subprocess.run(
        ["docker", "image", "inspect", image_name],
        capture_output=True,
    )
    return result.returncode == 0


def get_cache_dir(service: str, variant: str | None = None) -> Path:
    """Get the local cache directory path for a service.

    Args:
        service: Service name ("plantuml", "drawio", "notebook").
        variant: For notebook only: "lite" or "full".

    Returns:
        Path to the cache directory.
    """
    cache_dir = Path(CACHE_DIR_NAME) / service
    if service == "notebook" and variant:
        cache_dir = cache_dir / variant
    return cache_dir


def get_cache_args(
    service: str, use_cache: bool = True, variant: str | None = None
) -> tuple[list[str], list[str]]:
    """Get --cache-from and --cache-to arguments for builds.

    Uses local directory-based caching which works without pushing to a registry.

    Args:
        service: Service name ("plantuml", "drawio", "notebook").
        use_cache: Whether to include cache arguments.
        variant: For notebook only: "lite" or "full".

    Returns:
        Tuple of (cache_from_args, cache_to_args).
    """
    cache_from_args: list[str] = []
    cache_to_args: list[str] = []

    if not use_cache:
        return cache_from_args, cache_to_args

    cache_dir = get_cache_dir(service, variant)

    # Use local directory cache - this works without pushing to a registry
    if cache_dir.exists():
        cache_from_args.extend(["--cache-from", f"type=local,src={cache_dir}"])

    cache_to_args.extend(["--cache-to", f"type=local,dest={cache_dir},mode=max"])

    return cache_from_args, cache_to_args


def ensure_cache_dir(service: str, variant: str | None = None) -> Path:
    """Ensure the cache directory exists for a service.

    Args:
        service: Service name ("plantuml", "drawio", "notebook").
        variant: For notebook only: "lite" or "full".

    Returns:
        Path to the cache directory.
    """
    cache_dir = get_cache_dir(service, variant)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def build_service(
    service_name: str,
    version: str,
    docker_path: Path,
    use_cache: bool = True,
) -> bool:
    """Build a non-notebook service with local directory caching.

    Args:
        service_name: Short service name (plantuml, drawio).
        version: Version string for tagging.
        docker_path: Path to docker directory.
        use_cache: Whether to use local cache (default: True).

    Returns:
        True if build succeeded, False otherwise.
    """
    full_service_name = SERVICE_NAME_MAP.get(service_name, service_name)
    image_name = f"{HUB_NAMESPACE}/clm-{full_service_name}"

    dockerfile = docker_path / "Dockerfile"
    if not dockerfile.exists():
        console.print(f"[red]Error: Dockerfile not found in {docker_path}[/red]")
        return False

    console.print(f"[yellow]Building {service_name} (version {version})...[/yellow]")

    # Ensure cache directory exists
    ensure_cache_dir(service_name)

    # Build base arguments
    build_args = [
        "buildx",
        "build",
        "-f",
        str(dockerfile),
        "--build-arg",
        f"DOCKER_PATH=docker/{service_name}",
        "-t",
        f"{image_name}:{version}",
        "-t",
        f"{image_name}:latest",
        # Load the image into docker (buildx doesn't do this by default)
        "--load",
    ]

    # Add cache arguments (local directory-based caching)
    cache_from_args, cache_to_args = get_cache_args(service_name, use_cache)
    if cache_from_args:
        build_args.extend(cache_from_args)
        console.print("[blue]Using local cache for faster builds[/blue]")
    build_args.extend(cache_to_args)

    build_args.append(".")

    try:
        run_docker_command(build_args)
        console.print(f"[green]Successfully built {image_name}:{version}[/green]")
        console.print(f"[green]  Tagged as: {image_name}:{version}, {image_name}:latest[/green]")
        return True

    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to build {image_name}[/red]")
        return False


def build_notebook_variant(
    variant: str,
    version: str,
    docker_path: Path,
    use_cache: bool = True,
) -> bool:
    """Build a notebook variant with local directory caching.

    Args:
        variant: "lite" or "full".
        version: Version string for tagging.
        docker_path: Path to docker/notebook directory.
        use_cache: Whether to use local cache (default: True).

    Returns:
        True if build succeeded, False otherwise.
    """
    image_name = f"{HUB_NAMESPACE}/clm-notebook-processor"

    console.print(f"[yellow]Building notebook-processor:{variant} (version {version})...[/yellow]")

    # Ensure cache directory exists
    ensure_cache_dir("notebook", variant)

    # Build base arguments
    build_args = [
        "buildx",
        "build",
        "-f",
        str(docker_path / "Dockerfile"),
        "--build-arg",
        f"VARIANT={variant}",
        "--build-arg",
        "DOCKER_PATH=docker/notebook",
        # Load the image into docker (buildx doesn't do this by default)
        "--load",
    ]

    # Add cache arguments (local directory-based caching)
    cache_from_args, cache_to_args = get_cache_args("notebook", use_cache, variant)
    if cache_from_args:
        build_args.extend(cache_from_args)
        console.print("[blue]Using local cache for faster builds[/blue]")
    build_args.extend(cache_to_args)

    # Add tags based on variant
    # Lite is the default (gets :latest tag), full requires explicit :full tag
    if variant == "lite":
        build_args.extend(
            [
                "-t",
                f"{image_name}:{version}",
                "-t",
                f"{image_name}:{version}-lite",
                "-t",
                f"{image_name}:latest",
                "-t",
                f"{image_name}:lite",
            ]
        )
    else:
        build_args.extend(
            [
                "-t",
                f"{image_name}:{version}-full",
                "-t",
                f"{image_name}:full",
            ]
        )

    build_args.append(".")

    try:
        run_docker_command(build_args)
        console.print(f"[green]Successfully built {image_name}:{variant}[/green]")
        if variant == "lite":
            console.print(
                f"[green]  Tagged as: {image_name}:{version}, {image_name}:latest "
                f"(default = lite)[/green]"
            )
            console.print(
                f"[green]  Tagged as: {image_name}:{version}-lite, {image_name}:lite[/green]"
            )
        else:
            console.print(
                f"[green]  Tagged as: {image_name}:{version}-full, {image_name}:full[/green]"
            )
        return True

    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to build {image_name}:{variant}[/red]")
        return False


def build_notebook(
    variant: str | None,
    version: str,
    docker_path: Path,
    use_cache: bool = True,
) -> bool:
    """Build notebook service (one or both variants).

    Args:
        variant: "lite", "full", or None for both.
        version: Version string for tagging.
        docker_path: Path to docker/notebook directory.
        use_cache: Whether to use local cache (default: True).

    Returns:
        True if all builds succeeded, False otherwise.
    """
    if variant is None:
        # Build both variants
        console.print("[yellow]Building both notebook variants...[/yellow]")
        console.print()
        lite_ok = build_notebook_variant("lite", version, docker_path, use_cache)
        console.print()
        full_ok = build_notebook_variant("full", version, docker_path, use_cache)
        return lite_ok and full_ok
    else:
        return build_notebook_variant(variant, version, docker_path, use_cache)


def push_service(service_name: str, version: str) -> bool:
    """Push a service image to Docker Hub.

    Args:
        service_name: Full service name (e.g., "drawio-converter").
        version: Version string.

    Returns:
        True if push succeeded, False otherwise.
    """
    image_version = f"{HUB_NAMESPACE}/clm-{service_name}:{version}"
    image_latest = f"{HUB_NAMESPACE}/clm-{service_name}:latest"

    console.print(f"[yellow]Pushing {service_name}...[/yellow]")

    # Check if image exists
    result = subprocess.run(
        ["docker", "image", "inspect", image_version],
        capture_output=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Error: Image {image_version} not found[/red]")
        console.print("[blue]Run 'clm docker build' first[/blue]")
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

    Checks the Docker config file for stored credentials for Docker Hub.

    Returns:
        True if logged in, False otherwise.
    """
    import json

    # Docker config file location
    docker_config_path = Path.home() / ".docker" / "config.json"

    if not docker_config_path.exists():
        return False

    try:
        with open(docker_config_path) as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False

    # Check for auth entries for Docker Hub
    # Docker Hub uses these registry URLs
    docker_hub_registries = [
        "https://index.docker.io/v1/",
        "index.docker.io",
        "registry-1.docker.io",
        "docker.io",
    ]

    # Check direct auth entries
    auths = config.get("auths", {})
    for registry in docker_hub_registries:
        if registry in auths:
            # Entry exists - could have auth data or use credsStore
            auth_entry = auths[registry]
            if auth_entry:  # Non-empty entry (has auth or identitytoken)
                return True

    # Check if a credential store is configured (credsStore or credsHelpers)
    # If there's a credsStore, Docker may store credentials externally
    if config.get("credsStore") or config.get("credsHelpers"):
        # Credential helper is configured - check if we have an entry for Docker Hub
        for registry in docker_hub_registries:
            if registry in auths:
                # Empty dict means credentials are in the helper
                return True

    return False


@click.group(name="docker")
def docker_group():
    """Build and push CLM Docker images.

    These commands help manage Docker images for CLM workers.
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
@click.option(
    "--cache/--no-cache",
    default=True,
    help="Use local directory cache for faster builds (default: enabled).",
)
def docker_build(services: tuple[str, ...], build_all: bool, cache: bool):
    """Build Docker images for CLM workers.

    SERVICES can be: plantuml, drawio, notebook, notebook:lite, notebook:full

    If no services are specified, all services are built.
    For the notebook service, both lite and full variants are built by default.

    \b
    Caching:
      Build cache is stored locally in .docker-cache/ and reused automatically.
      Use --no-cache to rebuild from scratch.

    Examples:

        clm docker build                        # Build all services
        clm docker build plantuml               # Build plantuml only
        clm docker build notebook               # Build both notebook variants
        clm docker build notebook:lite          # Build only lite variant
        clm docker build notebook:full          # Build only full variant
        clm docker build --no-cache notebook    # Full rebuild without cache
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

        # Show caching status
        if cache:
            console.print("[blue]Cache enabled: using local directory cache[/blue]")
        else:
            console.print("[blue]Cache disabled: rebuilding from scratch[/blue]")
        console.print()

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
                success = build_notebook(variant, version, docker_path, cache)
            elif service in AVAILABLE_SERVICES:
                if variant:
                    console.print(
                        f"[red]Error: Service '{service}' does not support variants[/red]"
                    )
                    raise SystemExit(1)
                success = build_service(service, version, docker_path, cache)
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


def _build_quick_service(
    service_spec: str, project_root: Path, version: str, warn_missing_cache: bool = True
) -> bool:
    """Build a single service quickly using local cache.

    Args:
        service_spec: Service specification (e.g., "plantuml", "notebook:full").
        project_root: Path to project root.
        version: Version string for tagging.
        warn_missing_cache: Whether to warn about missing cache.

    Returns:
        True if build succeeded, False otherwise.
    """
    # Parse service:variant format
    parts = service_spec.split(":", 1)
    service = parts[0]
    variant = parts[1] if len(parts) > 1 else None

    # Check if local cache exists
    if warn_missing_cache:
        cache_dir = get_cache_dir(service, variant)
        if not cache_dir.exists():
            console.print("[yellow]Warning: Local cache not found[/yellow]")
            console.print(f"  Cache directory: {cache_dir}")
            console.print()
            console.print("[blue]For fastest builds, first run a full build:[/blue]")
            console.print(f"  clm docker build {service_spec}")
            console.print()
            console.print("[blue]Continuing without cache...[/blue]")
            console.print()

    docker_path = project_root / "docker" / service

    console.print(f"[yellow]Quick rebuild of {service_spec}...[/yellow]")

    # Build using local cache
    if service == "notebook":
        # Default to "lite" variant for notebook if not specified
        notebook_variant = variant if variant else "lite"
        return build_notebook_variant(
            variant=notebook_variant,
            version=version,
            docker_path=docker_path,
            use_cache=True,
        )
    else:
        return build_service(
            service_name=service,
            version=version,
            docker_path=docker_path,
            use_cache=True,
        )


@docker_group.command(name="build-quick")
@click.argument("service_spec", default="all")
def docker_build_quick(service_spec: str):
    """Quick rebuild of services using local cache.

    SERVICE_SPEC can be: all, plantuml, drawio, notebook:lite, notebook:full

    If no service is specified, all services are rebuilt (default).

    This is equivalent to 'clm docker build SERVICE' but provides a clearer
    intent for quick rebuilds after code changes.

    \b
    Cache:
      Build cache is stored in .docker-cache/ and populated on first build.
      Subsequent builds reuse cached layers automatically.

    Examples:

        clm docker build                    # First build populates cache
        # ... make changes to CLM code ...
        clm docker build-quick              # Quick rebuild all using cache

        clm docker build-quick plantuml     # Quick rebuild plantuml only
    """
    import os

    # Handle "all" service spec
    if service_spec == "all":
        service_specs = ["plantuml", "drawio", "notebook:lite", "notebook:full"]
    else:
        service_specs = [service_spec]

    # Validate all service specs first
    for spec in service_specs:
        parts = spec.split(":", 1)
        service = parts[0]
        variant = parts[1] if len(parts) > 1 else None

        if service not in AVAILABLE_SERVICES:
            console.print(f"[red]Error: Unknown service '{service}'[/red]")
            console.print(
                f"[yellow]Available services: all, {', '.join(AVAILABLE_SERVICES)}[/yellow]"
            )
            raise SystemExit(1)

        if service == "notebook":
            if variant is None:
                console.print("[red]Error: notebook requires a variant (lite or full)[/red]")
                console.print("[yellow]Usage: clm docker build-quick notebook:lite[/yellow]")
                console.print("[yellow]       clm docker build-quick notebook:full[/yellow]")
                raise SystemExit(1)
            if variant not in ("lite", "full"):
                console.print(f"[red]Error: Unknown notebook variant '{variant}'[/red]")
                console.print("[yellow]Available variants: lite, full[/yellow]")
                raise SystemExit(1)
        elif variant:
            console.print(f"[red]Error: Service '{service}' does not support variants[/red]")
            raise SystemExit(1)

    # Enable BuildKit
    os.environ["DOCKER_BUILDKIT"] = "1"

    # Find project root
    project_root = get_project_root()
    if project_root is None:
        console.print(
            "[red]Error: Could not find project root "
            "(directory with docker/ and pyproject.toml)[/red]"
        )
        raise SystemExit(1)

    # Change to project root
    original_dir = Path.cwd()
    os.chdir(project_root)

    try:
        version = get_version()

        if len(service_specs) > 1:
            console.print("[yellow]Quick rebuild of all services...[/yellow]")
            console.print()

        all_succeeded = True
        for spec in service_specs:
            success = _build_quick_service(
                spec, project_root, version, warn_missing_cache=(len(service_specs) == 1)
            )
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


@docker_group.command(name="cache-info")
def docker_cache_info():
    """Show information about local build cache for all services.

    Displays cache status and built images for each service.
    """
    console.print("[bold]Docker Build Cache Status[/bold]")
    console.print("=" * 60)
    console.print()

    for service in AVAILABLE_SERVICES:
        full_service_name = SERVICE_NAME_MAP.get(service, service)
        image_name = f"{HUB_NAMESPACE}/clm-{full_service_name}"

        if service == "notebook":
            # Notebook has variants
            for variant in ["lite", "full"]:
                console.print(f"[cyan]{service}:{variant}[/cyan]")

                # Check local cache directory
                cache_dir = get_cache_dir(service, variant)
                if cache_dir.exists():
                    console.print(f"  [green]✓[/green] cache: {cache_dir}")
                else:
                    console.print(f"  [red]✗[/red] cache: {cache_dir} (not found)")

                # Check final image
                final_image = f"{image_name}:{variant}"
                if image_exists_locally(final_image):
                    console.print(f"  [green]✓[/green] image: {final_image}")
                else:
                    console.print(f"  [red]✗[/red] image: {final_image} (not built)")

                console.print()
        else:
            # Non-notebook services
            console.print(f"[cyan]{service}[/cyan]")

            # Check local cache directory
            cache_dir = get_cache_dir(service)
            if cache_dir.exists():
                console.print(f"  [green]✓[/green] cache: {cache_dir}")
            else:
                console.print(f"  [red]✗[/red] cache: {cache_dir} (not found)")

            # Check final image
            final_image = f"{image_name}:latest"
            if image_exists_locally(final_image):
                console.print(f"  [green]✓[/green] image: {final_image}")
            else:
                console.print(f"  [red]✗[/red] image: {final_image} (not built)")

            console.print()

    console.print("[bold]To build and populate cache:[/bold]")
    console.print("  clm docker build plantuml")
    console.print("  clm docker build drawio")
    console.print("  clm docker build notebook:lite")
    console.print("  clm docker build notebook:full")
    console.print()
    console.print("[bold]To quick-rebuild using cache:[/bold]")
    console.print("  clm docker build-quick                 # All services (default)")
    console.print("  clm docker build-quick plantuml")
    console.print("  clm docker build-quick drawio")
    console.print("  clm docker build-quick notebook:lite")
    console.print("  clm docker build-quick notebook:full")


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

        clm docker push                         # Push all services
        clm docker push drawio-converter        # Push specific service
        clm docker push --force                 # Skip login check
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
                f"{HUB_NAMESPACE}/clm-*:{version}[/yellow]"
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

    console.print("[bold]Available CLM Docker Services[/bold]")
    console.print("=" * 60)
    console.print()

    version = get_version()

    for short_name in AVAILABLE_SERVICES:
        full_name = SERVICE_NAME_MAP[short_name]
        image_name = f"{HUB_NAMESPACE}/clm-{full_name}"

        console.print(f"[cyan]{short_name}[/cyan]")
        console.print(f"  Image: {image_name}")
        console.print(f"  Tags:  {image_name}:{version}, {image_name}:latest")

        if short_name == "notebook":
            console.print(
                f"  Variants: lite ({image_name}:lite = :latest), full ({image_name}:full)"
            )

        # Check if docker directory exists
        if project_root:
            docker_path = project_root / "docker" / short_name
            if docker_path.is_dir():
                console.print(f"  [green]Dockerfile: {docker_path / 'Dockerfile'}[/green]")
            else:
                console.print("  [red]Dockerfile: Not found[/red]")

        console.print()

    console.print("[bold]Usage:[/bold]")
    console.print("  clm docker build [services...]    # Build images (with caching)")
    console.print("  clm docker build-quick <variant>  # Quick rebuild using cache")
    console.print("  clm docker cache-info             # Show cache status")
    console.print("  clm docker push [services...]     # Push to Docker Hub")
    console.print("  clm docker pull [services...]     # Pull images from Docker Hub")


def pull_service(service_name: str, tag: str = "latest") -> bool:
    """Pull a service image from Docker Hub.

    Args:
        service_name: Full service name (e.g., "drawio-converter").
        tag: Image tag to pull (default: "latest").

    Returns:
        True if pull succeeded, False otherwise.
    """
    image_name = f"{HUB_NAMESPACE}/clm-{service_name}:{tag}"

    console.print(f"[yellow]Pulling {image_name}...[/yellow]")

    try:
        run_docker_command(["pull", image_name])
        console.print(f"[green]Successfully pulled {image_name}[/green]")
        return True

    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to pull {image_name}[/red]")
        return False


@docker_group.command(name="pull")
@click.argument("services", nargs=-1)
@click.option(
    "--all",
    "pull_all",
    is_flag=True,
    help="Pull all services (default if no services specified).",
)
@click.option(
    "--tag",
    "-t",
    default="latest",
    help="Image tag to pull (default: latest).",
)
def docker_pull(services: tuple[str, ...], pull_all: bool, tag: str):
    """Pull Docker images from Docker Hub.

    SERVICES should use full names: drawio-converter, notebook-processor, plantuml-converter

    If no services are specified, all services are pulled.

    Examples:

        clm docker pull                         # Pull all services (latest)
        clm docker pull drawio-converter        # Pull specific service
        clm docker pull --tag 1.0.0             # Pull specific version
    """
    # Available services for pull (use full names)
    available_pull_services = ["drawio-converter", "notebook-processor", "plantuml-converter"]

    # If no services specified, pull all
    if not services and not pull_all:
        pull_all = True

    if pull_all:
        console.print(
            f"[yellow]Pulling all services from Docker Hub ({HUB_NAMESPACE}/clm-*:{tag})[/yellow]"
        )
        services = tuple(available_pull_services)

    all_succeeded = True

    for service in services:
        if service not in available_pull_services:
            console.print(f"[red]Error: Unknown service '{service}'[/red]")
            console.print(
                f"[yellow]Available services: {', '.join(available_pull_services)}[/yellow]"
            )
            raise SystemExit(1)

        success = pull_service(service, tag)
        if not success:
            all_succeeded = False
        console.print()

    if all_succeeded:
        console.print("[green]Done![/green]")
    else:
        console.print("[red]Some services failed to pull[/red]")
        raise SystemExit(1)
