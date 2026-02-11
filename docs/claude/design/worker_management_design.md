# Worker Management System Design

**Version**: 1.0
**Date**: 2025-11-15
**Status**: Draft

## Overview

This document provides a comprehensive design for the CLM worker management system, addressing the requirements specified in `worker_management_requirements.md`. The design focuses on simplicity for common cases while supporting advanced use cases through progressive disclosure of configuration options.

## Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI Layer                               │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐               │
│  │clm build │  │clm start-    │  │clm workers  │               │
│  │          │  │   services   │  │   (list,    │               │
│  │          │  │clm stop-     │  │   cleanup)  │               │
│  │          │  │   services   │  │             │               │
│  └────┬─────┘  └──────┬───────┘  └──────┬──────┘               │
└───────┼────────────────┼──────────────────┼─────────────────────┘
        │                │                  │
        v                v                  v
┌─────────────────────────────────────────────────────────────────┐
│                   Worker Manager Layer                           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              WorkerLifecycleManager                        │ │
│  │  - Configuration loading                                   │ │
│  │  - Worker startup/shutdown orchestration                   │ │
│  │  - State management                                        │ │
│  │  - Health monitoring coordination                          │ │
│  └────┬─────────────────────────────────────────────┬─────────┘ │
│       │                                              │           │
│       v                                              v           │
│  ┌─────────────────┐                    ┌────────────────────┐  │
│  │WorkerPoolManager│                    │WorkerStateManager  │  │
│  │(existing)       │                    │(new)               │  │
│  │- Start workers  │                    │- Persistent state  │  │
│  │- Stop workers   │                    │- Worker discovery  │  │
│  │- Health monitor │                    │- Cleanup tracking  │  │
│  └─────────────────┘                    └────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
        │                                              │
        v                                              v
┌─────────────────────────────────────────────────────────────────┐
│                   Infrastructure Layer                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │WorkerExecutor│  │    Config    │  │   Database   │          │
│  │- Docker      │  │- TOML parser │  │- SQLite      │          │
│  │- Direct      │  │- Env vars    │  │- Job queue   │          │
│  │              │  │- Defaults    │  │- Workers tbl │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

#### 1. WorkerLifecycleManager (New)

**Purpose**: High-level orchestration of worker lifecycle based on configuration and CLI options.

**Responsibilities**:
- Load and merge configuration from all sources (files, env, CLI)
- Determine which workers to start (type, mode, count)
- Coordinate with WorkerPoolManager for actual worker startup
- Manage worker state for persistent deployments
- Handle cleanup on exit

**Key Methods**:
```python
class WorkerLifecycleManager:
    def __init__(self, config: WorkerManagementConfig, db_path: Path):
        """Initialize with configuration and database path."""

    def start_managed_workers(self) -> None:
        """Start workers that will be managed (auto-stopped) by this instance."""

    def start_persistent_workers(self) -> None:
        """Start workers that persist after this process exits."""

    def stop_managed_workers(self) -> None:
        """Stop all managed workers."""

    def stop_persistent_workers(self) -> None:
        """Stop all persistent workers registered in state."""

    def discover_existing_workers(self) -> List[WorkerInfo]:
        """Query database for existing workers."""

    def should_start_workers(self) -> bool:
        """Check if we need to start workers or can reuse existing."""
```

#### 2. WorkerStateManager (New)

**Purpose**: Manage persistent worker state for `clm start-services` / `clm stop-services`.

**Responsibilities**:
- Store worker registration information to disk
- Track which workers were started by `clm start-services`
- Provide cleanup capability for persistent workers
- Detect orphaned workers

**State File Structure** (`.clm/worker-state.json`):
```json
{
  "version": "1.0",
  "db_path": "/path/to/clm_jobs.db",
  "workers": [
    {
      "worker_type": "notebook",
      "execution_mode": "docker",
      "executor_id": "container_id_or_process_id",
      "db_worker_id": 123,
      "started_at": "2025-11-15T10:30:00Z",
      "config": {
        "image": "mhoelzl/clm-notebook-processor:0.3.0",
        "count": 2
      }
    }
  ],
  "metadata": {
    "created_at": "2025-11-15T10:30:00Z",
    "created_by": "clm start-services",
    "network_name": "clm_app-network"
  }
}
```

**Key Methods**:
```python
class WorkerStateManager:
    def __init__(self, state_file: Path = Path(".clm/worker-state.json")):
        """Initialize state manager."""

    def save_worker_state(self, workers: List[WorkerInfo]) -> None:
        """Save worker state to disk."""

    def load_worker_state(self) -> Optional[WorkerState]:
        """Load worker state from disk."""

    def clear_worker_state(self) -> None:
        """Clear worker state file."""

    def validate_state(self) -> bool:
        """Validate that workers in state are still running."""
```

#### 3. WorkerManagementConfig (New)

**Purpose**: Type-safe configuration model for worker management.

**Configuration Schema**:
```python
from pydantic import BaseModel, Field
from typing import Optional, Dict, Literal

class WorkerTypeConfig(BaseModel):
    """Configuration for a specific worker type."""
    execution_mode: Optional[Literal["direct", "docker"]] = None
    count: Optional[int] = None
    image: Optional[str] = None  # Required for docker mode
    memory_limit: str = "1g"
    max_job_time: int = 600

class WorkersManagementConfig(BaseModel):
    """Worker management configuration."""
    # Global defaults
    default_execution_mode: Literal["direct", "docker"] = "direct"
    default_worker_count: int = 1
    auto_start: bool = True  # Auto-start workers with clm build
    auto_stop: bool = True   # Auto-stop workers after clm build

    # Network configuration
    network_name: str = "clm_app-network"

    # Per-type configurations
    notebook: WorkerTypeConfig = Field(default_factory=WorkerTypeConfig)
    plantuml: WorkerTypeConfig = Field(default_factory=WorkerTypeConfig)
    drawio: WorkerTypeConfig = Field(default_factory=WorkerTypeConfig)

    def get_worker_config(self, worker_type: str) -> WorkerConfig:
        """Get effective configuration for a worker type."""
        type_config = getattr(self, worker_type)

        return WorkerConfig(
            worker_type=worker_type,
            execution_mode=type_config.execution_mode or self.default_execution_mode,
            count=type_config.count or self.default_worker_count,
            image=type_config.image,
            memory_limit=type_config.memory_limit,
            max_job_time=type_config.max_job_time
        )
```

This would be integrated into the existing `ClmConfig` class in `clm.infrastructure.config`:

```python
class ClmConfig(BaseSettings):
    # ... existing fields ...

    worker_management: WorkersManagementConfig = Field(
        default_factory=WorkersManagementConfig,
        description="Worker management configuration"
    )
```

#### 4. Enhanced Database Schema

**New Table: worker_sessions** (optional, alternative to state file):
```sql
CREATE TABLE IF NOT EXISTS worker_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_type TEXT NOT NULL CHECK(session_type IN ('managed', 'persistent')),
    db_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP,
    created_by TEXT,  -- e.g., 'clm build', 'clm start-services'
    metadata TEXT  -- JSON with network name, etc.
);

CREATE TABLE IF NOT EXISTS worker_session_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    worker_id INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES worker_sessions(id),
    FOREIGN KEY (worker_id) REFERENCES workers(id)
);
```

**Rationale**: This allows tracking which workers belong to which session without external state files. However, it couples the state to the database, which might be problematic if the database is deleted.

**Decision**: Use state file (`.clm/worker-state.json`) as primary mechanism, with database as fallback for discovery.

## Detailed Design

### 1. Configuration Loading and Merging

**Priority Order** (highest to lowest):
1. CLI options (`--workers=docker`, `--worker-count=2`)
2. Environment variables (`CLM_WORKER_MANAGEMENT__DEFAULT_EXECUTION_MODE`)
3. Project config (`.clm/config.toml`)
4. User config (`~/.config/clm/config.toml`)
5. System config (`/etc/clm/config.toml`)
6. Defaults (direct, count=1)

**Implementation**:
```python
def load_worker_config(cli_options: Dict[str, Any]) -> WorkersManagementConfig:
    """Load worker configuration from all sources."""
    # 1. Load base config from files + env (handled by ClmConfig)
    base_config = get_config().worker_management

    # 2. Apply CLI overrides
    if cli_options.get('workers'):
        base_config.default_execution_mode = cli_options['workers']

    if cli_options.get('worker_count'):
        base_config.default_worker_count = cli_options['worker_count']

    # Per-type overrides
    for worker_type in ['notebook', 'plantuml', 'drawio']:
        cli_key = f'{worker_type}_workers'
        if cli_options.get(cli_key):
            type_config = getattr(base_config, worker_type)
            type_config.count = cli_options[cli_key]

    return base_config
```

### 2. Automatic Worker Lifecycle (clm build)

**Flow**:
```
clm build course.yaml
    │
    ├─> Load configuration
    │
    ├─> Check for existing workers
    │   ├─> Query database for active workers
    │   ├─> Match against required worker types
    │   └─> Determine if sufficient workers exist
    │
    ├─> Start workers (if needed)
    │   ├─> Create WorkerLifecycleManager
    │   ├─> Start managed workers (will auto-stop)
    │   └─> Wait for worker registration
    │
    ├─> Process course
    │   ├─> Submit jobs to queue
    │   └─> Wait for completion
    │
    └─> Cleanup
        ├─> Stop managed workers
        ├─> Update database
        └─> Exit
```

**Implementation**:
```python
# In cli/main.py

async def main(...):
    # ... existing setup ...

    # Load worker configuration
    worker_config = load_worker_config({
        'workers': workers_mode,
        'worker_count': worker_count,
        'notebook_workers': notebook_workers,
        # ... other CLI options
    })

    # Initialize worker lifecycle manager
    worker_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=db_path,
        workspace_path=output_dir
    )

    try:
        # Check for existing workers
        existing = worker_manager.discover_existing_workers()

        if worker_manager.should_start_workers():
            logger.info("Starting workers...")
            worker_manager.start_managed_workers()
        else:
            logger.info(f"Using {len(existing)} existing worker(s)")

        # ... existing course processing ...

    finally:
        # Cleanup managed workers
        if worker_config.auto_stop:
            logger.info("Stopping workers...")
            worker_manager.stop_managed_workers()
```

### 3. Persistent Workers (clm start-services / stop-services)

**start-services Flow**:
```
clm start-services --db-path=/data/clm_jobs.db
    │
    ├─> Load configuration
    │
    ├─> Validate database path
    │   ├─> Check accessibility
    │   ├─> Initialize if needed
    │   └─> Set journal mode (DELETE for Windows/Docker)
    │
    ├─> Start workers
    │   ├─> Create WorkerLifecycleManager
    │   ├─> Start persistent workers
    │   └─> Wait for registration
    │
    ├─> Save state
    │   ├─> Write worker-state.json
    │   └─> Include database path, worker IDs
    │
    └─> Report success
        └─> Show how to use: clm build --db-path=...
```

**stop-services Flow**:
```
clm stop-services --db-path=/data/clm_jobs.db
    │
    ├─> Load state file
    │   └─> Validate database path matches
    │
    ├─> Stop workers
    │   ├─> For each worker in state
    │   ├─> Stop using appropriate executor
    │   └─> Mark as dead in database
    │
    ├─> Cleanup
    │   ├─> Remove state file
    │   └─> Clean up database records
    │
    └─> Report success
```

**Implementation**:
```python
# In cli/main.py

@cli.command()
@click.option('--db-path', type=click.Path(), default='clm_jobs.db')
@click.option('--wait/--no-wait', default=True, help='Wait for workers to register')
def start_services(db_path, wait):
    """Start persistent worker services."""
    db_path = Path(db_path).absolute()

    # Load configuration
    config = get_config().worker_management

    # Initialize database
    init_database(db_path)

    # Create lifecycle manager
    manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path,
        workspace_path=db_path.parent  # Workspace relative to DB
    )

    # Start persistent workers
    logger.info(f"Starting persistent workers (database: {db_path})")
    workers = manager.start_persistent_workers()

    if wait:
        logger.info("Waiting for workers to register...")
        manager.wait_for_registration(timeout=30)

    # Save state
    state_manager = WorkerStateManager()
    state_manager.save_worker_state(workers, db_path)

    logger.info(f"Started {len(workers)} worker(s)")
    logger.info(f"Use: clm build course.yaml --db-path={db_path}")


@cli.command()
@click.option('--db-path', type=click.Path(), default='clm_jobs.db')
@click.option('--force', is_flag=True, help='Force cleanup even if state missing')
def stop_services(db_path, force):
    """Stop persistent worker services."""
    db_path = Path(db_path).absolute()

    # Load state
    state_manager = WorkerStateManager()
    state = state_manager.load_worker_state()

    if not state and not force:
        logger.error("No worker state found. Use --force to clean up anyway.")
        return 1

    if state and state.db_path != str(db_path):
        logger.warning(
            f"Database path mismatch: state has {state.db_path}, "
            f"you specified {db_path}"
        )
        if not force:
            logger.error("Use --force to override")
            return 1

    # Create lifecycle manager
    config = get_config().worker_management
    manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path,
        workspace_path=db_path.parent
    )

    # Stop workers
    if state:
        logger.info(f"Stopping {len(state.workers)} worker(s)...")
        manager.stop_persistent_workers(state.workers)
    else:
        logger.info("Cleaning up workers from database...")
        manager.cleanup_all_workers()

    # Clear state
    state_manager.clear_worker_state()

    logger.info("Services stopped")
```

### 4. Windows/Docker Database Handling

**Key Issues**:
1. SQLite WAL mode uses shared memory files (.shm, .wal)
2. Docker on Windows runs in WSL2 (different filesystem)
3. Shared memory coordination doesn't work across OS boundary

**Solution**:
- Always use DELETE journal mode (not WAL) when Docker workers are used on Windows
- Mount database directory (not file) to containers
- Use environment variable to pass database path to containers

**Implementation**:
```python
def configure_database_for_docker(db_path: Path, is_windows: bool) -> None:
    """Configure database for Docker compatibility."""
    # Initialize database
    conn = init_database(db_path)

    if is_windows:
        # Use DELETE journal mode on Windows
        logger.info("Windows detected: using DELETE journal mode for Docker compatibility")
        conn.execute("PRAGMA journal_mode=DELETE")
    else:
        # Can use WAL mode on Linux/macOS
        conn.execute("PRAGMA journal_mode=WAL")

    conn.commit()
    conn.close()


def start_docker_worker(config: WorkerConfig, db_path: Path, workspace_path: Path):
    """Start a Docker worker with proper volume mounts."""
    # Mount database directory, not file
    db_dir = db_path.parent.absolute()
    db_filename = db_path.name

    # Detect Windows
    is_windows = sys.platform == 'win32'

    if is_windows:
        # Ensure database is configured for Windows/Docker
        configure_database_for_docker(db_path, is_windows)

        # Warn if database is not in a WSL-accessible location
        if not is_wsl_accessible(db_dir):
            logger.warning(
                f"Database directory {db_dir} may not be accessible from WSL2.\n"
                f"Consider using a path in /mnt/c/ or WSL filesystem."
            )

    # Start container with directory mount
    container = docker_client.containers.run(
        config.image,
        detach=True,
        volumes={
            str(workspace_path.absolute()): {'bind': '/workspace', 'mode': 'rw'},
            str(db_dir): {'bind': '/db', 'mode': 'rw'}  # Mount directory
        },
        environment={
            'DB_PATH': f'/db/{db_filename}',  # File path inside container
            'WORKER_TYPE': config.worker_type,
            # ...
        },
        # ...
    )

    return container.id
```

**WSL Path Detection**:
```python
def is_wsl_accessible(path: Path) -> bool:
    """Check if a Windows path is accessible from WSL2."""
    if sys.platform != 'win32':
        return True  # Not Windows, no issue

    path_str = str(path.absolute()).lower()

    # Windows paths that are accessible from WSL
    # C:\ -> /mnt/c/
    if path_str[1:3] == ':\\':
        return True  # Drive letter paths are accessible

    # UNC paths might not be accessible
    if path_str.startswith('\\\\'):
        return False

    return True
```

### 5. Worker Discovery and Health Checking

**Discovery Query**:
```sql
SELECT
    id,
    worker_type,
    container_id,
    status,
    last_heartbeat,
    jobs_processed,
    jobs_failed,
    started_at
FROM workers
WHERE status IN ('idle', 'busy')
  AND last_heartbeat > datetime('now', '-30 seconds')
ORDER BY worker_type, started_at
```

**Health Validation**:
```python
def validate_worker_health(worker_info: WorkerInfo) -> bool:
    """Validate that a worker is healthy and running."""
    # 1. Check heartbeat
    heartbeat_age = datetime.now() - worker_info.last_heartbeat
    if heartbeat_age > timedelta(seconds=30):
        logger.debug(f"Worker {worker_info.id} has stale heartbeat")
        return False

    # 2. Check process/container is actually running
    executor = get_executor_for_worker(worker_info)
    if not executor.is_worker_running(worker_info.executor_id):
        logger.debug(f"Worker {worker_info.id} process/container not running")
        return False

    # 3. Check status is not hung or dead
    if worker_info.status in ('hung', 'dead'):
        logger.debug(f"Worker {worker_info.id} status is {worker_info.status}")
        return False

    return True
```

**Should Start Workers Logic**:
```python
def should_start_workers(
    config: WorkersManagementConfig,
    existing_workers: List[WorkerInfo]
) -> bool:
    """Determine if we need to start new workers."""
    if not config.auto_start:
        return False

    # Check each required worker type
    for worker_type in ['notebook', 'plantuml', 'drawio']:
        required_config = config.get_worker_config(worker_type)
        required_count = required_config.count

        # Count healthy existing workers of this type
        healthy_count = sum(
            1 for w in existing_workers
            if w.worker_type == worker_type and validate_worker_health(w)
        )

        if healthy_count < required_count:
            logger.info(
                f"Need {required_count} {worker_type} workers, "
                f"only {healthy_count} healthy worker(s) found"
            )
            return True

    logger.info("Sufficient workers already running")
    return False
```

### 6. CLI Commands

**workers list**:
```python
@cli.group()
def workers():
    """Manage CLM workers."""
    pass


@workers.command()
@click.option('--db-path', type=click.Path(), default='clm_jobs.db')
@click.option('--format', type=click.Choice(['table', 'json']), default='table')
def list(db_path, format):
    """List all registered workers."""
    db_path = Path(db_path)
    job_queue = JobQueue(db_path)

    conn = job_queue._get_conn()
    cursor = conn.execute("""
        SELECT
            id, worker_type, container_id, status,
            started_at, last_heartbeat,
            jobs_processed, jobs_failed
        FROM workers
        ORDER BY worker_type, id
    """)

    workers = cursor.fetchall()

    if format == 'json':
        import json
        data = [
            {
                'id': w[0], 'type': w[1], 'executor_id': w[2],
                'status': w[3], 'started_at': w[4], 'last_heartbeat': w[5],
                'jobs_processed': w[6], 'jobs_failed': w[7]
            }
            for w in workers
        ]
        print(json.dumps(data, indent=2))
    else:
        # Table format
        from tabulate import tabulate
        headers = ['ID', 'Type', 'Executor', 'Status', 'Started', 'Last HB', 'Jobs', 'Failed']
        print(tabulate(workers, headers=headers))
```

**workers cleanup**:
```python
@workers.command()
@click.option('--db-path', type=click.Path(), default='clm_jobs.db')
@click.option('--force', is_flag=True, help='Force cleanup without confirmation')
def cleanup(db_path, force):
    """Clean up dead workers and orphaned processes."""
    db_path = Path(db_path)

    # Find dead workers
    job_queue = JobQueue(db_path)
    conn = job_queue._get_conn()
    cursor = conn.execute("""
        SELECT id, worker_type, container_id, status
        FROM workers
        WHERE status IN ('dead', 'hung')
           OR last_heartbeat < datetime('now', '-60 seconds')
    """)

    dead_workers = cursor.fetchall()

    if not dead_workers:
        logger.info("No dead workers to clean up")
        return

    logger.info(f"Found {len(dead_workers)} dead worker(s)")

    if not force:
        if not click.confirm('Remove these workers?'):
            return

    # Cleanup each worker
    cleaned = 0
    for worker_id, worker_type, executor_id, status in dead_workers:
        try:
            # Try to stop the process/container
            is_docker = not executor_id.startswith('direct-')
            if is_docker:
                executor = get_docker_executor()
            else:
                executor = get_direct_executor()

            try:
                executor.stop_worker(executor_id)
            except Exception as e:
                logger.debug(f"Could not stop worker {executor_id}: {e}")

            # Remove from database
            conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
            cleaned += 1

        except Exception as e:
            logger.error(f"Error cleaning up worker {worker_id}: {e}")

    conn.commit()
    logger.info(f"Cleaned up {cleaned} worker(s)")
```

## Implementation Considerations

### 1. State File vs. Database

**State File Approach** (.clm/worker-state.json):
- ✅ Simple, no schema changes needed
- ✅ Project-isolated (each project has own state)
- ✅ Easy to inspect and debug
- ❌ Can get out of sync with reality
- ❌ Needs manual cleanup if deleted

**Database Approach** (worker_sessions table):
- ✅ Always in sync with database
- ✅ Can query for state
- ❌ Couples state to database
- ❌ Requires schema changes
- ❌ Lost if database is deleted

**Recommendation**: Use state file as primary, with database as fallback for discovery.

### 2. Worker Reuse Strategy

**Options**:
1. **Always reuse**: Use existing workers if found
2. **Never reuse**: Always start fresh workers
3. **Health-checked reuse**: Reuse only if workers pass health check

**Recommendation**: Option 3 (health-checked reuse) with configuration override.

**Configuration**:
```toml
[worker_management]
reuse_workers = true  # Default
require_fresh_workers = false  # CLI: --fresh-workers
```

### 3. Error Handling Strategies

**No Workers Available**:
```python
def handle_no_workers_error(worker_type: str, config: WorkersManagementConfig):
    """Handle case when no workers are available."""
    if config.auto_start:
        logger.info(f"No {worker_type} workers found, starting...")
        # Try to start workers
        try:
            start_workers(worker_type, config)
        except Exception as e:
            raise RuntimeError(
                f"Failed to auto-start {worker_type} workers: {e}\n"
                f"You can start workers manually with:\n"
                f"  clm start-services\n"
                f"Or configure workers in .clm/config.toml"
            )
    else:
        raise RuntimeError(
            f"No {worker_type} workers available.\n"
            f"Auto-start is disabled. Please start workers with:\n"
            f"  clm start-services\n"
            f"Or enable auto-start in config: worker_management.auto_start = true"
        )
```

**Docker Not Available**:
```python
def check_docker_available() -> bool:
    """Check if Docker daemon is available."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception as e:
        logger.debug(f"Docker not available: {e}")
        return False


def handle_docker_not_available(config: WorkersManagementConfig):
    """Handle case when Docker is required but not available."""
    raise RuntimeError(
        "Docker is not available, but workers are configured for Docker mode.\n"
        "Options:\n"
        "  1. Install and start Docker\n"
        "  2. Change to direct mode: --workers=direct\n"
        "  3. Change default in config:\n"
        "     [worker_management]\n"
        "     default_execution_mode = \"direct\"\n"
    )
```

**External Tools Missing** (Direct Mode):
```python
def check_external_tools() -> Dict[str, bool]:
    """Check availability of external tools for direct mode."""
    tools = {}

    # PlantUML
    plantuml_jar = os.getenv('PLANTUML_JAR')
    tools['plantuml'] = plantuml_jar and Path(plantuml_jar).exists()

    # Draw.io
    drawio_exe = os.getenv('DRAWIO_EXECUTABLE')
    tools['drawio'] = drawio_exe and Path(drawio_exe).exists()

    return tools


def handle_missing_tools(worker_type: str, tools: Dict[str, bool]):
    """Handle missing external tools."""
    tool_map = {
        'plantuml': 'PlantUML',
        'drawio': 'Draw.io'
    }

    if worker_type in tool_map and not tools[worker_type]:
        tool_name = tool_map[worker_type]
        raise RuntimeError(
            f"{tool_name} is not configured, but required for {worker_type} workers.\n"
            f"Options:\n"
            f"  1. Install {tool_name} and set environment variable\n"
            f"  2. Use Docker mode instead: --workers=docker\n"
            f"  3. Use Docker only for {worker_type}:\n"
            f"     [worker_management.{worker_type}]\n"
            f"     execution_mode = \"docker\"\n"
        )
```

### 4. Platform-Specific Handling

**Windows Detection**:
```python
import sys
import platform

def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == 'win32'


def is_wsl() -> bool:
    """Check if running in WSL."""
    try:
        with open('/proc/version', 'r') as f:
            return 'microsoft' in f.read().lower()
    except:
        return False
```

**Path Conversion** (Windows/WSL):
```python
def convert_path_for_docker(path: Path) -> str:
    """Convert path for Docker volume mount."""
    if not is_windows():
        return str(path.absolute())

    # On Windows, Docker Desktop expects paths like:
    # C:\Users\... -> /c/Users/... (Git Bash style)
    # or C:\Users\... -> /mnt/c/Users/... (WSL style)

    path_str = str(path.absolute())

    # Convert C:\ to /mnt/c/
    if path_str[1:3] == ':\\':
        drive = path_str[0].lower()
        rest = path_str[3:].replace('\\', '/')
        return f'/mnt/{drive}/{rest}'

    return path_str
```

### 5. Testing Strategy

**Unit Tests**:
- Configuration loading and merging
- Worker discovery logic
- Health check validation
- State file read/write
- Platform detection

**Integration Tests**:
- Worker lifecycle (start/stop) for both modes
- Worker reuse logic
- Database interaction
- State management

**E2E Tests**:
- Full `clm build` with auto-start workers
- `clm start-services` / `clm stop-services` workflow
- Mixed mode (some direct, some docker)
- Windows/Docker compatibility

**Test Fixtures**:
```python
@pytest.fixture
def worker_config():
    """Provide test worker configuration."""
    return WorkersManagementConfig(
        default_execution_mode="direct",
        default_worker_count=1,
        auto_start=True
    )


@pytest.fixture
def mock_state_file(tmp_path):
    """Provide temporary state file."""
    state_file = tmp_path / ".clm" / "worker-state.json"
    state_file.parent.mkdir(parents=True)
    return state_file


@pytest.fixture
def lifecycle_manager(worker_config, tmp_path):
    """Provide WorkerLifecycleManager for testing."""
    db_path = tmp_path / "test.db"
    init_database(db_path)

    return WorkerLifecycleManager(
        config=worker_config,
        db_path=db_path,
        workspace_path=tmp_path
    )
```

## Migration Strategy

### Phase 1: Configuration Infrastructure
- Add `WorkersManagementConfig` to configuration system
- Update TOML schema and example config
- Add CLI options for worker configuration
- Write tests for configuration loading

### Phase 2: WorkerLifecycleManager
- Implement `WorkerLifecycleManager` class
- Integrate with existing `WorkerPoolManager`
- Add worker discovery and health checking
- Write tests for lifecycle management

### Phase 3: Integration with `clm build`
- Update `clm build` to use `WorkerLifecycleManager`
- Add automatic worker startup/shutdown
- Add worker reuse logic
- Update documentation

### Phase 4: Persistent Workers
- Implement `WorkerStateManager`
- Add `clm start-services` command
- Add `clm stop-services` command
- Write state file read/write logic
- Write tests

### Phase 5: Worker Management Commands
- Add `clm workers list` command
- Add `clm workers cleanup` command
- Add proper table formatting
- Write tests

### Phase 6: Windows/Docker Improvements
- Add platform detection
- Improve database configuration for Docker
- Add path conversion utilities
- Add WSL accessibility checks
- Test on Windows with Docker Desktop

### Phase 7: Polish and Documentation
- Update all documentation
- Add troubleshooting guide
- Add examples for common scenarios
- Final testing and bug fixes

## Open Questions and Decisions

### Q1: State File Location

**Question**: Where should the worker state file be stored?

**Options**:
- A: `.clm/worker-state.json` (project-local) ✅
- B: `~/.config/clm/worker-state.json` (user-global)
- C: `/var/run/clm/workers.json` (system-global)

**Decision**: Option A (project-local) for isolation and simplicity.

**Rationale**: Different projects may use different databases and workers. Project-local state keeps things isolated and predictable.

### Q2: Multiple Persistent Worker Sessions

**Question**: Should we allow multiple `clm start-services` sessions?

**Options**:
- A: Only one session at a time (error if state exists) ✅
- B: Multiple sessions with unique IDs
- C: Merge with existing session

**Decision**: Option A (single session) for simplicity.

**Rationale**: Multiple sessions add complexity. Users can stop and restart if needed.

### Q3: Worker Image Pull Behavior

**Question**: Should Docker images be pulled automatically?

**Options**:
- A: Auto-pull if missing ✅
- B: Error if image not found
- C: Configurable with default auto-pull

**Decision**: Option A (auto-pull) with logging.

**Rationale**: Better user experience. Users can pre-pull if they want control.

### Q4: Managed vs. Persistent Detection

**Question**: How to determine if existing workers are managed or persistent?

**Options**:
- A: Check state file (if exists, persistent) ✅
- B: Add session tracking to database
- C: Tag workers in container_id field

**Decision**: Option A (state file check) initially.

**Rationale**: Simple and works. Can enhance with database tracking later if needed.

### Q5: Worker Count Validation

**Question**: Should we enforce minimum/maximum worker counts?

**Options**:
- A: No limits (trust configuration) ✅
- B: Warn if count > 10
- C: Hard limit at some number

**Decision**: Option A (no limits) with warning for high counts.

**Rationale**: Users may have good reasons for many workers. Warn but don't block.

## Performance Considerations

### 1. Worker Startup Time

**Issue**: Starting many workers can take time (especially Docker).

**Mitigation**:
- Start workers in parallel using thread pool
- Show progress indicators
- Allow proceeding with partial worker set (if configured)

```python
def start_workers_parallel(configs: List[WorkerConfig], max_workers: int = 5):
    """Start multiple workers in parallel."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(start_single_worker, config, index)
            for config in configs
            for index in range(config.count)
        ]

        results = []
        for future in as_completed(futures):
            try:
                result = future.result(timeout=30)
                results.append(result)
            except Exception as e:
                logger.error(f"Worker startup failed: {e}")

        return results
```

### 2. Database Contention

**Issue**: Many workers polling same database.

**Mitigation**:
- Use appropriate polling intervals (current: 0.1s)
- Use database connection pooling
- Optimize queries with proper indexes
- Use IMMEDIATE transactions for writes

### 3. Health Check Overhead

**Issue**: Health checks for many workers can be slow.

**Mitigation**:
- Cache health check results (TTL: 5 seconds)
- Perform health checks in background thread
- Only check when needed (before starting new workers)

## Security Considerations

### 1. State File Permissions

**Issue**: State file contains database path and worker IDs.

**Mitigation**:
```python
def save_state_file(state: WorkerState, path: Path):
    """Save state file with appropriate permissions."""
    # Write atomically
    temp_path = path.with_suffix('.tmp')
    with temp_path.open('w') as f:
        json.dump(state.dict(), f, indent=2)

    # Set permissions (user read/write only)
    temp_path.chmod(0o600)

    # Atomic rename
    temp_path.replace(path)
```

### 2. Docker Socket Access

**Issue**: Docker socket access grants significant privileges.

**Mitigation**:
- Document security implications
- Only access Docker when needed
- Don't expose Docker socket to workers
- Use least-privilege approach

### 3. Database Path Injection

**Issue**: User-provided database path could be malicious.

**Mitigation**:
```python
def validate_db_path(db_path: Path) -> Path:
    """Validate and normalize database path."""
    # Resolve to absolute path
    db_path = db_path.resolve()

    # Check it's a file (not directory or device)
    if db_path.exists() and not db_path.is_file():
        raise ValueError(f"Database path must be a file: {db_path}")

    # Check parent directory is writable
    if not db_path.parent.exists():
        raise ValueError(f"Database directory does not exist: {db_path.parent}")

    if not os.access(db_path.parent, os.W_OK):
        raise ValueError(f"Database directory not writable: {db_path.parent}")

    return db_path
```

## Summary

This design provides:

1. **Zero-configuration experience**: Works out of the box with sensible defaults
2. **Progressive configuration**: Users can configure as much or as little as needed
3. **Multiple deployment modes**: Managed (auto-start/stop) and persistent workers
4. **Platform compatibility**: Special handling for Windows/Docker scenarios
5. **Clear error messages**: Helpful guidance when things go wrong
6. **Maintainability**: Clean separation of concerns, testable components

The implementation can be done incrementally, maintaining backward compatibility throughout.
