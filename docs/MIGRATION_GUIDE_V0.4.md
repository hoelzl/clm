# Migration Guide: CLM v0.3.x → v0.4.0

## Summary

CLM v0.4.0 introduces a **unified package architecture**, consolidating all worker code into a single `clm` package with optional extras. This significantly simplifies installation and maintenance.

## Breaking Changes

### 1. Worker Packages No Longer Separate

**Before (v0.3.x)**:
```bash
pip install -e .
pip install -e ./services/notebook-processor
pip install -e ./services/plantuml-converter
pip install -e ./services/drawio-converter
```

**After (v0.4.0)**:
```bash
# Option 1: Install everything
pip install -e ".[all]"

# Option 2: Install specific workers
pip install -e ".[notebook]"
pip install -e ".[plantuml]"
pip install -e ".[drawio]"

# Option 3: Install all workers (but not dev tools)
pip install -e ".[all-workers]"
```

### 2. Module Paths Changed

**Before (v0.3.x)**:
```python
import nb
import plantuml_converter
import drawio_converter
```

**After (v0.4.0)**:
```python
from clm.workers import notebook
from clm.workers import plantuml
from clm.workers import drawio
```

**Command-line**:
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

### 3. Docker Images Updated

Docker images now install from the unified package:

**Before (v0.3.x)**:
```dockerfile
COPY ./clm-common ./clm-common
COPY ${SERVICE_PATH} ./service
RUN pip install ./clm-common && pip install ./service
CMD ["python", "-m", "nb"]
```

**After (v0.4.0)**:
```dockerfile
COPY . ./clm
RUN pip install ./clm[notebook]
CMD ["python", "-m", "clm.workers.notebook"]
```

## What Changed

### Package Structure

```
clm/ (v0.4.0)
├── pyproject.toml                 # Single package definition
├── src/clm/
│   ├── core/                      # Domain logic
│   ├── infrastructure/            # Infrastructure & backends
│   ├── cli/                       # CLI
│   └── workers/                   # NEW: Worker implementations
│       ├── notebook/              # From notebook-processor
│       ├── plantuml/              # From plantuml-converter
│       └── drawio/                # From drawio-converter
```

### Installation Extras

New optional dependencies available:

- `[notebook]` - Jupyter notebook processing
- `[plantuml]` - PlantUML diagram conversion
- `[drawio]` - Draw.io diagram conversion
- `[all-workers]` - All workers
- `[ml]` - Machine learning packages (PyTorch, FastAI, etc.)
- `[dev]` - Development tools (pytest, mypy, ruff)
- `[tui]` - TUI monitoring (textual, rich)
- `[web]` - Web dashboard (fastapi, uvicorn)
- `[all]` - Everything

### Direct Execution Mode

Workers now check availability and provide helpful error messages:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Worker 'notebook' not available in direct mode
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

To use notebook worker in direct execution mode:

  pip install clm[notebook]

Or install all workers:

  pip install clm[all-workers]

Or use Docker mode instead (no extra installation needed):

  clm build --execution-mode docker <course.yaml>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Migration Steps

### For Development Environments

```bash
cd /path/to/clm

# Remove old virtual environment if you have one
rm -rf venv .venv

# Install with all dependencies
pip install -e ".[all]"

# Or with uv
uv pip install -e ".[all]"

# Verify
python -c "from clm.workers import notebook; print('✓ Workers available')"
```

### For Docker-Only Users

No changes needed! Docker images are built with the correct extras.

### For Testing

```bash
# Run automated setup (installs everything)
./.claude/setup-test-env.sh

# Or manual installation
pip install -e ".[all]"

# Run tests
pytest                # Unit tests
pytest -m integration # Integration tests
pytest -m e2e        # E2E tests
```

### For Production

**Direct execution mode**:
```bash
pip install clm[all-workers]
```

**Docker mode**:
```bash
# Build images (no changes to docker-compose.yaml needed)
./build-services.sh

# Run services
docker-compose up -d
```

## Benefits

1. **Simpler Installation**: One package instead of four
2. **Flexible Dependencies**: Install only what you need
3. **Better Error Messages**: Clear guidance when workers are missing
4. **Unified Versioning**: All components versioned together
5. **Easier Maintenance**: Single source tree
6. **Template Bundling**: Notebook templates now properly included in wheel

## Rollback

If you need to roll back to v0.3.1:

```bash
git checkout v0.3.1
pip install -e .
pip install -e ./services/notebook-processor
pip install -e ./services/plantuml-converter
pip install -e ./services/drawio-converter
```

## Questions?

See updated documentation:
- `CLAUDE.md` - Full developer guide
- `README.md` - User-facing quick start
- `.claude/design/unified-package-architecture.md` - Architecture design document

**Version**: 0.4.0
**Date**: 2025-11-18
