"""Path-formatting utilities for CLI output.

ANSI escape handling lives in :mod:`clm.infrastructure.utils.text_utils`
(it is shared with error categorization, which runs below the CLI).
"""

import os
from pathlib import Path


def make_relative_path(file_path: str | Path, base_path: str | Path | None = None) -> str:
    """Convert an absolute path to a relative path if possible.

    Args:
        file_path: The file path to convert
        base_path: Base path for relative conversion (defaults to cwd)

    Returns:
        Relative path if possible, otherwise the original path
    """
    if not file_path:
        return str(file_path)

    try:
        path = Path(file_path)

        # If already relative, return as-is
        if not path.is_absolute():
            return str(file_path)

        # Use current working directory as base if not specified
        if base_path is None:
            base_path = Path.cwd()
        else:
            base_path = Path(base_path)

        # Try to make relative
        try:
            relative = path.relative_to(base_path)
            return str(relative)
        except ValueError:
            # Path is not relative to base_path
            # Try using os.path.relpath which handles different drives on Windows
            try:
                rel_path = os.path.relpath(path, base_path)
                # If the relative path starts with many ".." levels, it's probably
                # better to use the absolute path
                if rel_path.count("..") > 3:
                    return str(file_path)
                return rel_path
            except ValueError:
                # On Windows, can't compute relative path across drives
                return str(file_path)

    except Exception:
        # Any error, return original
        return str(file_path)


def truncate_path(file_path: str | Path, max_length: int = 60) -> str:
    """Truncate a long path while preserving the filename and some context.

    Args:
        file_path: Path to truncate
        max_length: Maximum length of result

    Returns:
        Truncated path with "..." in the middle if needed
    """
    path_str = str(file_path)

    if len(path_str) <= max_length:
        return path_str

    path = Path(file_path)
    filename = path.name

    # Always keep the filename
    if len(filename) >= max_length - 3:
        return f"...{filename[-(max_length - 3) :]}"

    # Calculate how much of the path we can keep
    available = max_length - len(filename) - 4  # 4 for ".../""

    if available <= 0:
        return f".../{filename}"

    # Get the parent path
    parent = str(path.parent)

    # Take from the start of the parent path
    if len(parent) <= available:
        return path_str  # Shouldn't happen but be safe

    return f"{parent[:available]}.../{filename}"


def format_error_path(
    file_path: str | Path,
    base_path: str | Path | None = None,
    max_length: int | None = None,
) -> str:
    """Format a file path for error display.

    Converts to relative path if possible and optionally truncates.

    Args:
        file_path: The file path to format
        base_path: Base path for relative conversion (defaults to cwd)
        max_length: Maximum length (if None, no truncation)

    Returns:
        Formatted path string
    """
    # First make relative
    result = make_relative_path(file_path, base_path)

    # Then truncate if needed
    if max_length is not None and len(result) > max_length:
        result = truncate_path(result, max_length)

    return result
