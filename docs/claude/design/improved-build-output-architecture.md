# Design: Improved Build Output Architecture

**Status**: Draft
**Created**: 2025-11-17
**Author**: Claude (AI Assistant)
**Related Requirements**: [improved-build-output.md](../requirements/improved-build-output.md)

## Overview

This document describes the architectural design for improving the `clx build` output experience, focusing on progress reporting, error categorization, and user-friendly console output.

**Design Principles**:
1. **Minimal Changes**: Leverage existing infrastructure (ProgressTracker, JobQueue, monitoring)
2. **Clean Separation**: Output formatting separate from business logic
3. **Extensible**: Easy to add new output formats (JSON, structured logs, etc.)
4. **Backward Compatible**: Preserve existing CLI behavior with opt-in improvements

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI Layer                               │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ main.py    │──│ BuildReporter│──│ OutputFormatter        │  │
│  │ (build cmd)│  │  (new)       │  │  (new)                 │  │
│  └────────────┘  └──────────────┘  └────────────────────────┘  │
│                         │                      │                 │
└─────────────────────────┼──────────────────────┼─────────────────┘
                          │                      │
                   ┌──────▼──────┐        ┌──────▼──────────┐
                   │ProgressTracker│      │ ErrorCategorizer│
                   │ (enhanced)    │      │ (new)           │
                   └──────┬──────┘        └──────┬──────────┘
                          │                      │
┌─────────────────────────┼──────────────────────┼─────────────────┐
│                    Backend Layer                                  │
│  ┌────────────────┴───────────────┐  ┌─────────┴──────────────┐  │
│  │ SqliteBackend                  │  │ Worker Error Reporting │  │
│  │ - execute_operation()          │  │ - NotebookWorker       │  │
│  │ - wait_for_completion()        │  │ - PlantUMLWorker       │  │
│  │ - error handling               │  │ - DrawIOWorker         │  │
│  └────────────────────────────────┘  └────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. BuildReporter (New)

**Purpose**: Coordinate progress reporting and error collection during build

**Location**: `src/clx/cli/build_reporter.py`

**Responsibilities**:
- Initialize progress tracking
- Collect errors and warnings during build
- Generate final summary
- Coordinate with OutputFormatter for display

**Interface**:
```python
@dataclass
class BuildError:
    """Represents a build error with categorization."""
    error_type: Literal['user', 'configuration', 'infrastructure']
    category: str  # e.g., 'notebook_compilation', 'missing_tool'
    severity: Literal['error', 'warning', 'fatal']
    file_path: str
    message: str
    actionable_guidance: str
    job_id: Optional[int] = None
    correlation_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class BuildWarning:
    """Represents a build warning."""
    category: str
    message: str
    severity: Literal['high', 'medium', 'low']

class BuildReporter:
    """Coordinates build progress reporting and error collection."""

    def __init__(
        self,
        output_formatter: OutputFormatter,
        progress_tracker: Optional[ProgressTracker] = None,
    ):
        self.formatter = output_formatter
        self.progress_tracker = progress_tracker
        self.errors: List[BuildError] = []
        self.warnings: List[BuildWarning] = []
        self.start_time: datetime = None

    def start_build(self, course_name: str, total_files: int) -> None:
        """Initialize build reporting."""
        self.start_time = datetime.now()
        self.formatter.show_build_start(course_name, total_files)

    def start_stage(self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int) -> None:
        """Report stage start."""
        self.formatter.show_stage_start(stage_name, stage_num, total_stages, num_jobs)

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """Update progress display."""
        self.formatter.update_progress(completed, total, active_workers)

    def report_error(self, error: BuildError) -> None:
        """Report an error (display if appropriate, always collect)."""
        self.errors.append(error)
        if self.formatter.should_show_error(error):
            self.formatter.show_error(error)

    def report_warning(self, warning: BuildWarning) -> None:
        """Report a warning (display if appropriate, always collect)."""
        self.warnings.append(warning)
        if self.formatter.should_show_warning(warning):
            self.formatter.show_warning(warning)

    def finish_build(self) -> BuildSummary:
        """Generate and display final summary."""
        duration = (datetime.now() - self.start_time).total_seconds()
        summary = BuildSummary(
            duration=duration,
            total_files=self.progress_tracker.get_summary()['total'],
            errors=self.errors,
            warnings=self.warnings,
        )
        self.formatter.show_summary(summary)
        return summary
```

**Rationale**:
- Centralizes error/warning collection
- Decouples reporting logic from build logic
- Makes testing easier (mock the reporter)

---

### 2. OutputFormatter (New)

**Purpose**: Format and display build output in various modes

**Location**: `src/clx/cli/output_formatter.py`

**Responsibilities**:
- Display progress bars (using `rich`)
- Format error and warning messages
- Generate final summaries
- Support multiple output modes (default, verbose, quiet, JSON)

**Interface**:
```python
class OutputMode(Enum):
    """Output mode for build reporting."""
    DEFAULT = "default"
    VERBOSE = "verbose"
    QUIET = "quiet"
    JSON = "json"

class OutputFormatter(ABC):
    """Abstract base for output formatting."""

    @abstractmethod
    def show_build_start(self, course_name: str, total_files: int) -> None:
        """Display build initialization."""
        pass

    @abstractmethod
    def show_stage_start(self, stage_name: str, stage_num: int, total_stages: int, num_jobs: int) -> None:
        """Display stage start."""
        pass

    @abstractmethod
    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        """Update progress display."""
        pass

    @abstractmethod
    def should_show_error(self, error: BuildError) -> bool:
        """Determine if error should be displayed immediately."""
        pass

    @abstractmethod
    def show_error(self, error: BuildError) -> None:
        """Display an error."""
        pass

    @abstractmethod
    def should_show_warning(self, warning: BuildWarning) -> bool:
        """Determine if warning should be displayed immediately."""
        pass

    @abstractmethod
    def show_warning(self, warning: BuildWarning) -> None:
        """Display a warning."""
        pass

    @abstractmethod
    def show_summary(self, summary: BuildSummary) -> None:
        """Display final build summary."""
        pass

class DefaultOutputFormatter(OutputFormatter):
    """Default human-readable output with progress bars."""

    def __init__(self, show_progress: bool = True, use_color: bool = True):
        self.show_progress = show_progress
        self.use_color = use_color
        self.progress_bar: Optional[Progress] = None  # from rich
        self.console = Console()  # from rich

    def show_build_start(self, course_name: str, total_files: int) -> None:
        self.console.print(f"[bold]Building course: {course_name}[/bold]")
        if self.show_progress:
            self.progress_bar = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=self.console,
            )
            self.progress_bar.start()

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        if self.progress_bar:
            self.progress_bar.update(self.current_task, completed=completed, total=total)

    # ... other methods

class VerboseOutputFormatter(DefaultOutputFormatter):
    """Verbose output showing all log messages."""

    def should_show_error(self, error: BuildError) -> bool:
        return True  # Always show in verbose mode

    def should_show_warning(self, warning: BuildWarning) -> bool:
        return True  # Always show in verbose mode

class QuietOutputFormatter(OutputFormatter):
    """Minimal output, only errors and summary."""

    def show_build_start(self, course_name: str, total_files: int) -> None:
        pass  # Silent

    def update_progress(self, completed: int, total: int, active_workers: int = 0) -> None:
        pass  # No progress bar

    def should_show_error(self, error: BuildError) -> bool:
        return error.severity == 'error' or error.severity == 'fatal'

    def should_show_warning(self, warning: BuildWarning) -> bool:
        return False  # No warnings in quiet mode

class JSONOutputFormatter(OutputFormatter):
    """Machine-readable JSON output."""

    def __init__(self):
        self.output_data = {
            'status': 'in_progress',
            'errors': [],
            'warnings': [],
        }

    def show_summary(self, summary: BuildSummary) -> None:
        """Output final JSON."""
        self.output_data['status'] = 'completed' if not summary.errors else 'failed'
        self.output_data['duration_seconds'] = summary.duration
        self.output_data['total_files'] = summary.total_files
        self.output_data['errors'] = [self._error_to_dict(e) for e in summary.errors]
        self.output_data['warnings'] = [self._warning_to_dict(w) for w in summary.warnings]

        print(json.dumps(self.output_data, indent=2))

    # ... other methods
```

**Rationale**:
- Separation of concerns: formatting separate from reporting logic
- Easy to add new output formats
- Testable in isolation

---

### 3. ErrorCategorizer (New)

**Purpose**: Classify errors into user/configuration/infrastructure types

**Location**: `src/clx/cli/error_categorizer.py`

**Responsibilities**:
- Analyze error messages and exceptions
- Categorize errors by type
- Generate actionable guidance
- Extract notebook-specific details (cell numbers, code snippets)

**Interface**:
```python
class ErrorCategorizer:
    """Categorizes errors and generates actionable guidance."""

    @staticmethod
    def categorize_job_error(
        job_type: str,
        input_file: str,
        error_message: str,
        job_payload: Dict,
    ) -> BuildError:
        """Categorize a job failure error.

        Args:
            job_type: Type of job (notebook, plantuml, drawio)
            input_file: Path to input file
            error_message: Error message from worker
            job_payload: Job payload dict

        Returns:
            Categorized BuildError
        """
        if job_type == 'notebook':
            return ErrorCategorizer._categorize_notebook_error(
                input_file, error_message, job_payload
            )
        elif job_type == 'plantuml':
            return ErrorCategorizer._categorize_plantuml_error(
                input_file, error_message
            )
        elif job_type == 'drawio':
            return ErrorCategorizer._categorize_drawio_error(
                input_file, error_message
            )
        else:
            # Unknown job type - infrastructure error
            return BuildError(
                error_type='infrastructure',
                category='unknown_job_type',
                severity='error',
                file_path=input_file,
                message=f"Unknown job type: {job_type}",
                actionable_guidance="This is likely a bug in CLX. Please report this issue.",
            )

    @staticmethod
    def _categorize_notebook_error(
        input_file: str,
        error_message: str,
        payload: Dict,
    ) -> BuildError:
        """Categorize notebook processing error."""

        # Parse error message to extract details
        details = ErrorCategorizer._parse_notebook_error(error_message)

        # Determine error type based on error message patterns
        if 'SyntaxError' in error_message or 'NameError' in error_message:
            error_type = 'user'
            category = 'notebook_compilation'
            guidance = f"Fix the {details.get('error_class', 'error')} in cell #{details.get('cell_number', '?')} of your notebook"

        elif 'FileNotFoundError' in error_message and 'template' in error_message.lower():
            error_type = 'configuration'
            category = 'missing_template'
            guidance = "Ensure Jinja templates are available in the template directory"

        elif 'TimeoutError' in error_message or 'worker' in error_message.lower():
            error_type = 'infrastructure'
            category = 'worker_timeout'
            guidance = "Worker timed out. Check worker logs with 'clx monitor'"

        else:
            # Default to user error for notebooks (most likely)
            error_type = 'user'
            category = 'notebook_processing'
            guidance = "Check your notebook for errors"

        return BuildError(
            error_type=error_type,
            category=category,
            severity='error',
            file_path=input_file,
            message=error_message,
            actionable_guidance=guidance,
            details=details,
        )

    @staticmethod
    def _parse_notebook_error(error_message: str) -> Dict[str, Any]:
        """Parse notebook error message to extract structured details.

        Looks for patterns like:
        - Cell number: "in cell #5" or "at cell 5"
        - Error class: "SyntaxError:", "NameError:"
        - Line number within cell: "line 3"
        - Code snippet (if included in traceback)

        Returns:
            Dictionary with parsed details
        """
        details = {}

        # Extract cell number
        import re
        cell_match = re.search(r'(?:cell|Cell)\s*#?(\d+)', error_message)
        if cell_match:
            details['cell_number'] = int(cell_match.group(1))

        # Extract error class
        error_class_match = re.search(r'(\w+Error):', error_message)
        if error_class_match:
            details['error_class'] = error_class_match.group(1)

        # Extract short message (first line after error class)
        if error_class_match:
            msg_start = error_class_match.end()
            msg_end = error_message.find('\n', msg_start)
            if msg_end > msg_start:
                details['short_message'] = error_message[msg_start:msg_end].strip()

        # Extract code snippet (lines starting with >>> or numbered lines)
        code_lines = []
        for line in error_message.split('\n'):
            if re.match(r'^\s*\d+:', line) or line.strip().startswith('>>>'):
                code_lines.append(line)

        if code_lines:
            details['code_snippet'] = '\n'.join(code_lines[:5])  # First 5 lines

        return details

    @staticmethod
    def _categorize_plantuml_error(input_file: str, error_message: str) -> BuildError:
        """Categorize PlantUML processing error."""

        if 'PLANTUML_JAR' in error_message or 'not found' in error_message.lower():
            return BuildError(
                error_type='configuration',
                category='missing_plantuml',
                severity='error',
                file_path=input_file,
                message=error_message,
                actionable_guidance=(
                    "Install PlantUML JAR and set PLANTUML_JAR environment variable. "
                    "See: https://docs.claude.com/clx/configuration"
                ),
            )
        else:
            # Assume user error in PlantUML syntax
            return BuildError(
                error_type='user',
                category='plantuml_syntax',
                severity='error',
                file_path=input_file,
                message=error_message,
                actionable_guidance="Check your PlantUML diagram syntax",
            )

    @staticmethod
    def _categorize_drawio_error(input_file: str, error_message: str) -> BuildError:
        """Categorize DrawIO processing error."""

        if 'DRAWIO_EXECUTABLE' in error_message or 'not found' in error_message.lower():
            return BuildError(
                error_type='configuration',
                category='missing_drawio',
                severity='error',
                file_path=input_file,
                message=error_message,
                actionable_guidance=(
                    "Install DrawIO desktop and set DRAWIO_EXECUTABLE environment variable. "
                    "See: https://docs.claude.com/clx/configuration"
                ),
            )
        else:
            # Assume user error in DrawIO diagram
            return BuildError(
                error_type='user',
                category='drawio_processing',
                severity='error',
                file_path=input_file,
                message=error_message,
                actionable_guidance="Check your DrawIO diagram for errors",
            )

    @staticmethod
    def categorize_no_workers_error(job_type: str) -> BuildError:
        """Create error for no workers available."""
        return BuildError(
            error_type='infrastructure',
            category='no_workers',
            severity='fatal',
            file_path='',
            message=f"No workers available for job type '{job_type}'",
            actionable_guidance=(
                f"Start {job_type} workers with 'clx start-services' or check worker health with 'clx status'"
            ),
        )
```

**Rationale**:
- Encapsulates complex error categorization logic
- Easy to extend with new error patterns
- Provides structured error information for formatting

---

### 4. Enhanced ProgressTracker

**Purpose**: Extend existing ProgressTracker with build reporting integration

**Location**: `src/clx/infrastructure/workers/progress_tracker.py` (modify existing)

**Changes**:
1. Add callback mechanism for external reporting
2. Track stage-level progress
3. Provide real-time summary for progress bar

**New Interface**:
```python
class ProgressTracker:
    """Enhanced progress tracking with build reporter integration."""

    def __init__(
        self,
        progress_interval: float = 5.0,
        long_job_threshold: float = 30.0,
        show_worker_details: bool = True,
        on_progress_update: Optional[Callable[[ProgressUpdate], None]] = None,
    ):
        # ... existing init
        self.on_progress_update = on_progress_update
        self.current_stage: Optional[str] = None
        self.stage_job_counts: Dict[str, int] = {}

    def set_stage(self, stage_name: str, expected_jobs: int) -> None:
        """Set current processing stage.

        Args:
            stage_name: Name of the stage (e.g., "Notebooks", "PlantUML Diagrams")
            expected_jobs: Expected number of jobs in this stage
        """
        self.current_stage = stage_name
        self.stage_job_counts[stage_name] = expected_jobs

    def job_completed(self, job_id: int, duration: Optional[float] = None) -> None:
        """Record job completion and trigger progress callback."""
        # ... existing logic

        # Trigger progress update callback
        if self.on_progress_update:
            summary = self.get_summary()
            self.on_progress_update(ProgressUpdate(
                completed=summary['completed'],
                total=summary['total'],
                active=summary['active'],
                failed=summary['failed'],
                stage=self.current_stage,
            ))

    # ... rest of existing methods

@dataclass
class ProgressUpdate:
    """Progress update event."""
    completed: int
    total: int
    active: int
    failed: int
    stage: Optional[str] = None
```

**Rationale**:
- Minimal changes to existing working code
- Callback pattern allows external reporting without tight coupling
- Stage tracking enables better progress visualization

---

### 5. Integration with SqliteBackend

**Purpose**: Integrate BuildReporter into the build flow

**Location**: `src/clx/infrastructure/backends/sqlite_backend.py` (modify existing)

**Changes**:
1. Accept optional BuildReporter
2. Report errors through BuildReporter instead of just logging
3. Categorize errors before reporting

**Modified Methods**:
```python
class SqliteBackend(LocalOpsBackend):
    """SQLite backend with build reporting."""

    def __init__(
        self,
        # ... existing params
        build_reporter: Optional[BuildReporter] = None,
    ):
        # ... existing init
        self.build_reporter = build_reporter

        # Initialize progress tracker with reporter callback
        if self.enable_progress_tracking:
            config = get_progress_tracker_config()

            # Add progress callback if reporter exists
            if build_reporter:
                def on_progress(update: ProgressUpdate):
                    build_reporter.update_progress(
                        completed=update.completed,
                        total=update.total,
                        active_workers=update.active,
                    )
                config['on_progress_update'] = on_progress

            self.progress_tracker = ProgressTracker(**config)

    async def wait_for_completion(self) -> bool:
        """Wait for jobs with error categorization and reporting."""
        # ... existing logic

        # When job fails
        if status == 'failed':
            # Categorize error
            from clx.cli.error_categorizer import ErrorCategorizer

            categorized_error = ErrorCategorizer.categorize_job_error(
                job_type=job_info['job_type'],
                input_file=job_info['input_file'],
                error_message=error,
                job_payload=payload_dict,
            )
            categorized_error.job_id = job_id
            categorized_error.correlation_id = job_info.get('correlation_id')

            # Report through BuildReporter if available
            if self.build_reporter:
                self.build_reporter.report_error(categorized_error)
            else:
                # Fallback to logging (for backward compatibility)
                logger.error(
                    f"Job {job_id} failed: {job_info['input_file']} -> {job_info['output_file']}\n"
                    f"Error: {error}"
                )

            # ... rest of error handling
```

**Rationale**:
- Backward compatible (BuildReporter is optional)
- Centralizes error reporting
- Categorization happens at backend level where we have full context

---

### 6. CLI Integration

**Purpose**: Wire up BuildReporter in the CLI build command

**Location**: `src/clx/cli/main.py` (modify existing `build` command)

**Changes**:
```python
async def main(
    ctx,
    # ... existing params
    output_mode: str = 'default',  # New parameter
    no_progress: bool = False,     # New parameter
):
    # ... existing initialization

    # Determine output mode
    from clx.cli.output_formatter import (
        OutputMode,
        DefaultOutputFormatter,
        VerboseOutputFormatter,
        QuietOutputFormatter,
        JSONOutputFormatter,
    )

    # Select formatter based on mode and flags
    if output_mode == 'json':
        formatter = JSONOutputFormatter()
    elif log_level == 'DEBUG' or output_mode == 'verbose':
        formatter = VerboseOutputFormatter(show_progress=not no_progress)
    elif output_mode == 'quiet':
        formatter = QuietOutputFormatter()
    else:  # default
        formatter = DefaultOutputFormatter(show_progress=not no_progress)

    # Create build reporter
    from clx.cli.build_reporter import BuildReporter

    build_reporter = BuildReporter(
        output_formatter=formatter,
        progress_tracker=None,  # Will be created by backend
    )

    # ... existing worker setup

    backend = SqliteBackend(
        db_path=jobs_db_path,
        workspace_path=output_dir,
        db_manager=db_manager,
        ignore_db=ignore_db,
        build_reporter=build_reporter,  # Pass reporter to backend
    )

    try:
        async with backend:
            # ... existing directory cleanup

            # Start build reporting
            build_reporter.start_build(
                course_name=str(course.name),
                total_files=len(course.files),
            )

            # Process course (existing logic)
            await course.process_all(backend)

            # Finish build reporting
            summary = build_reporter.finish_build()

            # Exit with appropriate code
            if summary.has_fatal_errors():
                sys.exit(2)
            elif summary.has_errors():
                sys.exit(1)
            else:
                sys.exit(0)

    finally:
        # ... existing worker cleanup

# Add CLI options for new parameters
@cli.command()
# ... existing options
@click.option(
    "--output-mode",
    type=click.Choice(['default', 'verbose', 'quiet', 'json'], case_sensitive=False),
    default='default',
    help="Output mode for build reporting",
)
@click.option(
    "--no-progress",
    is_flag=True,
    help="Disable progress bar (useful for CI/CD)",
)
@click.pass_context
def build(
    ctx,
    # ... existing params
    output_mode,
    no_progress,
):
    # ... call main() with new params
```

**Rationale**:
- Minimal changes to existing CLI structure
- Clear separation of concerns
- Easy to test each component independently

---

## Data Flow

### Typical Build Flow

```
1. User runs: clx build course.yaml

2. CLI (main.py):
   ├─ Create OutputFormatter (based on mode)
   ├─ Create BuildReporter (with formatter)
   ├─ Create SqliteBackend (with reporter)
   └─ Call course.process_all(backend)

3. Course processing:
   ├─ BuildReporter.start_build()
   │  └─ OutputFormatter.show_build_start()
   │     └─ [Display: "Building course: Intro to Python"]
   │
   ├─ For each stage:
   │  ├─ BuildReporter.start_stage()
   │  │  └─ OutputFormatter.show_stage_start()
   │  │     └─ [Display: "Processing Stage 1/3: Notebooks"]
   │  │
   │  ├─ SqliteBackend.execute_operation() (for each file)
   │  │  ├─ Submit job to queue
   │  │  └─ ProgressTracker.job_submitted()
   │  │
   │  └─ SqliteBackend.wait_for_completion()
   │     ├─ Poll job status
   │     ├─ ProgressTracker.job_completed()
   │     │  └─ Callback: BuildReporter.update_progress()
   │     │     └─ OutputFormatter.update_progress()
   │     │        └─ [Display: Progress bar update]
   │     │
   │     └─ On job failure:
   │        ├─ ErrorCategorizer.categorize_job_error()
   │        ├─ BuildReporter.report_error()
   │        └─ OutputFormatter.show_error()
   │           └─ [Display: Categorized error message]
   │
   └─ BuildReporter.finish_build()
      └─ OutputFormatter.show_summary()
         └─ [Display: Final summary with error counts]

4. Exit with appropriate code (0, 1, or 2)
```

---

## Worker Error Reporting

Workers need to provide structured error information for proper categorization.

### Current Behavior
Workers catch exceptions and store error message in job.error field:
```python
try:
    # Process job
    result = process_notebook(...)
except Exception as e:
    job_queue.fail_job(job_id, error=str(e))
```

### Enhanced Behavior
Workers should provide structured error information:

```python
# In worker (notebook_worker.py)
try:
    result = await processor.process_notebook(payload)
    # ... write result
except Exception as e:
    # Extract structured error info
    error_info = {
        'error_class': type(e).__name__,
        'error_message': str(e),
        'traceback': traceback.format_exc(),
    }

    # For notebook errors, add cell info if available
    if hasattr(e, 'cell_number'):
        error_info['cell_number'] = e.cell_number
        error_info['cell_source'] = e.cell_source[:200]  # First 200 chars

    # Store as JSON in job.error
    job_queue.fail_job(job_id, error=json.dumps(error_info))
```

**Backend changes**:
```python
# In SqliteBackend.wait_for_completion()
if status == 'failed':
    # Try to parse structured error
    try:
        error_info = json.loads(error)
    except (json.JSONDecodeError, TypeError):
        # Fallback to plain string
        error_info = {'error_message': error}

    # Pass structured info to categorizer
    categorized_error = ErrorCategorizer.categorize_job_error(
        job_type=job_info['job_type'],
        input_file=job_info['input_file'],
        error_info=error_info,  # Structured dict instead of string
        job_payload=payload_dict,
    )
```

**Benefits**:
- Better error messages with cell numbers, code snippets
- Easier to categorize errors
- Backward compatible (falls back to string if not JSON)

---

## Implementation Strategy

### Phase 1: Foundation (MVP)

**Goal**: Basic progress bar and error categorization

**Tasks**:
1. Add `rich` dependency to `pyproject.toml`
2. Implement `BuildError`, `BuildWarning`, `BuildSummary` data classes
3. Implement `DefaultOutputFormatter` with progress bar
4. Implement basic `ErrorCategorizer` (simple heuristics)
5. Create `BuildReporter` class
6. Integrate `BuildReporter` into `main.py`
7. Modify `SqliteBackend` to accept and use `BuildReporter`
8. Add `--output-mode` and `--no-progress` CLI flags
9. Write unit tests for new components

**Deliverables**:
- Working progress bar during build
- Basic error categorization (user vs. infrastructure)
- Cleaner default output
- All existing tests pass

**Testing**:
- Unit tests for ErrorCategorizer
- Unit tests for OutputFormatter
- Integration test: build with progress bar
- Integration test: build with errors, verify categorization

**Estimated Effort**: 2-3 days

---

### Phase 2: Enhanced Error Reporting

**Goal**: Detailed notebook error parsing and actionable guidance

**Tasks**:
1. Enhance `ErrorCategorizer._parse_notebook_error()` with regex patterns
2. Update workers to provide structured error info (JSON)
3. Implement error message templates
4. Add code snippet extraction from notebook cells
5. Implement `VerboseOutputFormatter` and `QuietOutputFormatter`
6. Add tests for error parsing

**Deliverables**:
- Notebook errors show cell numbers and code snippets
- Clear categorization for PlantUML/DrawIO errors
- Actionable guidance for common errors
- Verbose and quiet modes working

**Testing**:
- Unit tests for notebook error parsing
- E2E test: notebook with syntax error, verify output
- E2E test: missing PlantUML JAR, verify guidance

**Estimated Effort**: 3-4 days

---

### Phase 3: JSON Output & CI/CD Support

**Goal**: Machine-readable output for automation

**Tasks**:
1. Implement `JSONOutputFormatter`
2. Add exit code logic based on error types
3. Auto-detect CI environment (CI=true, GITHUB_ACTIONS, etc.)
4. Disable progress bar automatically in CI
5. Add `--format=json` CLI flag
6. Document JSON output schema

**Deliverables**:
- JSON output mode works
- Proper exit codes (0/1/2)
- Auto-detection of CI environments
- Documentation for JSON schema

**Testing**:
- Unit test for JSONOutputFormatter
- E2E test: build with --format=json
- Verify exit codes in various scenarios

**Estimated Effort**: 1-2 days

---

### Phase 4: Monitoring Integration & Polish

**Goal**: Seamless integration with monitoring tools

**Tasks**:
1. Add monitoring tool suggestions to output
2. Include job IDs and correlation IDs in errors
3. Configuration file support (`~/.config/clx/config.toml`)
4. Environment variable support (`CLX_BUILD_OUTPUT_MODE`)
5. TTY detection and fallbacks
6. Documentation updates (user guide, developer guide)
7. Polish error messages and formatting

**Deliverables**:
- Monitoring integration messages
- Configuration system working
- Comprehensive documentation
- Polished UX

**Testing**:
- E2E test: verify monitoring suggestions
- E2E test: configuration file precedence
- Manual testing for UX polish

**Estimated Effort**: 2-3 days

---

## Testing Strategy

### Unit Tests

**Components to test**:
1. `ErrorCategorizer`:
   - Test error pattern matching
   - Test notebook error parsing
   - Test PlantUML/DrawIO error categorization
   - Test actionable guidance generation

2. `OutputFormatter`:
   - Test progress bar formatting (mock rich objects)
   - Test error message formatting
   - Test summary generation
   - Test mode-specific behavior (verbose/quiet/json)

3. `BuildReporter`:
   - Test error collection
   - Test warning collection
   - Test summary generation
   - Test formatter integration (mock formatter)

**Location**: `tests/cli/test_error_categorizer.py`, etc.

### Integration Tests

**Scenarios to test**:
1. Build with no errors (verify progress bar and summary)
2. Build with notebook compilation errors (verify categorization)
3. Build with missing PlantUML (verify configuration error)
4. Build with worker crash (verify infrastructure error)
5. Build in verbose mode (verify all messages shown)
6. Build in quiet mode (verify minimal output)
7. Build with JSON output (verify schema)

**Location**: `tests/integration/test_build_reporting.py`

### E2E Tests

**Scenarios to test**:
1. Full course build with mixed errors
2. Build in CI environment (auto-detect, no progress bar)
3. Build with configuration file
4. Build with different output modes

**Location**: `tests/e2e/test_improved_output.py`

---

## Risks and Mitigations

### Risk 1: Progress Bar Overhead
**Impact**: High (if progress bar slows down builds significantly)
**Probability**: Low (rich library is well-optimized)
**Mitigation**:
- Benchmark build times before/after
- Update progress at reasonable intervals (1-2s)
- Provide `--no-progress` flag as escape hatch

### Risk 2: Error Categorization Accuracy
**Impact**: Medium (wrong categorization confuses users)
**Probability**: Medium (hard to predict all error patterns)
**Mitigation**:
- Start with conservative heuristics
- Default to user error for notebooks (most common)
- Allow verbose mode to see full error for manual categorization
- Iterate based on user feedback

### Risk 3: Backward Compatibility
**Impact**: High (breaking existing workflows)
**Probability**: Low (changes are additive)
**Mitigation**:
- Make BuildReporter optional in SqliteBackend
- Preserve existing `--log-level` flag behavior
- Provide `--legacy-output` flag if needed
- Test with existing test suite

### Risk 4: Rich Library Dependency
**Impact**: Low (adds new dependency)
**Probability**: Low (rich is stable and widely used)
**Mitigation**:
- Rich is already used in monitoring (indirect dependency)
- Well-maintained, stable API
- Graceful fallback if import fails (optional feature)

### Risk 5: Worker Error Reporting Changes
**Impact**: Medium (requires changes to all workers)
**Probability**: Low (changes are backward compatible)
**Mitigation**:
- Make structured errors optional (fallback to string)
- Update workers incrementally
- Test with both old and new error formats

---

## Configuration

### Configuration File

**Location**: `~/.config/clx/config.toml`

```toml
[build]
output_mode = "default"  # default, verbose, quiet, json
show_progress = true
progress_style = "fancy"
auto_detect_ci = true

[logging]
log_level = "INFO"

[errors]
print_tracebacks = false
categorize_errors = true
show_code_snippets = true
```

### Environment Variables

```bash
CLX_BUILD_OUTPUT_MODE=quiet    # Override output mode
CLX_SHOW_PROGRESS=false        # Disable progress bar
CLX_BUILD_OUTPUT_FORMAT=json   # JSON output
```

### CLI Flags (highest priority)

```bash
clx build course.yaml --output-mode=verbose
clx build course.yaml --no-progress
clx build course.yaml --format=json
```

**Precedence**: CLI flags > Environment > Config file > Defaults

---

## Documentation Updates

### User Guide Updates

**File**: `docs/user-guide/building.md`

**New sections**:
- Understanding build output
- Progress bars and status
- Error types and how to fix them
- Output modes (default, verbose, quiet, JSON)
- Configuration options

### Developer Guide Updates

**File**: `docs/developer-guide/build-output.md` (new)

**Contents**:
- Architecture overview
- Adding new error categories
- Customizing output formatters
- Worker error reporting best practices
- Testing build output

### README Updates

**File**: `README.md`

**Changes**:
- Update build command examples
- Mention improved progress reporting
- Link to detailed documentation

---

## Future Enhancements (Out of Scope for MVP)

1. **Real-time log streaming to file**
   - Auto-write detailed logs to file while showing concise output
   - Use `--log-file=path` to enable

2. **Custom progress bar themes**
   - Allow users to customize progress bar appearance
   - Simple, fancy, minimal styles

3. **Desktop notifications**
   - Notify when long build completes (>5 minutes)
   - Integration with monitoring system

4. **Detailed performance profiling**
   - Show slowest files/workers
   - Suggest optimizations

5. **Historical build comparison**
   - Compare build times across runs
   - Detect performance regressions

6. **Interactive error fixing**
   - Prompt to open file in editor
   - Show suggestions for common errors

---

## Summary

This design provides:

✅ **Clean separation of concerns**: Reporting, formatting, and categorization are separate
✅ **Backward compatible**: All changes are additive, existing behavior preserved
✅ **Extensible**: Easy to add new output formats and error categories
✅ **Testable**: Each component can be tested in isolation
✅ **User-friendly**: Progress bars, clear errors, actionable guidance
✅ **CI/CD ready**: JSON output, proper exit codes, auto-detection
✅ **Incremental**: Can be implemented in phases, each adding value

**Key architectural decisions**:
- Use `rich` library for progress bars and formatting
- Create `BuildReporter` to coordinate reporting
- Implement `ErrorCategorizer` for intelligent error classification
- Extend `ProgressTracker` with callback mechanism
- Make all new components optional for backward compatibility

**Next steps**:
1. Review this design document
2. Get stakeholder feedback
3. Create implementation issues/tasks
4. Begin Phase 1 implementation
