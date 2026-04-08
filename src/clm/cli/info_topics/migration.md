# CLM {version} — Migration Guide

This guide covers breaking changes across major CLM versions.

## v0.3.x to v0.4.0: Unified Package Architecture

### Summary

CLM v0.4.0 consolidated all worker code into a single `clm` package with
optional extras, replacing separate worker packages.

### Breaking Changes

#### Worker packages are no longer separate

Before (v0.3.x):

```bash
pip install -e .
pip install -e ./services/notebook-processor
pip install -e ./services/plantuml-converter
pip install -e ./services/drawio-converter
```

After (v0.4.0+):

```bash
pip install -e ".[all]"           # Everything
pip install -e ".[all-workers]"   # All workers only
pip install -e ".[notebook]"      # Specific worker
```

#### Module paths changed

Before (v0.3.x):

```python
import nb
import plantuml_converter
import drawio_converter
```

After (v0.4.0+):

```python
from clm.workers import notebook
from clm.workers import plantuml
from clm.workers import drawio
```

Command-line entry points:

```bash
# Before
python -m nb
python -m plantuml_converter
python -m drawio_converter

# After
python -m clm.workers.notebook
python -m clm.workers.plantuml
python -m clm.workers.drawio
```

#### Docker images updated

```dockerfile
# Before
COPY ./clm-common ./clm-common
COPY ${SERVICE_PATH} ./service
RUN pip install ./clm-common && pip install ./service
CMD ["python", "-m", "nb"]

# After
COPY . ./clm
RUN pip install ./clm[notebook]
CMD ["python", "-m", "clm.workers.notebook"]
```

### Installation extras (v0.4.0+)

| Extra | Description |
|-------|-------------|
| `[notebook]` | Jupyter notebook processing |
| `[plantuml]` | PlantUML diagram conversion |
| `[drawio]` | Draw.io diagram conversion |
| `[all-workers]` | All workers |
| `[ml]` | ML packages (PyTorch, FastAI, etc.) |
| `[summarize]` | LLM-powered summaries and polish (openai) |
| `[voiceover]` | Video-to-speaker-notes pipeline |
| `[recordings]` | Video recording management and audio processing |
| `[slides]` | Slide authoring tools with fuzzy search |
| `[mcp]` | MCP server for AI-assisted slide authoring |
| `[dev]` | Development tools (pytest, mypy, ruff) |
| `[tui]` | TUI monitoring |
| `[web]` | Web dashboard |
| `[all]` | Everything |

---

## v0.2.x to v0.3.0: Consolidated Package

### Summary

CLM v0.3.0 merged four separate packages (`clm`, `clm-common`,
`clm-faststream-backend`, `clm-cli`) into a single `clm` package.

### Breaking Changes

#### Import paths changed

```python
# Core imports — add .core
from clm import Course          # -> from clm.core import Course
from clm.course_files import    # -> from clm.core.course_files import
from clm.operations import      # -> from clm.core.operations import
from clm.utils import           # -> from clm.core.utils import

# Infrastructure — replace clm_common
from clm_common import           # -> from clm.infrastructure import
from clm_common.backend import   # -> from clm.infrastructure.backend import
from clm_common.database import  # -> from clm.infrastructure.database import
from clm_common.messaging import # -> from clm.infrastructure.messaging import
from clm_common.workers import   # -> from clm.infrastructure.workers import

# Backends — replace clm_faststream_backend
from clm_faststream_backend import SqliteBackend
# -> from clm.infrastructure.backends import SqliteBackend
# Note: FastStreamBackend (RabbitMQ) was removed entirely

# CLI — replace clm_cli
from clm_cli.main import cli    # -> from clm.cli.main import cli
```

#### Convenience imports still work

```python
from clm import Course, Section, Topic, CourseFile, CourseSpec  # OK
```

### Uninstalling old packages

```bash
pip uninstall -y clm clm-cli clm-common clm-faststream-backend
pip install -e .
```
