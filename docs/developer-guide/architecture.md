# CLX Architecture

This document describes the current architecture of the CLX system (v0.4.0).

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
│                                (NEW in v0.4.0)             │
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
    status TEXT NOT NULL,                 -- 'idle', 'busy', 'hung', 'dead'
    started_at TIMESTAMP,
    last_heartbeat TIMESTAMP,
    jobs_processed INTEGER DEFAULT 0,
    jobs_failed INTEGER DEFAULT 0
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
   - Managed by docker-compose

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

**Purpose**: Concrete worker implementations for different file types (NEW in v0.4.0)

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
- `DrawioConverter` - Draw.io conversion logic

**Dependencies** (optional, install with `[drawio]`):
- aiofiles, tenacity

**External Dependencies**:
- Draw.io desktop application
- Xvfb (Linux only, for headless rendering)

**Entry Point**: `python -m clx.workers.drawio`

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

**Startup Time**: ~5 seconds (SQLite initialization + worker startup)

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

**v0.4.0** (November 2025): Integrated workers
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

**Last Updated**: 2025-11-19
**Version**: 0.4.0
