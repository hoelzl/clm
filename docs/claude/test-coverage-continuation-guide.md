# Test Coverage Improvement: Continuation Guide

**Created**: 2025-11-25
**Last Updated**: 2025-11-26
**Starting Coverage**: 53%
**Current Coverage**: 69%
**Target Coverage**: 75%+

This document provides detailed instructions for continuing the test coverage improvement effort.

---

## Summary of Completed Work (Phases 1-4)

### Phase Status

| Phase | Status | Tests Added | Coverage Gain |
|-------|--------|-------------|---------------|
| Phase 1: Quick Wins | ✅ Complete | ~290 | 53% → 61% |
| Phase 2: Infrastructure | ✅ Complete | ~100 | 61% → 63% |
| Phase 3: Worker Modules | ✅ Complete | ~200 | 63% → 68% |
| Phase 4: Complex Modules | ✅ Complete | ~80 | 68% → 69% |

### Tests Created (All Phases)

| Test File | Tests | Module Covered | Coverage |
|-----------|-------|----------------|----------|
| **Phase 1** | | | |
| `tests/workers/notebook/utils/test_prog_lang_utils.py` | 32 | `prog_lang_utils.py` | 98% |
| `tests/workers/notebook/test_output_spec.py` | 73 | `output_spec.py` | 100% |
| `tests/workers/notebook/utils/test_jupyter_utils.py` | 42 | `jupyter_utils.py` | 100% |
| `tests/workers/drawio/test_drawio_converter.py` | 31 | `drawio_converter.py` | 100% |
| `tests/workers/plantuml/test_plantuml_converter.py` | 32 | `plantuml_converter.py` | 30%* |
| `tests/infrastructure/workers/test_config_loader.py` | 32 | `config_loader.py` | 100% |
| **Phase 2** | | | |
| `tests/infrastructure/services/test_subprocess_tools.py` | 26 | `subprocess_tools.py` | 100% |
| `tests/infrastructure/workers/test_discovery.py` | 32 | `discovery.py` | 100% |
| `tests/core/operations/test_delete_file.py` | 15 | `delete_file.py` | 100% |
| `tests/web/api/test_routes.py` | 25 | `routes.py` | 100% |
| **Phase 3** | | | |
| `tests/workers/drawio/test_drawio_worker.py` | 37 | `drawio_worker.py` | 100% |
| `tests/workers/plantuml/test_plantuml_worker.py` | 34 | `plantuml_worker.py` | 62%* |
| `tests/workers/notebook/test_notebook_worker.py` | 45 | `notebook_worker.py` | 100% |
| `tests/infrastructure/workers/test_lifecycle_manager.py` | 48 | `lifecycle_manager.py` | 100% |
| **Phase 4** | | | |
| `tests/workers/notebook/test_notebook_processor.py` | 41 | `notebook_processor.py` | 84% |
| `tests/web/services/test_monitor_service.py` | 18 | `monitor_service.py` | 38% |
| `tests/web/api/test_websocket.py` | 19 | `websocket.py` | 65% |

*PlantUML tests are skipped without the JAR file present

### Bug Fixes Applied

1. **output_spec.py** - Fixed attrs `@define` fields not being properly overridden in subclasses:
   ```python
   # Before (broken - attrs fields not overridden)
   tags_to_retain_code_cell_contents = {"keep", "start"}

   # After (fixed)
   from attr import Factory, define
   tags_to_retain_code_cell_contents: set[str] = Factory(lambda: {"keep", "start"})
   ```

2. **monitor_service.py** - Fixed SQL query using incorrect column names:
   - `w.worker_id` → `w.container_id`
   - `w.created_at` → `w.started_at`

---

## Lessons Learned & Patterns

### 1. Logger Capture in Tests

When testing logging, you must specify the logger name:

```python
def test_logs_something(self, caplog):
    import logging
    # WRONG - won't capture logs
    with caplog.at_level(logging.INFO):
        do_something()

    # RIGHT - specify the logger name
    with caplog.at_level(logging.INFO, logger="clx.module.submodule"):
        do_something()

    assert "expected text" in caplog.text
```

### 2. NotebookNode for Cell Mocks

When creating mock notebook cells, use `NotebookNode` instead of plain dicts if the code accesses attributes (like `cell.source`):

```python
from nbformat import NotebookNode

@pytest.fixture
def make_cell():
    def _make_cell(cell_type="code", tags=None, source=""):
        return NotebookNode({
            "cell_type": cell_type,
            "source": source,
            "metadata": {"tags": tags or []},
        })
    return _make_cell
```

### 3. Pydantic Model Responses in FastAPI Tests

When mocking services that return Pydantic models, return actual model instances, not MagicMocks:

```python
# WRONG - causes validation errors
mock_service.get_status.return_value = MagicMock()

# RIGHT - return actual Pydantic models
from clx.web.models import StatusResponse, DatabaseInfoResponse
mock_service.get_status.return_value = StatusResponse(
    status="healthy",
    timestamp=datetime.now(),
    database=DatabaseInfoResponse(path="/test/db", accessible=True, exists=True),
    workers={},
    queue=QueueStatsResponse(pending=0, processing=0, completed_last_hour=0, failed_last_hour=0),
)
```

### 4. Frozen Attrs Classes

You cannot patch methods on frozen attrs classes. Instead, test the actual behavior:

```python
# WRONG - raises FrozenInstanceError
with patch.object(frozen_instance, 'method'):
    ...

# RIGHT - test actual behavior
assert temp_file.exists()
await frozen_instance.execute(backend=None)
assert not temp_file.exists()
```

### 5. PlantUML JAR Discovery

PlantUML tests require the JAR file. Use dynamic discovery and skipif markers:

```python
def _find_plantuml_jar():
    possible_paths = [
        Path(__file__).parents[4] / "docker" / "plantuml" / "plantuml-1.2024.6.jar",
    ]
    for path in possible_paths:
        if path.exists():
            return str(path)
    return None

_jar_path = _find_plantuml_jar()
if _jar_path:
    os.environ["PLANTUML_JAR"] = _jar_path

HAS_PLANTUML = _can_import_plantuml()

pytestmark = pytest.mark.skipif(
    not HAS_PLANTUML,
    reason="PlantUML JAR not found"
)
```

---

## Phase 3: Worker Modules ✅ COMPLETE

**Status**: Complete
**Actual coverage gain**: +5% (63% → 68%)

### 3.1 DrawIO Worker (`drawio_worker.py` - 81 statements)

**File to create**: `tests/workers/drawio/test_drawio_worker.py`

**What to test**:
- Worker initialization and registration
- Job polling and processing loop
- Input file reading
- Output format handling (png, svg, pdf)
- Converter invocation with correct parameters
- Output file writing
- Result caching
- Job cancellation detection
- Error handling and recovery

**Key mocking requirements**:
- Mock `JobQueue` for job operations
- Mock `convert_drawio` to avoid external tool dependency
- Mock file I/O operations

**Example structure**:

```python
"""Tests for DrawIO worker."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

# Skip if drawio dependencies not available
try:
    from clx.workers.drawio.drawio_worker import DrawioWorker
    from clx.infrastructure.messaging.drawio_classes import DrawioPayload, DrawioResult
    HAS_DRAWIO = True
except ImportError:
    HAS_DRAWIO = False

pytestmark = pytest.mark.skipif(not HAS_DRAWIO, reason="DrawIO worker dependencies not installed")


@pytest.fixture
def mock_job_queue():
    """Create a mock job queue."""
    queue = MagicMock()
    queue.register_worker = MagicMock()
    queue.poll_job = AsyncMock(return_value=None)
    queue.update_job_status = MagicMock()
    queue.store_result = MagicMock()
    queue.send_heartbeat = MagicMock()
    return queue


@pytest.fixture
def mock_drawio_payload():
    """Create a valid DrawIO payload."""
    return DrawioPayload(
        input_file="/test/diagram.drawio",
        output_file="/test/output/diagram.png",
        output_format="png",
    )


class TestDrawioWorkerInit:
    """Test worker initialization."""

    def test_worker_registers_with_queue(self, mock_job_queue):
        """Worker should register itself with the job queue on init."""
        worker = DrawioWorker(job_queue=mock_job_queue, worker_id="test-worker")
        mock_job_queue.register_worker.assert_called_once()

    def test_worker_has_correct_type(self, mock_job_queue):
        """Worker should have worker_type 'drawio'."""
        worker = DrawioWorker(job_queue=mock_job_queue, worker_id="test-worker")
        assert worker.worker_type == "drawio"


class TestDrawioWorkerProcessJob:
    """Test job processing."""

    @pytest.mark.asyncio
    async def test_process_job_calls_converter(self, mock_job_queue, mock_drawio_payload, tmp_path):
        """Processing a job should call the converter."""
        # Create input file
        input_file = tmp_path / "diagram.drawio"
        input_file.write_text("<diagram>content</diagram>")

        mock_drawio_payload.input_file = str(input_file)
        mock_drawio_payload.output_file = str(tmp_path / "output.png")

        worker = DrawioWorker(job_queue=mock_job_queue, worker_id="test-worker")

        with patch("clx.workers.drawio.drawio_worker.convert_drawio") as mock_convert:
            mock_convert.return_value = None

            result = await worker.process_payload(mock_drawio_payload, "correlation-id")

            mock_convert.assert_called_once()
            assert mock_convert.call_args[0][0] == str(input_file)

    @pytest.mark.asyncio
    async def test_process_job_handles_missing_input(self, mock_job_queue, mock_drawio_payload):
        """Should handle missing input file gracefully."""
        mock_drawio_payload.input_file = "/nonexistent/file.drawio"

        worker = DrawioWorker(job_queue=mock_job_queue, worker_id="test-worker")

        with pytest.raises(FileNotFoundError):
            await worker.process_payload(mock_drawio_payload, "correlation-id")


class TestDrawioWorkerHeartbeat:
    """Test heartbeat functionality."""

    @pytest.mark.asyncio
    async def test_sends_heartbeat_during_processing(self, mock_job_queue, mock_drawio_payload, tmp_path):
        """Worker should send heartbeats during long operations."""
        # This tests the heartbeat mechanism
        pass  # Implementation depends on worker architecture


class TestDrawioWorkerCancellation:
    """Test job cancellation."""

    @pytest.mark.asyncio
    async def test_detects_cancelled_job(self, mock_job_queue):
        """Worker should detect and handle cancelled jobs."""
        mock_job_queue.is_job_cancelled = MagicMock(return_value=True)

        worker = DrawioWorker(job_queue=mock_job_queue, worker_id="test-worker")
        # Test cancellation detection
```

### 3.2 PlantUML Worker (`plantuml_worker.py` - 81 statements)

**File to create**: `tests/workers/plantuml/test_plantuml_worker.py`

Follow the same pattern as DrawIO worker tests. Key differences:
- Use `PlantUmlPayload` and `PlantUmlResult`
- Mock `convert_plantuml` instead of `convert_drawio`
- Handle JAR file dependency with skipif marker

### 3.3 Notebook Worker (`notebook_worker.py` - 73 statements)

**File to create**: `tests/workers/notebook/test_notebook_worker.py`

**Additional considerations**:
- `NotebookPayload` contains more fields (output_spec, prog_lang, language)
- Notebook processing is more complex (calls `NotebookProcessor`)
- Multiple output formats possible

**Example structure**:

```python
"""Tests for Notebook worker."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

try:
    from clx.workers.notebook.notebook_worker import NotebookWorker
    from clx.infrastructure.messaging.notebook_classes import NotebookPayload, NotebookResult
    HAS_NOTEBOOK = True
except ImportError:
    HAS_NOTEBOOK = False

pytestmark = pytest.mark.skipif(not HAS_NOTEBOOK, reason="Notebook worker dependencies not installed")


@pytest.fixture
def mock_notebook_payload():
    """Create a valid notebook payload."""
    return NotebookPayload(
        input_file="/test/notebook.ipynb",
        output_file="/test/output/notebook.html",
        output_format="html",
        prog_lang="python",
        language="en",
        kind="completed",
    )


class TestNotebookWorkerProcessJob:
    """Test notebook processing."""

    @pytest.mark.asyncio
    async def test_process_job_calls_processor(self, mock_job_queue, mock_notebook_payload, tmp_path):
        """Processing should invoke NotebookProcessor."""
        # Create minimal test notebook
        input_file = tmp_path / "notebook.ipynb"
        input_file.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')

        mock_notebook_payload.input_file = str(input_file)
        mock_notebook_payload.output_file = str(tmp_path / "output.html")

        worker = NotebookWorker(job_queue=mock_job_queue, worker_id="test-worker")

        with patch("clx.workers.notebook.notebook_worker.NotebookProcessor") as MockProcessor:
            mock_processor = MagicMock()
            mock_processor.process = AsyncMock(return_value="<html>output</html>")
            MockProcessor.return_value = mock_processor

            result = await worker.process_payload(mock_notebook_payload, "correlation-id")

            MockProcessor.assert_called_once()
```

### 3.4 Lifecycle Manager (`lifecycle_manager.py` - 138 statements)

**File to create**: `tests/infrastructure/workers/test_lifecycle_manager.py`

**What to test**:
- Worker reuse decision logic (healthy workers exist vs need new)
- Health checking for existing workers
- Managed vs persistent worker startup
- Auto-stop functionality
- Configuration adjustment for worker counts
- Event logging

**Key mocking**:
- `WorkerDiscovery` - for finding existing workers
- `WorkerPoolManager` - for starting/stopping workers
- `WorkerStateManager` - for state tracking
- `WorkerEventLogger` - for event logging

**Example structure**:

```python
"""Tests for worker lifecycle manager."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from clx.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager


@pytest.fixture
def mock_discovery():
    """Mock worker discovery."""
    discovery = MagicMock()
    discovery.get_healthy_workers = MagicMock(return_value=[])
    discovery.get_workers_by_type = MagicMock(return_value=[])
    return discovery


@pytest.fixture
def mock_pool_manager():
    """Mock pool manager."""
    pool = MagicMock()
    pool.start_workers = AsyncMock()
    pool.stop_workers = AsyncMock()
    return pool


class TestWorkerReuse:
    """Test worker reuse logic."""

    def test_reuse_healthy_workers_when_available(self, mock_discovery, mock_pool_manager):
        """Should reuse existing healthy workers."""
        mock_discovery.get_healthy_workers.return_value = [
            {"worker_id": "w1", "worker_type": "notebook", "status": "idle"}
        ]

        manager = WorkerLifecycleManager(
            discovery=mock_discovery,
            pool_manager=mock_pool_manager,
            config=MagicMock(reuse_workers=True)
        )

        # Test that existing workers are reused
        workers = manager.get_or_start_workers("notebook", count=1)
        mock_pool_manager.start_workers.assert_not_called()

    def test_start_new_workers_when_none_healthy(self, mock_discovery, mock_pool_manager):
        """Should start new workers when no healthy ones exist."""
        mock_discovery.get_healthy_workers.return_value = []

        manager = WorkerLifecycleManager(
            discovery=mock_discovery,
            pool_manager=mock_pool_manager,
            config=MagicMock(reuse_workers=True)
        )

        # Should start new workers
        # ...


class TestAutoStop:
    """Test auto-stop functionality."""

    @pytest.mark.asyncio
    async def test_auto_stop_managed_workers(self, mock_discovery, mock_pool_manager):
        """Should auto-stop managed workers after completion."""
        manager = WorkerLifecycleManager(
            discovery=mock_discovery,
            pool_manager=mock_pool_manager,
            config=MagicMock(auto_stop=True)
        )

        await manager.cleanup()
        mock_pool_manager.stop_workers.assert_called()

    @pytest.mark.asyncio
    async def test_no_auto_stop_when_disabled(self, mock_discovery, mock_pool_manager):
        """Should not stop workers when auto_stop is disabled."""
        manager = WorkerLifecycleManager(
            discovery=mock_discovery,
            pool_manager=mock_pool_manager,
            config=MagicMock(auto_stop=False)
        )

        await manager.cleanup()
        mock_pool_manager.stop_workers.assert_not_called()
```

---

## Phase 4: Complex Modules ✅ COMPLETE

**Status**: Complete
**Actual coverage gain**: +1% (68% → 69%)

Note: Coverage gain was lower than expected because:
- CLI main.py already had substantial tests
- Focus was on behavior-focused tests for notebook_processor.py to support refactoring
- Some integration tests require database setup

### 4.1 Notebook Processor (`notebook_processor.py` - 204 statements)

**File to create**: `tests/workers/notebook/test_notebook_processor.py`

This is the most complex module to test. It handles:
- Cell filtering based on output specs
- Jinja2 template expansion
- Cell ID generation
- Format conversion
- Content inclusion/exclusion

**Approach**:
1. Create minimal test notebooks as fixtures
2. Mock nbconvert and jupytext for unit tests
3. Test each transformation separately

**Example structure**:

```python
"""Tests for notebook processor."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from nbformat import NotebookNode

try:
    from clx.workers.notebook.notebook_processor import NotebookProcessor
    from clx.workers.notebook.output_spec import CompletedOutput, CodeAlongOutput
    HAS_PROCESSOR = True
except ImportError:
    HAS_PROCESSOR = False

pytestmark = pytest.mark.skipif(not HAS_PROCESSOR, reason="Notebook processor dependencies not installed")


@pytest.fixture
def minimal_notebook():
    """Create a minimal test notebook."""
    return NotebookNode({
        "cells": [
            NotebookNode({
                "cell_type": "markdown",
                "source": "# Test Notebook",
                "metadata": {"tags": []},
            }),
            NotebookNode({
                "cell_type": "code",
                "source": "print('hello')",
                "metadata": {"tags": []},
                "outputs": [],
                "execution_count": None,
            }),
        ],
        "metadata": {
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    })


@pytest.fixture
def notebook_with_tags():
    """Create a notebook with various tags for testing filtering."""
    return NotebookNode({
        "cells": [
            NotebookNode({
                "cell_type": "markdown",
                "source": "# Public content",
                "metadata": {"tags": []},
            }),
            NotebookNode({
                "cell_type": "markdown",
                "source": "Speaker notes here",
                "metadata": {"tags": ["notes"]},
            }),
            NotebookNode({
                "cell_type": "code",
                "source": "# Keep this code",
                "metadata": {"tags": ["keep"]},
                "outputs": [],
                "execution_count": None,
            }),
            NotebookNode({
                "cell_type": "code",
                "source": "# Delete this",
                "metadata": {"tags": ["del"]},
                "outputs": [],
                "execution_count": None,
            }),
            NotebookNode({
                "cell_type": "code",
                "source": "# Starting code",
                "metadata": {"tags": ["start"]},
                "outputs": [],
                "execution_count": None,
            }),
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    })


class TestCellFiltering:
    """Test cell filtering based on output spec."""

    def test_completed_output_excludes_notes(self, notebook_with_tags):
        """Completed output should exclude notes cells."""
        spec = CompletedOutput()
        processor = NotebookProcessor(spec)

        result = processor.filter_cells(notebook_with_tags["cells"])

        sources = [cell["source"] for cell in result]
        assert "Speaker notes here" not in sources

    def test_completed_output_excludes_del_cells(self, notebook_with_tags):
        """Completed output should exclude del-tagged cells."""
        spec = CompletedOutput()
        processor = NotebookProcessor(spec)

        result = processor.filter_cells(notebook_with_tags["cells"])

        sources = [cell["source"] for cell in result]
        assert "# Delete this" not in sources

    def test_code_along_clears_code_cells(self, notebook_with_tags):
        """Code-along output should clear code cell contents."""
        spec = CodeAlongOutput()
        processor = NotebookProcessor(spec)

        result = processor.process_cells(notebook_with_tags["cells"])

        # Find the cell without keep/start tags
        # Its content should be cleared
        for cell in result:
            if cell["cell_type"] == "code" and "keep" not in cell["metadata"].get("tags", []):
                if "start" not in cell["metadata"].get("tags", []):
                    assert cell["source"] == "" or cell["source"].strip() == ""

    def test_code_along_keeps_keep_tagged_cells(self, notebook_with_tags):
        """Code-along should preserve keep-tagged cell contents."""
        spec = CodeAlongOutput()
        processor = NotebookProcessor(spec)

        result = processor.process_cells(notebook_with_tags["cells"])

        for cell in result:
            if "keep" in cell["metadata"].get("tags", []):
                assert "Keep this code" in cell["source"]


class TestJinjaExpansion:
    """Test Jinja2 template expansion."""

    def test_expands_jinja_variables(self):
        """Should expand Jinja2 variables in markdown cells."""
        notebook = NotebookNode({
            "cells": [
                NotebookNode({
                    "cell_type": "markdown",
                    "source": "# {{ title }}",
                    "metadata": {"tags": []},
                }),
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        })

        spec = CompletedOutput()
        processor = NotebookProcessor(spec, template_vars={"title": "My Title"})

        result = processor.process(notebook)

        assert "My Title" in result["cells"][0]["source"]


class TestFormatConversion:
    """Test format conversion."""

    def test_convert_to_html(self, minimal_notebook, tmp_path):
        """Should convert notebook to HTML."""
        spec = CompletedOutput(format="html")
        processor = NotebookProcessor(spec)

        with patch("clx.workers.notebook.notebook_processor.HTMLExporter") as MockExporter:
            mock_exporter = MagicMock()
            mock_exporter.from_notebook_node.return_value = ("<html>content</html>", {})
            MockExporter.return_value = mock_exporter

            result = processor.convert_to_format(minimal_notebook, "html")

            assert "<html>" in result

    def test_convert_to_python(self, minimal_notebook):
        """Should convert notebook to Python script."""
        spec = CompletedOutput(format="code", prog_lang="python")
        processor = NotebookProcessor(spec)

        with patch("clx.workers.notebook.notebook_processor.jupytext") as mock_jupytext:
            mock_jupytext.writes.return_value = "# Python script"

            result = processor.convert_to_format(minimal_notebook, "py")

            mock_jupytext.writes.assert_called_once()
```

### 4.2 CLI Main Module (`main.py` - 695 statements)

**File to create**: `tests/cli/test_main.py`

Use Click's `CliRunner` for testing CLI commands.

**What to test**:
- Command parsing for all commands (build, status, workers, monitor, serve, config)
- Option handling
- Output formatting
- Error handling and exit codes
- Configuration loading

**Example structure**:

```python
"""Tests for CLI main module."""

import pytest
from click.testing import CliRunner
from pathlib import Path
from unittest.mock import MagicMock, patch

from clx.cli.main import cli


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def temp_course_yaml(tmp_path):
    """Create a temporary course.yaml file."""
    course_file = tmp_path / "course.yaml"
    course_file.write_text("""
name: Test Course
version: 1.0
sections: []
""")
    return course_file


class TestBuildCommand:
    """Test the build command."""

    def test_build_help(self, runner):
        """Should show help for build command."""
        result = runner.invoke(cli, ["build", "--help"])
        assert result.exit_code == 0
        assert "Build" in result.output or "build" in result.output

    def test_build_missing_course_file(self, runner):
        """Should error when course file doesn't exist."""
        result = runner.invoke(cli, ["build", "/nonexistent/course.yaml"])
        assert result.exit_code != 0

    def test_build_with_valid_course(self, runner, temp_course_yaml):
        """Should process a valid course file."""
        with patch("clx.cli.main.Course") as MockCourse:
            mock_course = MagicMock()
            mock_course.process = MagicMock()
            MockCourse.from_yaml.return_value = mock_course

            result = runner.invoke(cli, ["build", str(temp_course_yaml)])

            # Check that Course was initialized
            MockCourse.from_yaml.assert_called_once()

    def test_build_with_output_dir(self, runner, temp_course_yaml, tmp_path):
        """Should accept --output-dir option."""
        output_dir = tmp_path / "output"

        with patch("clx.cli.main.Course") as MockCourse:
            mock_course = MagicMock()
            MockCourse.from_yaml.return_value = mock_course

            result = runner.invoke(cli, [
                "build", str(temp_course_yaml),
                "--output-dir", str(output_dir)
            ])

            # Verify output dir was passed


class TestStatusCommand:
    """Test the status command."""

    def test_status_help(self, runner):
        """Should show help for status command."""
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0

    def test_status_shows_worker_info(self, runner):
        """Should display worker information."""
        with patch("clx.cli.main.get_worker_status") as mock_status:
            mock_status.return_value = {
                "workers": {"notebook": {"total": 2, "idle": 1, "busy": 1}},
                "queue": {"pending": 5},
            }

            result = runner.invoke(cli, ["status"])

            assert result.exit_code == 0


class TestWorkersCommand:
    """Test the workers command."""

    def test_workers_list(self, runner):
        """Should list workers."""
        with patch("clx.cli.main.list_workers") as mock_list:
            mock_list.return_value = []

            result = runner.invoke(cli, ["workers", "list"])

            assert result.exit_code == 0

    def test_workers_cleanup(self, runner):
        """Should clean up dead workers."""
        with patch("clx.cli.main.cleanup_workers") as mock_cleanup:
            mock_cleanup.return_value = {"cleaned": 2}

            result = runner.invoke(cli, ["workers", "cleanup"])

            assert result.exit_code == 0


class TestConfigCommand:
    """Test the config command."""

    def test_config_show(self, runner):
        """Should show current configuration."""
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0


class TestLoggingSetup:
    """Test logging configuration."""

    def test_verbose_flag_increases_logging(self, runner, temp_course_yaml):
        """--verbose should increase log level."""
        with patch("clx.cli.main.setup_logging") as mock_setup:
            with patch("clx.cli.main.Course"):
                runner.invoke(cli, ["--verbose", "build", str(temp_course_yaml)])

                # Check that verbose was passed to logging setup

    def test_quiet_flag_decreases_logging(self, runner, temp_course_yaml):
        """--quiet should decrease log level."""
        with patch("clx.cli.main.setup_logging") as mock_setup:
            with patch("clx.cli.main.Course"):
                runner.invoke(cli, ["--quiet", "build", str(temp_course_yaml)])
```

### 4.3 Monitor Service (`monitor_service.py` - 81 statements)

**File to create**: `tests/web/services/test_monitor_service.py`

**What to test**:
- Status aggregation from database
- Worker statistics calculation
- Queue statistics calculation
- Error handling for database failures

**Example structure**:

```python
"""Tests for monitor service."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from clx.web.services.monitor_service import MonitorService
from clx.web.models import StatusResponse, WorkersListResponse


@pytest.fixture
def mock_job_queue():
    """Create a mock job queue."""
    queue = MagicMock()
    queue.get_workers = MagicMock(return_value=[])
    queue.get_jobs = MagicMock(return_value=[])
    queue.get_queue_stats = MagicMock(return_value={
        "pending": 0,
        "processing": 0,
        "completed_last_hour": 0,
        "failed_last_hour": 0,
    })
    return queue


@pytest.fixture
def monitor_service(mock_job_queue, tmp_path):
    """Create a monitor service."""
    return MonitorService(
        job_queue=mock_job_queue,
        db_path=tmp_path / "test.db",
    )


class TestGetStatus:
    """Test get_status method."""

    def test_returns_status_response(self, monitor_service):
        """Should return a StatusResponse."""
        status = monitor_service.get_status()
        assert isinstance(status, StatusResponse)

    def test_includes_worker_stats(self, monitor_service, mock_job_queue):
        """Should include worker statistics."""
        mock_job_queue.get_workers.return_value = [
            {"worker_id": "w1", "worker_type": "notebook", "status": "idle"},
            {"worker_id": "w2", "worker_type": "notebook", "status": "busy"},
        ]

        status = monitor_service.get_status()

        assert "notebook" in status.workers
        assert status.workers["notebook"].total == 2

    def test_includes_queue_stats(self, monitor_service, mock_job_queue):
        """Should include queue statistics."""
        mock_job_queue.get_queue_stats.return_value = {
            "pending": 10,
            "processing": 5,
            "completed_last_hour": 100,
            "failed_last_hour": 2,
        }

        status = monitor_service.get_status()

        assert status.queue.pending == 10
        assert status.queue.processing == 5


class TestGetWorkers:
    """Test get_workers method."""

    def test_returns_workers_list(self, monitor_service):
        """Should return WorkersListResponse."""
        workers = monitor_service.get_workers()
        assert isinstance(workers, WorkersListResponse)

    def test_includes_all_workers(self, monitor_service, mock_job_queue):
        """Should include all registered workers."""
        mock_job_queue.get_workers.return_value = [
            {"worker_id": "w1", "worker_type": "notebook", "status": "idle"},
            {"worker_id": "w2", "worker_type": "plantuml", "status": "busy"},
        ]

        workers = monitor_service.get_workers()

        assert workers.total == 2
        assert len(workers.workers) == 2
```

### 4.4 WebSocket Handler (`websocket.py` - 62 statements)

**File to create**: `tests/web/api/test_websocket.py`

Use `httpx` with `ASGITransport` or Starlette's `TestClient` with WebSocket support.

```python
"""Tests for WebSocket handler."""

import pytest
from unittest.mock import MagicMock, AsyncMock

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from clx.web.api.websocket import websocket_endpoint
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")


@pytest.fixture
def app():
    """Create a test app with WebSocket endpoint."""
    app = FastAPI()
    app.websocket("/ws")(websocket_endpoint)
    return app


class TestWebSocketConnection:
    """Test WebSocket connections."""

    def test_connect_and_receive(self, app):
        """Should accept connection and receive messages."""
        client = TestClient(app)

        with client.websocket_connect("/ws") as websocket:
            # Connection should succeed
            # Test message handling
            pass
```

---

## Phase 5: TUI and Remaining Modules (Optional)

**Estimated effort**: 2-3 days
**Expected coverage gain**: +3-5%

These modules are lower priority and more difficult to test.

### 5.1 TUI Widgets (`cli/monitor/widgets/*` - 204 statements)

Testing Textual TUI components requires the `textual` testing framework:

```python
from textual.testing import App

async def test_widget_renders():
    async with App().run_test() as pilot:
        # Test widget rendering
        pass
```

### 5.2 Pool Manager (`pool_manager.py` - 335 statements)

Complex async orchestration - focus on:
- Worker startup/shutdown
- Concurrency limits
- Error handling

### 5.3 Worker Executor (`worker_executor.py` - 249 statements)

Tests Docker and direct execution modes:
- Mock `docker` module for Docker tests
- Mock `subprocess` for direct execution tests

---

## Running Tests

```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov=src/clx --cov-report=term-missing

# Run specific test file
python -m pytest tests/workers/notebook/test_notebook_processor.py -v

# Run specific test class
python -m pytest tests/workers/notebook/test_notebook_processor.py::TestCellFiltering -v

# Run with verbose output
python -m pytest -v

# Run and stop on first failure
python -m pytest -x
```

---

## Coverage Goals

| Phase | Target Coverage | Modules |
|-------|-----------------|---------|
| After Phase 3 | 70% | Worker modules |
| After Phase 4 | 75% | Complex modules (processor, CLI) |
| After Phase 5 | 78%+ | TUI, pool manager |

---

## Tips for Success

1. **Start with mocks**: Mock external dependencies first, then test the logic
2. **Use fixtures**: Create reusable fixtures for common test data
3. **Test error paths**: Don't just test happy paths - test error handling
4. **Check coverage**: Run with `--cov-report=term-missing` to see exactly which lines aren't covered
5. **Small commits**: Commit working tests frequently
6. **Strong assertions**: Verify specific values, not just that something exists

---

*Document created: 2025-11-25*
*Author: Claude Code Assistant*
