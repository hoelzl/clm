"""Centralized path resolution for course directories.

This module provides a single source of truth for resolving course paths
from spec files, ensuring consistency across all CLI commands.
"""

from pathlib import Path


def resolve_course_paths(
    spec_file: Path,
    data_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Resolve course root and default output directories from a spec file.

    Args:
        spec_file: Path to the course specification XML file
        data_dir: Optional override for the data directory (course root)

    Returns:
        Tuple of (course_root, default_output_root):
        - course_root: The base directory containing course materials
        - default_output_root: The default output directory (course_root / "output")

    Note:
        Spec files are expected to be in a subdirectory (e.g., course-specs/),
        so the course_root is resolved as the grandparent of the spec file
        unless explicitly overridden via data_dir.
    """
    spec_file = spec_file.absolute()

    if data_dir is not None:
        course_root = data_dir
    else:
        # Spec files are in a subdirectory, so go up 2 levels
        course_root = spec_file.parents[1]

    default_output_root = course_root / "output"

    return course_root, default_output_root
