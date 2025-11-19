# Improvements for Claude Code Web Testing

**Date**: 2025-11-18
**Issue**: Integration and E2E tests were difficult to run reliably in Claude Code Web due to external tool dependencies (PlantUML, DrawIO) and Git LFS limitations.

## Summary of Changes

This update makes the CLX test suite work reliably in constrained environments like Claude Code Web by:

1. **Moving PlantUML JAR out of Git LFS** (highest impact)
2. **Adding smart auto-skip functionality** for tests requiring unavailable tools
3. **Creating diagnostic tools** to help users understand their environment
4. **Improving setup script** with retry logic and better error messages
5. **Updating documentation** with clear guidance for Claude Code Web

## Detailed Changes

### 1. PlantUML JAR No Longer in Git LFS

**Problem**: PlantUML JAR (22MB) was stored in Git LFS, which Claude Code Web cannot access.

**Solution**: Removed `*.jar` from `.gitattributes` and committed the actual PlantUML JAR directly to the repository.

**Impact**: PlantUML tests now work immediately in Claude Code Web without any downloads.

**Files Changed**:
- `.gitattributes` - Removed `*.jar` filter from LFS
- `services/plantuml-converter/plantuml-1.2024.6.jar` - Now actual JAR file (22MB) instead of LFS pointer

**Rationale**: 22MB is acceptable for a git repository. This immediately solves the Claude Code Web problem for the most commonly used converter.

### 2. Smart Auto-Skip Test Functionality

**Problem**: Tests failed when external tools were missing instead of skipping gracefully.

**Solution**: Added tool availability detection and automatic test skipping with clear messages.

**Implementation**:

#### New Pytest Markers (`tests/conftest.py`)
- `@pytest.mark.requires_plantuml` - Auto-skips if PlantUML/Java unavailable
- `@pytest.mark.requires_drawio` - Auto-skips if DrawIO unavailable
- `@pytest.mark.requires_xvfb` - Auto-skips if Xvfb not running

#### Tool Detection Functions
```python
def _is_plantuml_available() -> bool:
    """Check if PlantUML JAR exists and Java is available."""
    # Also detects if file is Git LFS pointer

def _is_drawio_available() -> bool:
    """Check if DrawIO executable exists and is functional."""

def _is_xvfb_running() -> bool:
    """Check if Xvfb display server is running."""

def get_tool_availability() -> dict:
    """Get cached tool availability status."""
```

#### Auto-Skip Hook (`pytest_collection_modifyitems`)
- Detects available tools at test session start
- Prints availability report before test collection
- Automatically marks tests for skipping when tools are missing
- Clear skip messages explaining what's needed

**Example Output**:
```
======================================================================
External Tool Availability:
  PlantUML: ✓ Available
  DrawIO:   ✗ Not available
  Xvfb:     ✗ Not running
======================================================================

...

SKIPPED [3] tests/test_drawio.py:42: DrawIO not available - set DRAWIO_EXECUTABLE or install DrawIO
```

**Impact**: Tests automatically adapt to available tools. No manual marker selection needed.

### 3. Diagnostic Script

**New File**: `.claude/diagnose-test-env.sh`

**Purpose**: Help users quickly understand their environment and what tests they can run.

**Features**:
- Checks Python environment and package installations
- Tests external tool availability (Java, PlantUML, DrawIO, Xvfb)
- Reports which test categories will work
- Provides recommended test commands
- Gives setup instructions for missing tools

**Usage**:
```bash
./.claude/diagnose-test-env.sh
```

**Example Output**:
```
Test Category          Status       Requirements
──────────────────────────────────────────────────────────────────
✓ Unit Tests           Available    None
✓ PlantUML Tests       Available    Java, PlantUML JAR
⊘ DrawIO Tests         Skipped      DrawIO, Xvfb, DISPLAY
✓ Integration Tests    Available    At least one converter
⊘ E2E Tests (Partial)  Available    Some tools

Recommended Test Commands:
  pytest                    # Run fast unit tests (always available)
  pytest -m integration     # Run integration tests with available converters
  pytest -m e2e            # Run end-to-end tests (some may be skipped)
```

### 4. Improved Setup Script

**File**: `.claude/setup-test-env.sh`

**Improvements**:

#### Better Git LFS Detection
- Checks file size, not just grep for git-lfs string
- PlantUML: Must be > 1MB to be considered valid
- DrawIO: Must be > 10MB to be considered valid

#### Retry Logic with Exponential Backoff
```bash
# PlantUML: 3 attempts with 2s, 4s backoff
for attempt in 1 2 3; do
    # Try download
    if success; then break; fi
    # Wait before retry
    sleep $((2 ** attempt))
done

# DrawIO: 2 attempts with 3s backoff (larger file, more likely to timeout)
```

#### Clearer Messaging
- "OPTIONAL" labels for non-essential tools
- Clear distinction between errors and warnings
- Explains that DrawIO tests will be skipped if unavailable
- Final summary shows what commands to run

#### Better Final Report
```bash
Test Commands:
──────────────────────────────────────────────────────────────────
  pytest                    # Unit tests (always available)
  pytest -m integration     # Integration tests (with available converters)
  pytest -m e2e            # End-to-end tests (some may be skipped)

Notes:
  • Tests automatically skip when required tools are unavailable
  • PlantUML tests should work (JAR is in repository)
  • DrawIO tests will be skipped if DrawIO is not installed
```

### 5. Documentation Updates

**File**: `CLAUDE.md`

**New Section**: "Testing in Claude Code Web and Constrained Environments"

**Content**:
- TL;DR for quick understanding
- What works out of the box (PlantUML ✓, Auto-skip ✓, DrawIO ⚠️)
- Tool availability detection explanation
- Diagnostic tool usage
- Test markers documentation
- Typical Claude Code Web workflow

**Key Messages**:
- PlantUML tests work immediately (JAR in repo)
- DrawIO tests automatically skip if unavailable
- Use `./.claude/diagnose-test-env.sh` to check environment
- Tests adapt automatically - just run `pytest`

## Testing Strategy

### What Works in Claude Code Web

✅ **Unit Tests** - Always work (no external dependencies)

✅ **PlantUML Integration Tests** - Work after running setup script (JAR in repo, Java usually available)

✅ **PlantUML E2E Tests** - Work after running setup script

⚠️ **DrawIO Integration Tests** - Skipped if DrawIO download times out (this is OK)

⚠️ **DrawIO E2E Tests** - Skipped if DrawIO download times out (this is OK)

### Expected Workflow in Claude Code Web

```bash
# 1. Run setup (PlantUML will install, DrawIO may timeout - that's normal)
./.claude/setup-test-env.sh

# 2. Check what's available (optional but helpful)
./.claude/diagnose-test-env.sh

# 3. Run tests - DrawIO tests will auto-skip
pytest                # Unit tests (fast)
pytest -m integration # PlantUML integration tests
pytest -m e2e        # PlantUML E2E tests

# Expected: PlantUML tests pass, DrawIO tests skipped with clear messages
```

## Migration Guide for Test Writers

### Adding Tool Requirements to Tests

#### Before (old way)
```python
@pytest.mark.integration
def test_plantuml_conversion():
    # Test would fail if PlantUML not available
    ...
```

#### After (new way)
```python
@pytest.mark.integration
@pytest.mark.requires_plantuml  # Auto-skips if PlantUML unavailable
def test_plantuml_conversion():
    # Test automatically skipped with clear message if PlantUML missing
    ...

@pytest.mark.integration
@pytest.mark.requires_drawio  # Auto-skips if DrawIO unavailable
@pytest.mark.requires_xvfb    # Auto-skips if Xvfb not running
def test_drawio_conversion():
    # Test automatically skipped with clear message if DrawIO or Xvfb missing
    ...
```

### Available Markers

- `@pytest.mark.requires_plantuml` - Requires PlantUML JAR and Java
- `@pytest.mark.requires_drawio` - Requires DrawIO executable
- `@pytest.mark.requires_xvfb` - Requires Xvfb display server

**Note**: These markers are automatically registered in `conftest.py` and don't need to be added to `pyproject.toml`.

## Benefits

### For Claude Code Web Users

✅ **Immediate PlantUML testing** - No downloads needed, JAR in repository

✅ **Graceful degradation** - Tests skip automatically when tools unavailable

✅ **Clear feedback** - Diagnostic script shows exactly what will work

✅ **Less frustration** - No more mysterious failures from missing tools

### For Local Developers

✅ **Faster setup** - PlantUML works immediately from repository

✅ **Better diagnostics** - Clear understanding of environment status

✅ **Flexible testing** - Run partial test suites based on available tools

✅ **Consistent experience** - Same auto-skip behavior across all environments

### For Maintainers

✅ **Less support burden** - Clear error messages reduce confusion

✅ **Better test organization** - Granular markers for tool requirements

✅ **Easier debugging** - Diagnostic script helps troubleshoot issues

✅ **More reliable CI** - Tests adapt to available infrastructure

## Technical Details

### Git LFS Pointer Detection

The scripts detect Git LFS pointers by checking file size:

```bash
FILE_SIZE=$(stat -c%s "$PLANTUML_JAR")
if [ $FILE_SIZE -gt 1000000 ]; then
    # Real file (22MB)
else
    # Git LFS pointer (133 bytes)
fi
```

### Tool Availability Caching

Tool detection is cached at module level to avoid repeated checks:

```python
_PLANTUML_AVAILABLE = None  # Cached result

def get_tool_availability():
    global _PLANTUML_AVAILABLE
    if _PLANTUML_AVAILABLE is None:
        _PLANTUML_AVAILABLE = _is_plantuml_available()
    return {'plantuml': _PLANTUML_AVAILABLE, ...}
```

### Skip Marker Application

Markers are applied during test collection:

```python
def pytest_collection_modifyitems(config, items):
    tool_status = get_tool_availability()

    skip_plantuml = pytest.mark.skip(reason="PlantUML not available...")

    for item in items:
        if "requires_plantuml" in [m.name for m in item.iter_markers()]:
            if not tool_status['plantuml']:
                item.add_marker(skip_plantuml)
```

## Future Improvements

### Potential Enhancements

1. **Add test markers automatically** - Analyze test code to infer tool requirements
2. **Parallel downloads** - Download PlantUML and DrawIO concurrently
3. **Alternative DrawIO sources** - Try multiple download mirrors
4. **Pre-built test environments** - Docker images with all tools pre-installed
5. **Test result reporting** - Track which tests run in which environments

### Known Limitations

- DrawIO (98MB) still in Git LFS - may timeout in Claude Code Web
- Requires Java to be pre-installed for PlantUML
- Xvfb must be started manually for DrawIO tests
- No automatic retry for Xvfb startup failures

## Files Modified

### Core Changes
- `.gitattributes` - Removed `*.jar` from Git LFS
- `services/plantuml-converter/plantuml-1.2024.6.jar` - Now actual 22MB file
- `tests/conftest.py` - Added tool detection and auto-skip logic
- `CLAUDE.md` - Added Claude Code Web testing section

### New Files
- `.claude/diagnose-test-env.sh` - Environment diagnostic script

### Enhanced Files
- `.claude/setup-test-env.sh` - Improved with retry logic and better messages

## Testing the Changes

To verify the improvements work:

```bash
# 1. Clone repository
git clone https://github.com/hoelzl/clx.git
cd clx

# 2. Verify PlantUML JAR is real file (not LFS pointer)
ls -lh services/plantuml-converter/plantuml-1.2024.6.jar
# Should show ~22MB, not 133 bytes

# 3. Run setup
./.claude/setup-test-env.sh

# 4. Run diagnostics
./.claude/diagnose-test-env.sh

# 5. Run tests
pytest                  # Should work
pytest -m integration   # PlantUML tests should work
pytest -m e2e          # PlantUML tests should work

# 6. Verify auto-skip messages
pytest -v | grep "SKIPPED.*DrawIO"
# Should show clear skip messages for DrawIO tests
```

## Conclusion

These changes make the CLX test suite significantly more usable in Claude Code Web and other constrained environments. The key insight is that **graceful degradation is better than hard failures** - tests should adapt to available infrastructure rather than failing mysteriously.

The PlantUML JAR being committed directly (no longer in Git LFS) is the highest-impact change, making the most commonly used converter tests work immediately without any downloads. Combined with auto-skip functionality and clear diagnostic tools, users can now confidently run tests in Claude Code Web knowing they'll get meaningful results.
