#!/usr/bin/env python3
"""Update imports from old package structure to new consolidated structure."""

import re
import sys
from pathlib import Path


def update_imports(file_path: Path) -> bool:
    """Update imports in a single file. Returns True if file was modified."""
    content = file_path.read_text()
    original_content = content

    # Define import replacements (order matters!)
    replacements = [
        # clx-faststream-backend imports
        (
            r"from clx_faststream_backend\.sqlite_backend import",
            r"from clx.infrastructure.backends.sqlite_backend import",
        ),
        (
            r"from clx_faststream_backend\.faststream_backend import",
            r"from clx.infrastructure.backends.faststream_backend import",
        ),
        (
            r"from clx_faststream_backend\.faststream_backend_handlers import",
            r"from clx.infrastructure.backends.handlers import",
        ),
        (r"from clx_faststream_backend import", r"from clx.infrastructure.backends import"),
        (
            r"import clx_faststream_backend\.sqlite_backend",
            r"import clx.infrastructure.backends.sqlite_backend",
        ),
        (
            r"import clx_faststream_backend\.faststream_backend",
            r"import clx.infrastructure.backends.faststream_backend",
        ),
        # clx-common imports (must come before general clx imports)
        (r"from clx_common\.backends import", r"from clx.infrastructure.backends import"),
        (r"from clx_common\.backend import", r"from clx.infrastructure.backend import"),
        (r"from clx_common\.database import", r"from clx.infrastructure.database import"),
        (r"from clx_common\.messaging import", r"from clx.infrastructure.messaging import"),
        (r"from clx_common\.workers import", r"from clx.infrastructure.workers import"),
        (r"from clx_common\.logging import", r"from clx.infrastructure.logging import"),
        (r"from clx_common\.services import", r"from clx.infrastructure.services import"),
        (r"from clx_common\.utils import", r"from clx.infrastructure.utils import"),
        (r"from clx_common\.operation import", r"from clx.infrastructure.operation import"),
        (r"from clx_common import", r"from clx.infrastructure import"),
        (r"import clx_common\.", r"import clx.infrastructure."),
        (r"import clx_common$", r"import clx.infrastructure"),
        # clx-cli imports
        (r"from clx_cli\.main import", r"from clx.cli.main import"),
        (r"from clx_cli\.file_event_handler import", r"from clx.cli.file_event_handler import"),
        (r"from clx_cli\.git_dir_mover import", r"from clx.cli.git_dir_mover import"),
        (r"from clx_cli import", r"from clx.cli import"),
        (r"import clx_cli\.", r"import clx.cli."),
        # clx core imports (specific subpackages first)
        (r"from clx\.course_files import", r"from clx.core.course_files import"),
        (r"from clx\.operations import", r"from clx.core.operations import"),
        (r"from clx\.utils import", r"from clx.core.utils import"),
        # General clx imports (but preserve clx.core, clx.infrastructure, clx.cli)
        # Only replace "from clx import" and "from clx.X import" where X is not core/infrastructure/cli
        (r"from clx\.course import", r"from clx.core.course import"),
        (r"from clx\.course_file import", r"from clx.core.course_file import"),
        (r"from clx\.course_spec import", r"from clx.core.course_spec import"),
        (r"from clx\.section import", r"from clx.core.section import"),
        (r"from clx\.topic import", r"from clx.core.topic import"),
        (r"from clx\.dir_group import", r"from clx.core.dir_group import"),
    ]

    # Apply replacements
    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    # Write back if changed
    if content != original_content:
        file_path.write_text(content)
        return True
    return False


def main():
    """Update all Python files in clx-new directory."""
    root = Path("clx-new")
    if not root.exists():
        print(f"Error: {root} does not exist")
        sys.exit(1)

    modified_files = []
    total_files = 0

    # Process all Python files
    for py_file in root.rglob("*.py"):
        total_files += 1
        if update_imports(py_file):
            modified_files.append(py_file)
            print(f"Updated: {py_file.relative_to(root)}")

    print(f"\nProcessed {total_files} files, modified {len(modified_files)} files")

    if modified_files:
        print("\nModified files:")
        for f in modified_files:
            print(f"  - {f.relative_to(root)}")


if __name__ == "__main__":
    main()
