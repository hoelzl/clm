# Implementation Challenges and Trade-offs

**Status**: Draft
**Created**: 2025-11-17
**Author**: Claude (AI Assistant)
**Related Documents**:
- [Requirements](../requirements/improved-build-output.md)
- [Architecture Design](improved-build-output-architecture.md)

## Overview

This document identifies potential implementation challenges, trade-offs, and decision points for the improved build output system. It serves as a guide for making informed decisions during implementation.

---

## 1. Progress Bar Library Selection

### Challenge
Choosing the right library for progress bars and formatted output.

### Options

#### Option A: rich (Recommended)
**Pros**:
- Already indirect dependency (monitoring uses it)
- Excellent progress bar and table formatting
- Great documentation and active maintenance
- Supports complex layouts, colors, styles
- Built-in TTY detection
- Handles terminal resize gracefully

**Cons**:
- Relatively large library (~500KB)
- Learning curve for advanced features
- Opinionated styling (might conflict with user preferences)

**Example**:
```python
from rich.progress import Progress, BarColumn, TaskProgressColumn

with Progress() as progress:
    task = progress.add_task("Processing...", total=100)
    for i in range(100):
        progress.update(task, advance=1)
```

#### Option B: tqdm
**Pros**:
- Very popular, battle-tested
- Lightweight
- Simple API
- Works well in Jupyter notebooks too

**Cons**:
- Less flexible than rich
- Limited formatting options
- Progress bar only (no tables, panels, etc.)

**Example**:
```python
from tqdm import tqdm

for i in tqdm(range(100), desc="Processing"):
    process_item(i)
```

#### Option C: alive-progress
**Pros**:
- Beautiful animations
- Very simple API
- Lightweight

**Cons**:
- Less mature than tqdm/rich
- Limited community/support
- Might be overkill for our needs

#### Option D: Custom implementation
**Pros**:
- Full control over behavior
- No external dependency
- Minimal overhead

**Cons**:
- Significant development effort
- Reinventing the wheel
- Won't have all features (TTY detection, terminal resize, etc.)
- Maintenance burden

### Recommendation

**Use rich library**

**Rationale**:
1. Already indirect dependency through monitoring system
2. Provides all features we need (progress, tables, formatting)
3. Excellent documentation and support
4. Future-proof (can leverage for other CLI improvements)
5. TTY detection and CI/CD support built-in

**Trade-off accepted**:
- Slightly larger dependency, but worth it for features and maintainability

---

## 2. Error Categorization Accuracy

### Challenge
Accurately categorizing errors into user/configuration/infrastructure types.

### Approaches

#### Approach A: Heuristic Pattern Matching
**How it works**: Use regex patterns to match error messages

**Pros**:
- Simple to implement
- Fast
- No external dependencies

**Cons**:
- Brittle (error messages can change)
- False positives/negatives
- Requires maintenance as new error patterns emerge

**Example**:
```python
if 'SyntaxError' in error_message or 'NameError' in error_message:
    return 'user_error'
elif 'PLANTUML_JAR' in error_message:
    return 'configuration_error'
```

#### Approach B: Structured Exceptions
**How it works**: Workers raise custom exception types that carry categorization

**Pros**:
- Accurate categorization
- Type-safe
- Self-documenting

**Cons**:
- Requires changes to all workers
- More complex
- Still needs fallback for unexpected exceptions

**Example**:
```python
# In worker
raise NotebookCompilationError(
    cell_number=5,
    error_class='SyntaxError',
    message='invalid syntax',
    category=ErrorCategory.USER_ERROR
)

# In backend
except NotebookCompilationError as e:
    # Already categorized
    return BuildError(error_type=e.category, ...)
```

#### Approach C: Hybrid (Recommended)
**How it works**: Use structured errors where possible, heuristics as fallback

**Pros**:
- Best of both worlds
- Backward compatible
- Incremental improvement

**Cons**:
- More complex implementation
- Need to maintain both paths

**Example**:
```python
# Worker provides structured error if possible
error_info = {
    'error_class': 'SyntaxError',
    'cell_number': 5,
    'category': 'user_error'  # Worker suggests category
}

# Backend uses suggestion but can override with heuristics
if error_info.get('category'):
    category = error_info['category']
else:
    category = ErrorCategorizer.guess_category(error_info)
```

### Recommendation

**Use Hybrid approach (Approach C)**

**Rationale**:
1. Start with heuristics for MVP (fast to implement)
2. Incrementally add structured errors to workers
3. Always have fallback for unexpected errors
4. Allows improving accuracy over time

**Trade-off accepted**:
- More complex than pure heuristics, but more accurate
- Requires updating workers, but can be done incrementally

**Implementation plan**:
1. Phase 1: Heuristic categorization only
2. Phase 2: Add structured errors to notebook worker
3. Phase 3: Add structured errors to PlantUML/DrawIO workers
4. Ongoing: Refine heuristics based on real-world errors

---

## 3. Progress Update Frequency

### Challenge
Balancing real-time feedback with performance overhead.

### Options

#### Option A: Update on every job completion
**Pros**:
- Most responsive
- Users see immediate feedback

**Cons**:
- High overhead for fast jobs
- Can cause terminal flicker
- Console I/O can block

#### Option B: Fixed interval (e.g., 1-2 seconds)
**Pros**:
- Predictable overhead
- Smooth updates
- No flicker

**Cons**:
- May miss rapid changes
- Slight delay in feedback

#### Option C: Adaptive (based on job duration)
**Pros**:
- Efficient for both fast and slow jobs
- Best user experience

**Cons**:
- Complex to implement
- Harder to predict behavior

### Recommendation

**Use fixed interval of 1 second (Option B)**

**Rationale**:
1. Simple to implement
2. Good balance of responsiveness and overhead
3. Most CLIs use 1-2 second intervals
4. Predictable for testing

**Trade-off accepted**:
- May not show every single job completion
- Acceptable since we show count anyway

**Implementation**:
```python
# In ProgressTracker
last_update_time = time.time()
update_interval = 1.0  # seconds

def job_completed(self, job_id):
    # ... existing logic

    current_time = time.time()
    if current_time - last_update_time >= update_interval:
        # Trigger progress update callback
        if self.on_progress_update:
            self.on_progress_update(self.get_summary())
        last_update_time = current_time
```

**Configuration option**:
- Allow users to configure interval: `CLX_PROGRESS_UPDATE_INTERVAL=2.0`
- Default: 1.0 second

---

## 4. Notebook Error Cell Extraction

### Challenge
Extracting cell number and code snippet from notebook compilation errors.

### Complexity Factors

1. **Multiple execution engines**: Python, C++, C#, Java, TypeScript
2. **Different error formats**: Each has different traceback format
3. **Jinja templating**: Errors might be in template expansion, not user code
4. **Asynchronous execution**: Stack traces can be complex

### Approaches

#### Approach A: Parse nbconvert/execution error output
**How it works**: Parse the error output from nbconvert/ExecutePreprocessor

**Pros**:
- Uses existing error information
- No changes to execution engine

**Cons**:
- Error formats vary by kernel
- Hard to parse consistently
- Fragile (depends on nbconvert output format)

**Example error output**:
```
CellExecutionError: An error occurred while executing the following cell:
------------------
print(undefined_variable)
------------------

NameError: name 'undefined_variable' is not defined
```

#### Approach B: Wrap cell execution with custom handler
**How it works**: Modify NotebookProcessor to catch and annotate errors

**Pros**:
- Full control over error information
- Can add cell metadata
- Consistent across kernels

**Cons**:
- Requires changes to NotebookProcessor
- More complex
- Might miss some error types

**Example**:
```python
# In NotebookProcessor
for cell_num, cell in enumerate(notebook.cells):
    try:
        execute_cell(cell)
    except Exception as e:
        # Annotate with cell info
        e.cell_number = cell_num
        e.cell_source = cell.source
        raise
```

#### Approach C: Post-process error messages (Recommended for MVP)
**How it works**: Parse error strings with regex to extract cell info

**Pros**:
- No changes to execution engine
- Works with existing errors
- Fast to implement

**Cons**:
- Relies on error message format
- May not work for all error types
- Heuristic-based

**Example**:
```python
def extract_cell_info(error_message: str) -> dict:
    """Extract cell number and code from error message."""
    # Look for patterns like "while executing the following cell"
    match = re.search(r'while executing the following cell:\s*-+\s*(.+?)\s*-+',
                     error_message, re.DOTALL)
    if match:
        return {'cell_source': match.group(1).strip()}

    # Look for cell number in traceback
    match = re.search(r'<ipython-input-(\d+)-', error_message)
    if match:
        return {'cell_number': int(match.group(1))}

    return {}
```

### Recommendation

**Use Approach C for MVP, plan Approach B for Phase 2**

**Rationale**:
1. Approach C is fastest to implement
2. Works with existing infrastructure
3. Good enough for most common errors
4. Can improve with Approach B later

**Trade-off accepted**:
- Won't catch all error types perfectly
- May miss cell info for some errors
- Acceptable for MVP, improve later

**Enhancement plan**:
1. MVP: Parse error strings (Approach C)
2. Phase 2: Add custom error handling in NotebookProcessor (Approach B)
3. Test with different kernels and error types
4. Iterate based on real-world usage

---

## 5. Output in CI/CD Environments

### Challenge
Detecting and adapting to CI/CD environments automatically.

### Detection Methods

#### Method A: TTY detection only
**How it works**: Use `sys.stdout.isatty()` to detect if output is to terminal

**Pros**:
- Simple
- Reliable
- Standard approach

**Cons**:
- Doesn't detect all CI environments
- Some CIs provide pseudo-TTY

**Example**:
```python
import sys

if sys.stdout.isatty():
    show_progress_bar = True
else:
    show_progress_bar = False
```

#### Method B: Environment variable detection
**How it works**: Check for CI-specific env vars

**Pros**:
- Accurately detects CI environments
- Can customize behavior per CI

**Cons**:
- Needs to know all CI env vars
- Maintenance burden

**Common CI env vars**:
- `CI=true` (generic)
- `GITHUB_ACTIONS=true`
- `GITLAB_CI=true`
- `JENKINS_URL=...`
- `CIRCLECI=true`

#### Method C: Combination (Recommended)
**How it works**: Check TTY first, then check CI env vars

**Pros**:
- Most accurate
- Handles edge cases
- User can override

**Cons**:
- Slightly more complex

**Example**:
```python
def is_ci_environment() -> bool:
    """Detect if running in CI/CD environment."""
    # Check common CI environment variables
    ci_vars = ['CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'JENKINS_URL',
               'CIRCLECI', 'TRAVIS']
    return any(os.environ.get(var) for var in ci_vars)

def should_show_progress_bar() -> bool:
    """Determine if progress bar should be shown."""
    # Explicit flag takes precedence
    if os.environ.get('CLX_SHOW_PROGRESS') == 'false':
        return False
    if os.environ.get('CLX_SHOW_PROGRESS') == 'true':
        return True

    # Auto-detect: no progress in CI or non-TTY
    if is_ci_environment() or not sys.stdout.isatty():
        return False

    return True
```

### Recommendation

**Use Method C (combination)**

**Rationale**:
1. Most accurate detection
2. Handles both piped output and CI
3. User can override with env var
4. Standard practice in modern CLIs

**Trade-off accepted**:
- Need to maintain list of CI env vars
- Small added complexity

**Implementation**:
- Provide `is_ci_environment()` helper function
- Use it in OutputFormatter initialization
- Allow override with `CLX_SHOW_PROGRESS` env var
- Document behavior in user guide

---

## 6. Error Message Verbosity

### Challenge
Balancing detail vs. conciseness in error messages.

### Dilemma

**Too verbose**:
- Full stack traces
- All error details
- Hard to scan
- Overwhelming

**Too concise**:
- Missing important context
- Users can't debug
- Need to re-run with --verbose

### Approach

#### Tiered verbosity (Recommended)

**Default mode**:
- Error type and category
- File path
- Brief error message (1 line)
- Actionable guidance
- Job ID for reference

**Verbose mode** (`--verbose` or `--print-tracebacks`):
- Everything from default
- Full stack trace
- Correlation ID
- Worker details
- Job payload

**Quiet mode** (`--quiet`):
- Only: "[Error] file.py: SyntaxError"
- Full list at end

### Example

**Default**:
```
[User Error] Notebook compilation failed
  File: slides/module-2/functions/worksheet-210.py
  Error: SyntaxError: invalid syntax in cell #5

  Action: Fix the syntax error in cell #5 of your notebook
  Job ID: #42
```

**Verbose** (adds):
```
  Correlation ID: nb-210-en-participant-html
  Worker: notebook-worker-2 (process-1234)

  Full traceback:
  Traceback (most recent call last):
    File "/app/notebook_processor.py", line 145, in process_cell
      exec(cell_source)
    File "<string>", line 2
      print(x y)
            ^
  SyntaxError: invalid syntax

  Cell source:
  1: x = 5
  2: print(x y)  # Missing operator
  3: print(x + y)
```

**Quiet**:
```
[Error] worksheet-210.py: SyntaxError
```

### Recommendation

**Use tiered verbosity approach**

**Rationale**:
1. Satisfies both "quick scan" and "deep debug" use cases
2. Default is clean but informative
3. --verbose provides full context when needed
4. --quiet for automation

**Trade-off accepted**:
- Users might need to re-run with --verbose
- Acceptable since default provides enough info for most cases

**Implementation**:
- `OutputFormatter.show_error()` checks mode
- Store full error in `BuildError.details` dict
- Format based on verbosity level

---

## 7. Progress Bar in Parallel Processing

### Challenge
Updating progress bar with concurrent job execution.

### Technical Issues

1. **Thread safety**: Multiple workers completing jobs simultaneously
2. **Race conditions**: Progress tracker state updates
3. **Console output**: Rich library and logging competing for console

### Solutions

#### Issue 1: Thread Safety

**Problem**: ProgressTracker methods called from multiple threads

**Solution**: Use threading.RLock for all state updates

```python
class ProgressTracker:
    def __init__(self):
        self._lock = threading.RLock()
        # ... other init

    def job_completed(self, job_id):
        with self._lock:
            # Update state
            self._completed_jobs.add(job_id)
            # ... trigger callback
```

#### Issue 2: Progress Update Batching

**Problem**: Too many progress updates cause flickering

**Solution**: Batch updates and use time-based throttling

```python
class ProgressTracker:
    def __init__(self):
        self._last_progress_update = time.time()
        self._update_interval = 1.0

    def job_completed(self, job_id):
        with self._lock:
            self._completed_jobs.add(job_id)

            # Only trigger callback if interval elapsed
            now = time.time()
            if now - self._last_progress_update >= self._update_interval:
                if self.on_progress_update:
                    self.on_progress_update(self.get_summary())
                self._last_progress_update = now
```

#### Issue 3: Console Synchronization

**Problem**: Log messages and progress bar compete

**Solution**: Use rich's logging handler or suppress logs during progress

```python
# Option A: Use rich's logging handler
from rich.logging import RichHandler

logging.basicConfig(
    level=logging.INFO,
    handlers=[RichHandler(rich_tracebacks=True)]
)

# Option B: Suppress logs during progress
class OutputFormatter:
    def __init__(self):
        self.console = Console()
        self.progress = None

    def update_progress(self, ...):
        # Progress bar automatically handles logging
        # Rich's Console.print() is thread-safe
        with self.progress:
            self.progress.update(...)
```

### Recommendation

**Use combination of all three solutions**

**Rationale**:
1. Thread safety is mandatory
2. Batching prevents flicker and overhead
3. Rich's Console handles synchronization

**Trade-off accepted**:
- Slightly more complex
- Worth it for correctness and smooth UX

---

## 8. Configuration Precedence

### Challenge
Multiple configuration sources with clear precedence.

### Sources (in precedence order)

1. **CLI flags** (highest)
2. **Environment variables**
3. **Project config** (`.clx/config.toml` or `clx.toml`)
4. **User config** (`~/.config/clx/config.toml`)
5. **System config** (`/etc/clx/config.toml`)
6. **Defaults** (lowest)

### Implementation

```python
from dataclasses import dataclass, field
from pathlib import Path
import os
import tomli

@dataclass
class BuildOutputConfig:
    """Build output configuration with precedence."""

    output_mode: str = 'default'
    show_progress: bool = True
    progress_update_interval: float = 1.0

    @classmethod
    def load(cls, cli_overrides: dict = None) -> 'BuildOutputConfig':
        """Load configuration with precedence."""
        config = {}

        # 1. Load from files (lowest to highest priority)
        for config_file in [
            Path('/etc/clx/config.toml'),           # System
            Path.home() / '.config/clx/config.toml', # User
            Path('.clx/config.toml'),                # Project
            Path('clx.toml'),                        # Project (alternate)
        ]:
            if config_file.exists():
                with open(config_file, 'rb') as f:
                    file_config = tomli.load(f)
                    if 'build' in file_config:
                        config.update(file_config['build'])

        # 2. Override with environment variables
        if os.getenv('CLX_BUILD_OUTPUT_MODE'):
            config['output_mode'] = os.getenv('CLX_BUILD_OUTPUT_MODE')
        if os.getenv('CLX_SHOW_PROGRESS'):
            config['show_progress'] = os.getenv('CLX_SHOW_PROGRESS').lower() == 'true'
        if os.getenv('CLX_PROGRESS_UPDATE_INTERVAL'):
            config['progress_update_interval'] = float(os.getenv('CLX_PROGRESS_UPDATE_INTERVAL'))

        # 3. Override with CLI flags (highest priority)
        if cli_overrides:
            config.update(cli_overrides)

        return cls(**config)
```

### Trade-offs

**Pros**:
- Clear precedence
- Flexible configuration
- Good defaults

**Cons**:
- Can be confusing for users
- Need to document well
- Debugging "where did this value come from?"

### Recommendation

**Implement full precedence with debugging tool**

**Debugging**:
```bash
# Show where each config value comes from
$ clx config show --verbose
output_mode: 'default' (default)
show_progress: true (environment variable: CLX_SHOW_PROGRESS)
progress_update_interval: 2.0 (user config: ~/.config/clx/config.toml)
```

---

## 9. Testing Strategy

### Challenge
Testing UI-heavy features (progress bars, formatted output).

### Approaches

#### Unit Testing

**Mock rich objects**:
```python
from unittest.mock import Mock, patch

def test_progress_bar():
    formatter = DefaultOutputFormatter()

    # Mock rich.progress.Progress
    with patch('clx.cli.output_formatter.Progress') as mock_progress:
        formatter.show_build_start("Test Course", 100)

        # Verify progress bar was created
        mock_progress.assert_called_once()
```

**Test output content** (without formatting):
```python
def test_error_message_content():
    """Test error message contains key information."""
    error = BuildError(
        error_type='user',
        category='notebook_compilation',
        file_path='test.py',
        message='SyntaxError: invalid syntax',
        actionable_guidance='Fix syntax error',
    )

    # Use quiet formatter (plain text)
    formatter = QuietOutputFormatter()

    # Capture output
    import io
    import sys

    captured = io.StringIO()
    sys.stdout = captured

    formatter.show_error(error)

    output = captured.getvalue()
    sys.stdout = sys.__stdout__

    # Verify content
    assert 'test.py' in output
    assert 'SyntaxError' in output
```

#### Integration Testing

**Test build with actual output**:
```python
@pytest.mark.integration
def test_build_with_progress_bar(tmp_path):
    """Test build shows progress bar."""
    # Create test course
    course_dir = create_test_course(tmp_path)

    # Run build and capture output
    result = subprocess.run(
        ['clx', 'build', str(course_dir / 'course.yaml')],
        capture_output=True,
        text=True,
    )

    # Verify progress indication in output
    assert 'Processing' in result.stdout
    assert 'completed' in result.stdout.lower()
```

#### E2E Testing

**Test with real errors**:
```python
@pytest.mark.e2e
def test_notebook_syntax_error_reporting(tmp_path):
    """Test notebook syntax error is categorized correctly."""
    # Create notebook with syntax error
    notebook = create_notebook_with_syntax_error(tmp_path)

    # Run build
    result = subprocess.run(
        ['clx', 'build', '--output-mode=verbose', str(tmp_path / 'course.yaml')],
        capture_output=True,
        text=True,
    )

    # Verify error categorization
    assert '[User Error]' in result.stdout
    assert 'SyntaxError' in result.stdout
    assert 'cell #' in result.stdout
    assert result.returncode == 1  # Errors but not fatal
```

### Recommendation

**Use all three levels**

**Rationale**:
1. Unit tests for logic (fast, isolated)
2. Integration tests for output format (realistic)
3. E2E tests for full user experience (comprehensive)

**Test coverage goals**:
- Unit tests: >90% coverage for new components
- Integration tests: All output modes (default, verbose, quiet, JSON)
- E2E tests: Common error scenarios

---

## 10. Performance Considerations

### Potential Performance Impacts

#### 1. Progress Bar Rendering

**Impact**: Console I/O can be slow, especially over SSH

**Mitigation**:
- Update at most once per second
- Use Rich's efficient rendering
- Disable in non-TTY environments
- Provide `--no-progress` flag

**Benchmark target**: <1% overhead

#### 2. Error Categorization

**Impact**: Regex parsing and analysis for each error

**Mitigation**:
- Only categorize on error (not hot path)
- Cache compiled regex patterns
- Keep heuristics simple

**Benchmark target**: <10ms per error

#### 3. JSON Serialization

**Impact**: Converting BuildError objects to JSON

**Mitigation**:
- Only serialize in JSON output mode
- Use dataclass / Pydantic for efficient serialization
- Lazy serialization (only at end of build)

**Benchmark target**: <100ms for full build summary

### Performance Testing

```python
@pytest.mark.performance
def test_progress_overhead():
    """Verify progress tracking overhead is minimal."""
    import time

    # Baseline: process without progress
    start = time.time()
    backend = SqliteBackend(enable_progress_tracking=False)
    # ... process jobs
    baseline_duration = time.time() - start

    # With progress tracking
    start = time.time()
    backend = SqliteBackend(enable_progress_tracking=True)
    # ... process same jobs
    with_progress_duration = time.time() - start

    # Verify overhead is <5%
    overhead = (with_progress_duration - baseline_duration) / baseline_duration
    assert overhead < 0.05, f"Progress overhead too high: {overhead*100:.1f}%"
```

---

## Summary of Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Progress library | **rich** | Already dependency, feature-rich, well-maintained |
| Error categorization | **Hybrid** (heuristics + structured) | Incremental improvement, backward compatible |
| Progress update frequency | **1 second fixed interval** | Balance of responsiveness and overhead |
| Notebook cell extraction | **Post-process for MVP** | Fast to implement, improve later |
| CI detection | **TTY + env vars** | Most accurate, handles edge cases |
| Error verbosity | **Tiered** (default/verbose/quiet) | Satisfies all use cases |
| Thread safety | **RLock + batching** | Correctness + performance |
| Configuration | **Full precedence chain** | Flexible, standard practice |
| Testing | **All levels** (unit/integration/e2e) | Comprehensive coverage |
| Performance target | **<5% overhead** | Acceptable for better UX |

---

## Implementation Priorities

### Must Have (MVP)

1. ✅ Progress bar with percentage and counts
2. ✅ Basic error categorization (user vs. infrastructure)
3. ✅ Concise default output
4. ✅ Verbose mode with full logs
5. ✅ Thread-safe progress updates

### Should Have (Phase 2)

6. ✅ Notebook cell number extraction
7. ✅ Code snippet preview in errors
8. ✅ Actionable guidance for common errors
9. ✅ Quiet mode for CI/CD
10. ✅ Configuration file support

### Could Have (Phase 3)

11. ✅ JSON output mode
12. ✅ CI environment auto-detection
13. ✅ Monitoring tool integration
14. ✅ Structured errors from workers

### Won't Have (Out of Scope)

15. ❌ Real-time log file streaming
16. ❌ Desktop notifications
17. ❌ Custom progress bar themes
18. ❌ Historical build comparison

---

## Risk Mitigation Summary

| Risk | Mitigation |
|------|------------|
| Progress bar overhead | 1-second updates, benchmarking, `--no-progress` flag |
| Error categorization accuracy | Start conservative, iterate based on feedback, verbose mode shows full info |
| Backward compatibility | Optional BuildReporter, preserve existing flags, comprehensive tests |
| Rich dependency | Already indirect dependency, graceful fallback, well-maintained |
| Worker changes | Structured errors optional, backward compatible, incremental rollout |
| Performance regression | Benchmark suite, performance tests, profiling |
| Complex configuration | Clear documentation, `clx config show` debugging, good defaults |

---

## Open Questions for Discussion

1. **Should we show warnings in default mode?**
   - Proposal: Show high-priority warnings only, count others
   - Need: Categorize warnings by priority

2. **How to handle partial build failures?**
   - Current: Continue processing
   - Option: Add `--fail-fast` flag to stop on first error
   - Recommendation: Keep current behavior, add flag for users who want it

3. **Should progress bar show ETA?**
   - Pros: Helpful for long builds
   - Cons: Can be inaccurate if job durations vary
   - Recommendation: Show ETA, make configurable

4. **Log file auto-generation?**
   - Proposal: `--log-file=auto` creates timestamped log
   - Alternative: User explicitly specifies path or uses shell redirect
   - Recommendation: Explicit path only for MVP

5. **Color in error messages?**
   - Pros: Improves readability
   - Cons: Might not work in all terminals
   - Recommendation: Use color if TTY supports it, provide `--no-color` flag

---

## Next Steps

1. **Review this document** with stakeholders
2. **Finalize open questions** before implementation
3. **Create implementation issues** for each phase
4. **Set up benchmarking** before starting (establish baseline)
5. **Begin Phase 1 implementation** (MVP features)

---

## References

- [Requirements Document](../requirements/improved-build-output.md)
- [Architecture Design](improved-build-output-architecture.md)
- [Rich Library Documentation](https://rich.readthedocs.io/)
- [Click Documentation](https://click.palletsprojects.com/)
- [Best Practices for CLI Design](https://clig.dev/)
