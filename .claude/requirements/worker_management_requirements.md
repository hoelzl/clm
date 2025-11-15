# Worker Management Requirements

**Version**: 1.0
**Date**: 2025-11-15
**Status**: Draft

## Executive Summary

This document specifies requirements for improving CLX's worker management system to provide a better user experience while maintaining flexibility for advanced use cases. The goal is to make worker management automatic and invisible for most users, while supporting persistent worker deployments for production scenarios.

## Background

### Current State

The CLX system currently supports two worker execution modes:

1. **Direct Mode**: Workers run as subprocesses on the host machine
   - Requires external tools installed locally (PlantUML JAR, Draw.io executable)
   - Fast startup, no Docker overhead
   - Shares filesystem and memory directly with the main process

2. **Docker Mode**: Workers run in Docker containers
   - Self-contained, all dependencies bundled
   - Isolated execution environment
   - Requires Docker daemon and network setup

Currently, users must manually manage workers using `WorkerPoolManager` or rely on integration test fixtures. There's no built-in CLI support for starting/stopping workers, and no configuration-based worker management.

### Pain Points

1. **Manual Worker Management**: Users must write Python code to start workers
2. **No Configuration Support**: Worker settings cannot be configured declaratively
3. **No Persistent Workers**: All workers must be managed by the calling program
4. **Windows/Docker Complexity**: SQLite shared memory issues across WSL boundary
5. **Database Location Coupling**: docker-compose.yaml hardcodes database path

## Requirements

### 1. Automatic Worker Lifecycle Management

**REQ-1.1**: The system SHALL automatically start workers when `clx build` command runs.

**REQ-1.2**: The system SHALL automatically stop workers when `clx build` command exits (normally or via signal).

**REQ-1.3**: Worker lifecycle SHALL be configurable via configuration files (project, user, system).

**REQ-1.4**: Command-line options SHALL override configuration file settings.

**REQ-1.5**: If neither configuration nor CLI options specify settings, the system SHALL use sensible defaults:
- Default execution mode: `direct`
- Default worker count per type: `1`

### 2. Worker Configuration

**REQ-2.1**: Configuration files SHALL support a `[workers]` section with the following structure:

```toml
[workers]
# Global default execution mode: "direct" or "docker"
default_execution_mode = "direct"

# Global default worker count (applies to all worker types unless overridden)
default_worker_count = 1

# Per-worker-type configuration
[workers.notebook]
execution_mode = "direct"  # Optional: override global default
count = 2                   # Optional: override global default
image = "mhoelzl/clx-notebook-processor:0.3.0"  # Required for docker mode

[workers.plantuml]
execution_mode = "docker"
count = 1
image = "mhoelzl/clx-plantuml-converter:0.3.0"

[workers.drawio]
execution_mode = "direct"
count = 1
```

**REQ-2.2**: The system SHALL validate that Docker-mode workers have an `image` specified.

**REQ-2.3**: The system SHALL support environment variable overrides:
- `CLX_WORKERS__DEFAULT_EXECUTION_MODE`: Override default execution mode
- `CLX_WORKERS__DEFAULT_WORKER_COUNT`: Override default worker count
- `CLX_WORKERS__NOTEBOOK__EXECUTION_MODE`: Override notebook worker execution mode
- `CLX_WORKERS__NOTEBOOK__COUNT`: Override notebook worker count
- Similar patterns for other worker types

**REQ-2.4**: Command-line options SHALL provide runtime overrides:
```bash
clx build course.yaml --workers=direct          # Use direct workers
clx build course.yaml --workers=docker          # Use docker workers
clx build course.yaml --worker-count=2          # Start 2 workers per type
clx build course.yaml --notebook-workers=3      # Start 3 notebook workers
```

### 3. Persistent Workers

**REQ-3.1**: The system SHALL support persistent workers that are started independently and not managed by `clx build`.

**REQ-3.2**: The system SHALL provide `clx start-services` command to start persistent workers.

**REQ-3.3**: The system SHALL provide `clx stop-services` command to stop persistent workers.

**REQ-3.4**: The `clx start-services` command SHALL:
- Start workers based on configuration
- Register workers in the database
- Support both direct and docker modes
- Return immediately after workers are registered (not wait for completion)

**REQ-3.5**: The `clx stop-services` command SHALL:
- Stop all registered workers
- Clean up database entries
- Remove Docker containers if applicable
- Handle graceful shutdown with timeouts

**REQ-3.6**: The `clx build` command SHALL detect existing registered workers and:
- Skip starting new workers if sufficient workers are already running
- Report which workers are being used
- Optionally: Allow `--force-new-workers` flag to start fresh workers

**REQ-3.7**: Persistent workers SHALL continue running after `clx build` exits.

### 4. Windows/Docker Database Handling

**REQ-4.1**: When using Docker workers on Windows, the system SHALL use DELETE journal mode (not WAL) for SQLite to avoid shared memory issues.

**REQ-4.2**: The system SHALL mount the database directory (not file) to Docker containers for Windows compatibility.

**REQ-4.3**: The `clx start-services` command SHALL:
- Accept `--db-path` option to specify database location
- Pass database path to Docker containers via volume mount and environment variable
- Store database configuration for `clx stop-services` to reference

**REQ-4.4**: For Docker workers, the database path SHALL be:
- Configurable at service start time
- Stored in a state file (e.g., `.clx/worker-state.json`) for cleanup
- Validated to ensure it's accessible from both host and containers

**REQ-4.5**: The system SHALL detect Windows/WSL environment and automatically:
- Use DELETE journal mode instead of WAL
- Warn users if database path is not in a location accessible to WSL containers

### 5. Service Discovery and Registration

**REQ-5.1**: Workers SHALL self-register in the database upon startup with:
- Worker type
- Execution mode (docker or direct)
- Container ID or process ID
- Startup timestamp
- Initial status (idle)

**REQ-5.2**: The system SHALL query the database to discover available workers before submitting jobs.

**REQ-5.3**: The system SHALL provide `clx workers list` command to show:
- All registered workers
- Worker status (idle, busy, hung, dead)
- Execution mode
- Jobs processed
- Uptime

**REQ-5.4**: The system SHALL provide `clx workers cleanup` command to:
- Remove dead worker records
- Stop orphaned containers
- Kill orphaned processes

### 6. Backward Compatibility

**REQ-6.1**: Existing `WorkerPoolManager` API SHALL continue to work.

**REQ-6.2**: Existing test fixtures SHALL continue to work without modification.

**REQ-6.3**: Configuration files are OPTIONAL - the system SHALL work with defaults if no configuration exists.

**REQ-6.4**: Existing docker-compose.yaml SHALL continue to work for manual docker deployments.

### 7. Error Handling

**REQ-7.1**: If no workers are available when submitting jobs, the system SHALL:
- Attempt to auto-start workers if configured
- Fail with clear error message if auto-start is disabled
- Suggest commands to start workers manually

**REQ-7.2**: If worker startup fails, the system SHALL:
- Log detailed error information
- Retry up to N times (configurable, default 3)
- Fail gracefully and clean up partial state

**REQ-7.3**: If database is locked or inaccessible, the system SHALL:
- Retry with exponential backoff
- Fail with clear error message after timeout
- Provide diagnostic information (file permissions, path, etc.)

**REQ-7.4**: The system SHALL detect and warn about:
- Missing external tools for direct mode (PlantUML, Draw.io)
- Missing Docker daemon for docker mode
- Insufficient resources (memory, disk space)

### 8. Logging and Observability

**REQ-8.1**: Worker startup SHALL be logged with:
- Worker type
- Execution mode
- Worker ID
- Configuration source (file, env, default)

**REQ-8.2**: Worker shutdown SHALL be logged with:
- Worker ID
- Reason for shutdown
- Jobs processed count
- Uptime

**REQ-8.3**: The system SHALL provide `--verbose` flag for detailed worker management logging.

**REQ-8.4**: Progress updates SHALL include worker status:
- Number of active workers per type
- Queue depth per worker type
- Average job processing time

## Non-Requirements

**NR-1**: This system does NOT aim to support distributed worker deployments across multiple machines.

**NR-2**: This system does NOT aim to support worker auto-scaling based on load.

**NR-3**: This system does NOT aim to support worker resource limits configuration (memory, CPU) beyond Docker's built-in limits.

**NR-4**: This system does NOT aim to support worker plugins or dynamic worker type registration.

## Success Criteria

1. **Zero-Configuration Experience**: User runs `clx build course.yaml` and it "just works" without any worker setup.

2. **Flexible Configuration**: Advanced users can configure worker modes, counts, and persistence via config files.

3. **Production Ready**: Users can deploy persistent workers with `clx start-services` for long-running server scenarios.

4. **Windows Compatible**: System works seamlessly on Windows with Docker Desktop and WSL2.

5. **Clear Error Messages**: When things go wrong, users get actionable error messages.

## Open Questions

1. **State Management**: Where should persistent worker state be stored?
   - Option A: `.clx/worker-state.json` (project-local)
   - Option B: User config directory (`~/.config/clx/workers.json`)
   - Option C: SQLite database (new table)
   - **Recommendation**: Option A for project isolation

2. **Docker Network**: Should `clx start-services` create/manage Docker network?
   - Option A: Always create `clx_app-network` if using Docker
   - Option B: Allow network name configuration
   - **Recommendation**: Option A with configurable name

3. **Worker Reuse**: How aggressive should worker reuse be?
   - Option A: Always reuse existing workers if available
   - Option B: Reuse only if explicitly configured
   - Option C: Check worker health before reusing
   - **Recommendation**: Option C (health check + reuse)

4. **Graceful Degradation**: What if only some worker types are available?
   - Option A: Fail if any worker type is missing
   - Option B: Process what we can, skip rest
   - Option C: Configurable behavior
   - **Recommendation**: Option A (fail fast) with clear error message

5. **Docker Image Management**: Should the system auto-pull images?
   - Option A: Auto-pull missing images
   - Option B: Fail if image not available
   - Option C: Configurable behavior
   - **Recommendation**: Option A with opt-out flag

## Dependencies

- Existing `WorkerPoolManager` and executor classes
- Existing configuration system (`clx.infrastructure.config`)
- Existing database schema (may need extensions)
- Click CLI framework
- Docker SDK for Python (already dependency)

## Timeline

- **Phase 1**: Configuration schema and parsing (1-2 days)
- **Phase 2**: Automatic worker lifecycle in `clx build` (2-3 days)
- **Phase 3**: Persistent workers (`clx start-services`, `clx stop-services`) (3-4 days)
- **Phase 4**: Windows/Docker database handling improvements (2-3 days)
- **Phase 5**: Worker discovery commands (`clx workers list`, `clx workers cleanup`) (1-2 days)
- **Phase 6**: Testing, documentation, polish (2-3 days)

**Total Estimate**: 11-17 days

## Risks

1. **SQLite Locking**: Database contention with many workers
   - Mitigation: Use connection pooling, retry logic, optimize queries

2. **Docker on Windows**: Path translation and permissions issues
   - Mitigation: Extensive testing on Windows, clear documentation

3. **Breaking Changes**: Configuration changes might break existing setups
   - Mitigation: Strong backward compatibility, migration guide

4. **Complexity**: Too many configuration options confuse users
   - Mitigation: Good defaults, progressive disclosure, examples

## Appendix A: Example Configuration Files

### Minimal Configuration (Defaults)

```toml
# .clx/config.toml - Use all defaults
[workers]
# All defaults:
# - Direct execution mode
# - 1 worker per type
# - Auto-start/stop with clx build
```

### Development Configuration

```toml
# .clx/config.toml - Fast local development
[workers]
default_execution_mode = "direct"
default_worker_count = 1

[workers.notebook]
count = 2  # More parallelism for notebooks
```

### Production Configuration

```toml
# .clx/config.toml - Production server
[workers]
default_execution_mode = "docker"
default_worker_count = 2

[workers.notebook]
count = 4
image = "mhoelzl/clx-notebook-processor:0.3.0"

[workers.plantuml]
count = 2
image = "mhoelzl/clx-plantuml-converter:0.3.0"

[workers.drawio]
count = 2
image = "mhoelzl/clx-drawio-converter:0.3.0"
```

### Mixed-Mode Configuration

```toml
# .clx/config.toml - Hybrid approach
[workers]
default_execution_mode = "direct"

# Use Docker only for draw.io (requires display server)
[workers.drawio]
execution_mode = "docker"
count = 1
image = "mhoelzl/clx-drawio-converter:0.3.0"
```

## Appendix B: Example CLI Usage

```bash
# Simple case: just works with defaults
clx build course.yaml

# Use Docker workers instead of direct
clx build course.yaml --workers=docker

# Start persistent workers for server deployment
clx start-services --db-path=/data/clx_jobs.db

# Process course using persistent workers
clx build course.yaml --db-path=/data/clx_jobs.db

# List running workers
clx workers list

# Clean up workers when done
clx stop-services --db-path=/data/clx_jobs.db

# Or use workers cleanup for force cleanup
clx workers cleanup
```
