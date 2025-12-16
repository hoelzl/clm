# CLX Architecture

This document describes the current architecture of the CLX system (v0.6.0).

## Overview

CLX is a course content processing system that converts educational materials (Jupyter notebooks, PlantUML diagrams, Draw.io diagrams) into multiple output formats using a worker-based architecture orchestrated by an SQLite job queue.

**Key Characteristics**:
- Single unified Python package with integrated workers
- SQLite-based job queue (no message broker)
- Direct file system access (no serialization overhead)
- Worker pools (Docker containers or direct processes)
- Clean four-layer architecture

## Four-Layer Architecture

```
┌───────────────────────────────────────────────────────────┐
│                     clx.core (Domain)                      │
│                                                             │
│  Course, Section, Topic, CourseFile, CourseSpec            │
│  ├── course_files/ (NotebookFile, PlantUmlFile, etc.)     │
│  ├── operations/ (process_notebook, convert_plantuml)     │
│  └── utils/ (notebook_utils, text_utils, execution_utils) │
│                                                             │
│  NO infrastructure dependencies                             │
└────────────────────────┬──────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────┐
│              clx.infrastructure (Runtime)                   │
│                                                             │
│  Backend, Operation, JobQueue, Worker Management           │
│  ├── backends/ (SqliteBackend, LocalOpsBackend, DummyBackend) │
│  ├── database/ (schema, job_queue, db_operations)         │
│  ├── messaging/ (payloads, results)                       │
│  ├── workers/ (worker_base, pool_manager, executor)       │
│  └── services/ (service registry)                         │
│                                                             │
└────────────────────────┬──────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────┐
│          clx.workers (Worker Implementations)              │
│                                (NEW in v0.6.0)             │
│  ├── notebook/ (NotebookWorker, templates, processors)    │
│  ├── plantuml/ (PlantUmlWorker, converter)                │
│  └── drawio/ (DrawioWorker, converter)                    │
│                                                             │
│  Optional dependencies: [notebook], [plantuml], [drawio]  │
└────────────────────────┬──────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────┐
│                   clx.cli (Interface)                       │
│                                                             │
│  main.py (Click-based CLI)                                 │
│  file_event_handler.py (watchdog for file monitoring)     │
│  git_dir_mover.py (git directory utilities)               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Layer 1: Core (Domain Logic)

**Purpose**: Course processing logic without any infrastructure dependencies

**Key Classes**:
- `Course` - Main course representation, manages sections and topics
- `Section` - Course section, contains topics
- `Topic` - Individual topic, contains files
- `CourseFile` - Base class for all file types (abstract)
- `NotebookFile`, `PlantUmlFile`, `DrawioFile` - Concrete file handlers

**File Types Supported**:
- Jupyter notebooks (`.py` source → `.ipynb` output + HTML/PDF/etc.)
- PlantUML diagrams (`.puml` → PNG/SVG)
- Draw.io diagrams (`.drawio` → PNG/SVG/PDF)

**Design Principle**: The core layer has ZERO dependencies on infrastructure. It can be tested in complete isolation.

### Layer 2: Infrastructure (Runtime Support)

**Purpose**: Job orchestration, worker management, backend implementations

**Key Components**:

#### SQLite Database

**Tables**:
```sql
-- Job queue (replaces message broker)
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL,              -- 'notebook', 'drawio', 'plantuml'
    status TEXT NOT NULL,                 -- 'pending', 'processing', 'completed', 'failed'
    input_file TEXT NOT NULL,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,           -- For caching
    payload TEXT NOT NULL,                -- JSON with job parameters
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error TEXT,
    created_at TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    worker_id INTEGER
);

-- Results cache (avoids re-processing unchanged files)
CREATE TABLE results_cache (
    id INTEGER PRIMARY KEY,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result_metadata TEXT,
    created_at TIMESTAMP,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    UNIQUE(output_file, content_hash)
);

-- Worker registration and health monitoring
CREATE TABLE workers (
    id INTEGER PRIMARY KEY,
    worker_type TEXT NOT NULL,
    container_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,                 -- 'created', 'idle', 'busy', 'hung', 'dead'
    started_at TIMESTAMP,
    last_heartbeat TIMESTAMP,
    jobs_processed INTEGER DEFAULT 0,
    jobs_failed INTEGER DEFAULT 0,
    parent_pid INTEGER                    -- For orphan detection
);
```

**Journal Mode**: `DELETE` (not WAL) for cross-platform compatibility with Docker volume mounts

**Why SQLite?**:
- No separate broker infrastructure required
- Simple SQL queries for monitoring
- Built-in, no external dependencies
- Efficient for single-host use case
- Direct file access (no serialization)

#### Worker Management

**WorkerBase** - Abstract base class for all workers:
```python
class Worker(ABC):
    def __init__(self, worker_id, worker_type, db_path):
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.job_queue = JobQueue(db_path)

    @abstractmethod
    def process_job(self, job: Job) -> None:
        """Process a single job"""
        pass

    def run(self):
        """Main worker loop: poll, process, update"""
        while self.running:
            job = self.job_queue.get_next_job(self.worker_type)
            if job:
                self.process_job(job)
                self.job_queue.update_job_status(job.id, 'completed')
```

**Worker Execution Modes**:

1. **Docker Mode** (isolated, production):
   - Workers run in Docker containers
   - Isolated environments with specific dependencies
   - Bind-mounted volumes for file access
   - Started automatically by `clx build` via Docker SDK

2. **Direct Mode** (fast, development):
   - Workers run as host processes (subprocesses)
   - Faster startup, easier debugging
   - Requires external tools installed (PlantUML, DrawIO)
   - Managed by PoolManager

**PoolManager** - Manages worker pools:
- Starts/stops worker processes or containers
- Monitors worker health (heartbeat, CPU, memory)
- Auto-restarts hung or crashed workers
- Load balancing across available workers

### Layer 3: Workers (Worker Implementations)

**Purpose**: Concrete worker implementations for different file types (NEW in v0.6.0)

Workers are now integrated into the main `clx` package under `clx.workers/`. Previously they were separate packages in the `services/` directory.

**Worker Modules**:

#### clx.workers.notebook - Notebook Processing

**Location**: `src/clx/workers/notebook/`

**Key Components**:
- `NotebookWorker` - Worker implementation extending `WorkerBase`
- `NotebookProcessor` - Core notebook processing logic
- `OutputSpec` - Output format specifications
- Language-specific templates (Python, C++, C#, Java, TypeScript)

**Dependencies** (optional, install with `[notebook]`):
- IPython, nbconvert, jupytext
- matplotlib, pandas, scikit-learn
- And more (see pyproject.toml)

**Entry Point**: `python -m clx.workers.notebook`

#### clx.workers.plantuml - PlantUML Conversion

**Location**: `src/clx/workers/plantuml/`

**Key Components**:
- `PlantUmlWorker` - Worker implementation extending `WorkerBase`
- `PlantUmlConverter` - PlantUML conversion logic

**Dependencies** (optional, install with `[plantuml]`):
- aiofiles, tenacity

**External Dependencies**:
- Java Runtime Environment
- PlantUML JAR file

**Entry Point**: `python -m clx.workers.plantuml`

#### clx.workers.drawio - Draw.io Conversion

**Location**: `src/clx/workers/drawio/`

**Key Components**:
- `DrawioWorker` - Worker implementation extending `WorkerBase`
- `DrawioConverter` - Draw.io conversion logic with automatic crash retry

**Dependencies** (optional, install with `[drawio]`):
- aiofiles, tenacity

**External Dependencies**:
- Draw.io desktop application
- Xvfb (Linux only, for headless rendering)

**Entry Point**: `python -m clx.workers.drawio`

**Crash Recovery**: DrawIO uses Electron (Node.js + Chromium) which can experience transient V8 crashes. The converter automatically retries up to 3 times with a 2-second delay between attempts. See [Subprocess Crash Retry Logic](#subprocess-crash-retry-logic) for details.

**Worker Execution Modes**:

1. **Direct Execution Mode** (Default):
   - Workers run as subprocesses
   - Requires worker dependencies installed: `pip install -e ".[all-workers]"`
   - Faster for development
   - External tools required (PlantUML JAR, Draw.io app)

2. **Docker Mode**:
   - Workers run in Docker containers
   - No worker dependencies needed on host
   - Better isolation
   - Requires Docker daemon
   - Legacy: `services/` directory contains Docker build artifacts

### Layer 4: CLI (User Interface)

**Entry Point**: `clx` command (via `clx.cli.main:cli`)

**Main Commands**:
```bash
clx build <course.yaml>         # Build/convert course
clx build --watch               # Watch for changes and auto-rebuild
```

**File Watching**:
- Uses `watchdog` library to monitor file changes
- Automatically triggers rebuilds when files change
- Debouncing to avoid duplicate builds

## Job Processing Flow

```
┌─────────────┐
│ User runs   │
│ clx build   │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ Course.process()    │──────┐
│ - Parse course spec │      │
│ - Scan files        │      │
│ - Create jobs       │      │
└──────┬──────────────┘      │
       │                     │
       ▼                     ▼
┌─────────────────┐    ┌──────────────┐
│ Check cache     │───▶│ Cache hit?   │
│ (content hash)  │    │ Skip job     │
└─────┬───────────┘    └──────────────┘
      │                       │
      │ Cache miss            │
      ▼                       │
┌─────────────────┐           │
│ Add job to      │           │
│ SQLite queue    │           │
│ (status: pending)          │
└──────┬──────────┘           │
       │                      │
       │                      │
┌──────▼─────────────────┐    │
│ Worker polls queue     │    │
│ SELECT * FROM jobs     │    │
│ WHERE status='pending' │    │
│ LIMIT 1                │    │
└──────┬─────────────────┘    │
       │                      │
       ▼                      │
┌─────────────────┐           │
│ Read input file │           │
│ from filesystem │           │
└──────┬──────────┘           │
       │                      │
       ▼                      │
┌─────────────────┐           │
│ Process job     │           │
│ (convert/execute)          │
└──────┬──────────┘           │
       │                      │
       ▼                      │
┌─────────────────┐           │
│ Write output    │           │
│ to filesystem   │           │
└──────┬──────────┘           │
       │                      │
       ▼                      │
┌─────────────────┐           │
│ Update job:     │           │
│ status=completed│           │
│ Add to cache    │           │
└──────┬──────────┘           │
       │                      │
       └──────────────────────┘
              │
              ▼
       ┌───────────┐
       │  Done!    │
       └───────────┘
```

## Worker Services

### Notebook Processor

**Purpose**: Process Jupyter notebooks

**Capabilities**:
- Execute notebooks with multiple kernels (Python, C++, C#, Java, TypeScript)
- Convert to formats: HTML, slides, PDF, Python script
- Template support for different languages
- Language variants (English, German)
- Output modes (speaker notes, participant versions)

**Dependencies**: Python, IPython, Jupyter, xeus kernels

**Location**: `services/notebook-processor/`

### PlantUML Converter

**Purpose**: Convert PlantUML diagrams to images

**Output Formats**: PNG, SVG

**Dependencies**:
- Java Runtime Environment
- PlantUML JAR file (plantuml-1.2024.6.jar)

**Environment Variable**: `PLANTUML_JAR` - Path to PlantUML JAR

**Location**: `services/plantuml-converter/`

### DrawIO Converter

**Purpose**: Convert Draw.io diagrams to images

**Output Formats**: PNG, SVG, PDF

**Dependencies**:
- Draw.io desktop application
- Xvfb (X virtual framebuffer, for headless rendering)

**Environment Variable**: `DRAWIO_EXECUTABLE` - Path to Draw.io executable

**Location**: `services/drawio-converter/`

**Special Requirements**: Requires Xvfb running in headless environments

## Caching Strategy

**Content-Based Caching**:
- Each file's content is hashed (SHA-256)
- Before processing, check if output file + content hash exists in cache
- If cache hit: skip processing, use cached result
- If cache miss: process file, store result in cache

**Benefits**:
- Avoid re-processing unchanged files
- Faster incremental builds
- Especially important for slow operations (notebook execution, PlantUML rendering)

**Cache Tables**:
- `results_cache` - Stores cached results
- Indexed by (output_file, content_hash)
- Tracks access count and last accessed time

## Configuration

**Environment Variables**:
- `DB_PATH` - Path to SQLite database (default: `clx_jobs.db`)
- `PLANTUML_JAR` - Path to PlantUML JAR file
- `DRAWIO_EXECUTABLE` - Path to Draw.io executable
- `LOG_LEVEL` - Logging level (DEBUG, INFO, WARNING, ERROR)
- `CLX_SKIP_DOWNLOADS` - Skip downloads in sessionStart hook
- `CLX_WORKER_ID` - Pre-assigned worker ID (set by parent process for worker pre-registration)

**Course Specification** (`course.yaml`):
```yaml
name: "Course Name"
language: en
prog_lang: python
output_dir: "./output"
sections:
  - name: "Section 1"
    topics:
      - name: "Topic 1"
        files:
          - "topic_001.py"
          - "diagram.puml"
```

## Testing Strategy

**Test Markers**:
- **(no marker)** - Fast unit tests, mocked dependencies
- `@pytest.mark.integration` - Real workers, requires external tools
- `@pytest.mark.e2e` - Full course conversion
- `@pytest.mark.slow` - Long-running tests

**Test Organization**:
```
tests/
├── core/              # Core domain logic tests (43 tests)
├── infrastructure/    # Infrastructure tests (114 tests)
├── cli/               # CLI tests (15 tests)
└── e2e/               # End-to-end tests (49 tests)
```

**Default**: Skips slow, broker, integration, and e2e tests

**Running Tests**:
```bash
pytest              # Fast unit tests only
pytest -m integration  # Include integration tests
pytest -m e2e          # Include e2e tests
pytest -m ""           # Run ALL tests
```

## Performance Characteristics

**Startup Time**: ~1-2 seconds (SQLite initialization + worker pre-registration)

**Memory Usage**: ~500MB (workers only, no broker infrastructure)

**Concurrency**:
- SQLite handles concurrent access with DELETE journal mode
- Multiple workers can process jobs in parallel
- Worker pools configurable per job type

**Scalability**:
- Single-host design (SQLite limitation)
- Can scale workers up to CPU core count
- Designed for local development and CI/CD, not large-scale distributed processing

## Migration History

CLX has evolved significantly:

**v0.1.x - v0.2.x**: Message broker-based architecture
- 4 separate packages
- RabbitMQ message broker with FastStream framework
- Prometheus + Grafana monitoring
- Message serialization overhead

**v0.3.0 - v0.3.1** (November 2025): Simplified architecture
- Single unified package
- SQLite job queue (RabbitMQ/FastStream removed)
- Direct file system access
- No message broker required
- Reduced from 8 Docker services to 3

**v0.6.0** (November 2025): Integrated workers
- Workers integrated into main package (`clx.workers`)
- Optional dependencies for each worker
- Four-layer architecture (core, infrastructure, workers, cli)
- No separate worker package installation needed
- New `[all-workers]` and `[ml]` dependency groups

For detailed migration history, see `docs/archive/migration-history/`.

## Design Decisions

### Why SQLite instead of a Message Broker?

**Pros**:
- ✅ No separate broker infrastructure
- ✅ Simple SQL queries for monitoring
- ✅ Built-in to Python (no external dependencies)
- ✅ Direct file access (no serialization)
- ✅ Easier debugging and testing
- ✅ Simpler architecture

**Cons**:
- ❌ Single-host limitation (not distributed)
- ❌ Lower write concurrency than message brokers

**Decision**: For CLX's use case (local development, educational content processing), simplicity and ease of use outweigh the scalability limitations. The project has completely removed RabbitMQ/FastStream in favor of pure SQLite orchestration.

### Why Direct Worker Execution?

**Pros**:
- ✅ Faster startup (no container overhead)
- ✅ Easier debugging (direct process access)
- ✅ Lower resource usage

**Cons**:
- ❌ Requires external tools installed
- ❌ Less isolation than containers

**Decision**: Support both modes - Docker for production/CI, direct for development.

### Why DELETE Journal Mode instead of WAL?

**Problem**: WAL mode doesn't work reliably across Docker volume mounts on Windows

**Solution**: Use DELETE journal mode for cross-platform compatibility

**Tradeoff**: Slightly lower write concurrency, but reliable everywhere

## Known Issues and Solutions

### Signal Handling and asyncio Cleanup

**Problem**: Spurious "Aborted!" messages after successful builds

**Root Cause**: When using signal handlers with `asyncio.run()`, there's a timing-sensitive interaction:

1. The CLI registers custom signal handlers (SIGINT, SIGTERM) that raise `KeyboardInterrupt`
2. When the build completes, signal handlers are restored in a `finally` block
3. `asyncio.run()` performs its own cleanup (canceling tasks, closing the loop)
4. If a signal arrives during asyncio cleanup (after handlers are restored but before `asyncio.run()` returns), Python's default handler raises `KeyboardInterrupt`
5. Click catches this exception and prints "Aborted!" even though the build succeeded

**Solution**: Track build completion status and suppress late `KeyboardInterrupt`:

```python
# In the async main() function:
build_completed = False
try:
    # ... build logic ...
    build_completed = True  # Set AFTER successful completion
finally:
    signal.signal(signal.SIGINT, original_sigint)  # Restore handlers
return build_completed

# In the sync build() command:
try:
    build_completed = asyncio.run(main(...))
except KeyboardInterrupt:
    if not build_completed:
        raise  # Real interruption - propagate
    # Build completed - ignore late signal
    pass
```

**Key Insight**: Signal handlers in Python can be called at almost any point during execution. When raising exceptions in signal handlers, you must account for the exception being raised during cleanup code, not just during the main execution.

### Worker Orphan Processes

**Problem**: Worker processes becoming orphaned when parent CLX process crashes

**Root Cause**: Workers had no mechanism to detect when their parent process died. They would continue running indefinitely, consuming resources.

**Solution**: Workers now monitor their parent process:

1. Store parent PID at worker initialization
2. Periodically check if parent is alive (every 5 seconds)
3. Use `os.kill(pid, 0)` - sends signal 0 (no-op) to check process existence
4. If parent dies, worker logs a warning and exits gracefully
5. Additionally, `atexit` handlers provide emergency cleanup on normal exit

**Implementation Details**:
- `os.kill(pid, 0)` raises `OSError` if process doesn't exist
- Works cross-platform (Windows/Linux/macOS)
- Database schema tracks `parent_pid` for diagnostics
- Worker events include `parent_died` event type

### Subprocess Crash Retry Logic

**Problem**: External tools like DrawIO (which uses Electron/Node.js) can experience transient crashes due to V8 JavaScript engine issues, such as "Invoke in DisallowJavascriptExecutionScope" errors caused by garbage collection race conditions.

**Root Cause**: Electron-based applications can crash transiently when:
- V8 garbage collection interrupts JavaScript execution at an invalid point
- Multiple instances compete for system resources
- Memory pressure causes instability

**Solution**: The subprocess execution system (`subprocess_tools.py`) now supports configurable retry logic for crash recovery:

```python
from clx.infrastructure.services.subprocess_tools import RetryConfig, run_subprocess

# Configure retry behavior
config = RetryConfig(
    max_retries=3,          # Number of retry attempts
    base_timeout=60,        # Base timeout (doubles with each retry)
    retry_on_crash=True,    # Enable retry on non-zero exit codes
    retry_delay=2.0         # Delay between crash retries (seconds)
)

# Use with run_subprocess
process, stdout, stderr = await run_subprocess(
    cmd=["drawio", "--export", "file.drawio"],
    correlation_id="job-123",
    retry_config=config,
    env=custom_env
)
```

**Key Components**:

1. **RetryConfig** - Dataclass for configuring retry behavior:
   - `max_retries`: Total attempts before failing (default: 3)
   - `base_timeout`: Initial timeout, doubles each retry (default: 60s)
   - `retry_on_crash`: Enable retry on non-zero exit (default: False)
   - `retry_delay`: Wait time between crash retries (default: 1.0s)

2. **SubprocessCrashError** - Exception for crash failures:
   - Subclass of `SubprocessError`
   - Contains `return_code`, `stderr`, and `stdout` attributes
   - Raised when all crash retries are exhausted

3. **DrawIO Integration** - DrawIO converter uses crash retry by default:
   ```python
   # In drawio_converter.py
   DRAWIO_RETRY_CONFIG = RetryConfig(
       max_retries=3,
       base_timeout=60,
       retry_on_crash=True,   # Enabled for DrawIO
       retry_delay=2.0        # 2-second delay for resource recovery
   )
   ```

**Retry Behavior**:
- Timeout errors: Always retried (with exponential backoff)
- Non-zero exit codes: Only retried if `retry_on_crash=True`
- FileNotFoundError/PermissionError: Never retried (fail immediately)

**Backward Compatibility**: By default, `retry_on_crash=False`, preserving the original behavior where non-zero exit codes return normally without raising exceptions.

**Platform-Specific Handling**: The DrawIO converter only sets `DISPLAY=":99"` on non-Windows platforms, since Windows DrawIO uses native GUI and doesn't require X11.

### Signal Handler Reentrancy with Logging

**Problem**: `RuntimeError: reentrant call inside <_io.BufferedWriter>` when receiving signals

**Root Cause**: Signal handlers can interrupt Python code at almost any point, including while the logging system is writing to a file. If the signal handler then calls `logger.info()` or similar, this causes a reentrant call to the logging system, which is not thread-safe.

**Example Error**:
```
RuntimeError: reentrant call inside <_io.BufferedWriter name='...clx.log'>
```

**Solution**: Never call logging functions from signal handlers:

```python
def shutdown_handler(signum, frame):
    # NOTE: Do not log here - signal handlers can interrupt logging
    # and cause reentrant call errors
    nonlocal shutdown_requested
    shutdown_requested = True
    raise KeyboardInterrupt(f"Shutdown signal {signum} received")
```

**Key Rules for Signal Handlers**:
1. Do minimal work - just set flags and/or raise exceptions
2. Never call `logger.info()`, `logger.warning()`, etc.
3. Never call `print()` to files (only `sys.stderr` is somewhat safe)
4. Defer logging to exception handlers or cleanup code that runs after the signal handler returns

## Multiple Output Targets Architecture

**Added in v0.4.x**: Support for defining multiple output directories with selective content generation. This enables scenarios like delayed solution release, language-specific distributions, and separate instructor packages.

### Design Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         XML Course Spec                                  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  <output-targets>                                                │   │
│  │    <output-target name="students">...</output-target>            │   │
│  │    <output-target name="solutions">...</output-target>           │   │
│  │  </output-targets>                                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      CourseSpec.output_targets                          │
│                      list[OutputTargetSpec]                             │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │ Course.from_spec()
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Course.output_targets                           │
│                         list[OutputTarget]                              │
│                                                                         │
│  For each file, for each target:                                       │
│    → Generate only the kinds/formats/languages in target config        │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Output Generation                                     │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐        │
│  │ Target: students │ │ Target: solutions│ │ Target: instructor│       │
│  │ Path: ./students │ │ Path: ./solutions│ │ Path: ./private   │       │
│  │ Kinds: code-along│ │ Kinds: completed │ │ Kinds: speaker    │       │
│  └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘        │
│           ▼                    ▼                    ▼                   │
│    ./students/            ./solutions/         ./private/               │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Components

#### OutputTargetSpec (course_spec.py)

Parses `<output-target>` XML elements and stores the specification:

```python
@frozen
class OutputTargetSpec:
    name: str                      # Unique identifier
    path: str                      # Output directory path
    kinds: list[str] | None        # Filter: code-along, completed, speaker
    formats: list[str] | None      # Filter: html, notebook, code
    languages: list[str] | None    # Filter: de, en
```

**Design Decision**: `None` means "all values" for any filter. This provides backward compatibility - targets without explicit filters generate everything.

#### OutputTarget (output_target.py)

Runtime representation with resolved paths and efficient filtering:

```python
@define
class OutputTarget:
    name: str
    output_root: Path              # Resolved absolute path
    kinds: frozenset[str]          # Immutable set for O(1) lookup
    formats: frozenset[str]
    languages: frozenset[str]

    def should_generate(self, lang: str, fmt: str, kind: str) -> bool:
        """Check if this combination should be generated."""
        return (lang in self.languages and
                fmt in self.formats and
                kind in self.kinds)
```

**Design Decision**: Use `frozenset` instead of `list` for filters. This enables O(1) membership testing and makes targets immutable/hashable.

#### ExecutionDependencyResolver (execution_dependencies.py)

Handles the critical requirement that some outputs depend on cached execution results from other outputs.

**The Problem**: HTML `completed` output reuses cached execution from HTML `speaker`. If a user configures a target with only `completed` (no `speaker`), we must still execute notebooks to populate the cache.

**Solution**: Explicit dependency resolution:

```python
class ExecutionRequirement(Enum):
    NONE = auto()           # No execution needed
    POPULATES_CACHE = auto() # Executes and caches results
    REUSES_CACHE = auto()    # Depends on cached results

EXECUTION_REQUIREMENTS = {
    ("html", "code-along"): ExecutionRequirement.NONE,
    ("html", "speaker"): ExecutionRequirement.POPULATES_CACHE,
    ("html", "completed"): ExecutionRequirement.REUSES_CACHE,
    # notebook and code formats don't need execution
}

class ExecutionDependencyResolver:
    CACHE_PROVIDERS = {
        ("html", "completed"): ("html", "speaker"),
    }

    def resolve_implicit_executions(self, requested_outputs):
        """Return additional executions needed for cache."""
        implicit = set()
        for lang, fmt, kind in requested_outputs:
            if get_execution_requirement(fmt, kind) == ExecutionRequirement.REUSES_CACHE:
                provider = self.CACHE_PROVIDERS.get((fmt, kind))
                if provider and (lang, *provider) not in requested_outputs:
                    implicit.add((lang, *provider))
        return implicit
```

**Why This Design**:
1. **Explicit Dependencies**: The `EXECUTION_REQUIREMENTS` table documents which outputs need execution
2. **Extensible**: New formats can be added by extending the tables
3. **Testable**: Dependency resolution can be unit tested in isolation
4. **Clear Logging**: When implicit executions are added, it's logged for debugging

### Processing Flow

```
1. Course.from_spec() creates OutputTarget objects
   │
2. Course.process_all() collects all requested outputs
   │
3. ExecutionDependencyResolver.resolve_implicit_executions()
   │  └── Returns additional speaker HTML executions if needed
   │
4. For each execution stage:
   │  For each target:
   │    For each file:
   │      → output_specs(target=target) filters outputs
   │      → Operations created only for matching combinations
   │
5. Implicit executions run but don't write outputs
```

### Integration with Existing Code

#### output_specs() Function

Extended to accept an `OutputTarget` parameter:

```python
def output_specs(
    course: Course,
    root_dir: Path,
    skip_html: bool = False,
    languages: list[str] | None = None,
    kinds: list[str] | None = None,
    target: OutputTarget | None = None,  # NEW
) -> Iterator[OutputSpec]:
```

When `target` is provided, its filters take precedence over explicit `languages`/`kinds` parameters.

#### NotebookFile.get_processing_operation()

Extended to pass target and handle implicit executions:

```python
async def get_processing_operation(
    self,
    target_dir: Path,
    stage: int | None = None,
    target: OutputTarget | None = None,
    implicit_executions: set[tuple[str, str, str]] | None = None,
) -> Operation:
```

The `implicit_executions` parameter contains (lang, format, kind) tuples that should be executed for cache population but whose outputs should not be saved.

### CLI Integration

**New `--targets` option**:
```bash
clx build course.xml --targets students,solutions
```

**New `targets` command**:
```bash
clx targets course.xml
```

### Backward Compatibility

The feature is fully backward compatible:

1. **No `<output-targets>`**: Uses default single target with all kinds/formats/languages
2. **`--output-dir` CLI flag**: Overrides all spec targets with a single default target
3. **Existing code paths**: Continue to work unchanged when `target=None`

### Testing Strategy

**Unit Tests** (tests/core/test_output_target*.py, test_execution_dependencies.py):
- OutputTargetSpec XML parsing and validation
- OutputTarget creation and filtering
- ExecutionDependencyResolver implicit execution resolution

**Integration Tests** (tests/core/test_multi_target_course.py):
- Course.from_spec() with multiple targets
- CLI override behavior
- Target selection and filtering
- Implicit execution handling

### Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Filter representation | `frozenset` | O(1) lookup, immutable |
| None semantics | Means "all" | Backward compatible defaults |
| Implicit executions | Explicit resolver | Testable, extensible, documented |
| Code format restriction | Only for `completed` | Maintains existing semantic |
| Path resolution | At Course creation | Fail fast, single resolution point |

### Future Enhancements

Potential improvements:

- Parallel target processing (currently sequential within each stage)
- Per-target progress reporting in TUI
- Target-specific configuration (e.g., different templates)
- Conditional targets based on environment variables

---

## Future Enhancements

Potential improvements (not currently planned):

- Web UI for monitoring job queue
- Job priorities
- Remote workers (distributed processing)
- Prometheus metrics exporter
- Auto-scaling worker pools based on queue depth

## References

- **CLAUDE.md** - Comprehensive guide for AI assistants
- **Migration History** - `docs/archive/migration-history/`
- **Phase Summaries** - `docs/archive/phases/`
- **Source Code** - `src/clx/`

---

**Last Updated**: 2025-11-29
**Version**: 0.6.0
