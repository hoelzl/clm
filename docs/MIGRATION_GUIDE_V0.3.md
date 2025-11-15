# Migration Guide: CLX 0.2.x → 0.3.0

This guide helps you migrate from the old multi-package structure (v0.2.x) to the new consolidated package (v0.3.0).

## Quick Migration

### 1. Uninstall Old Packages

```bash
pip uninstall -y clx clx-cli clx-common clx-faststream-backend
# Note: clx-faststream-backend has been removed in post-v0.3.0 releases
```

### 2. Install New Package

```bash
cd /path/to/clx
pip install -e .
# or with uv
uv pip install -e .
```

### 3. Update Imports

Use find-and-replace in your codebase:

```python
# Core imports - Add .core
from clx import Course          → from clx.core import Course
from clx.course_files import    → from clx.core.course_files import
from clx.operations import      → from clx.core.operations import
from clx.utils import           → from clx.core.utils import

# Infrastructure imports - Replace clx_common with clx.infrastructure
from clx_common import           → from clx.infrastructure import
from clx_common.backend import   → from clx.infrastructure.backend import
from clx_common.database import  → from clx.infrastructure.database import
from clx_common.messaging import → from clx.infrastructure.messaging import
from clx_common.workers import   → from clx.infrastructure.workers import

# Backend imports - clx_faststream_backend has been removed
# If you were using SqliteBackend:
from clx_faststream_backend import SqliteBackend
  → from clx.infrastructure.backends import SqliteBackend
# Note: FastStreamBackend has been removed completely

# CLI imports - Replace clx_cli with clx.cli
from clx_cli.main import cli     → from clx.cli.main import cli
```

### 4. Convenience Imports Still Work!

These top-level imports are still available for backward compatibility:

```python
from clx import Course, Section, Topic, CourseFile, CourseSpec  # ✅ Still works!
```

## Detailed Changes

### Package Structure

**Before (v0.2.x)**:
```
clx/
clx-common/
clx-faststream-backend/  # Removed in post-v0.3.0
clx-cli/
```

**After (v0.3.0+)**:
```
clx/
  ├── clx.core/
  ├── clx.infrastructure/  # FastStream backend removed
  └── clx.cli/
```

### Import Examples

#### Core Classes

```python
# Old
from clx import Course, Section, Topic
from clx.course_file import CourseFile

# New (explicit)
from clx.core import Course, Section, Topic
from clx.core.course_file import CourseFile

# New (convenience - still works!)
from clx import Course, Section, Topic
```

#### File Handlers

```python
# Old
from clx.course_files.notebook_file import NotebookFile
from clx.course_files.plantuml_file import PlantUmlFile

# New
from clx.core.course_files.notebook_file import NotebookFile
from clx.core.course_files.plantuml_file import PlantUmlFile
```

#### Infrastructure

```python
# Old
from clx_common.backend import Backend
from clx_common.database.job_queue import JobQueue
from clx_common.messaging.base_classes import Payload
from clx_common.workers.worker_base import WorkerBase

# New
from clx.infrastructure.backend import Backend
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.messaging.base_classes import Payload
from clx.infrastructure.workers.worker_base import WorkerBase
```

#### Backends

```python
# Old
from clx_faststream_backend.sqlite_backend import SqliteBackend

# New
from clx.infrastructure.backends.sqlite_backend import SqliteBackend

# Or (shorter)
from clx.infrastructure.backends import SqliteBackend

# Note: FastStreamBackend (RabbitMQ) has been completely removed in post-v0.3.0 releases
```

#### CLI

```python
# Old
from clx_cli.main import cli

# New
from clx.cli.main import cli
```

## Service Workers

If you maintain service workers (notebook-processor, plantuml-converter, drawio-converter), update dependencies in `pyproject.toml`:

```toml
# Old
dependencies = [
    "clx-common==0.2.2",
]

# New
dependencies = [
    "clx>=0.3.0",
]
```

Then update imports in worker code:

```python
# Old
from clx_common.messaging import NotebookPayload
from clx_common.workers import WorkerBase

# New
from clx.infrastructure.messaging import NotebookPayload
from clx.infrastructure.workers import WorkerBase
```

## Automated Migration Script

Save this as `migrate_imports.py`:

```python
#!/usr/bin/env python3
"""Automatically migrate imports from v0.2.x to v0.3.0"""

import re
from pathlib import Path
import sys

def update_imports(file_path: Path) -> bool:
    """Update imports in a single file."""
    content = file_path.read_text()
    original = content

    replacements = [
        # clx-faststream-backend
        (r'from clx_faststream_backend\.sqlite_backend import',
         r'from clx.infrastructure.backends.sqlite_backend import'),
        (r'from clx_faststream_backend\.faststream_backend import',
         r'from clx.infrastructure.backends.faststream_backend import'),
        (r'from clx_faststream_backend import',
         r'from clx.infrastructure.backends import'),

        # clx-common
        (r'from clx_common\.backend import',
         r'from clx.infrastructure.backend import'),
        (r'from clx_common\.database import',
         r'from clx.infrastructure.database import'),
        (r'from clx_common\.messaging import',
         r'from clx.infrastructure.messaging import'),
        (r'from clx_common\.workers import',
         r'from clx.infrastructure.workers import'),
        (r'from clx_common\.logging import',
         r'from clx.infrastructure.logging import'),
        (r'from clx_common\.utils import',
         r'from clx.infrastructure.utils import'),
        (r'from clx_common import',
         r'from clx.infrastructure import'),

        # clx-cli
        (r'from clx_cli\.main import',
         r'from clx.cli.main import'),
        (r'from clx_cli import',
         r'from clx.cli import'),

        # clx core
        (r'from clx\.course_files import',
         r'from clx.core.course_files import'),
        (r'from clx\.operations import',
         r'from clx.core.operations import'),
        (r'from clx\.utils import',
         r'from clx.core.utils import'),
        (r'from clx\.course import',
         r'from clx.core.course import'),
        (r'from clx\.section import',
         r'from clx.core.section import'),
        (r'from clx\.topic import',
         r'from clx.core.topic import'),
    ]

    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    if content != original:
        file_path.write_text(content)
        return True
    return False

def main():
    """Update all Python files in current directory and subdirectories."""
    root = Path(".")
    modified = []

    for py_file in root.rglob("*.py"):
        if update_imports(py_file):
            modified.append(py_file)
            print(f"Updated: {py_file}")

    print(f"\nModified {len(modified)} files")

if __name__ == "__main__":
    main()
```

Run with:
```bash
python migrate_imports.py
```

## Testing After Migration

1. **Test imports**:
   ```bash
   python -c "from clx import Course; print('✓ Imports work!')"
   ```

2. **Test CLI**:
   ```bash
   clx --help
   ```

3. **Run your tests**:
   ```bash
   pytest
   ```

## Rollback

If you need to rollback:

```bash
# Uninstall new package
pip uninstall -y clx

# Reinstall old packages
pip install -e clx-common/
pip install -e clx/
pip install -e clx-faststream-backend/
pip install -e clx-cli/
```

## FAQ

**Q: Do I need to update my code?**
A: If you only use top-level imports like `from clx import Course`, you don't need to change anything. If you import from submodules, you'll need to update imports.

**Q: Will old code break?**
A: Top-level convenience imports still work. Direct submodule imports need updating.

**Q: What about service workers?**
A: Update dependencies in `pyproject.toml` and imports in worker code.

**Q: Can I use the old and new packages together?**
A: No, they conflict. You must uninstall old packages before installing the new one.

**Q: Is the API the same?**
A: Yes! Only import paths changed. The API is identical.

## Support

- **Issues**: https://github.com/hoelzl/clx/issues
- **Docs**: https://github.com/hoelzl/clx/blob/main/CLAUDE.md

---

**Migration completed?** ✅ You're now on CLX 0.3.0!
