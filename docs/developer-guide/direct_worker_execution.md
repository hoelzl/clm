# Direct Worker Execution

## Overview

The CLX worker system now supports running workers directly as Python subprocesses, in addition to the traditional Docker container mode. This provides a simpler execution model for development, testing, and environments where Docker is not available or desired.

## Key Features

- **No Docker Required**: Workers run as regular Python subprocesses
- **Faster Startup**: No container image building or pulling needed
- **Easier Debugging**: Direct access to worker processes for debugging
- **Mixed Mode**: Run some workers in Docker and others directly in the same pool
- **Same API**: Identical configuration and management interface

## Execution Modes

### Docker Mode (Default)

Workers run in isolated Docker containers:

```python
from clx_common.workers.worker_executor import WorkerConfig

config = WorkerConfig(
    worker_type='notebook',
    count=2,
    execution_mode='docker',  # Default
    image='mhoelzl/clx-notebook-processor:0.2.2',
    memory_limit='1g'
)
```

**Advantages:**
- Process isolation
- Resource limits (memory, CPU)
- Consistent environment
- Production-ready

**Requirements:**
- Docker installed and running
- Worker images built/available

### Direct Mode

Workers run as Python subprocesses:

```python
from clx_common.workers.worker_executor import WorkerConfig

config = WorkerConfig(
    worker_type='notebook',
    count=2,
    execution_mode='direct'
)
```

**Advantages:**
- No Docker required
- Faster startup
- Easier debugging
- Simpler for development

**Requirements:**
- Worker dependencies installed locally
- Python modules accessible

## Usage Examples

### Basic Direct Worker

```python
from pathlib import Path
from clx_common.database.schema import init_database
from clx_common.workers.pool_manager import WorkerPoolManager
from clx_common.workers.worker_executor import WorkerConfig

# Setup
db_path = Path("./jobs.db")
workspace_path = Path("./workspace")
init_database(db_path)

# Configure direct workers
worker_configs = [
    WorkerConfig(
        worker_type='notebook',
        count=2,
        execution_mode='direct'
    )
]

# Start workers
manager = WorkerPoolManager(
    db_path=db_path,
    workspace_path=workspace_path,
    worker_configs=worker_configs
)

manager.start_pools()
manager.start_monitoring()

# Workers are now running and will process jobs from the queue
```

### Mixed Mode

Run some workers in Docker and others directly:

```python
worker_configs = [
    # Docker worker
    WorkerConfig(
        worker_type='notebook',
        count=1,
        execution_mode='docker',
        image='mhoelzl/clx-notebook-processor:0.2.2',
        memory_limit='1g'
    ),
    # Direct workers
    WorkerConfig(
        worker_type='drawio',
        count=2,
        execution_mode='direct'
    ),
    WorkerConfig(
        worker_type='plantuml',
        count=1,
        execution_mode='direct'
    )
]

manager = WorkerPoolManager(
    db_path=db_path,
    workspace_path=workspace_path,
    worker_configs=worker_configs
)

manager.start_pools()
```

### Development Setup

For local development without Docker:

```python
# All workers in direct mode
worker_configs = [
    WorkerConfig(worker_type='notebook', count=1, execution_mode='direct'),
    WorkerConfig(worker_type='drawio', count=1, execution_mode='direct'),
    WorkerConfig(worker_type='plantuml', count=1, execution_mode='direct')
]

manager = WorkerPoolManager(
    db_path=Path("./dev.db"),
    workspace_path=Path("./workspace"),
    worker_configs=worker_configs,
    log_level='DEBUG'  # More verbose logging for development
)
```

## Architecture

### Worker Executor Abstraction

The system uses an abstract `WorkerExecutor` interface with two implementations:

```
WorkerExecutor (ABC)
├── DockerWorkerExecutor
│   └── Manages containers via Docker API
└── DirectWorkerExecutor
    └── Manages subprocesses via Python subprocess
```

### Worker Identification

Workers identify themselves differently based on execution mode:

- **Docker Mode**: Uses container hostname (container ID)
- **Direct Mode**: Uses generated ID format: `direct-{type}-{index}-{uuid}`

Both modes register in the same `workers` database table using the `container_id` field.

### Process Management

**Docker Mode:**
- Uses Docker API for start/stop/monitoring
- Container lifecycle managed by Docker daemon
- Resource limits enforced by Docker

**Direct Mode:**
- Uses Python `subprocess.Popen`
- Process groups for clean shutdown
- Graceful termination (SIGTERM → SIGKILL)
- No resource limits (relies on OS)

## Configuration

### Required Environment Variables

Workers require these environment variables (automatically set by the pool manager):

- `WORKER_TYPE`: Type of worker (notebook, drawio, plantuml)
- `WORKER_ID` or `HOSTNAME`: Unique worker identifier
- `DB_PATH`: Path to SQLite database
- `WORKSPACE_PATH`: Path to workspace directory
- `LOG_LEVEL`: Logging verbosity
- `USE_SQLITE_QUEUE`: Enable SQLite queue mode

### WorkerConfig Parameters

```python
@dataclass
class WorkerConfig:
    worker_type: str           # Required: 'notebook', 'drawio', 'plantuml'
    count: int                 # Required: Number of workers
    execution_mode: str = 'docker'  # 'docker' or 'direct'
    image: Optional[str] = None     # Required for docker mode
    memory_limit: str = '1g'        # Docker only
    max_job_time: int = 600         # Job timeout in seconds
```

## Health Monitoring

Health monitoring works for both execution modes:

- **Heartbeat Tracking**: Workers update database every poll cycle
- **Process/Container Checks**: Pool manager verifies worker is running
- **CPU Monitoring**: Available for Docker mode only
- **Automatic Recovery**: Dead/hung workers can be detected and restarted

Example monitoring:

```python
manager.start_monitoring(check_interval=10)  # Check every 10 seconds

# Get worker statistics
stats = manager.get_worker_stats()
# Returns: {'notebook': {'idle': 2, 'busy': 0}, ...}
```

## Testing

### Unit Tests

Test individual executor implementations:

```bash
pytest clx-common/tests/workers/test_worker_executor.py
```

### Integration Tests

Test full worker lifecycle with actual jobs:

```bash
pytest clx-common/tests/workers/test_direct_integration.py -m integration
```

## Debugging

### Direct Mode Advantages

Direct workers are easier to debug:

1. **Attach Debugger**: Use standard Python debugging tools
2. **See Output**: stdout/stderr directly visible
3. **Process Inspection**: Use OS tools (ps, top, etc.)
4. **No Container Overhead**: Simpler process model

### Example: Running with Debugger

```python
# In your debugger, set breakpoints in worker code
# Start pool manager in direct mode
# Workers will run as child processes and hit breakpoints
```

### Logging

Enable debug logging to see worker lifecycle:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

manager = WorkerPoolManager(
    db_path=db_path,
    workspace_path=workspace_path,
    worker_configs=worker_configs,
    log_level='DEBUG'
)
```

## Migration Guide

### From Docker to Direct Mode

1. **Install Dependencies**: Ensure all worker dependencies are installed locally
2. **Update Configuration**: Change `execution_mode` to `'direct'`
3. **Remove Docker Settings**: No need for `image` or `memory_limit`
4. **Test**: Verify workers start and process jobs correctly

Example:

```python
# Before (Docker)
WorkerConfig(
    worker_type='notebook',
    count=2,
    execution_mode='docker',
    image='mhoelzl/clx-notebook-processor:0.2.2',
    memory_limit='1g'
)

# After (Direct)
WorkerConfig(
    worker_type='notebook',
    count=2,
    execution_mode='direct'
)
```

### Gradual Migration

Use mixed mode to migrate incrementally:

```python
worker_configs = [
    # Keep existing Docker workers
    WorkerConfig(
        worker_type='notebook',
        count=1,
        execution_mode='docker',
        image='mhoelzl/clx-notebook-processor:0.2.2'
    ),
    # Add new direct workers
    WorkerConfig(
        worker_type='notebook',
        count=1,
        execution_mode='direct'
    )
]
```

## Best Practices

### When to Use Direct Mode

✅ **Good for:**
- Local development
- Testing and debugging
- CI/CD pipelines
- Environments without Docker
- Simple deployments

❌ **Not recommended for:**
- Production deployments (use Docker)
- Untrusted code execution
- Resource-constrained environments
- Multi-tenant systems

### Performance Considerations

- **Startup**: Direct mode is faster (no container overhead)
- **Runtime**: Similar performance for both modes
- **Memory**: Docker provides better isolation and limits
- **Debugging**: Direct mode is easier to debug

### Security

**Docker Mode:**
- Process isolation via containers
- Limited filesystem access
- Network isolation
- Resource quotas

**Direct Mode:**
- No process isolation
- Full filesystem access
- No resource limits
- **Use only in trusted environments**

## Troubleshooting

### Worker Won't Start

**Direct Mode:**
1. Check Python module is installed
2. Verify DB_PATH is accessible
3. Check logs for import errors
4. Ensure PYTHONPATH includes worker modules

**Docker Mode:**
1. Check Docker is running
2. Verify image exists
3. Check container logs
4. Verify network configuration

### Worker Not Registering

Check database for worker record:

```sql
SELECT * FROM workers WHERE container_id LIKE 'direct-%';
```

Verify environment variables are set:
- `WORKER_ID` (direct mode)
- `HOSTNAME` (docker mode)

### Performance Issues

**Direct Mode:**
- Check system resources (CPU, memory)
- Monitor process with OS tools
- Review worker logs

**Docker Mode:**
- Check container stats
- Review memory limits
- Monitor Docker daemon

## Examples

See [examples/direct_worker_example.py](../examples/direct_worker_example.py) for complete working examples.

## API Reference

### WorkerExecutor

Abstract base class for worker executors.

```python
class WorkerExecutor(ABC):
    @abstractmethod
    def start_worker(self, worker_type: str, index: int, config: WorkerConfig) -> Optional[str]:
        """Start a worker and return its unique identifier."""

    @abstractmethod
    def stop_worker(self, worker_id: str) -> bool:
        """Stop a specific worker."""

    @abstractmethod
    def is_worker_running(self, worker_id: str) -> bool:
        """Check if a worker is currently running."""

    @abstractmethod
    def get_worker_stats(self, worker_id: str) -> Optional[Dict]:
        """Get resource usage statistics for a worker."""

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up all workers managed by this executor."""
```

### DirectWorkerExecutor

```python
class DirectWorkerExecutor(WorkerExecutor):
    def __init__(
        self,
        db_path: Path,
        workspace_path: Path,
        log_level: str = 'INFO'
    ):
        """Initialize direct process executor."""
```

### DockerWorkerExecutor

```python
class DockerWorkerExecutor(WorkerExecutor):
    def __init__(
        self,
        docker_client: docker.DockerClient,
        db_path: Path,
        workspace_path: Path,
        network_name: str = 'clx_app-network',
        log_level: str = 'INFO'
    ):
        """Initialize Docker executor."""
```

## Future Enhancements

Potential improvements:

1. **Resource Limits**: Add resource limiting for direct mode using OS features
2. **Process Monitoring**: Better CPU/memory stats for direct workers
3. **Auto-Selection**: Automatically choose execution mode based on environment
4. **Configuration Files**: Support for YAML/JSON worker configuration
5. **Worker Pools**: Support for dynamic scaling of worker pools

## Contributing

When adding new worker types, ensure they support both execution modes by:

1. Using `WORKER_ID` or `HOSTNAME` for registration
2. Reading configuration from environment variables
3. Testing in both Docker and direct modes
4. Documenting any mode-specific requirements
