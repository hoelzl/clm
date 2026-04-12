# CLM {version} — Migration Guide

This guide covers breaking changes across major CLM versions.

## Migrating from `-build.xml` subset specs to `enabled="false"`

CLM {version} introduced the `enabled` attribute on `<section>` elements and
the `clm build --only-sections` flag. Together they replace the common
pattern of carrying a second "buildable subset" spec file alongside the
full roadmap spec.

Before, courses with not-yet-implemented topics typically looked like
this:

```text
course-specs/
├── machine-learning-azav.xml        # full roadmap; wraps unfinished
│                                    # sections in <!-- XML comments -->
└── machine-learning-azav-build.xml  # same spec with those sections
                                      # removed so clm build succeeds
```

Three-step migration:

1. **Add `enabled="false"` to not-yet-ready sections** in the full
   roadmap spec. A disabled section may omit `<topics>` entirely or
   reference topic IDs that do not exist — it is not built or validated.

   ```xml
   <section id="w17" enabled="false">
       <name>
           <de>Woche 17: Fortgeschrittene Themen</de>
           <en>Week 17: Advanced Topics</en>
       </name>
       <topics>
           <topic>not_yet_implemented_topic</topic>
       </topics>
   </section>
   ```

2. **Delete the `-build.xml` subset file.** One source of truth from
   now on.

3. **Update any scripts or automation** that reference the `-build.xml`
   path to use the full spec instead.

Verification:

- `clm build course.xml` — builds the full roadmap minus disabled
  sections.
- `clm build course.xml --only-sections w03` — dev-time iteration on a
  single section (see `clm info commands`).
- `clm outline course.xml --include-disabled` — lists the disabled
  sections with a `(disabled)` marker so you can see the full roadmap.
- `clm validate-spec course.xml --include-disabled` — validates disabled
  sections' topics with a `(disabled)` suffix on each finding so you can
  track which topics still need to be created.

See also: `clm info spec-files` for the `enabled` / `id` attribute
reference and `clm info commands` for the `--only-sections` selector
syntax.

## v1.2.0 to v1.2.1: Voiceover sync argument order change

### Breaking Change

`clm voiceover sync` now accepts **multiple video files**. To support this,
the argument order was flipped:

```bash
# Before (v1.2.0)
clm voiceover sync VIDEO SLIDES --lang de

# After (v1.2.1)
clm voiceover sync SLIDES VIDEO... --lang de
```

`SLIDES` is now the first positional argument, followed by one or more
`VIDEO` paths. Single-video invocations work the same way — just swap the
argument order.

### New default: merge mode

`clm voiceover sync` now **merges** transcript content into existing
voiceover cells by default instead of overwriting them. Use `--overwrite`
to restore the old destructive behavior. Note that `--mode verbatim`
without `--overwrite` is now an error.

---

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
