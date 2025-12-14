# Notebook Error Context Tracking

## Status: PHASE 2 COMPLETE

**Last Updated**: 2025-12-14
**Phase 1 Commits**: `1ce3630` (Add CellContext tracking and fix error extraction patterns)
**Phase 2 Commits**: (Add TrackingExecutePreprocessor for execution-time cell tracking)

## Problem Statement

When C++ (and other) notebooks fail with compilation errors, the error output was missing critical debugging information:

**Before (problematic output):**
```
✗ [User Error]
  File: slides\module_500_solid_grasp\topic_270_adventure_factory\slides_adventure_factory.cpp
  Error: CompilationError
  Message: no template named 'vector' in namespace 'std'
  Action: Check your notebook for errors
  Job ID: #4050
```

**Missing information:**
- Cell number (which cell failed?)
- Code context (what code caused the error?)

## Investigation Findings

### Error Flow Architecture

The error handling flows through 4 stages:

1. **Notebook Processor** (`notebook_processor.py:669-791`)
   - `_enhance_notebook_error()` creates enhanced error message
   - `_find_failing_cell()` uses multiple strategies to identify failing cell
   - Extracts C++ compiler error details from xeus-cling format

2. **Worker Base** (`worker_base.py:670-726`)
   - Catches exceptions from notebook execution
   - Creates JSON error_info with error details
   - Calls ErrorCategorizer for classification

3. **Error Categorizer** (`error_categorizer.py:20-480`)
   - `_parse_notebook_error()` extracts cell_number, code_snippet, error details
   - Categorizes errors as user/configuration/infrastructure
   - Returns BuildError with details dict

4. **Output Formatter** (`output_formatter.py:240-284`)
   - Displays cell_number and code_snippet from error.details
   - Formats error for console output

### Root Causes Identified

1. **Line number extraction pattern too strict** (error_categorizer.py:273)
   - Pattern `r"line\s+(\d+)"` didn't match "Line: 2" format (with colon)
   - Enhanced errors use "Line: N, Column: M" format

2. **Code snippet extraction too greedy** (error_categorizer.py:282)
   - Regex `r"Cell content:\s*\n((?:\s+.+\n?)+)"` captured all indented lines
   - This included the "  Error:" line, polluting the code snippet

3. **No execution-time cell tracking**
   - Cell identification relied on post-hoc detection strategies
   - Could fail if error occurs before cell outputs are populated

### Existing Test Coverage (Before Changes)

| Test Area | Coverage |
|-----------|----------|
| `_parse_notebook_error()` | 10+ tests |
| `categorize_job_error()` | 6+ tests |
| C++ xeus-cling patterns | 3 tests |
| Cell number extraction | 4+ tests |
| `_find_failing_cell()` | Not directly tested |

## Work Completed (Phase 1)

### 1. Fixed Line Number Extraction

**File**: `src/clx/cli/error_categorizer.py` (lines 271-279)

```python
# Before:
line_match = re.search(r"line\s+(\d+)", full_text, re.IGNORECASE)

# After:
# Pattern 1: "Line: N" format (from enhanced errors)
line_match = re.search(r"[Ll]ine:\s*(\d+)", full_text)
if not line_match:
    # Pattern 2: "line N" format (without colon)
    line_match = re.search(r"line\s+(\d+)", full_text, re.IGNORECASE)
```

### 2. Fixed Code Snippet Extraction

**File**: `src/clx/cli/error_categorizer.py` (lines 285-291)

```python
# Before:
cell_content_match = re.search(
    r"Cell content:\s*\n((?:\s+.+\n?)+)", full_text, re.MULTILINE
)

# After (stops at "Error:" line):
cell_content_match = re.search(
    r"Cell content:\s*\n((?:\s+.+\n?)+?)(?=\s*Error:|\Z)", full_text, re.MULTILINE
)
```

### 3. Added CellContext Dataclass

**File**: `src/clx/workers/notebook/notebook_processor.py` (lines 74-85)

```python
@dataclass
class CellContext:
    """Context for the currently executing cell."""
    cell_index: int
    cell_source: str
    cell_type: str = "code"
```

### 4. Added _current_cell Tracking Attribute

**File**: `src/clx/workers/notebook/notebook_processor.py` (line 125)

```python
class NotebookProcessor:
    def __init__(self, ...):
        ...
        self._current_cell: CellContext | None = None
```

### 5. Updated _enhance_notebook_error

**File**: `src/clx/workers/notebook/notebook_processor.py` (lines 754-789)

Priority order for cell identification:
1. Use tracked `_current_cell` context (most reliable)
2. Extract cell number from error message
3. Use `_find_failing_cell()` post-hoc detection

### 6. Created Comprehensive Test Suite

**File**: `tests/workers/notebook/test_notebook_error_context.py` (new, 580+ lines)

Test classes:
- `TestFindFailingCell` - 5 tests for cell detection strategies
- `TestEnhanceNotebookError` - 4 tests for error enhancement
- `TestErrorCategorizerCellExtraction` - 4 tests for categorizer parsing
- `TestErrorPathIntegration` - 2 tests for full error path
- `TestCellContextTracking` - 5 tests for TrackingExecutePreprocessor behavior
- `TestCppErrorWithDocker` - Docker integration tests (skipped, requires manual run)

## Test Results

```
Phase 1: 71 tests pass in error context + build output files
Phase 2: 1444 tests pass in full test suite
0 regressions
```

## Files Modified

| File | Changes |
|------|---------|
| `src/clx/cli/error_categorizer.py` | Fixed line_number and code_snippet extraction patterns |
| `src/clx/workers/notebook/notebook_processor.py` | Added CellContext, _current_cell, updated _enhance_notebook_error |
| `tests/workers/notebook/test_notebook_error_context.py` | New comprehensive test file |

## Work Completed (Phase 2)

### 1. Implemented TrackingExecutePreprocessor

**File**: `src/clx/workers/notebook/notebook_processor.py` (lines 88-126)

Created a subclass of `ExecutePreprocessor` that tracks the currently executing cell:

```python
class TrackingExecutePreprocessor(ExecutePreprocessor):
    """ExecutePreprocessor that tracks the currently executing cell."""

    def __init__(self, processor: "NotebookProcessor", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.processor = processor

    def preprocess_cell(self, cell, resources, cell_index):
        # Set the current cell context before execution
        self.processor._current_cell = CellContext(
            cell_index=cell_index,
            cell_source=cell.get("source", ""),
            cell_type=cell.get("cell_type", "code"),
        )
        # Execute the cell - on success, clear context; on error, preserve it
        result = super().preprocess_cell(cell, resources, cell_index)
        # Only clear on success - preserve context for error reporting
        self.processor._current_cell = None
        return result
```

**Key design decisions**:
- Context is only cleared on **successful** execution
- On error, context is preserved so `_enhance_notebook_error()` can use it
- Context is cleared after error enhancement in `_execute_notebook_with_path()`

### 2. Updated _execute_notebook_with_path

**File**: `src/clx/workers/notebook/notebook_processor.py` (lines 597-643)

Changed to use `TrackingExecutePreprocessor` instead of `ExecutePreprocessor`:

```python
# Create FRESH TrackingExecutePreprocessor for each attempt
# TrackingExecutePreprocessor updates _current_cell for error reporting
ep = TrackingExecutePreprocessor(self, timeout=None, startup_timeout=300)
```

### 3. Added New Tests for Cell Context Tracking

**File**: `tests/workers/notebook/test_notebook_error_context.py`

New tests in `TestCellContextTracking` class:
- `test_current_cell_set_during_execution` - Verifies context is set before cell execution
- `test_current_cell_cleared_after_success` - Verifies context is cleared on success
- `test_current_cell_preserved_on_error` - Verifies context remains available on error

### 4. Updated Existing Tests

**File**: `tests/workers/notebook/test_notebook_processor.py`

Updated 4 tests that mock `ExecutePreprocessor` to mock `TrackingExecutePreprocessor` instead.

## Future Work (Phase 3)

### 1. Docker Integration Tests

**Status**: Test stubs created, need Docker execution

Tests in `TestCppErrorWithDocker` class are skipped because they require:
- Docker daemon running
- `mhoelzl/clx-notebook-processor:full` image (has xeus-cling)

To run manually:
```bash
pytest tests/workers/notebook/test_notebook_error_context.py::TestCppErrorWithDocker -v -m integration
```

### 3. Additional Error Patterns

Potential patterns to add support for:
- Julia kernel errors
- R kernel errors
- Other xeus-based kernels

## Architecture Diagram

```
NotebookProcessor._execute_notebook_with_path()
    ↓ (creates TrackingExecutePreprocessor)
TrackingExecutePreprocessor.preprocess_cell()
    ├─ Sets _current_cell with CellContext BEFORE execution
    ├─ Calls super().preprocess_cell()
    ├─ On SUCCESS: clears _current_cell
    └─ On ERROR: preserves _current_cell (for error reporting)
    ↓ (exception propagates)
NotebookProcessor._enhance_notebook_error()
    ├─ Priority 1: Use _current_cell (most reliable)
    ├─ Priority 2: Parse error message for cell info
    ├─ Priority 3: Call _find_failing_cell() as fallback
    └─ Build enhanced message with Cell: #N and Cell content:
    ↓
Worker.run() - Exception handler
    ├─ Create JSON error_info
    ├─ Call ErrorCategorizer.categorize_job_error()
    └─ Store to job queue
    ↓
SqliteBackend.wait_for_completion()
    ├─ Retrieve error from database
    └─ Call build_reporter.report_error()
    ↓
OutputFormatter.show_error()
    ├─ Display cell_number from details
    ├─ Display code_snippet with syntax highlighting
    └─ Show actionable_guidance
```

## Expected Output After Fix

```
✗ [User Error]
  File: slides/.../slides_adventure_factory.cpp
  Cell: #3
  Error: CompilationError
  Message: no template named 'vector' in namespace 'std'

  Code context:
    std::vector<int> data;
    // ... rest of cell code

  Action: Fix the error in cell #3 in your notebook
  Job ID: #4050
```

## Key Code Locations

| Component | File | Line(s) |
|-----------|------|---------|
| CellContext dataclass | `notebook_processor.py` | 74-86 |
| TrackingExecutePreprocessor | `notebook_processor.py` | 88-126 |
| _current_cell attribute | `notebook_processor.py` | 168 |
| _execute_notebook_with_path | `notebook_processor.py` | 576-643 |
| _enhance_notebook_error | `notebook_processor.py` | 731-847 |
| _find_failing_cell | `notebook_processor.py` | 849-892 |
| Line number extraction | `error_categorizer.py` | 271-279 |
| Code snippet extraction | `error_categorizer.py` | 285-291 |
| Error display | `output_formatter.py` | 240-284 |
| TDD tests | `test_notebook_error_context.py` | 1-630+ |

## References

- Original issue: C++ notebook errors not showing cell context
- Related commits: `56d88f4`, `8a7bc87`, `1011059`, `ea81ef7`
- Plan file: `C:\Users\tc\.claude\plans\tender-soaring-gem.md` (temporary)
