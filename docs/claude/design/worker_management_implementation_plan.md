# Worker Management Implementation Plan

**Version**: 1.0
**Date**: 2025-11-15
**Status**: Draft

## Overview

This document provides a detailed, step-by-step implementation plan for the worker management system described in `worker_management_design.md`. Each phase includes specific tasks, file changes, testing requirements, and potential pitfalls.

## Phase 1: Configuration Infrastructure (2-3 days)

### Tasks

#### 1.1: Extend Configuration Schema

**File**: `src/clm/infrastructure/config.py`

**Changes**:
```python
# Add new configuration models

class WorkerTypeConfig(BaseModel):
    """Configuration for a specific worker type."""
    execution_mode: Optional[Literal["direct", "docker"]] = None
    count: Optional[int] = Field(None, ge=1, le=20)  # Validate range
    image: Optional[str] = None
    memory_limit: str = "1g"
    max_job_time: int = Field(600, ge=10, le=3600)

    @model_validator(mode='after')
    def validate_docker_image(self) -> 'WorkerTypeConfig':
        """Validate that docker mode has image specified."""
        if self.execution_mode == 'docker' and not self.image:
            raise ValueError("Docker execution mode requires 'image' to be specified")
        return self


class WorkersManagementConfig(BaseModel):
    """Worker management configuration."""
    # Global defaults
    default_execution_mode: Literal["direct", "docker"] = "direct"
    default_worker_count: int = Field(1, ge=1, le=20)
    auto_start: bool = True
    auto_stop: bool = True
    reuse_workers: bool = True

    # Network configuration (Docker)
    network_name: str = "clm_app-network"

    # Worker startup
    startup_timeout: int = 30  # seconds to wait for registration
    startup_parallel: int = 5   # number of workers to start in parallel

    # Per-type configurations
    notebook: WorkerTypeConfig = Field(default_factory=WorkerTypeConfig)
    plantuml: WorkerTypeConfig = Field(default_factory=WorkerTypeConfig)
    drawio: WorkerTypeConfig = Field(default_factory=WorkerTypeConfig)

    def get_worker_config(self, worker_type: str) -> WorkerConfig:
        """Get effective configuration for a worker type.

        Merges per-type config with global defaults.
        """
        if worker_type not in ('notebook', 'plantuml', 'drawio'):
            raise ValueError(f"Unknown worker type: {worker_type}")

        type_config = getattr(self, worker_type)

        # Determine effective values
        execution_mode = type_config.execution_mode or self.default_execution_mode
        count = type_config.count if type_config.count is not None else self.default_worker_count

        # Validate docker mode has image
        if execution_mode == 'docker' and not type_config.image:
            # Try to use default image
            default_images = {
                'notebook': 'mhoelzl/clm-notebook-processor:0.3.0',
                'plantuml': 'mhoelzl/clm-plantuml-converter:0.3.0',
                'drawio': 'mhoelzl/clm-drawio-converter:0.3.0'
            }
            image = default_images.get(worker_type)
            if not image:
                raise ValueError(
                    f"Docker mode for {worker_type} requires image specification"
                )
        else:
            image = type_config.image

        return WorkerConfig(
            worker_type=worker_type,
            execution_mode=execution_mode,
            count=count,
            image=image,
            memory_limit=type_config.memory_limit,
            max_job_time=type_config.max_job_time
        )

    def get_all_worker_configs(self) -> List[WorkerConfig]:
        """Get configurations for all worker types."""
        return [
            self.get_worker_config('notebook'),
            self.get_worker_config('plantuml'),
            self.get_worker_config('drawio')
        ]


# Add to ClmConfig
class ClmConfig(BaseSettings):
    # ... existing fields ...

    worker_management: WorkersManagementConfig = Field(
        default_factory=WorkersManagementConfig,
        description="Worker management configuration"
    )
```

**Update**: `create_example_config()` function to include worker management section:

```python
def create_example_config() -> str:
    """Create an example configuration file content."""
    return """# CLM Configuration File
# ... existing content ...

[worker_management]
# Worker lifecycle management configuration

# Global defaults
default_execution_mode = "direct"  # or "docker"
default_worker_count = 1
auto_start = true   # Auto-start workers with 'clm build'
auto_stop = true    # Auto-stop workers after 'clm build'
reuse_workers = true  # Reuse existing healthy workers

# Docker network name
network_name = "clm_app-network"

# Worker startup settings
startup_timeout = 30  # seconds to wait for worker registration
startup_parallel = 5  # number of workers to start in parallel

# Per-worker-type configuration
[worker_management.notebook]
# execution_mode = "direct"  # Override global default
# count = 2                   # Override global default
# image = "mhoelzl/clm-notebook-processor:0.3.0"  # Required for docker mode
memory_limit = "1g"
max_job_time = 600

[worker_management.plantuml]
# execution_mode = "docker"
# count = 1
# image = "mhoelzl/clm-plantuml-converter:0.3.0"
memory_limit = "512m"
max_job_time = 300

[worker_management.drawio]
# execution_mode = "direct"
# count = 1
# image = "mhoelzl/clm-drawio-converter:0.3.0"
memory_limit = "512m"
max_job_time = 300
"""
```

#### 1.2: Add CLI Options

**File**: `src/clm/cli/main.py`

**Changes**:
```python
@cli.command()
@click.argument(...)
# ... existing options ...
@click.option(
    '--workers',
    type=click.Choice(['direct', 'docker'], case_sensitive=False),
    help='Worker execution mode (overrides config)'
)
@click.option(
    '--worker-count',
    type=int,
    help='Number of workers per type (overrides config)'
)
@click.option(
    '--notebook-workers',
    type=int,
    help='Number of notebook workers (overrides config and --worker-count)'
)
@click.option(
    '--plantuml-workers',
    type=int,
    help='Number of PlantUML workers'
)
@click.option(
    '--drawio-workers',
    type=int,
    help='Number of Draw.io workers'
)
@click.option(
    '--no-auto-start',
    is_flag=True,
    help='Disable automatic worker startup'
)
@click.option(
    '--no-auto-stop',
    is_flag=True,
    help='Keep workers running after build completes'
)
@click.option(
    '--fresh-workers',
    is_flag=True,
    help='Start fresh workers (don\'t reuse existing)'
)
@click.pass_context
def build(ctx, ..., workers, worker_count, notebook_workers, plantuml_workers,
          drawio_workers, no_auto_start, no_auto_stop, fresh_workers):
    """Build course from specification file."""
    # Collect CLI overrides
    cli_overrides = {
        'workers': workers,
        'worker_count': worker_count,
        'notebook_workers': notebook_workers,
        'plantuml_workers': plantuml_workers,
        'drawio_workers': drawio_workers,
        'no_auto_start': no_auto_start,
        'no_auto_stop': no_auto_stop,
        'fresh_workers': fresh_workers
    }

    # Pass to main function
    asyncio.run(main(ctx, spec_file, ..., cli_overrides))
```

#### 1.3: Configuration Loading Helper

**New File**: `src/clm/infrastructure/workers/config_loader.py`

```python
"""Configuration loading utilities for worker management."""

import logging
from typing import Dict, Any, Optional
from clm.infrastructure.config import get_config, WorkersManagementConfig

logger = logging.getLogger(__name__)


def load_worker_config(cli_overrides: Optional[Dict[str, Any]] = None) -> WorkersManagementConfig:
    """Load worker configuration from all sources with CLI overrides.

    Args:
        cli_overrides: Dictionary of CLI option overrides

    Returns:
        Merged WorkersManagementConfig
    """
    cli_overrides = cli_overrides or {}

    # Load base config from files + env
    config = get_config().worker_management

    # Apply CLI overrides to global settings
    if cli_overrides.get('workers'):
        config.default_execution_mode = cli_overrides['workers']
        logger.info(f"CLI override: default_execution_mode = {config.default_execution_mode}")

    if cli_overrides.get('worker_count'):
        config.default_worker_count = cli_overrides['worker_count']
        logger.info(f"CLI override: default_worker_count = {config.default_worker_count}")

    if cli_overrides.get('no_auto_start'):
        config.auto_start = False
        logger.info("CLI override: auto_start = False")

    if cli_overrides.get('no_auto_stop'):
        config.auto_stop = False
        logger.info("CLI override: auto_stop = False")

    if cli_overrides.get('fresh_workers'):
        config.reuse_workers = False
        logger.info("CLI override: reuse_workers = False")

    # Apply per-type overrides
    for worker_type in ['notebook', 'plantuml', 'drawio']:
        cli_key = f'{worker_type}_workers'
        if cli_overrides.get(cli_key):
            type_config = getattr(config, worker_type)
            type_config.count = cli_overrides[cli_key]
            logger.info(f"CLI override: {worker_type}.count = {type_config.count}")

    return config
```

#### 1.4: Tests

**New File**: `tests/infrastructure/test_worker_config.py`

```python
"""Tests for worker configuration loading and merging."""

import pytest
from pathlib import Path
from clm.infrastructure.config import WorkersManagementConfig
from clm.infrastructure.workers.config_loader import load_worker_config


def test_default_config():
    """Test default configuration values."""
    config = WorkersManagementConfig()

    assert config.default_execution_mode == 'direct'
    assert config.default_worker_count == 1
    assert config.auto_start is True
    assert config.auto_stop is True
    assert config.reuse_workers is True


def test_get_worker_config_with_defaults():
    """Test getting worker config with all defaults."""
    config = WorkersManagementConfig()

    notebook_config = config.get_worker_config('notebook')

    assert notebook_config.worker_type == 'notebook'
    assert notebook_config.execution_mode == 'direct'
    assert notebook_config.count == 1


def test_get_worker_config_with_overrides():
    """Test getting worker config with per-type overrides."""
    config = WorkersManagementConfig(
        default_execution_mode='direct',
        default_worker_count=1
    )
    config.notebook.execution_mode = 'docker'
    config.notebook.count = 3
    config.notebook.image = 'test-image:latest'

    notebook_config = config.get_worker_config('notebook')

    assert notebook_config.execution_mode == 'docker'
    assert notebook_config.count == 3
    assert notebook_config.image == 'test-image:latest'


def test_cli_overrides():
    """Test CLI overrides."""
    cli_overrides = {
        'workers': 'docker',
        'worker_count': 2,
        'notebook_workers': 4
    }

    config = load_worker_config(cli_overrides)

    assert config.default_execution_mode == 'docker'
    assert config.default_worker_count == 2

    notebook_config = config.get_worker_config('notebook')
    assert notebook_config.count == 4  # Per-type override takes precedence


def test_docker_mode_validation():
    """Test that docker mode requires image."""
    config = WorkersManagementConfig(default_execution_mode='docker')
    config.notebook.image = None  # No image specified

    # Should use default image
    notebook_config = config.get_worker_config('notebook')
    assert notebook_config.image == 'mhoelzl/clm-notebook-processor:0.3.0'


def test_invalid_worker_type():
    """Test that invalid worker type raises error."""
    config = WorkersManagementConfig()

    with pytest.raises(ValueError, match="Unknown worker type"):
        config.get_worker_config('invalid')
```

**Deliverables**:
- [ ] Extended configuration schema with validation
- [ ] Updated example config with worker_management section
- [ ] CLI options for worker configuration
- [ ] Configuration loader with merge logic
- [ ] Unit tests for configuration (100% coverage)
- [ ] Updated `clm config show` to display worker settings

**Risks**:
- Configuration complexity overwhelming users
- Breaking changes to existing configurations
- Validation edge cases

**Mitigation**:
- Comprehensive examples and documentation
- Backward compatibility (all settings optional)
- Extensive validation tests

---

## Phase 2: WorkerLifecycleManager (3-4 days)

### Tasks

#### 2.1: WorkerStateManager

**New File**: `src/clm/infrastructure/workers/state_manager.py`

```python
"""Worker state management for persistent workers."""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class WorkerInfo(BaseModel):
    """Information about a worker."""
    worker_type: str
    execution_mode: str
    executor_id: str  # Container ID or direct-worker-id
    db_worker_id: int
    started_at: str
    config: Dict[str, Any]


class WorkerState(BaseModel):
    """Persistent worker state."""
    version: str = "1.0"
    db_path: str
    workers: List[WorkerInfo]
    metadata: Dict[str, Any]


class WorkerStateManager:
    """Manage persistent worker state."""

    def __init__(self, state_file: Optional[Path] = None):
        """Initialize state manager.

        Args:
            state_file: Path to state file. Defaults to .clm/worker-state.json
        """
        if state_file is None:
            state_file = Path('.clm') / 'worker-state.json'

        self.state_file = state_file

    def save_worker_state(
        self,
        workers: List[WorkerInfo],
        db_path: Path,
        **metadata
    ) -> None:
        """Save worker state to disk.

        Args:
            workers: List of worker information
            db_path: Path to database
            **metadata: Additional metadata to store
        """
        # Ensure directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Build state
        state = WorkerState(
            db_path=str(db_path.absolute()),
            workers=workers,
            metadata={
                'created_at': datetime.now().isoformat(),
                'created_by': 'clm start-services',
                **metadata
            }
        )

        # Write atomically
        temp_file = self.state_file.with_suffix('.tmp')
        try:
            with temp_file.open('w') as f:
                f.write(state.model_dump_json(indent=2))

            # Set restrictive permissions (user rw only)
            temp_file.chmod(0o600)

            # Atomic rename
            temp_file.replace(self.state_file)

            logger.info(f"Saved worker state to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save worker state: {e}")
            if temp_file.exists():
                temp_file.unlink()
            raise

    def load_worker_state(self) -> Optional[WorkerState]:
        """Load worker state from disk.

        Returns:
            WorkerState if file exists and is valid, None otherwise
        """
        if not self.state_file.exists():
            logger.debug(f"State file does not exist: {self.state_file}")
            return None

        try:
            with self.state_file.open('r') as f:
                data = json.load(f)

            state = WorkerState(**data)
            logger.debug(f"Loaded worker state from {self.state_file}")
            return state

        except Exception as e:
            logger.error(f"Failed to load worker state: {e}")
            return None

    def clear_worker_state(self) -> None:
        """Clear worker state file."""
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                logger.info(f"Cleared worker state: {self.state_file}")
            except Exception as e:
                logger.error(f"Failed to clear worker state: {e}")

    def validate_state(self, db_path: Path) -> bool:
        """Validate that state file matches expected database.

        Args:
            db_path: Expected database path

        Returns:
            True if state is valid and matches db_path
        """
        state = self.load_worker_state()
        if not state:
            return False

        # Check database path matches
        if state.db_path != str(db_path.absolute()):
            logger.warning(
                f"Database path mismatch: "
                f"state={state.db_path}, expected={db_path}"
            )
            return False

        # Could add more validation here
        # - Check workers still exist
        # - Check workers still healthy
        # etc.

        return True
```

#### 2.2: Worker Discovery and Health Checking

**New File**: `src/clm/infrastructure/workers/discovery.py`

```python
"""Worker discovery and health checking utilities."""

import logging
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from dataclasses import dataclass

from clm.infrastructure.database.job_queue import JobQueue
from clm.infrastructure.workers.worker_executor import (
    WorkerConfig,
    WorkerExecutor,
    DockerWorkerExecutor,
    DirectWorkerExecutor
)

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredWorker:
    """Information about a discovered worker."""
    db_id: int
    worker_type: str
    executor_id: str
    status: str
    last_heartbeat: datetime
    jobs_processed: int
    jobs_failed: int
    started_at: datetime
    is_docker: bool
    is_healthy: bool


class WorkerDiscovery:
    """Discover and validate existing workers."""

    def __init__(self, db_path: Path, executors: Optional[Dict[str, WorkerExecutor]] = None):
        """Initialize worker discovery.

        Args:
            db_path: Path to database
            executors: Optional dict of execution_mode -> executor
        """
        self.db_path = db_path
        self.job_queue = JobQueue(db_path)
        self.executors = executors or {}

    def discover_workers(
        self,
        worker_type: Optional[str] = None,
        status_filter: Optional[List[str]] = None
    ) -> List[DiscoveredWorker]:
        """Discover workers from database.

        Args:
            worker_type: Filter by worker type (None = all types)
            status_filter: Filter by status (None = all statuses)

        Returns:
            List of discovered workers
        """
        conn = self.job_queue._get_conn()

        # Build query
        query = """
            SELECT
                id, worker_type, container_id, status,
                last_heartbeat, jobs_processed, jobs_failed, started_at
            FROM workers
        """

        conditions = []
        params = []

        if worker_type:
            conditions.append("worker_type = ?")
            params.append(worker_type)

        if status_filter:
            placeholders = ','.join('?' * len(status_filter))
            conditions.append(f"status IN ({placeholders})")
            params.extend(status_filter)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY worker_type, id"

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        # Convert to DiscoveredWorker objects
        workers = []
        for row in rows:
            is_docker = not row[2].startswith('direct-')

            worker = DiscoveredWorker(
                db_id=row[0],
                worker_type=row[1],
                executor_id=row[2],
                status=row[3],
                last_heartbeat=datetime.fromisoformat(row[4]),
                jobs_processed=row[5],
                jobs_failed=row[6],
                started_at=datetime.fromisoformat(row[7]),
                is_docker=is_docker,
                is_healthy=False  # Will be set by health check
            )

            workers.append(worker)

        # Perform health checks
        for worker in workers:
            worker.is_healthy = self.check_worker_health(worker)

        return workers

    def check_worker_health(self, worker: DiscoveredWorker) -> bool:
        """Check if a worker is healthy.

        Args:
            worker: Worker to check

        Returns:
            True if worker is healthy
        """
        # 1. Check status
        if worker.status not in ('idle', 'busy'):
            logger.debug(f"Worker {worker.db_id} status is {worker.status}")
            return False

        # 2. Check heartbeat (within last 30 seconds)
        heartbeat_age = datetime.now() - worker.last_heartbeat
        if heartbeat_age > timedelta(seconds=30):
            logger.debug(
                f"Worker {worker.db_id} has stale heartbeat "
                f"({heartbeat_age.total_seconds():.1f}s ago)"
            )
            return False

        # 3. Check process/container is actually running
        executor_type = 'docker' if worker.is_docker else 'direct'

        if executor_type not in self.executors:
            logger.debug(f"No executor for type {executor_type}")
            return False

        executor = self.executors[executor_type]

        try:
            if not executor.is_worker_running(worker.executor_id):
                logger.debug(f"Worker {worker.db_id} process/container not running")
                return False
        except Exception as e:
            logger.debug(f"Error checking worker {worker.db_id}: {e}")
            return False

        return True

    def count_healthy_workers(self, worker_type: str) -> int:
        """Count healthy workers of a specific type.

        Args:
            worker_type: Worker type to count

        Returns:
            Number of healthy workers
        """
        workers = self.discover_workers(
            worker_type=worker_type,
            status_filter=['idle', 'busy']
        )

        return sum(1 for w in workers if w.is_healthy)

    def get_worker_summary(self) -> Dict[str, Dict[str, int]]:
        """Get summary of workers by type and status.

        Returns:
            Dict of worker_type -> {status -> count}
        """
        workers = self.discover_workers()

        summary = {}
        for worker in workers:
            if worker.worker_type not in summary:
                summary[worker.worker_type] = {
                    'total': 0,
                    'healthy': 0,
                    'unhealthy': 0
                }

            summary[worker.worker_type]['total'] += 1

            if worker.is_healthy:
                summary[worker.worker_type]['healthy'] += 1
            else:
                summary[worker.worker_type]['unhealthy'] += 1

        return summary
```

#### 2.3: WorkerLifecycleManager

**New File**: `src/clm/infrastructure/workers/lifecycle_manager.py`

```python
"""High-level worker lifecycle management."""

import logging
import sys
from pathlib import Path
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from clm.infrastructure.workers.pool_manager import WorkerPoolManager
from clm.infrastructure.workers.worker_executor import WorkerConfig
from clm.infrastructure.workers.discovery import WorkerDiscovery, DiscoveredWorker
from clm.infrastructure.workers.state_manager import WorkerStateManager, WorkerInfo
from clm.infrastructure.config import WorkersManagementConfig

logger = logging.getLogger(__name__)


class WorkerLifecycleManager:
    """Manage worker lifecycle based on configuration."""

    def __init__(
        self,
        config: WorkersManagementConfig,
        db_path: Path,
        workspace_path: Path
    ):
        """Initialize lifecycle manager.

        Args:
            config: Worker management configuration
            db_path: Path to database
            workspace_path: Path to workspace directory
        """
        self.config = config
        self.db_path = db_path
        self.workspace_path = workspace_path

        # Worker pool manager (used for actual worker start/stop)
        self.pool_manager: Optional[WorkerPoolManager] = None

        # Worker discovery
        self.discovery = WorkerDiscovery(db_path)

        # State manager (for persistent workers)
        self.state_manager = WorkerStateManager()

        # Track managed workers (for cleanup)
        self.managed_workers: List[WorkerInfo] = []

    def should_start_workers(self) -> bool:
        """Determine if we need to start new workers.

        Returns:
            True if workers should be started
        """
        if not self.config.auto_start:
            logger.info("Auto-start is disabled")
            return False

        if not self.config.reuse_workers:
            logger.info("Worker reuse is disabled, will start fresh workers")
            return True

        # Check if we have sufficient healthy workers for each type
        for worker_type in ['notebook', 'plantuml', 'drawio']:
            required_config = self.config.get_worker_config(worker_type)
            required_count = required_config.count

            healthy_count = self.discovery.count_healthy_workers(worker_type)

            if healthy_count < required_count:
                logger.info(
                    f"Need {required_count} {worker_type} worker(s), "
                    f"found {healthy_count} healthy worker(s)"
                )
                return True

        logger.info("Sufficient healthy workers already running")
        return False

    def start_managed_workers(self) -> List[WorkerInfo]:
        """Start workers that will be managed (auto-stopped) by this instance.

        Returns:
            List of started worker information
        """
        logger.info("Starting managed workers...")

        # Get worker configurations
        worker_configs = self.config.get_all_worker_configs()

        # If reusing workers, adjust counts based on existing workers
        if self.config.reuse_workers:
            worker_configs = self._adjust_configs_for_reuse(worker_configs)

        if not worker_configs:
            logger.info("No workers to start (sufficient workers already running)")
            return []

        # Create pool manager
        self.pool_manager = WorkerPoolManager(
            db_path=self.db_path,
            workspace_path=self.workspace_path,
            worker_configs=worker_configs,
            network_name=self.config.network_name,
            log_level=logging.getLevelName(logger.getEffectiveLevel())
        )

        # Start pools
        self.pool_manager.start_pools()

        # Collect worker info for tracking
        self.managed_workers = self._collect_worker_info()

        logger.info(f"Started {len(self.managed_workers)} managed worker(s)")
        return self.managed_workers

    def start_persistent_workers(self) -> List[WorkerInfo]:
        """Start workers that persist after this process exits.

        Returns:
            List of started worker information
        """
        logger.info("Starting persistent workers...")

        # Get worker configurations
        worker_configs = self.config.get_all_worker_configs()

        # Create pool manager
        self.pool_manager = WorkerPoolManager(
            db_path=self.db_path,
            workspace_path=self.workspace_path,
            worker_configs=worker_configs,
            network_name=self.config.network_name,
            log_level=logging.getLevelName(logger.getEffectiveLevel())
        )

        # Start pools
        self.pool_manager.start_pools()

        # Collect worker info
        workers = self._collect_worker_info()

        logger.info(f"Started {len(workers)} persistent worker(s)")
        return workers

    def stop_managed_workers(self) -> None:
        """Stop all managed workers."""
        if not self.config.auto_stop:
            logger.info("Auto-stop is disabled, keeping workers running")
            return

        if not self.pool_manager:
            logger.debug("No pool manager to stop")
            return

        logger.info("Stopping managed workers...")
        self.pool_manager.stop_pools()
        self.managed_workers.clear()

    def stop_persistent_workers(self, workers: List[WorkerInfo]) -> None:
        """Stop persistent workers from state.

        Args:
            workers: Workers to stop (from state file)
        """
        logger.info(f"Stopping {len(workers)} persistent worker(s)...")

        # Group workers by execution mode for efficient shutdown
        docker_workers = [w for w in workers if w.execution_mode == 'docker']
        direct_workers = [w for w in workers if w.execution_mode == 'direct']

        # Stop docker workers
        if docker_workers:
            self._stop_docker_workers(docker_workers)

        # Stop direct workers
        if direct_workers:
            self._stop_direct_workers(direct_workers)

    def cleanup_all_workers(self) -> None:
        """Clean up all workers from database (force cleanup)."""
        logger.info("Cleaning up all workers from database...")

        # Discover all workers
        all_workers = self.discovery.discover_workers()

        # Try to stop each
        for worker in all_workers:
            # Will be implemented
            pass

    def _adjust_configs_for_reuse(
        self,
        configs: List[WorkerConfig]
    ) -> List[WorkerConfig]:
        """Adjust worker counts based on existing healthy workers.

        Args:
            configs: Original worker configurations

        Returns:
            Adjusted configurations (may be empty if no workers needed)
        """
        adjusted = []

        for config in configs:
            healthy_count = self.discovery.count_healthy_workers(config.worker_type)
            needed_count = max(0, config.count - healthy_count)

            if needed_count > 0:
                # Create new config with adjusted count
                new_config = WorkerConfig(
                    worker_type=config.worker_type,
                    execution_mode=config.execution_mode,
                    count=needed_count,
                    image=config.image,
                    memory_limit=config.memory_limit,
                    max_job_time=config.max_job_time
                )
                adjusted.append(new_config)

                logger.info(
                    f"Adjusted {config.worker_type}: "
                    f"needed={config.count}, healthy={healthy_count}, "
                    f"starting={needed_count}"
                )

        return adjusted

    def _collect_worker_info(self) -> List[WorkerInfo]:
        """Collect information about workers from pool manager.

        Returns:
            List of worker information
        """
        if not self.pool_manager:
            return []

        workers_info = []

        for worker_type, workers in self.pool_manager.workers.items():
            for worker_dict in workers:
                info = WorkerInfo(
                    worker_type=worker_type,
                    execution_mode=worker_dict['config'].execution_mode,
                    executor_id=worker_dict['executor_id'],
                    db_worker_id=worker_dict['db_worker_id'],
                    started_at=worker_dict['started_at'].isoformat(),
                    config=worker_dict['config'].__dict__
                )
                workers_info.append(info)

        return workers_info

    def _stop_docker_workers(self, workers: List[WorkerInfo]) -> None:
        """Stop Docker workers."""
        # Implementation similar to existing pool_manager.stop_pools()
        pass

    def _stop_direct_workers(self, workers: List[WorkerInfo]) -> None:
        """Stop direct workers."""
        # Implementation similar to existing pool_manager.stop_pools()
        pass
```

#### 2.4: Tests

**New File**: `tests/infrastructure/workers/test_state_manager.py`

```python
"""Tests for WorkerStateManager."""

import pytest
import json
from pathlib import Path
from datetime import datetime

from clm.infrastructure.workers.state_manager import (
    WorkerStateManager,
    WorkerInfo,
    WorkerState
)


def test_save_and_load_state(tmp_path):
    """Test saving and loading worker state."""
    state_file = tmp_path / ".clm" / "worker-state.json"
    manager = WorkerStateManager(state_file)

    # Create test workers
    workers = [
        WorkerInfo(
            worker_type='notebook',
            execution_mode='docker',
            executor_id='container_abc123',
            db_worker_id=1,
            started_at=datetime.now().isoformat(),
            config={'image': 'test:latest', 'count': 1}
        )
    ]

    db_path = tmp_path / "test.db"

    # Save state
    manager.save_worker_state(workers, db_path, test_key='test_value')

    # Verify file exists
    assert state_file.exists()

    # Verify permissions (user rw only)
    assert oct(state_file.stat().st_mode)[-3:] == '600'

    # Load state
    loaded = manager.load_worker_state()

    assert loaded is not None
    assert loaded.db_path == str(db_path.absolute())
    assert len(loaded.workers) == 1
    assert loaded.workers[0].worker_type == 'notebook'
    assert loaded.metadata['test_key'] == 'test_value'


def test_load_nonexistent_state(tmp_path):
    """Test loading state when file doesn't exist."""
    state_file = tmp_path / "nonexistent.json"
    manager = WorkerStateManager(state_file)

    loaded = manager.load_worker_state()
    assert loaded is None


def test_clear_state(tmp_path):
    """Test clearing state file."""
    state_file = tmp_path / "state.json"
    state_file.write_text('{}')

    manager = WorkerStateManager(state_file)
    manager.clear_worker_state()

    assert not state_file.exists()


def test_validate_state(tmp_path):
    """Test state validation."""
    state_file = tmp_path / "state.json"
    manager = WorkerStateManager(state_file)

    db_path = tmp_path / "test.db"
    workers = []

    # Save state
    manager.save_worker_state(workers, db_path)

    # Validate with correct path
    assert manager.validate_state(db_path) is True

    # Validate with wrong path
    wrong_path = tmp_path / "wrong.db"
    assert manager.validate_state(wrong_path) is False
```

**Additional tests needed**:
- `test_discovery.py` - Worker discovery and health checking
- `test_lifecycle_manager.py` - Worker lifecycle management
- Integration tests with actual workers

**Deliverables**:
- [ ] WorkerStateManager with file I/O
- [ ] WorkerDiscovery with health checking
- [ ] WorkerLifecycleManager with start/stop logic
- [ ] Unit tests (>90% coverage)
- [ ] Integration tests with mock workers

**Risks**:
- Race conditions in worker discovery
- State file corruption
- Executor cleanup edge cases

**Mitigation**:
- Atomic file writes
- Comprehensive error handling
- Thorough testing with edge cases

---

## Phase 3: Integration with `clm build` (2-3 days)

### Tasks

#### 3.1: Update `clm build` command

**File**: `src/clm/cli/main.py`

**Changes**:
```python
async def main(
    ctx,
    spec_file,
    data_dir,
    output_dir,
    watch,
    print_tracebacks,
    print_correlation_ids,
    log_level,
    db_path,
    ignore_db,
    force_db_init,
    keep_directory,
    cli_overrides  # New parameter
):
    start_time = time()
    spec_file = spec_file.absolute()
    setup_logging(log_level)

    # ... existing setup code ...

    # Load worker configuration
    from clm.infrastructure.workers.config_loader import load_worker_config
    from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager

    worker_config = load_worker_config(cli_overrides)

    # Initialize worker lifecycle manager
    worker_manager = WorkerLifecycleManager(
        config=worker_config,
        db_path=db_path,
        workspace_path=output_dir
    )

    with DatabaseManager(db_path, force_init=force_db_init) as db_manager:
        backend = SqliteBackend(
            db_path=db_path,
            workspace_path=output_dir,
            db_manager=db_manager,
            ignore_db=ignore_db
        )

        async with backend:
            try:
                # Check for existing workers and start if needed
                if worker_manager.should_start_workers():
                    logger.info("Starting workers...")
                    worker_manager.start_managed_workers()
                else:
                    # Log existing workers
                    summary = worker_manager.discovery.get_worker_summary()
                    logger.info(f"Using existing workers: {summary}")

                # ... existing course processing code ...

                with git_dir_mover(root_dirs, keep_directory):
                    # ... existing code ...
                    await course.process_all(backend)
                    # ... existing code ...

            finally:
                # Cleanup managed workers
                logger.info("Shutting down workers...")
                worker_manager.stop_managed_workers()

            # ... rest of existing code (watch mode, etc.) ...
```

#### 3.2: Tests

**New File**: `tests/e2e/test_auto_worker_lifecycle.py`

```python
"""End-to-end tests for automatic worker lifecycle."""

import pytest
from pathlib import Path
from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
from clm.infrastructure.config import WorkersManagementConfig


@pytest.mark.integration
def test_build_with_auto_start_stop(tmp_path, sample_course_spec):
    """Test that clm build auto-starts and stops workers."""
    # This test would invoke clm build and verify:
    # 1. Workers are started automatically
    # 2. Course is processed
    # 3. Workers are stopped automatically
    pass


@pytest.mark.integration
def test_build_with_existing_workers(tmp_path):
    """Test that clm build reuses existing workers."""
    # 1. Start persistent workers
    # 2. Run clm build
    # 3. Verify it reused workers (didn't start new ones)
    # 4. Verify workers still running after build
    pass


@pytest.mark.integration
def test_build_with_fresh_workers_flag(tmp_path):
    """Test --fresh-workers flag starts new workers."""
    # 1. Start some workers
    # 2. Run clm build --fresh-workers
    # 3. Verify new workers were started
    pass
```

**Deliverables**:
- [ ] Integrated worker lifecycle into `clm build`
- [ ] Graceful error handling for worker failures
- [ ] E2E tests with actual course processing
- [ ] Performance testing with various worker counts

**Risks**:
- Worker startup delaying course processing
- Cleanup failures leaving orphaned workers
- Watch mode complications

**Mitigation**:
- Parallel worker startup
- Robust cleanup with timeout
- Signal handling for watch mode

---

## Phase 4: Persistent Workers Commands (3-4 days)

### Tasks

#### 4.1: `clm start-services` command

**File**: `src/clm/cli/main.py`

```python
@cli.command(name='start-services')
@click.option(
    '--db-path',
    type=click.Path(),
    default='clm_jobs.db',
    help='Path to SQLite database'
)
@click.option(
    '--workspace',
    type=click.Path(),
    default='.',
    help='Workspace path for workers'
)
@click.option(
    '--wait/--no-wait',
    default=True,
    help='Wait for workers to register'
)
@click.pass_context
def start_services(ctx, db_path, workspace, wait):
    """Start persistent worker services.

    This starts workers that will continue running after this command exits.
    Workers must be explicitly stopped with 'clm stop-services'.

    Examples:
        # Start with default settings
        clm start-services

        # Start with custom database path
        clm start-services --db-path=/data/clm_jobs.db

        # Start and return immediately (don't wait for registration)
        clm start-services --no-wait
    """
    from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
    from clm.infrastructure.workers.config_loader import load_worker_config
    from clm.infrastructure.database.schema import init_database

    db_path = Path(db_path).absolute()
    workspace = Path(workspace).absolute()

    # Validate paths
    if not workspace.exists():
        logger.error(f"Workspace directory does not exist: {workspace}")
        return 1

    # Initialize database
    logger.info(f"Initializing database: {db_path}")
    init_database(db_path)

    # Load configuration
    config = load_worker_config()

    # Create lifecycle manager
    manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path,
        workspace_path=workspace
    )

    try:
        # Start persistent workers
        logger.info("Starting persistent workers...")
        workers = manager.start_persistent_workers()

        if not workers:
            logger.warning("No workers were started")
            return 1

        # Save state
        manager.state_manager.save_worker_state(
            workers=workers,
            db_path=db_path,
            workspace_path=str(workspace),
            network_name=config.network_name
        )

        # Report success
        logger.info(f"✓ Started {len(workers)} worker(s)")
        logger.info("")
        logger.info("Workers by type:")
        from collections import Counter
        counts = Counter(w.worker_type for w in workers)
        for worker_type, count in sorted(counts.items()):
            logger.info(f"  {worker_type}: {count}")

        logger.info("")
        logger.info("To process a course:")
        logger.info(f"  clm build course.yaml --db-path={db_path}")
        logger.info("")
        logger.info("To stop workers:")
        logger.info(f"  clm stop-services --db-path={db_path}")

        return 0

    except Exception as e:
        logger.error(f"Failed to start services: {e}", exc_info=True)
        return 1
```

#### 4.2: `clm stop-services` command

```python
@cli.command(name='stop-services')
@click.option(
    '--db-path',
    type=click.Path(),
    default='clm_jobs.db',
    help='Path to SQLite database'
)
@click.option(
    '--force',
    is_flag=True,
    help='Force cleanup even if state file is missing'
)
@click.pass_context
def stop_services(ctx, db_path, force):
    """Stop persistent worker services.

    Stops workers that were started with 'clm start-services'.

    Examples:
        # Stop services
        clm stop-services

        # Stop with custom database path
        clm stop-services --db-path=/data/clm_jobs.db

        # Force cleanup (even if state file missing)
        clm stop-services --force
    """
    from clm.infrastructure.workers.lifecycle_manager import WorkerLifecycleManager
    from clm.infrastructure.workers.config_loader import load_worker_config
    from clm.infrastructure.workers.state_manager import WorkerStateManager

    db_path = Path(db_path).absolute()

    # Load state
    state_manager = WorkerStateManager()
    state = state_manager.load_worker_state()

    if not state and not force:
        logger.error("No worker state found.")
        logger.error("Did you run 'clm start-services'?")
        logger.error("Use --force to clean up workers from database anyway.")
        return 1

    if state:
        # Validate database path matches
        if state.db_path != str(db_path):
            logger.warning(
                f"Database path mismatch:\n"
                f"  State file:  {state.db_path}\n"
                f"  You specified: {db_path}"
            )
            if not force:
                logger.error("Use --force to override")
                return 1

    # Load configuration
    config = load_worker_config()

    # Create lifecycle manager
    manager = WorkerLifecycleManager(
        config=config,
        db_path=db_path,
        workspace_path=db_path.parent  # Doesn't matter for shutdown
    )

    try:
        if state and state.workers:
            logger.info(f"Stopping {len(state.workers)} worker(s)...")
            manager.stop_persistent_workers(state.workers)
        else:
            logger.info("Cleaning up workers from database...")
            manager.cleanup_all_workers()

        # Clear state file
        state_manager.clear_worker_state()

        logger.info("✓ Services stopped")
        return 0

    except Exception as e:
        logger.error(f"Failed to stop services: {e}", exc_info=True)
        return 1
```

#### 4.3: Tests

**New File**: `tests/e2e/test_persistent_workers.py`

```python
"""Tests for persistent worker commands."""

import pytest
from pathlib import Path
from click.testing import CliRunner
from clm.cli.main import cli


@pytest.mark.integration
def test_start_stop_services_workflow(tmp_path):
    """Test full start-services / stop-services workflow."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"

    # Start services
    result = runner.invoke(cli, [
        'start-services',
        '--db-path', str(db_path),
        '--workspace', str(tmp_path)
    ])

    assert result.exit_code == 0
    assert 'Started' in result.output

    # Verify state file exists
    state_file = Path('.clm/worker-state.json')
    assert state_file.exists()

    # Stop services
    result = runner.invoke(cli, [
        'stop-services',
        '--db-path', str(db_path)
    ])

    assert result.exit_code == 0
    assert 'stopped' in result.output.lower()

    # Verify state file removed
    assert not state_file.exists()


@pytest.mark.integration
def test_start_services_with_config(tmp_path, monkeypatch):
    """Test start-services respects configuration."""
    # Create config file
    config_file = tmp_path / ".clm" / "config.toml"
    config_file.parent.mkdir()
    config_file.write_text("""
[worker_management]
default_worker_count = 2

[worker_management.notebook]
count = 3
""")

    # Change to tmp_path so config is found
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    db_path = tmp_path / "test.db"

    result = runner.invoke(cli, [
        'start-services',
        '--db-path', str(db_path),
        '--workspace', str(tmp_path)
    ])

    # Should start 3 notebook workers, 2 plantuml, 2 drawio
    # Verify via database query or logs
    assert result.exit_code == 0
```

**Deliverables**:
- [ ] `clm start-services` command
- [ ] `clm stop-services` command
- [ ] State file management
- [ ] Clear user-facing messages
- [ ] E2E tests for full workflow

**Risks**:
- State file corruption or loss
- Workers not stopping cleanly
- Database path confusion

**Mitigation**:
- Atomic state file writes
- Robust shutdown with timeouts
- Clear error messages and validation

---

## Phase 5: Worker Management Commands (2-3 days)

### Tasks

#### 5.1: `clm workers list` command

**File**: `src/clm/cli/main.py`

```python
@cli.group(name='workers')
def workers_group():
    """Manage CLM workers."""
    pass


@workers_group.command(name='list')
@click.option(
    '--db-path',
    type=click.Path(),
    default='clm_jobs.db',
    help='Path to SQLite database'
)
@click.option(
    '--format',
    type=click.Choice(['table', 'json'], case_sensitive=False),
    default='table',
    help='Output format'
)
@click.option(
    '--status',
    multiple=True,
    type=click.Choice(['idle', 'busy', 'hung', 'dead'], case_sensitive=False),
    help='Filter by status (can specify multiple)'
)
@click.pass_context
def workers_list(ctx, db_path, format, status):
    """List registered workers.

    Examples:
        # List all workers
        clm workers list

        # List only idle workers
        clm workers list --status=idle

        # List in JSON format
        clm workers list --format=json

        # List busy or hung workers
        clm workers list --status=busy --status=hung
    """
    from clm.infrastructure.workers.discovery import WorkerDiscovery

    db_path = Path(db_path)

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    # Discover workers
    discovery = WorkerDiscovery(db_path)
    status_filter = list(status) if status else None
    workers = discovery.discover_workers(status_filter=status_filter)

    if not workers:
        logger.info("No workers found")
        return 0

    if format == 'json':
        import json
        data = [
            {
                'id': w.db_id,
                'type': w.worker_type,
                'executor_id': w.executor_id,
                'status': w.status,
                'started_at': w.started_at.isoformat(),
                'last_heartbeat': w.last_heartbeat.isoformat(),
                'jobs_processed': w.jobs_processed,
                'jobs_failed': w.jobs_failed,
                'is_healthy': w.is_healthy
            }
            for w in workers
        ]
        print(json.dumps(data, indent=2))
    else:
        # Table format
        try:
            from tabulate import tabulate
        except ImportError:
            logger.error("tabulate library not installed. Use --format=json instead.")
            return 1

        rows = []
        for w in workers:
            # Calculate uptime
            uptime = datetime.now() - w.started_at
            uptime_str = str(uptime).split('.')[0]  # Remove microseconds

            # Health indicator
            health = '✓' if w.is_healthy else '✗'

            rows.append([
                w.db_id,
                w.worker_type,
                w.executor_id[:12] if len(w.executor_id) > 12 else w.executor_id,
                w.status,
                health,
                uptime_str,
                w.jobs_processed,
                w.jobs_failed
            ])

        headers = ['ID', 'Type', 'Executor', 'Status', 'Health', 'Uptime', 'Processed', 'Failed']
        print(tabulate(rows, headers=headers, tablefmt='simple'))

    return 0
```

#### 5.2: `clm workers cleanup` command

```python
@workers_group.command(name='cleanup')
@click.option(
    '--db-path',
    type=click.Path(),
    default='clm_jobs.db',
    help='Path to SQLite database'
)
@click.option(
    '--force',
    is_flag=True,
    help='Skip confirmation prompt'
)
@click.option(
    '--all',
    'cleanup_all',
    is_flag=True,
    help='Clean up all workers (not just dead/hung)'
)
@click.pass_context
def workers_cleanup(ctx, db_path, force, cleanup_all):
    """Clean up dead workers and orphaned processes.

    By default, this removes workers that are:
    - Marked as 'dead' or 'hung' in the database
    - Have stale heartbeats (>60 seconds old)

    Examples:
        # Clean up dead workers
        clm workers cleanup

        # Clean up without confirmation
        clm workers cleanup --force

        # Clean up ALL workers
        clm workers cleanup --all --force
    """
    from clm.infrastructure.workers.discovery import WorkerDiscovery
    from clm.infrastructure.database.job_queue import JobQueue

    db_path = Path(db_path)

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    # Discover workers to clean up
    discovery = WorkerDiscovery(db_path)

    if cleanup_all:
        workers = discovery.discover_workers()
        logger.warning("Cleaning up ALL workers")
    else:
        # Only dead/hung workers or stale heartbeats
        workers = discovery.discover_workers(
            status_filter=['dead', 'hung']
        )

        # Also include workers with very stale heartbeats
        all_workers = discovery.discover_workers(
            status_filter=['idle', 'busy']
        )
        stale_workers = [
            w for w in all_workers
            if (datetime.now() - w.last_heartbeat).total_seconds() > 60
        ]
        workers.extend(stale_workers)

    if not workers:
        logger.info("No workers to clean up")
        return 0

    # Show what will be cleaned
    logger.info(f"Found {len(workers)} worker(s) to clean up:")
    for w in workers:
        logger.info(f"  #{w.db_id} ({w.worker_type}, {w.status})")

    # Confirm
    if not force:
        import click
        if not click.confirm('Remove these workers?'):
            logger.info("Cancelled")
            return 0

    # Clean up
    job_queue = JobQueue(db_path)
    conn = job_queue._get_conn()

    cleaned = 0
    for worker in workers:
        try:
            # Try to stop the process/container
            # (Implementation would use executors)

            # Remove from database
            conn.execute("DELETE FROM workers WHERE id = ?", (worker.db_id,))
            cleaned += 1
            logger.info(f"  Cleaned up worker #{worker.db_id}")

        except Exception as e:
            logger.error(f"  Error cleaning worker #{worker.db_id}: {e}")

    conn.commit()

    logger.info(f"✓ Cleaned up {cleaned} worker(s)")
    return 0
```

**Deliverables**:
- [ ] `clm workers list` command with table/JSON output
- [ ] `clm workers cleanup` command
- [ ] Optional tabulate dependency for pretty tables
- [ ] Tests for worker management commands

**Risks**:
- Accidentally cleaning up active workers
- Output formatting issues

**Mitigation**:
- Confirmation prompts
- Clear status indicators
- Extensive testing

---

## Phase 6: Windows/Docker Improvements (2-3 days)

### Tasks

#### 6.1: Platform Detection Utilities

**New File**: `src/clm/infrastructure/utils/platform_utils.py`

```python
"""Platform detection and path utilities."""

import sys
import platform
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == 'win32'


def is_wsl() -> bool:
    """Check if running in WSL (Windows Subsystem for Linux)."""
    try:
        with open('/proc/version', 'r') as f:
            return 'microsoft' in f.read().lower()
    except:
        return False


def is_docker_desktop() -> bool:
    """Check if Docker Desktop is being used (Windows/Mac)."""
    if not is_windows():
        return False

    try:
        import docker
        client = docker.from_env()
        info = client.info()

        # Docker Desktop typically runs in a VM
        os_type = info.get('OSType', '').lower()
        kernel = info.get('KernelVersion', '').lower()

        return 'linuxkit' in kernel or os_type == 'linux'
    except:
        return False


def convert_windows_path_to_wsl(path: Path) -> str:
    """Convert Windows path to WSL mount path.

    Args:
        path: Windows path

    Returns:
        WSL path string (e.g., /mnt/c/Users/...)
    """
    if not is_windows():
        return str(path.absolute())

    path_str = str(path.absolute())

    # Handle drive letters: C:\\ -> /mnt/c/
    if len(path_str) >= 3 and path_str[1:3] == ':\\\\':
        drive = path_str[0].lower()
        rest = path_str[3:].replace('\\\\', '/')
        return f'/mnt/{drive}/{rest}'

    # Fallback
    return path_str.replace('\\\\', '/')


def is_path_accessible_from_docker(path: Path) -> bool:
    """Check if a path is accessible from Docker containers.

    On Windows, Docker Desktop can access:
    - Drive letter paths (C:\\Users\\... -> /mnt/c/Users/...)
    - WSL filesystem paths

    On Linux/Mac, all paths are accessible.

    Args:
        path: Path to check

    Returns:
        True if path should be accessible from Docker
    """
    if not is_windows():
        return True

    path_str = str(path.absolute()).lower()

    # Drive letter paths are accessible
    if len(path_str) >= 3 and path_str[1:3] == ':\\\\':
        return True

    # UNC paths may not be accessible
    if path_str.startswith('\\\\\\\\'):
        logger.warning(f"UNC path may not be accessible from Docker: {path}")
        return False

    return True
```

#### 6.2: Database Configuration for Windows/Docker

**File**: `src/clm/infrastructure/database/schema.py`

**Enhancement**:
```python
def init_database(db_path: Path, force_delete_journal: bool = False) -> sqlite3.Connection:
    """Initialize database with schema.

    Args:
        db_path: Path to SQLite database file
        force_delete_journal: Force DELETE journal mode (for Windows/Docker)

    Returns:
        SQLite connection object
    """
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)

    # Determine journal mode
    if force_delete_journal or should_use_delete_journal():
        logger.info("Using DELETE journal mode (Windows/Docker compatibility)")
        conn.execute("PRAGMA journal_mode=DELETE")
    else:
        logger.debug("Using WAL journal mode")
        conn.execute("PRAGMA journal_mode=WAL")

    # ... rest of existing code ...


def should_use_delete_journal() -> bool:
    """Determine if DELETE journal mode should be used.

    DELETE mode is needed when:
    - Running on Windows with Docker (WSL shared files)
    - Docker containers will access the database

    Returns:
        True if DELETE mode should be used
    """
    from clm.infrastructure.utils.platform_utils import is_windows, is_docker_desktop

    # On Windows with Docker Desktop, use DELETE mode
    if is_windows() and is_docker_desktop():
        return True

    # Could add env var override
    if os.getenv('CLM_FORCE_DELETE_JOURNAL', '').lower() in ('1', 'true', 'yes'):
        return True

    return False
```

#### 6.3: Docker Worker Path Handling

**File**: `src/clm/infrastructure/workers/worker_executor.py`

**Enhancement to `DockerWorkerExecutor.start_worker`**:
```python
def start_worker(self, worker_type: str, index: int, config: WorkerConfig) -> Optional[str]:
    """Start a worker in a Docker container."""
    import docker
    from clm.infrastructure.utils.platform_utils import (
        convert_windows_path_to_wsl,
        is_path_accessible_from_docker
    )

    container_name = f"clm-{worker_type}-worker-{index}"

    try:
        # ... existing code ...

        # Mount the database directory, not the file (Windows compatibility)
        db_dir = self.db_path.parent.absolute()
        db_filename = self.db_path.name

        # Validate path is accessible from Docker
        if not is_path_accessible_from_docker(db_dir):
            logger.error(
                f"Database directory may not be accessible from Docker: {db_dir}\\n"
                f"Consider moving the database to a location in your user directory."
            )

        # Convert paths for Docker
        workspace_mount = str(self.workspace_path.absolute())
        db_mount = str(db_dir)

        logger.debug(
            f"Mounting volumes for {container_name}:\\n"
            f"  Workspace: {workspace_mount} -> /workspace\\n"
            f"  Database:  {db_mount} -> /db\\n"
            f"  DB_PATH env: /db/{db_filename}"
        )

        container = self.docker_client.containers.run(
            config.image,
            name=container_name,
            detach=True,
            remove=False,
            mem_limit=config.memory_limit,
            volumes={
                workspace_mount: {'bind': '/workspace', 'mode': 'rw'},
                db_mount: {'bind': '/db', 'mode': 'rw'}
            },
            environment={
                'WORKER_TYPE': worker_type,
                'DB_PATH': f'/db/{db_filename}',
                'LOG_LEVEL': self.log_level,
                'USE_SQLITE_QUEUE': 'true'
            },
            network=self.network_name
        )

        # ... rest of existing code ...
```

**Deliverables**:
- [ ] Platform detection utilities
- [ ] Automatic journal mode selection
- [ ] Path validation for Docker
- [ ] Clear warnings for Windows users
- [ ] Windows-specific tests

**Risks**:
- Platform detection failures
- Path conversion edge cases
- Performance impact of DELETE journal mode

**Mitigation**:
- Defensive programming
- Comprehensive platform testing
- Performance benchmarks

---

## Summary and Timeline

### Estimated Timeline

| Phase | Description | Duration | Dependencies |
|-------|-------------|----------|--------------|
| 1 | Configuration Infrastructure | 2-3 days | None |
| 2 | WorkerLifecycleManager | 3-4 days | Phase 1 |
| 3 | Integration with clm build | 2-3 days | Phase 2 |
| 4 | Persistent Workers Commands | 3-4 days | Phase 2 |
| 5 | Worker Management Commands | 2-3 days | Phase 2 |
| 6 | Windows/Docker Improvements | 2-3 days | Phases 2-4 |
| **Total** | | **14-20 days** | |

### Testing Strategy

- **Unit Tests**: Each module >90% coverage
- **Integration Tests**: Worker lifecycle with real workers
- **E2E Tests**: Full `clm build` workflow
- **Platform Tests**: Windows-specific testing
- **Performance Tests**: Worker startup time, database contention

### Rollout Plan

1. **Alpha** (Phases 1-2): Internal testing, configuration only
2. **Beta** (Phases 3-4): Early adopters, basic functionality
3. **RC** (Phases 5-6): Feature complete, polish
4. **Release**: Full documentation, migration guide

### Success Metrics

- Zero-config `clm build` works on first try
- Worker startup time < 10 seconds for 3 worker types
- No orphaned workers after normal shutdown
- Works on Windows with Docker Desktop
- 100% backward compatibility with existing code

### Documentation Deliverables

- User guide: Worker configuration
- Reference: Configuration options
- Tutorial: Setting up persistent workers
- Troubleshooting: Common issues
- Migration guide: Upgrading from previous versions

This completes the implementation plan. Each phase is designed to be independently testable and provides incremental value.
