# Test Coverage Improvement Plan

**Starting Coverage**: 53% (3219 statements missing of 6898 total)
**Current Coverage**: 69% (2170 statements missing of 7071 total)
**Target Coverage**: 75%+
**Date**: 2025-11-25
**Last Updated**: 2025-11-26

## Current Status

| Phase | Status | Coverage After |
|-------|--------|---------------|
| Phase 1: Quick Wins | ✅ Complete | 61% |
| Phase 2: Infrastructure | ✅ Complete | 63% |
| Phase 3: Worker Modules | ✅ Complete | 68% |
| Phase 4: Complex Modules | ✅ Complete | 69% |
| Phase 5: Remaining Modules | 🔲 Not Started | Target: 75%+ |

## Executive Summary

The CLX codebase has made significant progress on test coverage. Initial gaps have been largely addressed:
1. **Worker modules** - Now 62-100% coverage (was 0%)
2. **CLI main module** - Already had substantial tests
3. **Web API modules** - Now 65-100% coverage (was 0%)
4. **Infrastructure workers** - Now 68-100% coverage (was 10-53%)

**Remaining gap**: 6 percentage points to reach 75% target.

---

## Coverage Analysis Summary

### Modules with 0% Coverage (High Priority)

| Module | Statements | Description |
|--------|------------|-------------|
| `workers/notebook/notebook_processor.py` | 204 | Core notebook processing engine |
| `workers/notebook/output_spec.py` | 122 | Output specification definitions |
| `workers/notebook/utils/jupyter_utils.py` | 85 | Jupyter notebook utilities |
| `workers/notebook/notebook_worker.py` | 73 | Notebook worker implementation |
| `workers/drawio/drawio_worker.py` | 81 | Draw.io worker |
| `workers/plantuml/plantuml_worker.py` | 81 | PlantUML worker |
| `workers/notebook/utils/prog_lang_utils.py` | 42 | Programming language configs |
| `workers/plantuml/plantuml_converter.py` | 40 | PlantUML conversion |
| `workers/drawio/drawio_converter.py` | 26 | Draw.io conversion |
| `web/api/routes.py` | 47 | REST API routes |
| `web/api/websocket.py` | 62 | WebSocket handling |
| `web/services/monitor_service.py` | 81 | Monitoring service |
| `infrastructure/services/subprocess_tools.py` | 41 | Subprocess utilities |
| `infrastructure/logging/loguru_setup.py` | 22 | Logging setup |
| `cli/monitor/app.py` | 68 | Monitor TUI app |
| `cli/monitor/widgets/*` | 204 | TUI widgets |
| `core/operations/delete_file.py` | 18 | File deletion operation |

### Modules with Very Low Coverage (<35%)

| Module | Coverage | Missing | Description |
|--------|----------|---------|-------------|
| `infrastructure/workers/config_loader.py` | 10% | 44 | Worker config loading |
| `infrastructure/workers/lifecycle_manager.py` | 16% | 116 | Worker lifecycle |
| `cli/git_dir_mover.py` | 27% | 37 | Git directory utilities |
| `infrastructure/workers/discovery.py` | 29% | 61 | Worker discovery |
| `cli/main.py` | 34% | 459 | Main CLI entry point |
| `cli/text_utils.py` | 35% | 33 | Text utilities |
| `cli/monitor/data_provider.py` | 35% | 37 | Monitor data provider |

---

## Phased Implementation Plan

### Phase 1: Quick Wins (Easy, High Impact)

**Estimated effort**: 1-2 days
**Expected coverage gain**: +5-7%

These modules have no external dependencies and are pure logic/data:

#### 1.1 `prog_lang_utils.py` (42 statements, VERY EASY)

**What to test**:
- Language configuration lookup for all 6 languages (Python, C++, C#, Java, TypeScript, Rust)
- `suffix_for()` - file extensions
- `jinja_prefix_for()` - Jinja2 comment prefixes
- `jupytext_format_for()` - format strings
- `language_info()` - kernel metadata
- `kernelspec_for()` - kernel specs
- Error handling for unsupported languages

**Test file**: `tests/workers/notebook/utils/test_prog_lang_utils.py`

```python
# Example test structure
class TestProgLangConfig:
    def test_suffix_for_python(self):
        assert suffix_for("python") == ".py"

    def test_suffix_for_all_languages(self):
        expected = {"python": ".py", "cpp": ".cpp", "csharp": ".cs",
                    "java": ".java", "typescript": ".ts", "rust": ".rs"}
        for lang, suffix in expected.items():
            assert suffix_for(lang) == suffix

    def test_jinja_prefix_for_python(self):
        assert jinja_prefix_for("python") == "# j2"

    def test_unsupported_language_raises(self):
        with pytest.raises(KeyError):
            suffix_for("unknown")
```

#### 1.2 `output_spec.py` (122 statements, EASY)

**What to test**:
- `OutputSpec` base class properties
- `CompletedOutput` - cell inclusion/exclusion logic
- `CodeAlongOutput` - code-along format rules
- `SpeakerOutput` - speaker notes inclusion
- `EditScriptOutput` - edit script format
- `create_output_spec()` factory function
- `create_output_specs()` for all combinations
- Tag-based filtering (del, notes, start, answer, alt, private, keep)
- Language-based filtering
- File suffix/format generation

**Test file**: `tests/workers/notebook/test_output_spec.py`

```python
# Example test structure
class TestCompletedOutput:
    def test_file_suffix_ipynb(self):
        spec = CompletedOutput(lang="python", format="ipynb")
        assert spec.file_suffix == ".ipynb"

    def test_deleted_cell_excluded(self):
        spec = CompletedOutput(lang="python", format="ipynb")
        cell = {"metadata": {"tags": ["del"]}}
        assert not spec.is_cell_included(cell)

    def test_notes_cell_excluded(self):
        spec = CompletedOutput(lang="python", format="ipynb")
        cell = {"metadata": {"tags": ["notes"]}}
        assert not spec.is_cell_included(cell)

class TestCodeAlongOutput:
    def test_code_cells_emptied(self):
        spec = CodeAlongOutput(lang="python", format="ipynb")
        cell = {"cell_type": "code", "metadata": {"tags": []}}
        assert not spec.is_cell_contents_included(cell)

    def test_keep_tagged_cells_preserved(self):
        spec = CodeAlongOutput(lang="python", format="ipynb")
        cell = {"cell_type": "code", "metadata": {"tags": ["keep"]}}
        assert spec.is_cell_contents_included(cell)
```

#### 1.3 `jupyter_utils.py` (85 statements, EASY)

**What to test**:
- Cell type detection (`is_code_cell`, `is_markdown_cell`, `get_cell_type`)
- Tag operations (`get_tags`, `set_tags`, `has_tag`)
- Language filtering (`get_cell_language`, `is_cell_included_for_language`)
- Cell classification (`is_deleted_cell`, `is_private_cell`, `is_public_cell`, etc.)
- Slide tag extraction
- Title extraction via regex (`find_notebook_titles`)
- File name sanitization (`sanitize_file_name`)
- Invalid tag warnings

**Test file**: `tests/workers/notebook/utils/test_jupyter_utils.py`

```python
# Example test structure
class TestCellTypeDetection:
    def test_code_cell_detected(self):
        cell = {"cell_type": "code"}
        assert is_code_cell(cell)
        assert not is_markdown_cell(cell)

    def test_markdown_cell_detected(self):
        cell = {"cell_type": "markdown"}
        assert is_markdown_cell(cell)
        assert not is_code_cell(cell)

class TestTagOperations:
    def test_get_tags_from_metadata(self):
        cell = {"metadata": {"tags": ["tag1", "tag2"]}}
        assert get_tags(cell) == ["tag1", "tag2"]

    def test_get_tags_missing_metadata(self):
        cell = {}
        assert get_tags(cell) == []

    def test_has_tag(self):
        cell = {"metadata": {"tags": ["del", "notes"]}}
        assert has_tag(cell, "del")
        assert not has_tag(cell, "keep")

class TestTitleExtraction:
    def test_find_single_title(self):
        content = '# j2 title="My Notebook"'
        titles = find_notebook_titles(content)
        assert titles == ["My Notebook"]
```

#### 1.4 `drawio_converter.py` (26 statements, EASY)

**What to test**:
- Command construction for different formats (png, svg, pdf)
- Format-specific options (scale for png, embed for svg)
- Environment variable handling for executable path
- Error handling

**Test file**: `tests/workers/drawio/test_drawio_converter.py`

```python
# Example test structure
@pytest.mark.asyncio
class TestDrawioConverter:
    async def test_convert_to_png(self, mocker):
        mock_subprocess = mocker.patch(
            "clx.workers.drawio.drawio_converter.run_subprocess",
            return_value=(b"", b"")
        )
        await convert_drawio("/input.drawio", "/output.png", "png", "corr-id")

        call_args = mock_subprocess.call_args
        assert "--export" in call_args[0][0]
        assert "--format" in call_args[0][0]
        assert "png" in call_args[0][0]

    async def test_png_includes_scale_option(self, mocker):
        mock_subprocess = mocker.patch(...)
        await convert_drawio("/input.drawio", "/output.png", "png", "corr-id")
        assert "--scale" in mock_subprocess.call_args[0][0]
```

#### 1.5 `plantuml_converter.py` (40 statements, EASY)

**What to test**:
- Output name extraction from `@startuml` directive
- Regex patterns (quoted, unquoted, multiline)
- JAR path detection (env var, defaults)
- Command construction
- Java options

**Test file**: `tests/workers/plantuml/test_plantuml_converter.py`

```python
# Example test structure
class TestOutputNameExtraction:
    def test_extract_quoted_name(self):
        content = '@startuml "my-diagram"\n...'
        assert get_plantuml_output_name(content) == "my-diagram"

    def test_extract_unquoted_name(self):
        content = "@startuml diagram_name\n..."
        assert get_plantuml_output_name(content) == "diagram_name"

    def test_no_name_returns_none(self):
        content = "@startuml\n..."
        assert get_plantuml_output_name(content) is None

@pytest.mark.asyncio
class TestPlantUmlConversion:
    async def test_convert_builds_correct_command(self, mocker):
        mock_subprocess = mocker.patch(...)
        await convert_plantuml("/input.pu", "/output", "png", "corr-id")
        # Verify java command with JAR and options
```

#### 1.6 `config_loader.py` (49 statements, EASY)

**What to test**:
- Base config loading
- CLI overrides (workers, counts, auto-start/stop, reuse)
- Config-style key overrides
- Override precedence
- Per-worker-type overrides
- Logging of applied overrides

**Test file**: `tests/infrastructure/workers/test_config_loader.py`

```python
# Example test structure
class TestConfigLoader:
    def test_load_base_config(self, mocker):
        mock_config = mocker.patch("clx.infrastructure.workers.config_loader.get_config")
        mock_config.return_value.workers = WorkersManagementConfig()

        config = load_worker_config()
        assert config is not None

    def test_cli_override_worker_count(self, mocker):
        # Test that CLI args override base config
        config = load_worker_config(notebook_workers=5)
        assert config.notebook_workers == 5
```

---

### Phase 2: Infrastructure Modules (Medium Difficulty)

**Estimated effort**: 2-3 days
**Expected coverage gain**: +4-6%

#### 2.1 `subprocess_tools.py` (41 statements, MEDIUM)

**What to test**:
- Successful subprocess execution
- Timeout handling with retry (exponential backoff)
- Non-retriable errors (FileNotFoundError, PermissionError)
- Process termination (SIGTERM, SIGKILL)
- Retry count exhaustion

**Mocking**: Mock `asyncio.create_subprocess_exec`

**Test file**: `tests/infrastructure/services/test_subprocess_tools.py`

#### 2.2 `discovery.py` (86 statements, MEDIUM)

**What to test**:
- Worker discovery from database
- Filtering by type and status
- Health check logic (status, heartbeat, process running)
- Heartbeat timeout detection (30 seconds)
- Healthy worker counting
- Summary statistics

**Mocking**: Mock JobQueue/database, timestamps

**Test file**: `tests/infrastructure/workers/test_discovery.py`

#### 2.3 `delete_file.py` (18 statements, EASY)

**What to test**:
- File deletion operation
- Error handling for missing files
- Directory handling

**Test file**: `tests/core/operations/test_delete_file.py`

#### 2.4 `routes.py` (47 statements, EASY)

**What to test**:
- Health check endpoint
- Version endpoint
- Status endpoint
- Workers list endpoint
- Jobs list endpoint with pagination

**Approach**: Use FastAPI TestClient

**Test file**: `tests/web/api/test_routes.py`

```python
from fastapi.testclient import TestClient

class TestRoutes:
    def test_health_check(self, client: TestClient):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert "version" in response.json()
```

---

### Phase 3: Worker Modules (Medium-Hard)

**Estimated effort**: 3-4 days
**Expected coverage gain**: +8-10%

#### 3.1 `drawio_worker.py` & `plantuml_worker.py` (162 statements, MEDIUM)

**What to test**:
- Job registration with database
- Job cancellation detection
- Input file reading
- Output format detection
- Converter invocation
- Output file writing
- Result caching
- Error handling

**Mocking**: Mock job queue, worker base, file I/O, converters

**Test files**:
- `tests/workers/drawio/test_drawio_worker.py`
- `tests/workers/plantuml/test_plantuml_worker.py`

#### 3.2 `notebook_worker.py` (73 statements, MEDIUM)

**What to test**:
- Similar pattern to drawio/plantuml workers
- NotebookPayload construction
- NotebookProcessor invocation
- Multiple output spec handling

**Test file**: `tests/workers/notebook/test_notebook_worker.py`

#### 3.3 `lifecycle_manager.py` (138 statements, MEDIUM-HARD)

**What to test**:
- Worker reuse decision logic
- Health checking for existing workers
- Managed vs persistent worker startup
- Auto-stop logic
- Config adjustment for worker count
- Event logging

**Mocking**: WorkerDiscovery, WorkerPoolManager, WorkerStateManager, WorkerEventLogger

**Test file**: `tests/infrastructure/workers/test_lifecycle_manager.py`

---

### Phase 4: Complex Modules (Hard)

**Estimated effort**: 4-5 days
**Expected coverage gain**: +10-15%

#### 4.1 `notebook_processor.py` (204 statements, HARD)

**What to test**:
- Cell filtering logic (code/markdown/unknown)
- Cell ID generation (uniqueness, collision detection)
- Jinja2 template expansion
- Markdown formatting (notes, answers)
- Content inclusion/exclusion based on output spec
- Format conversion (ipynb → py, ipynb → html)
- Error handling

**Approach**:
- Create minimal test notebooks
- Mock nbconvert and jupytext for unit tests
- Use real notebooks for integration tests

**Test file**: `tests/workers/notebook/test_notebook_processor.py`

#### 4.2 `main.py` CLI (695 statements, HARD)

**What to test**:
- CLI argument parsing for all commands
- Logging setup
- Course initialization
- Worker configuration
- Build process
- Watch mode
- Output formatting
- Error handling and exit codes

**Approach**: Use Click's CliRunner for testing

**Test file**: `tests/cli/test_main.py`

```python
from click.testing import CliRunner
from clx.cli.main import cli

class TestBuildCommand:
    def test_build_with_valid_course(self, runner: CliRunner, tmp_path):
        result = runner.invoke(cli, ["build", str(tmp_path / "course.yaml")])
        assert result.exit_code == 0
```

#### 4.3 Web/Monitor Modules (MEDIUM-HARD)

**Modules**:
- `monitor_service.py` (81 statements)
- `websocket.py` (62 statements)
- `cli/monitor/app.py` (68 statements)
- `cli/monitor/widgets/*` (204 statements)

**Approach**:
- Mock database for monitor_service
- Use WebSocket testing utilities for websocket.py
- Textual testing for TUI components (optional, low priority)

---

## Test Infrastructure Requirements

### New Test Fixtures Needed

```python
# tests/workers/conftest.py

@pytest.fixture
def mock_cell():
    """Create a mock notebook cell."""
    def _make_cell(cell_type="code", tags=None, source=""):
        return {
            "cell_type": cell_type,
            "source": source,
            "metadata": {"tags": tags or []}
        }
    return _make_cell

@pytest.fixture
def mock_notebook():
    """Create a mock notebook."""
    def _make_notebook(cells=None):
        return {
            "cells": cells or [],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5
        }
    return _make_notebook

@pytest.fixture
def mock_job():
    """Create a mock job object."""
    def _make_job(job_type="notebook", payload=None):
        return Job(
            id=str(uuid.uuid4()),
            job_type=job_type,
            payload=payload or {},
            status="pending"
        )
    return _make_job
```

### Directory Structure for New Tests

```
tests/
├── workers/
│   ├── __init__.py
│   ├── conftest.py                    # Worker test fixtures
│   ├── notebook/
│   │   ├── __init__.py
│   │   ├── test_notebook_processor.py
│   │   ├── test_notebook_worker.py
│   │   ├── test_output_spec.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── test_jupyter_utils.py
│   │       └── test_prog_lang_utils.py
│   ├── drawio/
│   │   ├── __init__.py
│   │   ├── test_drawio_converter.py
│   │   └── test_drawio_worker.py
│   └── plantuml/
│       ├── __init__.py
│       ├── test_plantuml_converter.py
│       └── test_plantuml_worker.py
├── web/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── test_routes.py
│   │   └── test_websocket.py
│   └── services/
│       └── test_monitor_service.py
└── infrastructure/
    ├── services/
    │   └── test_subprocess_tools.py
    └── workers/
        ├── test_config_loader.py
        ├── test_discovery.py
        └── test_lifecycle_manager.py
```

---

## Priority Matrix

| Priority | Module | Statements | Difficulty | Impact |
|----------|--------|------------|------------|--------|
| 1 | prog_lang_utils.py | 42 | Very Easy | Medium |
| 2 | output_spec.py | 122 | Easy | High |
| 3 | jupyter_utils.py | 85 | Easy | High |
| 4 | drawio_converter.py | 26 | Easy | Medium |
| 5 | plantuml_converter.py | 40 | Easy | Medium |
| 6 | config_loader.py | 49 | Easy | Medium |
| 7 | delete_file.py | 18 | Easy | Low |
| 8 | subprocess_tools.py | 41 | Medium | Medium |
| 9 | discovery.py | 86 | Medium | Medium |
| 10 | routes.py | 47 | Easy | Low |
| 11 | drawio_worker.py | 81 | Medium | Medium |
| 12 | plantuml_worker.py | 81 | Medium | Medium |
| 13 | notebook_worker.py | 73 | Medium | High |
| 14 | lifecycle_manager.py | 138 | Hard | Medium |
| 15 | notebook_processor.py | 204 | Hard | High |
| 16 | main.py (CLI) | 695 | Hard | High |
| 17 | monitor_service.py | 81 | Medium | Low |
| 18 | websocket.py | 62 | Medium | Low |
| 19 | TUI widgets | 204 | Hard | Low |

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Overall Coverage | 53% | 75%+ |
| Workers Package | 0% | 70%+ |
| Infrastructure Workers | ~40% | 70%+ |
| CLI Package | ~50% | 65%+ |
| Web Package | 0% | 60%+ |
| Core Package | ~85% | 90%+ |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| External tool dependencies (PlantUML, DrawIO) | Mock subprocess calls, use marker-based skipping |
| Complex async code | Use pytest-asyncio, proper fixtures |
| Notebook execution in tests | Use minimal test notebooks, mock ExecutePreprocessor |
| TUI testing complexity | Lower priority, focus on data providers first |
| Large CLI module | Break into smaller test files by command |

---

## Next Steps

1. **Immediate**: Create test directory structure for workers
2. **Week 1**: Implement Phase 1 tests (quick wins)
3. **Week 2**: Implement Phase 2 tests (infrastructure)
4. **Week 3**: Implement Phase 3 tests (workers)
5. **Week 4**: Implement Phase 4 tests (complex modules)
6. **Ongoing**: Add tests for new features, maintain coverage

---

*Document created: 2025-11-25*
*Author: Claude Code Assistant*
