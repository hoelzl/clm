# Migration Guide: CLM 0.2.x → 0.3.0

This guide helps you migrate from the old multi-package structure (v0.2.x) to the new consolidated package (v0.3.0).

## Quick Migration

### 1. Uninstall Old Packages

```bash
pip uninstall -y clm clm-cli clm-common clm-faststream-backend
# Note: clm-faststream-backend has been removed in post-v0.3.0 releases
```

### 2. Install New Package

```bash
cd /path/to/clm
pip install -e .
# or with uv
uv pip install -e .
```

### 3. Update Imports

Use find-and-replace in your codebase:

```python
# Core imports - Add .core
from clm import Course          → from clm.core import Course
from clm.course_files import    → from clm.core.course_files import
from clm.operations import      → from clm.core.operations import
from clm.utils import           → from clm.core.utils import

# Infrastructure imports - Replace clm_common with clm.infrastructure
from clm_common import           → from clm.infrastructure import
from clm_common.backend import   → from clm.infrastructure.backend import
from clm_common.database import  → from clm.infrastructure.database import
from clm_common.messaging import → from clm.infrastructure.messaging import
from clm_common.workers import   → from clm.infrastructure.workers import

# Backend imports - clm_faststream_backend has been removed
# If you were using SqliteBackend:
from clm_faststream_backend import SqliteBackend
  → from clm.infrastructure.backends import SqliteBackend
# Note: FastStreamBackend has been removed completely

# CLI imports - Replace clm_cli with clm.cli
from clm_cli.main import cli     → from clm.cli.main import cli
```

### 4. Convenience Imports Still Work!

These top-level imports are still available for backward compatibility:

```python
from clm import Course, Section, Topic, CourseFile, CourseSpec  # ✅ Still works!
```

## Detailed Changes

### Package Structure

**Before (v0.2.x)**:
```
clm/
clm-common/
clm-faststream-backend/  # Removed in post-v0.3.0
clm-cli/
```

**After (v0.3.0+)**:
```
clm/
  ├── clm.core/
  ├── clm.infrastructure/  # FastStream backend removed
  └── clm.cli/
```

### Import Examples

#### Core Classes

```python
# Old
from clm import Course, Section, Topic
from clm.course_file import CourseFile

# New (explicit)
from clm.core import Course, Section, Topic
from clm.core.course_file import CourseFile

# New (convenience - still works!)
from clm import Course, Section, Topic
```

#### File Handlers

```python
# Old
from clm.course_files.notebook_file import NotebookFile
from clm.course_files.plantuml_file import PlantUmlFile

# New
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_files.plantuml_file import PlantUmlFile
```

#### Infrastructure

```python
# Old
from clm_common.backend import Backend
from clm_common.database.job_queue import JobQueue
from clm_common.messaging.base_classes import Payload
from clm_common.workers.worker_base import WorkerBase

# New
from clm.infrastructure.backend import Backend
from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.messaging.base_classes import Payload
from clm.infrastructure.workers.worker_base import WorkerBase
```

#### Backends

```python
# Old
from clm_faststream_backend.sqlite_backend import SqliteBackend

# New
from clm.infrastructure.backends.sqlite_backend import SqliteBackend

# Or (shorter)
from clm.infrastructure.backends import SqliteBackend

# Note: FastStreamBackend (RabbitMQ) has been completely removed in post-v0.3.0 releases
```

#### CLI

```python
# Old
from clm_cli.main import cli

# New
from clm.cli.main import cli
```

## Service Workers

If you maintain service workers (notebook-processor, plantuml-converter, drawio-converter), update dependencies in `pyproject.toml`:

```toml
# Old
dependencies = [
    "clm-common==0.2.2",
]

# New
dependencies = [
    "clm>=0.3.0",
]
```

Then update imports in worker code:

```python
# Old
from clm_common.messaging import NotebookPayload
from clm_common.workers import WorkerBase

# New
from clm.infrastructure.messaging import NotebookPayload
from clm.infrastructure.workers import WorkerBase
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
        # clm-faststream-backend
        (r'from clm_faststream_backend\.sqlite_backend import',
         r'from clm.infrastructure.backends.sqlite_backend import'),
        (r'from clm_faststream_backend\.faststream_backend import',
         r'from clm.infrastructure.backends.faststream_backend import'),
        (r'from clm_faststream_backend import',
         r'from clm.infrastructure.backends import'),

        # clm-common
        (r'from clm_common\.backend import',
         r'from clm.infrastructure.backend import'),
        (r'from clm_common\.database import',
         r'from clm.infrastructure.database import'),
        (r'from clm_common\.messaging import',
         r'from clm.infrastructure.messaging import'),
        (r'from clm_common\.workers import',
         r'from clm.infrastructure.workers import'),
        (r'from clm_common\.logging import',
         r'from clm.infrastructure.logging import'),
        (r'from clm_common\.utils import',
         r'from clm.infrastructure.utils import'),
        (r'from clm_common import',
         r'from clm.infrastructure import'),

        # clm-cli
        (r'from clm_cli\.main import',
         r'from clm.cli.main import'),
        (r'from clm_cli import',
         r'from clm.cli import'),

        # clm core
        (r'from clm\.course_files import',
         r'from clm.core.course_files import'),
        (r'from clm\.operations import',
         r'from clm.core.operations import'),
        (r'from clm\.utils import',
         r'from clm.core.utils import'),
        (r'from clm\.course import',
         r'from clm.core.course import'),
        (r'from clm\.section import',
         r'from clm.core.section import'),
        (r'from clm\.topic import',
         r'from clm.core.topic import'),
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
   python -c "from clm import Course; print('✓ Imports work!')"
   ```

2. **Test CLI**:
   ```bash
   clm --help
   ```

3. **Run your tests**:
   ```bash
   pytest
   ```

## Rollback

If you need to rollback:

```bash
# Uninstall new package
pip uninstall -y clm

# Reinstall old packages
pip install -e clm-common/
pip install -e clm/
pip install -e clm-faststream-backend/
pip install -e clm-cli/
```

## FAQ

**Q: Do I need to update my code?**
A: If you only use top-level imports like `from clm import Course`, you don't need to change anything. If you import from submodules, you'll need to update imports.

**Q: Will old code break?**
A: Top-level convenience imports still work. Direct submodule imports need updating.

**Q: What about service workers?**
A: Update dependencies in `pyproject.toml` and imports in worker code.

**Q: Can I use the old and new packages together?**
A: No, they conflict. You must uninstall old packages before installing the new one.

**Q: Is the API the same?**
A: Yes! Only import paths changed. The API is identical.

## Support

- **Issues**: https://github.com/hoelzl/clm/issues
- **Docs**: https://github.com/hoelzl/clm/blob/main/CLAUDE.md

---

**Migration completed?** ✅ You're now on CLM 0.3.0!
