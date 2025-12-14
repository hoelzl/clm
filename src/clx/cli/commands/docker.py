"""Docker image build and push commands.

This module provides commands for building and pushing CLX worker Docker images.
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


def get_version() -> str:
    """Get CLX version from the package.

    Returns:
        Version string.
    """
    from clx import __version__

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


def get_cache_image_name(service: str, stage: str, variant: str | None = None) -> str:
    """Get the cache image name for a build stage.

    Args:
        service: Service name ("plantuml", "drawio", "notebook").
        stage: Stage name (e.g., "deps", "common", "packages").
        variant: For notebook only: "lite" or "full".

    Returns:
        Full image name for the cached stage.
    """
    full_service_name = SERVICE_NAME_MAP.get(service, service)
    image_name = f"{HUB_NAMESPACE}/clx-{full_service_name}"

    # For notebook packages stage, include variant in tag
    if service == "notebook" and stage == "packages" and variant:
        return f"{image_name}:cache-{stage}-{variant}"
    return f"{image_name}:cache-{stage}"


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


def get_cache_from_args(
    service: str, use_cache: bool = True, variant: str | None = None
) -> list[str]:
    """Get --cache-from arguments for builds.

    Uses type=registry format which works for both local and remote images.
    The registry type checks local images first before attempting remote fetch.

    Args:
        service: Service name ("plantuml", "drawio", "notebook").
        use_cache: Whether to include cache arguments.
        variant: For notebook only: "lite" or "full".

    Returns:
        List of --cache-from arguments.
    """
    if not use_cache:
        return []

    full_service_name = SERVICE_NAME_MAP.get(service, service)
    image_name = f"{HUB_NAMESPACE}/clx-{full_service_name}"
    args = []

    # Add cached stage images (only if they exist locally)
    for stage in SERVICE_CACHE_STAGES.get(service, []):
        cache_image = get_cache_image_name(service, stage, variant)
        if image_exists_locally(cache_image):
            # Use type=registry format - buildx checks local first, then remote
            args.extend(["--cache-from", f"type=registry,ref={cache_image}"])

    # Add final image as cache source
    if service == "notebook" and variant:
        # For notebook, check variant-specific tags
        for tag in [variant, "latest"]:
            full_image = f"{image_name}:{tag}"
            if image_exists_locally(full_image):
                args.extend(["--cache-from", f"type=registry,ref={full_image}"])
    else:
        # For other services, check latest tag
        for tag in ["latest"]:
            full_image = f"{image_name}:{tag}"
            if image_exists_locally(full_image):
                args.extend(["--cache-from", f"type=registry,ref={full_image}"])

    return args


def build_cache_stage(
    service: str,
    stage: str,
    docker_path: Path,
    use_cache: bool = True,
    variant: str | None = None,
) -> bool:
    """Build and tag a single cache stage.

    Args:
        service: Service name ("plantuml", "drawio", "notebook").
        stage: Stage name (e.g., "deps", "common", "packages").
        docker_path: Path to docker service directory.
        use_cache: Whether to use existing cache.
        variant: For notebook only: "lite" or "full".

    Returns:
        True if build succeeded, False otherwise.
    """
    # Determine the target stage name
    if service == "notebook" and stage == "packages" and variant:
        target_stage = f"{stage}-{variant}"
    else:
        target_stage = stage

    cache_image = get_cache_image_name(service, stage, variant)

    console.print(f"[blue]Building and caching '{target_stage}' stage...[/blue]")

    build_args = [
        "buildx",
        "build",
        "-f",
        str(docker_path / "Dockerfile"),
        "--target",
        target_stage,
        "--build-arg",
        f"DOCKER_PATH=docker/{service}",
        "-t",
        cache_image,
        # Export inline cache metadata so it can be imported later
        "--cache-to",
        "type=inline",
        # Load the image into docker (buildx doesn't do this by default)
        "--load",
    ]

    # Add variant arg for notebook
    if service == "notebook" and variant:
        build_args.extend(["--build-arg", f"VARIANT={variant}"])

    # Add cache-from for previous stages
    build_args.extend(get_cache_from_args(service, use_cache, variant))

    build_args.append(".")

    try:
        run_docker_command(build_args)
        console.print(f"[green]Cached stage '{target_stage}' as {cache_image}[/green]")
        return True
    except subprocess.CalledProcessError:
        console.print(f"[yellow]Warning: Failed to cache '{target_stage}' stage[/yellow]")
        return False


def build_service(
    service_name: str,
    version: str,
    docker_path: Path,
    use_cache: bool = True,
    cache_stages: bool = False,
) -> bool:
    """Build a non-notebook service with optional stage caching.

    Args:
        service_name: Short service name (plantuml, drawio).
        version: Version string for tagging.
        docker_path: Path to docker directory.
        use_cache: Whether to use cached stages (default: True).
        cache_stages: Whether to build and tag intermediate stages (default: False).

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

    # If caching stages, build intermediate stages first
    if cache_stages:
        stages = SERVICE_CACHE_STAGES.get(service_name, [])
        if stages:
            console.print("[blue]Building and caching intermediate stages...[/blue]")
            for stage in stages:
                build_cache_stage(service_name, stage, docker_path, use_cache)
            console.print()

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
        # Export inline cache metadata for future builds
        "--cache-to",
        "type=inline",
    ]

    # Add cache-from arguments
    cache_args = get_cache_from_args(service_name, use_cache)
    if cache_args:
        build_args.extend(cache_args)
        console.print(f"[blue]Using {len(cache_args) // 2} cached image(s) as build cache[/blue]")

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
    cache_stages: bool = False,
) -> bool:
    """Build a notebook variant with optional stage caching.

    Args:
        variant: "lite" or "full".
        version: Version string for tagging.
        docker_path: Path to docker/notebook directory.
        use_cache: Whether to use cached stages (default: True).
        cache_stages: Whether to build and tag intermediate stages (default: False).

    Returns:
        True if build succeeded, False otherwise.
    """
    image_name = f"{HUB_NAMESPACE}/clx-notebook-processor"

    console.print(f"[yellow]Building notebook-processor:{variant} (version {version})...[/yellow]")

    # If caching stages, build intermediate stages first
    if cache_stages:
        console.print("[blue]Building and caching intermediate stages...[/blue]")
        for stage in SERVICE_CACHE_STAGES.get("notebook", []):
            build_cache_stage("notebook", stage, docker_path, use_cache, variant)
        console.print()

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
        # Export inline cache metadata for future builds
        "--cache-to",
        "type=inline",
    ]

    # Add cache-from arguments
    cache_args = get_cache_from_args("notebook", use_cache, variant)
    if cache_args:
        build_args.extend(cache_args)
        console.print(f"[blue]Using {len(cache_args) // 2} cached image(s) as build cache[/blue]")

    # Add tags based on variant
    if variant == "full":
        build_args.extend(
            [
                "-t",
                f"{image_name}:{version}",
                "-t",
                f"{image_name}:{version}-full",
                "-t",
                f"{image_name}:latest",
                "-t",
                f"{image_name}:full",
            ]
        )
    else:
        build_args.extend(
            [
                "-t",
                f"{image_name}:{version}-lite",
                "-t",
                f"{image_name}:lite",
            ]
        )

    build_args.append(".")

    try:
        run_docker_command(build_args)
        console.print(f"[green]Successfully built {image_name}:{variant}[/green]")
        if variant == "full":
            console.print(
                f"[green]  Tagged as: {image_name}:{version}, {image_name}:latest "
                f"(default = full)[/green]"
            )
            console.print(
                f"[green]  Tagged as: {image_name}:{version}-full, {image_name}:full[/green]"
            )
        else:
            console.print(
                f"[green]  Tagged as: {image_name}:{version}-lite, {image_name}:lite[/green]"
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
    cache_stages: bool = False,
) -> bool:
    """Build notebook service (one or both variants).

    Args:
        variant: "lite", "full", or None for both.
        version: Version string for tagging.
        docker_path: Path to docker/notebook directory.
        use_cache: Whether to use cached stages (default: True).
        cache_stages: Whether to build and tag intermediate stages (default: False).

    Returns:
        True if all builds succeeded, False otherwise.
    """
    if variant is None:
        # Build both variants
        console.print("[yellow]Building both notebook variants...[/yellow]")
        console.print()
        lite_ok = build_notebook_variant("lite", version, docker_path, use_cache, cache_stages)
        console.print()
        full_ok = build_notebook_variant("full", version, docker_path, use_cache, cache_stages)
        return lite_ok and full_ok
    else:
        return build_notebook_variant(variant, version, docker_path, use_cache, cache_stages)


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
@click.option(
    "--cache/--no-cache",
    default=True,
    help="Use cached intermediate stages for faster builds (default: enabled).",
)
@click.option(
    "--cache-stages",
    is_flag=True,
    help="Build and tag intermediate stages for reuse in future builds. "
    "Use this for full builds; subsequent builds can reuse these stages.",
)
def docker_build(services: tuple[str, ...], build_all: bool, cache: bool, cache_stages: bool):
    """Build Docker images for CLX workers.

    SERVICES can be: plantuml, drawio, notebook, notebook:lite, notebook:full

    If no services are specified, all services are built.
    For the notebook service, both lite and full variants are built by default.

    \b
    Caching Options:
      --cache (default)    Use previously cached stages for faster builds
      --no-cache           Rebuild all stages from scratch
      --cache-stages       Build and tag intermediate stages (common, packages)
                          for reuse in future builds

    \b
    Recommended workflow for notebook builds:
      1. First build with --cache-stages to create cached intermediate images:
         clx docker build --cache-stages notebook:full

      2. After CLX code changes, rebuild quickly using cached stages:
         clx docker build notebook:full
         (or use: clx docker build-quick full)

    Examples:

        clx docker build                        # Build all services
        clx docker build plantuml               # Build plantuml only
        clx docker build notebook               # Build both notebook variants
        clx docker build notebook:lite          # Build only lite variant
        clx docker build notebook:full          # Build only full variant
        clx docker build --cache-stages notebook:full  # Cache intermediate stages
        clx docker build --no-cache notebook    # Full rebuild without cache
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
            console.print("[blue]Cache enabled: will use cached stages if available[/blue]")
        else:
            console.print("[blue]Cache disabled: rebuilding all stages[/blue]")
        if cache_stages:
            console.print("[blue]Will cache intermediate stages for future builds[/blue]")
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
                success = build_notebook(variant, version, docker_path, cache, cache_stages)
            elif service in AVAILABLE_SERVICES:
                if variant:
                    console.print(
                        f"[red]Error: Service '{service}' does not support variants[/red]"
                    )
                    raise SystemExit(1)
                success = build_service(service, version, docker_path, cache, cache_stages)
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
    """Build a single service quickly using cached stages.

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

    # Check if cached stages exist
    if warn_missing_cache:
        missing_caches = []
        for stage in SERVICE_CACHE_STAGES.get(service, []):
            cache_image = get_cache_image_name(service, stage, variant)
            if not image_exists_locally(cache_image):
                missing_caches.append(f"  - {cache_image}")

        if missing_caches:
            console.print("[yellow]Warning: Some cached stages are missing:[/yellow]")
            for cache in missing_caches:
                console.print(cache)
            console.print()
            console.print("[blue]For fastest builds, first run:[/blue]")
            console.print(f"  clx docker build --cache-stages {service_spec}")
            console.print()
            console.print("[blue]Continuing with available cache...[/blue]")
            console.print()

    docker_path = project_root / "docker" / service

    console.print(f"[yellow]Quick rebuild of {service_spec}...[/yellow]")

    # Build using cached stages (don't rebuild the cache stages themselves)
    if service == "notebook":
        # Default to "lite" variant for notebook if not specified
        notebook_variant = variant if variant else "lite"
        return build_notebook_variant(
            variant=notebook_variant,
            version=version,
            docker_path=docker_path,
            use_cache=True,
            cache_stages=False,
        )
    else:
        return build_service(
            service_name=service,
            version=version,
            docker_path=docker_path,
            use_cache=True,
            cache_stages=False,
        )


@docker_group.command(name="build-quick")
@click.argument("service_spec", default="all")
def docker_build_quick(service_spec: str):
    """Quick rebuild of services using cached stages.

    SERVICE_SPEC can be: all, plantuml, drawio, notebook:lite, notebook:full

    If no service is specified, all services are rebuilt (default).

    This builds only the final stage of the image, reusing previously
    cached intermediate stages. Use this after making changes to CLX code
    when you haven't modified the Dockerfile's earlier stages.

    \b
    Prerequisites:
      First run a full build with --cache-stages to create the cached stages:
        clx docker build --cache-stages

    \b
    This is equivalent to:
        clx docker build SERVICE

    But explicitly designed for the quick-rebuild use case after CLX code changes.

    Examples:

        clx docker build --cache-stages                # Cache all services
        # ... make changes to CLX code ...
        clx docker build-quick                         # Quick rebuild all

        clx docker build --cache-stages plantuml       # Cache plantuml only
        # ... make changes to CLX code ...
        clx docker build-quick plantuml                # Quick rebuild plantuml
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
                console.print("[yellow]Usage: clx docker build-quick notebook:lite[/yellow]")
                console.print("[yellow]       clx docker build-quick notebook:full[/yellow]")
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
    """Show information about cached build stages for all services.

    Displays which intermediate stages are cached locally and can be used
    for faster rebuilds.
    """
    console.print("[bold]Docker Build Cache Status[/bold]")
    console.print("=" * 60)
    console.print()

    for service in AVAILABLE_SERVICES:
        full_service_name = SERVICE_NAME_MAP.get(service, service)
        image_name = f"{HUB_NAMESPACE}/clx-{full_service_name}"
        stages = SERVICE_CACHE_STAGES.get(service, [])

        if service == "notebook":
            # Notebook has variants
            for variant in ["lite", "full"]:
                console.print(f"[cyan]{service}:{variant}[/cyan]")

                # Check cached stages
                for stage in stages:
                    cache_image = get_cache_image_name(service, stage, variant)
                    if image_exists_locally(cache_image):
                        console.print(f"  [green]✓[/green] {stage}: {cache_image}")
                    else:
                        console.print(f"  [red]✗[/red] {stage}: {cache_image} (not cached)")

                # Check final image
                final_image = f"{image_name}:{variant}"
                if image_exists_locally(final_image):
                    console.print(f"  [green]✓[/green] final: {final_image}")
                else:
                    console.print(f"  [red]✗[/red] final: {final_image} (not built)")

                console.print()
        else:
            # Non-notebook services
            console.print(f"[cyan]{service}[/cyan]")

            # Check cached stages
            for stage in stages:
                cache_image = get_cache_image_name(service, stage)
                if image_exists_locally(cache_image):
                    console.print(f"  [green]✓[/green] {stage}: {cache_image}")
                else:
                    console.print(f"  [red]✗[/red] {stage}: {cache_image} (not cached)")

            # Check final image
            final_image = f"{image_name}:latest"
            if image_exists_locally(final_image):
                console.print(f"  [green]✓[/green] final: {final_image}")
            else:
                console.print(f"  [red]✗[/red] final: {final_image} (not built)")

            console.print()

    console.print("[bold]To create cached stages:[/bold]")
    console.print("  clx docker build --cache-stages plantuml")
    console.print("  clx docker build --cache-stages drawio")
    console.print("  clx docker build --cache-stages notebook:lite")
    console.print("  clx docker build --cache-stages notebook:full")
    console.print()
    console.print("[bold]To quick-rebuild using cache:[/bold]")
    console.print("  clx docker build-quick                 # All services (default)")
    console.print("  clx docker build-quick plantuml")
    console.print("  clx docker build-quick drawio")
    console.print("  clx docker build-quick notebook:lite")
    console.print("  clx docker build-quick notebook:full")


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

    version = get_version()

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
    console.print("  clx docker build --cache-stages   # Build with stage caching")
    console.print("  clx docker build-quick <variant>  # Quick rebuild using cache")
    console.print("  clx docker cache-info             # Show cache status")
    console.print("  clx docker push [services...]     # Push to Docker Hub")
    console.print("  clx docker pull [services...]     # Pull images from Docker Hub")


def pull_service(service_name: str, tag: str = "latest") -> bool:
    """Pull a service image from Docker Hub.

    Args:
        service_name: Full service name (e.g., "drawio-converter").
        tag: Image tag to pull (default: "latest").

    Returns:
        True if pull succeeded, False otherwise.
    """
    image_name = f"{HUB_NAMESPACE}/clx-{service_name}:{tag}"

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

        clx docker pull                         # Pull all services (latest)
        clx docker pull drawio-converter        # Pull specific service
        clx docker pull --tag 0.5.1             # Pull specific version
    """
    # Available services for pull (use full names)
    available_pull_services = ["drawio-converter", "notebook-processor", "plantuml-converter"]

    # If no services specified, pull all
    if not services and not pull_all:
        pull_all = True

    if pull_all:
        console.print(
            f"[yellow]Pulling all services from Docker Hub ({HUB_NAMESPACE}/clx-*:{tag})[/yellow]"
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
