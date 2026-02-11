# Phase 7: Package Consolidation Design

## Overview

This phase consolidates the four separate packages (`clm`, `clm-common`, `clm-cli`, `clm-faststream-backend`) into a single unified `clm` package with clear subpackage organization. Services remain as separate packages.

## Goals

1. **Simplify installation**: Single `pip install clm` instead of 4 packages
2. **Improve code organization**: Clear subpackage structure that reflects system architecture
3. **Modern packaging**: Use pyproject.toml with uv support
4. **Maintain clarity**: Logical separation of concerns through subpackages
5. **Preserve functionality**: All tests must pass after migration

## Current Structure (4 Packages)

```
clm/                              # 24 Python files
├── src/clm/
│   ├── course.py, course_file.py, course_spec.py
│   ├── section.py, topic.py, dir_group.py
│   ├── course_files/            # notebook, plantuml, drawio
│   ├── operations/
│   └── utils/
└── tests/

clm-common/                       # 31 Python files
├── src/clm_common/
│   ├── backend.py, operation.py
│   ├── backends/
│   ├── database/                # SQLite job queue
│   ├── messaging/               # Pydantic models
│   ├── workers/                 # Worker management
│   ├── logging/
│   ├── services/
│   └── utils/
└── tests/

clm-faststream-backend/           # 4 Python files
├── src/clm_faststream_backend/
│   ├── sqlite_backend.py        # Primary backend
│   ├── faststream_backend.py    # Legacy RabbitMQ
│   └── faststream_backend_handlers.py
└── tests/

clm-cli/                          # 4 Python files
├── src/clm_cli/
│   ├── main.py                  # Click CLI
│   ├── file_event_handler.py    # Watchdog integration
│   └── git_dir_mover.py
└── tests/
```

**Dependencies:**
- `clm` → `clm-common`
- `clm-cli` → `clm`, `clm-faststream-backend`
- `clm-faststream-backend` → `clm-common`

## New Structure (Single Package)

```
clm/
├── src/clm/
│   ├── __init__.py
│   ├── __version__.py           # Version info
│   │
│   ├── core/                    # Core course processing (from old clm)
│   │   ├── __init__.py
│   │   ├── course.py
│   │   ├── course_file.py
│   │   ├── course_spec.py
│   │   ├── section.py
│   │   ├── topic.py
│   │   ├── dir_group.py
│   │   ├── course_files/        # File type handlers
│   │   │   ├── __init__.py
│   │   │   ├── notebook_file.py
│   │   │   ├── plantuml_file.py
│   │   │   └── drawio_file.py
│   │   ├── operations/          # File operations
│   │   │   ├── __init__.py
│   │   │   ├── copy_course_file_operation.py
│   │   │   ├── copy_dir_group_operation.py
│   │   │   ├── drawio_operation.py
│   │   │   ├── notebook_operation.py
│   │   │   └── plantuml_operation.py
│   │   └── utils/               # Course-specific utilities
│   │       ├── __init__.py
│   │       ├── execution.py
│   │       ├── notebook.py
│   │       └── text.py
│   │
│   ├── infrastructure/          # Infrastructure (from old clm-common)
│   │   ├── __init__.py
│   │   ├── backend.py           # Backend interface
│   │   ├── operation.py         # Operation base class
│   │   ├── backends/            # Backend implementations
│   │   │   ├── __init__.py
│   │   │   ├── sqlite_backend.py      (from clm-faststream-backend)
│   │   │   ├── faststream_backend.py  (from clm-faststream-backend)
│   │   │   └── handlers.py            (faststream handlers)
│   │   ├── database/            # SQLite job queue system
│   │   │   ├── __init__.py
│   │   │   ├── schema.py
│   │   │   ├── job_queue.py
│   │   │   └── db_operations.py
│   │   ├── messaging/           # Message payloads and results
│   │   │   ├── __init__.py
│   │   │   ├── base_classes.py
│   │   │   ├── correlation_ids.py
│   │   │   ├── routing_keys.py
│   │   │   ├── notebook_classes.py
│   │   │   ├── plantuml_classes.py
│   │   │   └── drawio_classes.py
│   │   ├── workers/             # Worker management
│   │   │   ├── __init__.py
│   │   │   ├── worker_base.py
│   │   │   ├── pool_manager.py
│   │   │   └── worker_executor.py
│   │   ├── logging/             # Logging utilities
│   │   │   ├── __init__.py
│   │   │   └── loguru_setup.py
│   │   ├── services/            # Service registry
│   │   │   ├── __init__.py
│   │   │   └── service_registry.py
│   │   └── utils/               # Infrastructure utilities
│   │       ├── __init__.py
│   │       ├── copy_file_data.py
│   │       ├── copy_dir_group_data.py
│   │       ├── file.py
│   │       └── path_utils.py
│   │
│   ├── cli/                     # CLI (from old clm-cli)
│   │   ├── __init__.py
│   │   ├── main.py              # Click CLI entry point
│   │   ├── file_event_handler.py
│   │   └── git_dir_mover.py
│   │
│   └── py.typed                 # PEP 561 type marker
│
├── tests/                       # All tests consolidated
│   ├── __init__.py
│   ├── conftest.py              # Root fixtures
│   │
│   ├── core/                    # Tests for core module
│   │   ├── __init__.py
│   │   ├── test_course.py
│   │   ├── test_course_spec.py
│   │   ├── test_topic.py
│   │   ├── course_files/
│   │   │   ├── __init__.py
│   │   │   ├── test_drawio_file.py
│   │   │   ├── test_notebook_file.py
│   │   │   └── test_plantuml_file.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── test_execution.py
│   │       ├── test_notebook_utils.py
│   │       └── test_text_utils.py
│   │
│   ├── infrastructure/          # Tests for infrastructure
│   │   ├── __init__.py
│   │   ├── test_operation.py
│   │   ├── test_correlation_ids.py
│   │   ├── backends/
│   │   │   ├── __init__.py
│   │   │   ├── test_sqlite_backend.py
│   │   │   └── test_faststream_handlers.py
│   │   ├── database/
│   │   │   ├── __init__.py
│   │   │   ├── test_job_queue.py
│   │   │   ├── test_schema.py
│   │   │   └── test_db_operations.py
│   │   ├── workers/
│   │   │   ├── __init__.py
│   │   │   ├── test_worker_base.py
│   │   │   ├── test_pool_manager.py
│   │   │   └── test_worker_executor.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── test_path_utils.py
│   │
│   ├── cli/                     # Tests for CLI
│   │   ├── __init__.py
│   │   ├── conftest.py          # CLI-specific fixtures
│   │   ├── test_cli_unit.py
│   │   ├── test_cli_integration.py
│   │   └── test_cli_subprocess.py
│   │
│   └── e2e/                     # End-to-end tests
│       ├── __init__.py
│       └── test_e2e_course_conversion.py
│
├── pyproject.toml               # Modern packaging configuration
├── README.md
├── LICENSE
└── MIGRATION_GUIDE.md           # Guide for users upgrading
```

## Key Design Decisions

### 1. Three-Layer Architecture

The package is organized into three clear layers:

1. **`core`** - Domain logic for course processing
   - No dependencies on infrastructure or CLI
   - Pure course/file/topic models and operations
   - Can be used standalone for programmatic course manipulation

2. **`infrastructure`** - Infrastructure and runtime support
   - Backend implementations (SQLite, RabbitMQ)
   - Job queue system
   - Worker management
   - Message definitions
   - Can be used to build alternative interfaces (web, etc.)

3. **`cli`** - Command-line interface
   - Depends on both core and infrastructure
   - User-facing commands
   - File watching
   - Progress reporting

### 2. Import Path Changes

**Old imports:**
```python
from clm import Course, Section, Topic
from clm.course_files import NotebookFile, PlantUmlFile
from clm_common.backend import Backend
from clm_common.database import JobQueue
from clm_common.messaging import NotebookPayload
from clm_common.workers import WorkerBase
from clm_faststream_backend import SqliteBackend
from clm_cli.main import cli
```

**New imports:**
```python
from clm.core import Course, Section, Topic
from clm.core.course_files import NotebookFile, PlantUmlFile
from clm.infrastructure.backend import Backend
from clm.infrastructure.database import JobQueue
from clm.infrastructure.messaging import NotebookPayload
from clm.infrastructure.workers import WorkerBase
from clm.infrastructure.backends import SqliteBackend
from clm.cli.main import cli
```

**Backward compatibility:**
```python
# Top-level __init__.py will provide shortcuts
from clm import Course, Section, Topic  # Still works via __init__.py
```

### 3. Modern Packaging

**Single pyproject.toml:**
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "clm"
version = "0.3.0"  # Bump to indicate breaking changes
description = "Coding-Academy Lecture Manager eXperimental"
authors = [{name = "Dr. Matthias Hölzl", email = "tc@xantira.com"}]
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}

dependencies = [
    "pydantic~=2.8.2",
    "click>=8.1.0",
    "watchdog>=6.0.0",
    "attrs>=25.4.0",
    "faststream[rabbit]~=0.5.19",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
]

[project.scripts]
clm = "clm.cli.main:cli"

[tool.hatch.build.targets.wheel]
packages = ["src/clm"]
```

### 4. Test Organization

Tests mirror the source structure:
- `tests/core/` - Core module tests
- `tests/infrastructure/` - Infrastructure tests
- `tests/cli/` - CLI tests
- `tests/e2e/` - End-to-end tests

All test markers are consolidated in root `pyproject.toml`.

### 5. Services Remain Separate

The three worker services remain as separate packages:
- `services/notebook-processor/`
- `services/plantuml-converter/`
- `services/drawio-converter/`

These will update their imports to use `clm.infrastructure.*` instead of `clm_common.*`.

## Migration Steps

1. **Create new structure** - Set up new directory layout
2. **Move core code** - Copy clm → clm.core
3. **Move infrastructure** - Copy clm-common → clm.infrastructure
4. **Move backends** - Copy clm-faststream-backend → clm.infrastructure.backends
5. **Move CLI** - Copy clm-cli → clm.cli
6. **Update imports** - Fix all import statements
7. **Consolidate tests** - Merge test suites
8. **Update services** - Fix service imports
9. **Test everything** - Run full test suite
10. **Update documentation** - Update all docs

## Breaking Changes

### Version Bump

**0.2.2 → 0.3.0** (minor version bump for breaking changes)

### Import Changes

All import paths change:
- `clm` → `clm.core`
- `clm_common` → `clm.infrastructure`
- `clm_faststream_backend` → `clm.infrastructure.backends`
- `clm_cli` → `clm.cli`

### Installation Changes

**Old:**
```bash
pip install -e clm-common/
pip install -e clm/
pip install -e clm-faststream-backend/
pip install -e clm-cli/
```

**New:**
```bash
pip install -e .
# or
uv pip install -e .
```

## Rollout Plan

1. Create new structure in parallel (keep old packages)
2. Run tests to ensure parity
3. Update services to support both old and new imports
4. Tag release as v0.3.0
5. Remove old package directories
6. Update CI/CD
7. Update documentation

## Success Criteria

- [ ] All unit tests pass (no skipped tests)
- [ ] All integration tests pass
- [ ] All e2e tests pass
- [ ] Package installs with `pip install -e .`
- [ ] Package installs with `uv pip install -e .`
- [ ] CLI works: `clm build <course.yaml>`
- [ ] Services can import from new structure
- [ ] Documentation updated
- [ ] Migration guide created

## Benefits

1. **Simpler installation**: One command instead of four
2. **Clearer architecture**: Subpackages reflect system layers
3. **Better IDE support**: Single package root
4. **Easier refactoring**: No cross-package concerns
5. **Modern tooling**: Works with uv, pip-tools, poetry
6. **Single version**: No version sync issues
7. **Better discoverability**: Clear import hierarchy

## Risks and Mitigations

**Risk**: Breaking existing code
**Mitigation**: Provide backward compatibility shims, clear migration guide

**Risk**: Import cycles
**Mitigation**: Clear layering (core → infrastructure → cli)

**Risk**: Test failures
**Mitigation**: Incremental migration, test at each step

**Risk**: Service breakage
**Mitigation**: Update services before removing old packages

---

**Status**: Design phase
**Next**: Implementation
**Owner**: Claude (Phase 7 migration)
