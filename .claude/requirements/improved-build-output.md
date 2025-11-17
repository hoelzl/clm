# Requirements: Improved Build Output and Progress Reporting

**Status**: Draft
**Created**: 2025-11-17
**Author**: Claude (AI Assistant)
**Related Issue**: Improve clx build output visibility

## Executive Summary

The `clx build` command currently produces verbose log output that makes it difficult for users to:
- Understand the current state of the build process
- Distinguish between different types of warnings and errors
- Identify whether problems are user-fixable or infrastructure-related
- Monitor progress of long-running builds

This document defines comprehensive requirements for improving the build output experience while leveraging the existing monitoring infrastructure.

---

## Current State Analysis

### What Works Well

1. **Comprehensive logging**: All events are logged with appropriate levels
2. **Monitoring infrastructure exists**:
   - `clx monitor` - Real-time TUI with Textual
   - `clx serve` - Web dashboard with REST API
   - `clx status` - CLI status command
3. **Progress tracking foundation**: `ProgressTracker` class provides job lifecycle tracking
4. **Job queue visibility**: SQLite database provides complete job status history
5. **Correlation IDs**: Jobs can be traced through the system

### Current Problems

1. **Information Overload**
   - All log messages (DEBUG, INFO, WARNING, ERROR) stream to console
   - Mix of infrastructure logs, worker logs, and domain logic logs
   - Hard to focus on actionable information

2. **No Visual Progress Indication**
   - No progress bars or percentage indicators
   - Users have no sense of "how much is left?"
   - Long-running builds appear frozen

3. **Unclear Error Attribution**
   - Notebook compilation errors (user code problems)
   - Worker crashes (infrastructure problems)
   - Configuration errors (user setup problems)
   - All reported similarly, difficult to distinguish

4. **Poor Signal-to-Noise Ratio**
   - Important warnings get lost in verbose output
   - No summary of issues at the end
   - Duplicate topic IDs warning (user-fixable) looks the same as worker crash (not user-fixable)

5. **Limited Context in Errors**
   - When a notebook fails compilation, stack trace from worker is shown
   - User can't easily tell if it's their code or a bug in the worker
   - No clear indication of which file/line caused the problem in their notebook

### User Personas and Needs

#### 1. Course Developer (Primary User)
**Context**: Running `clx build course.yaml` to convert course materials
**Needs**:
- Know build is progressing (not frozen)
- See clear errors for problems in their notebooks/diagrams
- Distinguish their mistakes from tool bugs
- Get actionable error messages
- Quick summary at end

#### 2. CI/CD Pipeline
**Context**: Automated builds in GitHub Actions/GitLab CI
**Needs**:
- Machine-readable output option (JSON)
- Exit codes that reflect build status
- Logs that can be parsed for artifacts
- Minimal output by default

#### 3. Debugging/Development (Secondary)
**Context**: Developer troubleshooting CLX itself
**Needs**:
- Access to verbose logs when needed
- Correlation IDs for tracing
- Worker status and job queue visibility
- Integration with monitoring tools

---

## Requirements

### R1: Visual Progress Indication

**Priority**: High
**Rationale**: Users need to know the build is progressing and estimate completion time

#### R1.1: Progress Bar Display
- **MUST** show a progress bar during build execution
- **MUST** display percentage complete (e.g., "45% complete")
- **MUST** show job counts (e.g., "120/267 jobs completed")
- **SHOULD** show estimated time remaining (based on average job duration)
- **SHOULD** update at reasonable frequency (every 1-2 seconds)
- **SHOULD** work in both interactive terminals and CI/CD environments

#### R1.2: Phase Indication
- **MUST** indicate current processing phase
  - Example: "Processing stage 1/3: Notebooks"
  - Example: "Processing stage 2/3: PlantUML diagrams"
- **SHOULD** show per-phase progress within overall progress

#### R1.3: Live Status
- **SHOULD** show active worker count
- **SHOULD** show queue depth (pending jobs)
- **COULD** show currently processing files (sample, not all)

**Success Criteria**:
- User can glance at terminal and immediately know: "Build is 60% done, 3 minutes remaining"
- Progress bar updates smoothly without overwhelming the terminal

---

### R2: Intelligent Error Reporting

**Priority**: High
**Rationale**: Users need to quickly identify and fix problems

#### R2.1: Error Classification
**MUST** categorize errors into clear types:

1. **User Errors** (fixable by course developer)
   - Notebook compilation errors (syntax errors, runtime errors in code cells)
   - Invalid notebook metadata
   - Missing template files
   - Duplicate topic IDs
   - Invalid Draw.io/PlantUML source files

2. **Infrastructure Errors** (not user-fixable)
   - Worker crashes or timeouts
   - No workers available
   - Database connection errors
   - File system errors (permissions, disk full)

3. **Configuration Errors** (fixable by user setup)
   - Missing external tools (PlantUML JAR, DrawIO executable)
   - Invalid course.yaml structure
   - Missing required directories

#### R2.2: Error Message Format
Each error **MUST** include:
- **Clear classification**: "User Error", "Configuration Error", or "Infrastructure Error"
- **Affected file**: Full path to the source file
- **Error description**: Plain language explanation
- **Actionable guidance**: What the user should do
  - User Error: "Fix the syntax error in cell 5 of your notebook"
  - Config Error: "Install PlantUML JAR and set PLANTUML_JAR environment variable"
  - Infrastructure Error: "Contact support or file a GitHub issue"

#### R2.3: Notebook Error Reporting (Special Case)
For notebook compilation errors, **MUST** provide:
- Notebook file path
- Cell number where error occurred
- Code snippet from the failing cell (first 3 lines)
- Error message from the Python/C++/etc. compiler
- Indication if it's likely user code vs. Jinja template problem

Example:
```
[User Error] Notebook compilation failed
  File: slides/module-1/topic-intro/worksheet-110.py
  Cell: #5 (code cell)
  Error: NameError: name 'undefined_variable' is not defined

  Code preview:
    1: result = process_data(input_data)
    2: print(undefined_variable)  # <-- Error here
    3: show_result(result)

  Action: Fix the undefined variable in cell #5 of your notebook
```

#### R2.4: Warning vs Error
- **Warnings** (build continues):
  - Duplicate topic IDs
  - Deprecated template usage
  - Performance issues (slow workers)

- **Errors** (job fails but build continues):
  - Individual notebook/diagram compilation failures
  - File not found for specific input

- **Fatal Errors** (build stops):
  - No workers available
  - Invalid course.yaml
  - Database initialization failure

#### R2.5: Error Summary
At end of build, **MUST** provide:
- Count of errors by category
- List of failed files with brief error
- Suggestion to check detailed logs for more info

Example:
```
Build completed with errors:
  3 User Errors (notebooks with compilation errors)
  1 Configuration Error (PlantUML JAR not found)
  0 Infrastructure Errors

Failed files:
  [User Error] slides/module-1/intro/worksheet-110.py: NameError in cell #5
  [User Error] slides/module-2/classes/worksheet-210.py: SyntaxError in cell #2
  [User Error] slides/module-3/functions/worksheet-305.py: ImportError in cell #1
  [Config Error] diagrams/architecture.puml: PlantUML JAR not configured

Run 'clx build --verbose' for detailed error messages
```

**Success Criteria**:
- User can immediately tell if they need to fix their code or if it's a tool problem
- Errors provide enough context to fix the problem without digging through logs
- Summary makes it easy to prioritize fixes

---

### R3: Concise Console Output

**Priority**: High
**Rationale**: Default output should not overwhelm users with information

#### R3.1: Default Output Mode
By default, `clx build` **MUST**:
- Show progress bar with phase indication
- Show only warnings and errors (suppress DEBUG/INFO)
- Show worker startup/shutdown messages (brief)
- Show final summary

By default, `clx build` **MUST NOT**:
- Log every job submission
- Log every job completion
- Show worker heartbeats
- Show cache hits/misses
- Show correlation IDs

#### R3.2: Verbose Output Mode
`clx build --verbose` **MUST**:
- Show all log levels (DEBUG, INFO, WARNING, ERROR)
- Show job lifecycle events
- Show cache operations
- Show worker details
- Show correlation IDs

#### R3.3: Quiet Output Mode
`clx build --quiet` **MUST**:
- Suppress progress bar
- Show only errors (no warnings)
- Show only final summary
- Suitable for CI/CD pipelines

#### R3.4: Structured Output Mode
`clx build --format=json` **MUST**:
- Output machine-readable JSON
- Include all warnings and errors
- Include summary statistics
- Suppress human-readable output
- Exit with appropriate code (0 = success, 1 = errors, 2 = fatal)

**Success Criteria**:
- Default output fits in ~20-30 lines for typical build
- User can scan output in 2-3 seconds to understand status
- Verbose mode provides debugging info when needed
- JSON mode enables automation

---

### R4: Integration with Monitoring System

**Priority**: Medium
**Rationale**: Leverage existing monitoring infrastructure for deep inspection

#### R4.1: Cross-Reference to Monitoring
When build is running, **SHOULD** display:
- Message indicating monitoring is available
- Command to launch monitor: `clx monitor --jobs-db-path=<path>`
- URL if web dashboard is running: `http://localhost:8000`

Example:
```
Building course...  [=====>    ] 45% (120/267 jobs)

For detailed monitoring:
  Terminal UI: clx monitor --jobs-db-path=clx_jobs.db
  Web UI:      clx serve --jobs-db-path=clx_jobs.db
```

#### R4.2: Error Cross-Reference
When errors occur, **SHOULD** mention:
- Job ID for tracking in monitoring system
- Correlation ID for end-to-end tracing
- Suggestion to use monitoring for details

Example:
```
[User Error] Notebook compilation failed (Job #42, CID: nb-110-en-participant-html)
  Run 'clx monitor' to see detailed job history
```

**Success Criteria**:
- Users discover monitoring tools naturally
- Error messages provide IDs for tracking in monitoring system

---

### R5: Configurable Output Behavior

**Priority**: Medium
**Rationale**: Different users have different preferences and constraints

#### R5.1: Configuration Options
**MUST** support configuration via:
- CLI flags (highest priority): `--verbose`, `--quiet`, `--format=json`
- Environment variables: `CLX_BUILD_OUTPUT_MODE=quiet`
- Config file: `~/.config/clx/config.toml`

#### R5.2: Progress Bar Customization
**SHOULD** allow configuration:
- Progress update interval (default: 2 seconds)
- Show/hide ETA (default: show)
- Show/hide active workers (default: hide)
- Progress bar style (simple, fancy, minimal)

#### R5.3: TTY Detection
**MUST** automatically detect:
- Interactive terminal vs. pipe/redirect
- If not TTY, use simpler output (no progress bar animations)
- Preserve colored output if TTY supports it

**Success Criteria**:
- Configuration system is intuitive
- Defaults work well for most users
- Advanced users can customize as needed

---

### R6: Backward Compatibility

**Priority**: High
**Rationale**: Existing users and scripts should continue to work

#### R6.1: Log Level Compatibility
**MUST** preserve existing `--log-level` flag:
- `--log-level=DEBUG`: Equivalent to `--verbose`
- `--log-level=INFO`: New default behavior
- `--log-level=WARNING`: Quieter than default
- `--log-level=ERROR`: Similar to `--quiet`

#### R6.2: Existing Flags
**MUST** preserve:
- `--print-tracebacks`: Include full stack traces in errors
- `--print-correlation-ids`: Show correlation IDs in output

#### R6.3: Deprecation Path
If changing behavior significantly:
- **MUST** provide deprecation warnings
- **SHOULD** maintain old behavior with flag (e.g., `--legacy-output`)
- **SHOULD** document migration in CHANGELOG

**Success Criteria**:
- Existing scripts don't break
- Users can opt into old behavior if needed

---

## Non-Functional Requirements

### NFR1: Performance
- Progress bar updates **MUST NOT** add more than 5% overhead
- Console output **MUST** handle high-frequency updates (100+ jobs/sec)
- Log formatting **MUST NOT** block job processing

### NFR2: Accessibility
- Progress bar **SHOULD** work with screen readers
- Colored output **SHOULD** have fallback for monochrome terminals
- Error messages **SHOULD** be clear without color

### NFR3: Internationalization (Future)
- Error messages **SHOULD** be structured for i18n
- Use message IDs for errors (e.g., `ERR_NOTEBOOK_COMPILATION`)
- Keep English for MVP, prepare for localization

---

## Success Metrics

### Quantitative
- Default build output is ≤30 lines for typical course (100-300 notebooks)
- Users can identify error type in <5 seconds
- 90% of errors provide actionable guidance
- Progress bar overhead is <5% of total build time

### Qualitative
- User feedback: "Much easier to understand what's happening"
- User feedback: "I can immediately see if it's my mistake or a bug"
- Developer feedback: "Monitoring integration is seamless"

---

## Out of Scope (for MVP)

### Explicitly NOT in Scope
1. **Real-time log streaming to file**: Use `--verbose 2>&1 | tee build.log`
2. **Email notifications on completion**: CI/CD handles this
3. **Desktop notifications**: Use monitoring system
4. **Custom progress bar themes**: Keep it simple for MVP
5. **Detailed performance profiling**: Use monitoring system
6. **Historical build comparison**: Future feature

---

## Dependencies

### Internal Dependencies
- `ProgressTracker` class (exists, needs enhancement)
- `SqliteBackend` job tracking (exists)
- `JobQueue` database (exists)
- CLI infrastructure (exists)

### External Dependencies
- `rich` library (for progress bars and formatted output) - **NEW**
- `click` library (already used for CLI)
- Textual, FastAPI (already used for monitoring)

### Potential Libraries to Evaluate
- **rich**: Excellent for progress bars, tables, and formatted console output
- **alive-progress**: Alternative progress bar library
- **tqdm**: Simple progress bars, widely used
- **yaspin**: Spinners for indeterminate progress

**Recommendation**: Use `rich` library
- Integrates well with existing monitoring (already uses `rich.table`)
- Powerful formatting capabilities
- Active maintenance
- Great documentation

---

## Open Questions

### Q1: How verbose should notebook error reporting be?
- **Option A**: Show full stack trace (current behavior)
- **Option B**: Show summary with option to see full trace
- **Option C**: Show only the final error and cell number
- **Recommendation**: Option B - Summary by default, `--print-tracebacks` for full

### Q2: Should we log to file automatically?
- **Option A**: Auto-log everything to `clx_build.log`
- **Option B**: Require explicit `--log-file=path`
- **Option C**: Never auto-log (user can redirect)
- **Recommendation**: Option B - Explicit opt-in, less surprise

### Q3: How to handle warnings that might be important?
- **Option A**: Always show warnings in default mode
- **Option B**: Count warnings, show at end
- **Option C**: Categorize warnings (high/medium/low priority)
- **Recommendation**: Option C - Show high-priority warnings, summarize others

### Q4: Should progress bar be disabled in CI/CD automatically?
- **Option A**: Auto-detect CI environment variables (CI=true, etc.)
- **Option B**: Require explicit `--no-progress`
- **Option C**: Auto-detect based on TTY only
- **Recommendation**: Option A - Smart defaults for CI

### Q5: How to handle partial build failures?
- **Current**: Build continues after individual job failures
- **Option A**: Stop on first error (like make)
- **Option B**: Continue but collect errors (current behavior)
- **Option C**: Add `--fail-fast` flag for either behavior
- **Recommendation**: Option C - Default continues, `--fail-fast` to stop

---

## Implementation Phases

### Phase 1: Foundation (MVP)
**Goal**: Basic progress bar and improved error categorization

**Deliverables**:
1. Add `rich` dependency
2. Implement progress bar in `SqliteBackend`
3. Categorize errors in error handling
4. Update CLI output modes (verbose/quiet)
5. Basic error summary at end

**Estimated Effort**: 2-3 days

### Phase 2: Enhanced Error Reporting
**Goal**: Detailed error messages with context

**Deliverables**:
1. Notebook error parser (extract cell numbers, code snippets)
2. Error message templates
3. Actionable guidance system
4. Integration with worker error reporting

**Estimated Effort**: 3-4 days

### Phase 3: Monitoring Integration
**Goal**: Seamless integration with existing monitoring

**Deliverables**:
1. Cross-reference to monitoring in output
2. Job ID and correlation ID in errors
3. Auto-detection of monitoring tools
4. Documentation updates

**Estimated Effort**: 1-2 days

### Phase 4: Configuration & Polish
**Goal**: Configurable output with good defaults

**Deliverables**:
1. Configuration file support
2. Environment variable support
3. TTY detection and fallbacks
4. JSON output mode for CI/CD
5. Comprehensive testing

**Estimated Effort**: 2-3 days

**Total Estimated Effort**: 8-12 days (1.5-2.5 weeks)

---

## Related Work

### Similar Tools
- **webpack**: Excellent progress bars and error categorization
- **cargo (Rust)**: Clean output with warnings/errors clearly marked
- **pytest**: Good at distinguishing test failures from infrastructure issues
- **npm/yarn**: Progress indicators and concise output by default

### Lessons Learned
- Users appreciate progress bars that show ETA
- Error categorization is critical (user vs. tool)
- Default should be clean, verbose available when needed
- CI/CD mode should be machine-readable

---

## Appendix A: Example Output Scenarios

### Scenario 1: Successful Build (Default Mode)

```
Initializing CLX build...
✓ Started 3 notebook workers, 1 PlantUML worker, 1 DrawIO worker

Building course: Introduction to Python
Processing Stage 1/3: Notebooks
  [========================================] 100% (245/245 jobs) - 2m 15s

Processing Stage 2/3: PlantUML Diagrams
  [========================================] 100% (12/12 jobs) - 8s

Processing Stage 3/3: DrawIO Diagrams
  [========================================] 100% (5/5 jobs) - 15s

✓ Build completed successfully in 2m 38s

Summary:
  262 files processed
  0 errors
  2 warnings

Warnings:
  [Low Priority] Duplicate topic ID 'intro' (using first occurrence)
  [Low Priority] Worker notebook-worker-2 took 45s (slower than average)

Output directory: /path/to/output
```

### Scenario 2: Build with User Errors (Default Mode)

```
Initializing CLX build...
✓ Started 3 notebook workers, 1 PlantUML worker, 1 DrawIO worker

Building course: Introduction to Python
Processing Stage 1/3: Notebooks
  [=====================>                  ] 52% (127/245 jobs) - ~2m remaining

✗ [User Error] Notebook compilation failed
  File: slides/module-2/functions/worksheet-210.py
  Cell: #5 (code cell)
  Error: SyntaxError: invalid syntax

  Action: Fix the syntax error in cell #5 of your notebook
  Job ID: #42 | Run 'clx monitor' for details

Processing Stage 1/3: Notebooks (continued)
  [========================================] 100% (245/245 jobs) - 2m 20s

Processing Stage 2/3: PlantUML Diagrams
  [========================================] 100% (12/12 jobs) - 8s

Processing Stage 3/3: DrawIO Diagrams
  [========================================] 100% (5/5 jobs) - 15s

✗ Build completed with errors in 2m 43s

Summary:
  262 files processed
  3 errors
  1 warning

Errors:
  [User Error] slides/module-2/functions/worksheet-210.py: SyntaxError in cell #5
  [User Error] slides/module-3/classes/worksheet-305.py: NameError in cell #2
  [User Error] slides/module-4/loops/worksheet-410.py: IndentationError in cell #3

Warnings:
  [Low Priority] Duplicate topic ID 'intro' (using first occurrence)

Output directory: /path/to/output
Run 'clx build --verbose' for detailed logs
```

### Scenario 3: Infrastructure Error (Default Mode)

```
Initializing CLX build...
✗ No workers available for job type 'notebook'

[Infrastructure Error] Cannot start build without workers
  Expected: At least 1 notebook worker
  Found: 0 workers registered

  Possible causes:
  1. Workers not started - Run 'clx start-services' first
  2. Workers crashed - Check 'clx status' for worker health
  3. Database path mismatch - Verify --jobs-db-path

  For worker status: clx status
  For worker logs: clx monitor

Exit code: 2 (fatal error)
```

### Scenario 4: CI/CD Mode (JSON Output)

```bash
$ clx build course.yaml --format=json
```

```json
{
  "status": "completed_with_errors",
  "exit_code": 1,
  "duration_seconds": 163,
  "summary": {
    "total_files": 262,
    "successful": 259,
    "failed": 3,
    "warnings": 1
  },
  "errors": [
    {
      "type": "user_error",
      "category": "notebook_compilation",
      "file": "slides/module-2/functions/worksheet-210.py",
      "cell": 5,
      "error_message": "SyntaxError: invalid syntax",
      "job_id": 42,
      "correlation_id": "nb-210-en-participant-html"
    },
    {
      "type": "user_error",
      "category": "notebook_compilation",
      "file": "slides/module-3/classes/worksheet-305.py",
      "cell": 2,
      "error_message": "NameError: name 'undefined_var' is not defined",
      "job_id": 87,
      "correlation_id": "nb-305-en-participant-html"
    },
    {
      "type": "user_error",
      "category": "notebook_compilation",
      "file": "slides/module-4/loops/worksheet-410.py",
      "cell": 3,
      "error_message": "IndentationError: unexpected indent",
      "job_id": 134,
      "correlation_id": "nb-410-en-participant-html"
    }
  ],
  "warnings": [
    {
      "type": "warning",
      "category": "duplicate_topic_id",
      "message": "Duplicate topic ID 'intro' (using first occurrence)",
      "severity": "low"
    }
  ],
  "output_directory": "/path/to/output"
}
```

---

## Appendix B: Error Message Templates

### Template 1: Notebook Compilation Error

```
[{error_type}] Notebook compilation failed
  File: {file_path}
  Cell: #{cell_number} ({cell_type} cell)
  Error: {error_class}: {error_message}

  {if code_snippet}
  Code preview:
    {line_num}: {code_line_1}
    {line_num}: {code_line_2}  # <-- Error here
    {line_num}: {code_line_3}
  {endif}

  Action: {actionable_guidance}
  {if verbose}Job ID: #{job_id} | Correlation ID: {correlation_id}{endif}
```

### Template 2: Missing External Tool

```
[Configuration Error] Required tool not found: {tool_name}
  Job: {job_type}
  File: {file_path}

  {tool_name} is required to process {file_type} files.

  Installation:
    {installation_instructions}

  Configuration:
    Set environment variable: {env_var}={example_path}
    Or configure in: ~/.config/clx/config.toml

  See: {documentation_url}
```

### Template 3: Worker Crash

```
[Infrastructure Error] Worker crashed during job processing
  Worker: {worker_id} ({worker_type})
  Job: #{job_id}
  File: {input_file}

  This is likely a bug in CLX. Please report this issue.

  Debug information:
    Worker status: {worker_status}
    Last heartbeat: {last_heartbeat}
    Error: {error_message}

  GitHub Issues: https://github.com/hoelzl/clx/issues
  Include: Job ID #{job_id}, Worker ID {worker_id}
```

---

## Appendix C: Configuration File Example

**File**: `~/.config/clx/config.toml`

```toml
[build]
# Output mode: "default", "verbose", "quiet", "json"
output_mode = "default"

# Show progress bar (auto-detects TTY)
show_progress = true

# Progress bar style: "simple", "fancy", "minimal"
progress_style = "fancy"

# Update interval in seconds
progress_update_interval = 2.0

# Show estimated time remaining
show_eta = true

# Show active workers in progress bar
show_active_workers = false

[logging]
# Default log level
log_level = "INFO"

# Auto-detect CI environment and adjust output
auto_detect_ci = true

# Log file (optional, requires explicit config)
# log_file = "/var/log/clx/build.log"

[errors]
# Show full stack traces
print_tracebacks = false

# Show correlation IDs in errors
print_correlation_ids = false

# Categorize errors
categorize_errors = true

# Show code snippets for notebook errors
show_code_snippets = true

# Number of context lines in code snippets
code_snippet_context_lines = 3

[monitoring]
# Suggest monitoring tools in output
suggest_monitoring = true

# Auto-start web dashboard (for local dev)
auto_start_dashboard = false
```

---

## References

1. CLX Documentation: https://github.com/hoelzl/clx
2. Rich Library: https://rich.readthedocs.io/
3. Click Documentation: https://click.palletsprojects.com/
4. Textual TUI Framework: https://textual.textualize.io/
5. Best Practices for CLI Output: https://clig.dev/
