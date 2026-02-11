# Worker Lifecycle Management

CLM provides comprehensive worker lifecycle management capabilities for automatic worker management during builds.

## Features

### Automatic Worker Management (Default)

When running `clm build`, workers are automatically started and stopped:

```bash
clm build course.yaml
```

- Workers start automatically based on configuration
- Workers are reused if already running
- Workers stop automatically after build completes

### Worker Inspection

List and manage workers:

```bash
# List all workers
clm workers list

# List only idle workers
clm workers list --status=idle

# Output as JSON
clm workers list --format=json

# Clean up dead/hung workers
clm workers cleanup

# Force cleanup of all workers
clm workers cleanup --all --force
```

## Configuration

### Basic Configuration

Create a configuration file:

```bash
clm config init --location=project
```

Edit `.clm/config.toml`:

```toml
[worker_management]
# Execution mode: "direct" or "docker"
default_execution_mode = "direct"

# Number of workers per type
default_worker_count = 1

# Automatically start/stop workers with clm build
auto_start = true
auto_stop = true

# Reuse existing healthy workers
reuse_workers = true

# Worker startup settings
startup_timeout = 30
startup_parallel = 5
```

### Per-Worker-Type Configuration

Configure specific worker types:

```toml
[worker_management.notebook]
execution_mode = "docker"
count = 2
memory_limit = "1g"
max_job_time = 600

[worker_management.plantuml]
execution_mode = "direct"
count = 1
memory_limit = "512m"
max_job_time = 300

[worker_management.drawio]
execution_mode = "direct"
count = 1
memory_limit = "512m"
max_job_time = 300
```

### Environment Variables

Override configuration with environment variables:

```bash
# Set execution mode
export CLM_WORKER_MANAGEMENT__DEFAULT_EXECUTION_MODE=docker

# Set worker count
export CLM_WORKER_MANAGEMENT__DEFAULT_WORKER_COUNT=3

# Disable auto-start
export CLM_WORKER_MANAGEMENT__AUTO_START=false

# Build
clm build course.yaml
```

## Execution Modes

### Direct Execution Mode

Workers run as direct processes on the host:

**Advantages:**
- Faster startup
- Lower overhead
- Direct access to host filesystem
- Easier debugging

**Requirements:**
- PlantUML JAR file installed
- Draw.io desktop application installed
- Xvfb for headless Draw.io rendering

**Configuration:**
```toml
[worker_management]
default_execution_mode = "direct"
```

### Docker Execution Mode

Workers run in Docker containers:

**Advantages:**
- Isolated execution
- No host dependencies
- Reproducible environment
- Production-ready

**Requirements:**
- Docker daemon running
- Docker images built or available

**Configuration:**
```toml
[worker_management]
default_execution_mode = "docker"

[worker_management.notebook]
image = "mhoelzl/clm-notebook-processor:0.5.0"

[worker_management.plantuml]
image = "mhoelzl/clm-plantuml-converter:0.5.0"

[worker_management.drawio]
image = "mhoelzl/clm-drawio-converter:0.5.0"
```

## Architecture

### Components

1. **WorkerLifecycleManager** - High-level orchestration
2. **WorkerPoolManager** - Pool management and worker startup
3. **WorkerEventLogger** - Lifecycle event logging
4. **WorkerDiscovery** - Health checking and discovery

### Database Schema

Worker lifecycle events are logged to the `worker_events` table:

```sql
SELECT * FROM worker_events
WHERE event_type IN ('worker_starting', 'worker_registered', 'worker_ready')
ORDER BY created_at DESC;
```

Worker information is stored in the `workers` table with extended metadata.

### Event Types

- `worker_starting` - Worker process/container starting
- `worker_registered` - Worker successfully registered in DB
- `worker_ready` - Worker ready to accept jobs
- `worker_stopping` - Worker shutdown initiated
- `worker_stopped` - Worker successfully stopped
- `worker_failed` - Worker failed to start or crashed
- `pool_starting` - Worker pool starting
- `pool_started` - Worker pool fully started
- `pool_stopping` - Worker pool shutdown initiated
- `pool_stopped` - Worker pool fully stopped

### Worker Pre-Registration

Workers use a pre-registration mechanism to eliminate startup delays. Instead of waiting for each worker subprocess to self-register (which could take 2-10 seconds due to Python startup and module imports), the parent process pre-registers workers in the database.

**How it works:**

1. **Pre-registration**: Parent process inserts worker row with `status='created'` and a UUID
2. **Subprocess start**: Worker subprocess receives the pre-assigned `CLM_WORKER_ID` via environment variable
3. **Activation**: Worker updates its status from `created` to `idle` when ready to accept jobs
4. **No blocking wait**: Parent proceeds immediately after starting subprocesses

**Worker Status Values:**

| Status | Description |
|--------|-------------|
| `created` | Pre-registered by parent, subprocess not yet ready |
| `idle` | Worker ready to accept jobs |
| `busy` | Worker currently processing a job |
| `hung` | Worker not responding (detected via heartbeat) |
| `dead` | Worker terminated |

**Stuck Worker Cleanup:**

Workers stuck in `created` status for more than 30 seconds are automatically cleaned up. This handles cases where:
- The subprocess failed to start
- The subprocess crashed before activating
- The parent process died before starting the subprocess

The cleanup also detects orphaned workers by checking if the parent process (tracked via `parent_pid`) is still alive.

**Job Submission Behavior:**

When jobs are submitted, the `SqliteBackend` intelligently handles the case where workers are pre-registered but not yet activated:

1. First checks for activated workers (status='idle' or 'busy')
2. If none found, checks for pre-registered workers (status='created')
3. If pre-registered workers exist, waits up to 30 seconds for them to activate
4. Only raises "No workers available" error if no workers exist or timeout is exceeded

This ensures seamless operation even when jobs are submitted immediately after worker processes are started.

## Advanced Usage

### Custom Worker Counts

```bash
# Start 2 notebook workers, 1 each for PlantUML and Draw.io
clm build course.yaml \
  --workers=docker \
  --notebook-workers=2 \
  --plantuml-workers=1 \
  --drawio-workers=1
```

### Disable Auto-Management

```bash
# Don't auto-start workers (use existing)
clm build course.yaml --no-auto-start

# Don't auto-stop workers (keep running)
clm build course.yaml --no-auto-stop

# Start fresh workers (don't reuse)
clm build course.yaml --fresh-workers
```

## Troubleshooting

### Workers Not Starting

Check logs and worker status:

```bash
# List workers
clm workers list

# Check for dead/hung workers
clm workers cleanup

# Force fresh workers
clm build course.yaml --fresh-workers
```

### Stale Workers

Clean up orphaned workers:

```bash
# Clean up dead/hung workers
clm workers cleanup

# Force cleanup all workers
clm workers cleanup --all --force
```

### Database Issues

Reset database if needed:

```bash
# Delete database
clm delete-database

# Rebuild
clm build course.yaml
```

## See Also

- [Configuration Guide](configuration.md)
- [Testing Guide](testing.md)
- [Direct Worker Execution](direct_worker_execution.md)
- [Architecture Overview](architecture.md)
