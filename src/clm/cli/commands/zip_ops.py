"""ZIP archive operations for course output directories.

This module provides commands for creating ZIP archives of course output
directories, enabling easy distribution of generated course content.
"""

import logging
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePath

import click

from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec
from clm.core.utils.text_utils import sanitize_file_name

logger = logging.getLogger(__name__)

# Directories and patterns to exclude from ZIP archives
_EXCLUDED_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
_EXCLUDED_EXTENSIONS = {".pyc", ".pyo"}


def zip_directory(source_dir: Path, archive_path: Path, *, dry_run: bool = False) -> Path:
    """Create a ZIP archive of a directory.

    Creates a deterministically-ordered ZIP archive with maximum compression.
    The archive contains a top-level directory matching the source directory name.

    Based on Stefan Behnel's implementation from the legacy/v0.8.x branch.

    Args:
        source_dir: Directory to archive
        archive_path: Path where the ZIP file should be written
        dry_run: If True, report what would be done without creating the archive

    Returns:
        Path to the created archive
    """
    if not source_dir.is_dir():
        raise click.ClickException(f"Directory does not exist: {source_dir}")

    if dry_run:
        click.echo(f"  [dry-run] Would create: {archive_path}")
        return archive_path

    archive_dir = PurePath(source_dir.name)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        for dirpath, dirnames, filenames in os.walk(source_dir):
            # Filter out excluded directories (modifying in-place affects os.walk traversal)
            dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRS)

            rel_dir = PurePath(dirpath).relative_to(source_dir)
            archive_relpath = archive_dir / rel_dir

            for filename in sorted(filenames):
                if PurePath(filename).suffix in _EXCLUDED_EXTENSIONS:
                    continue
                file_path = Path(dirpath) / filename
                arcname = str(archive_relpath / filename)
                zf.write(file_path, arcname)

    logger.info(f"Created archive: {archive_path}")
    return archive_path


class OutputDirectory:
    """Represents an output directory that can be archived."""

    def __init__(
        self,
        path: Path,
        target_name: str,
        language: str,
    ):
        self.path = path
        self.target_name = target_name
        self.language = language

    @property
    def display_name(self) -> str:
        return f"{self.target_name}/{self.language}"

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


def find_output_directories(
    spec_file: Path,
    target_filter: str | None = None,
) -> list[OutputDirectory]:
    """Find all output directories for a course spec.

    Args:
        spec_file: Path to course spec file
        target_filter: Optional target name to filter by

    Returns:
        List of OutputDirectory objects
    """
    spec = CourseSpec.from_file(spec_file)
    course_root, default_output = resolve_course_paths(spec_file)
    course_name = spec.name

    directories: list[OutputDirectory] = []

    if spec.output_targets:
        for target_spec in spec.output_targets:
            if target_filter and target_spec.name != target_filter:
                continue

            path = Path(target_spec.path)
            if not path.is_absolute():
                path = course_root / path

            languages = target_spec.languages or ["de", "en"]

            for lang in languages:
                course_dir_name = sanitize_file_name(course_name[lang])
                output_path = path / lang.capitalize() / course_dir_name

                directories.append(
                    OutputDirectory(
                        path=output_path,
                        target_name=target_spec.name,
                        language=lang,
                    )
                )
    else:
        for target_name in ["public", "speaker"]:
            if target_filter and target_name != target_filter:
                continue

            for lang in ["de", "en"]:
                course_dir_name = sanitize_file_name(course_name[lang])
                output_path = default_output / target_name / lang.capitalize() / course_dir_name

                directories.append(
                    OutputDirectory(
                        path=output_path,
                        target_name=target_name,
                        language=lang,
                    )
                )

    return directories


def _archive_name(directory: OutputDirectory) -> str:
    """Generate archive filename for an output directory."""
    return f"{directory.path.name}_{directory.target_name}_{directory.language}.zip"


@click.group(name="zip")
def zip_group():
    """Create and manage ZIP archives of course output."""
    pass


@zip_group.command(name="create")
@click.argument("spec_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--target",
    "target_filter",
    default=None,
    help="Only archive a specific output target (by name).",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory where ZIP files are written (default: alongside each output directory).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be archived without creating files.",
)
def create_archives(
    spec_file: Path,
    target_filter: str | None,
    output_dir: Path | None,
    dry_run: bool,
):
    """Create ZIP archives of course output directories.

    Creates one ZIP file per output-target and language combination.
    """
    directories = find_output_directories(spec_file, target_filter)

    if not directories:
        click.echo("No output directories found.")
        return

    # Filter to existing directories
    existing = [d for d in directories if d.exists]
    if not existing:
        click.echo("No built output directories found. Run 'clm build' first.")
        return

    click.echo(
        f"Found {len(existing)} output director{'y' if len(existing) == 1 else 'ies'} to archive:"
    )

    # Build list of (source_dir, archive_path) pairs
    tasks: list[tuple[Path, Path]] = []
    for d in existing:
        if output_dir:
            archive_path = output_dir / _archive_name(d)
        else:
            archive_path = d.path.parent / _archive_name(d)

        click.echo(f"  {d.display_name}: {d.path} -> {archive_path}")
        tasks.append((d.path, archive_path))

    if dry_run:
        click.echo("\n[dry-run] No archives created.")
        return

    # Create archives in parallel
    def _create(task: tuple[Path, Path]) -> Path:
        source, archive = task
        return zip_directory(source, archive)

    with ThreadPoolExecutor() as executor:
        results = list(executor.map(_create, tasks))

    click.echo(f"\nCreated {len(results)} archive(s).")


@zip_group.command(name="list")
@click.argument("spec_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--target",
    "target_filter",
    default=None,
    help="Only list a specific output target (by name).",
)
def list_directories(spec_file: Path, target_filter: str | None):
    """List output directories that would be archived."""
    directories = find_output_directories(spec_file, target_filter)

    if not directories:
        click.echo("No output directories found.")
        return

    click.echo(f"Output directories ({len(directories)}):\n")
    for d in directories:
        status = "exists" if d.exists else "not built"
        click.echo(f"  {d.display_name}: {d.path} [{status}]")
