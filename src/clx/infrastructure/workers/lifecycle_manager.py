"""High-level worker lifecycle management."""

import logging
import time
from datetime import datetime
from pathlib import Path

from clx.infrastructure.config import WorkersManagementConfig
from clx.infrastructure.workers.discovery import WorkerDiscovery
from clx.infrastructure.workers.event_logger import WorkerEventLogger
from clx.infrastructure.workers.pool_manager import WorkerPoolManager
from clx.infrastructure.workers.state_manager import WorkerInfo, WorkerStateManager
from clx.infrastructure.workers.worker_executor import (
    DirectWorkerExecutor,
    DockerWorkerExecutor,
    WorkerConfig,
)

logger = logging.getLogger(__name__)


class WorkerLifecycleManager:
    """Manage worker lifecycle based on configuration.

    This class provides high-level orchestration of worker lifecycle:
    - Starting and stopping managed workers (auto-lifecycle with clx build)
    - Starting and stopping persistent workers (manual lifecycle)
    - Worker discovery and health checking
    - Lifecycle event logging
    """

    def __init__(
        self,
        config: WorkersManagementConfig,
        db_path: Path,
        workspace_path: Path,
        session_id: str | None = None,
    ):
        """Initialize lifecycle manager.

        Args:
            config: Worker management configuration
            db_path: Path to database
            workspace_path: Path to workspace directory
            session_id: Optional session ID for event logging
        """
        self.config = config
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.session_id = session_id or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # Worker pool manager (used for actual worker start/stop)
        self.pool_manager: WorkerPoolManager | None = None

        # Event logger
        self.event_logger = WorkerEventLogger(db_path, session_id=self.session_id)

        # Worker discovery
        # Create executors for health checking
        executors = {
            "direct": DirectWorkerExecutor(
                db_path=db_path,
                workspace_path=workspace_path,
                log_level="INFO",
            ),
        }

        # Try to create Docker executor if Docker is available
        try:
            import docker

            docker_client = docker.from_env()
            executors["docker"] = DockerWorkerExecutor(
                docker_client=docker_client,
                db_path=db_path,
                workspace_path=workspace_path,
                network_name=config.network_name,
                log_level="INFO",
            )
        except Exception:
            # Docker not available, only direct executor will be used
            logger.debug("Docker not available, Docker executor not created")

        self.discovery = WorkerDiscovery(db_path, executors=executors)

        # State manager (for persistent workers)
        self.state_manager = WorkerStateManager()

        # Track managed workers (for cleanup)
        self.managed_workers: list[WorkerInfo] = []

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
        for worker_type in ["notebook", "plantuml", "drawio"]:
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

    def start_managed_workers(self) -> list[WorkerInfo]:
        """Start workers that will be managed (auto-stopped) by this instance.

        Returns:
            List of started worker information
        """
        start_time = time.time()

        logger.info("Starting managed workers...")

        # Get worker configurations
        worker_configs = self.config.get_all_worker_configs()

        # If reusing workers, adjust counts based on existing workers
        if self.config.reuse_workers:
            worker_configs = self._adjust_configs_for_reuse(worker_configs)

        if not worker_configs:
            logger.info("No workers to start (sufficient workers already running)")
            # Return information about existing healthy workers
            return self._collect_reused_worker_info()

        total_workers = sum(c.count for c in worker_configs)

        # Log pool starting
        self.event_logger.log_pool_starting(worker_configs, total_workers)

        # Create pool manager
        self.pool_manager = WorkerPoolManager(
            db_path=self.db_path,
            workspace_path=self.workspace_path,
            worker_configs=worker_configs,
            network_name=self.config.network_name,
            log_level=logging.getLevelName(logger.getEffectiveLevel()),
        )

        # Update discovery to use pool_manager's executors for accurate health checks
        # This ensures we check the actual process/container state
        self.discovery.executors = self.pool_manager.executors

        # Start pools
        self.pool_manager.start_pools()

        # Collect worker info for tracking
        self.managed_workers = self._collect_worker_info()

        # Log pool started
        duration = time.time() - start_time
        self.event_logger.log_pool_started(len(self.managed_workers), duration)

        logger.info(f"Started {len(self.managed_workers)} managed worker(s)")
        return self.managed_workers

    def start_persistent_workers(self) -> list[WorkerInfo]:
        """Start workers that persist after this process exits.

        Returns:
            List of started worker information
        """
        start_time = time.time()

        logger.info("Starting persistent workers...")

        # Get worker configurations
        worker_configs = self.config.get_all_worker_configs()

        total_workers = sum(c.count for c in worker_configs)

        # Log pool starting
        self.event_logger.log_pool_starting(worker_configs, total_workers)

        # Create pool manager
        self.pool_manager = WorkerPoolManager(
            db_path=self.db_path,
            workspace_path=self.workspace_path,
            worker_configs=worker_configs,
            network_name=self.config.network_name,
            log_level=logging.getLevelName(logger.getEffectiveLevel()),
        )

        # Update discovery to use pool_manager's executors for accurate health checks
        # This ensures we check the actual process/container state
        self.discovery.executors = self.pool_manager.executors

        # Start pools
        self.pool_manager.start_pools()

        # Collect worker info
        workers = self._collect_worker_info()

        # Log pool started
        duration = time.time() - start_time
        self.event_logger.log_pool_started(len(workers), duration)

        logger.info(f"Started {len(workers)} persistent worker(s)")
        return workers

    def stop_managed_workers(self, workers: list[WorkerInfo]) -> None:
        """Stop managed workers.

        Args:
            workers: List of workers to stop
        """
        if not self.config.auto_stop:
            logger.info("Auto-stop is disabled, keeping workers running")
            return

        if not workers:
            logger.debug("No workers to stop")
            return

        if not self.pool_manager:
            logger.debug("No pool manager to stop")
            return

        start_time = time.time()

        self.event_logger.log_pool_stopping()
        logger.info(f"Stopping {len(workers)} worker(s)...")

        # Stop pools
        self.pool_manager.stop_pools()

        # Log pool stopped
        duration = time.time() - start_time
        self.event_logger.log_pool_stopped(len(workers), duration)

        # Clear tracked workers if they match
        if self.managed_workers == workers:
            self.managed_workers.clear()

    def stop_persistent_workers(self, workers: list[WorkerInfo]) -> None:
        """Stop persistent workers from state.

        Args:
            workers: Workers to stop (from state file)
        """
        logger.info(f"Stopping {len(workers)} persistent worker(s)...")

        # The pool_manager should already be created if we're stopping persistent workers
        if self.pool_manager:
            self.pool_manager.stop_pools()

    def cleanup_all_workers(self) -> None:
        """Clean up all workers from database (force cleanup)."""
        logger.info("Cleaning up all workers from database...")

        # Discover all workers
        all_workers = self.discovery.discover_workers()

        logger.info(f"Found {len(all_workers)} worker(s) to clean up")

        # The actual cleanup would involve stopping processes/containers
        # For now, just log
        for worker in all_workers:
            logger.info(f"  Worker #{worker.db_id} ({worker.worker_type}, {worker.status})")

    def _adjust_configs_for_reuse(self, configs: list[WorkerConfig]) -> list[WorkerConfig]:
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
                    max_job_time=config.max_job_time,
                )
                adjusted.append(new_config)

                logger.info(
                    f"Adjusted {config.worker_type}: "
                    f"needed={config.count}, healthy={healthy_count}, "
                    f"starting={needed_count}"
                )
            else:
                logger.info(
                    f"Skipping {config.worker_type}: {healthy_count} healthy worker(s) already available"
                )

        return adjusted

    def _collect_worker_info(self) -> list[WorkerInfo]:
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
                    execution_mode=worker_dict["config"].execution_mode,
                    executor_id=worker_dict["executor_id"],
                    db_worker_id=worker_dict["db_worker_id"],
                    started_at=worker_dict["started_at"].isoformat(),
                    config={
                        "execution_mode": worker_dict["config"].execution_mode,
                        "image": worker_dict["config"].image,
                        "memory_limit": worker_dict["config"].memory_limit,
                        "max_job_time": worker_dict["config"].max_job_time,
                    },
                )
                workers_info.append(info)

        return workers_info

    def _collect_reused_worker_info(self) -> list[WorkerInfo]:
        """Collect information about existing healthy workers being reused.

        Returns:
            List of worker information for reused workers
        """
        workers_info = []

        # Get all worker configs to know what types we need
        all_configs = self.config.get_all_worker_configs()

        for config in all_configs:
            if config.count == 0:
                continue

            # Discover healthy workers of this type
            discovered = self.discovery.discover_workers(
                worker_type=config.worker_type, status_filter=["idle", "busy"]
            )

            # Take up to config.count healthy workers
            healthy_workers = [w for w in discovered if w.is_healthy][: config.count]

            for worker in healthy_workers:
                info = WorkerInfo(
                    worker_type=worker.worker_type,
                    execution_mode="docker" if worker.is_docker else "direct",
                    executor_id=worker.executor_id,
                    db_worker_id=worker.db_id,
                    started_at=worker.started_at.isoformat(),
                    config={
                        "execution_mode": "docker" if worker.is_docker else "direct",
                        "image": None,  # Unknown for reused workers
                        "memory_limit": config.memory_limit,
                        "max_job_time": config.max_job_time,
                    },
                )
                workers_info.append(info)

        return workers_info
