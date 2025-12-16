"""Worker pool manager for coordinating long-lived workers.

This module provides the WorkerPoolManager class that manages workers
running in different modes (Docker containers or direct processes),
monitors their health, and handles restarts.

The pool manager also registers atexit handlers to ensure workers are
cleaned up even if the main process exits unexpectedly.
"""

import atexit
import logging
import os
import threading
import time
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, cast

# Note: docker package is optional - may not be installed
from clx.infrastructure.api.server import (
    WorkerApiServer,
    start_worker_api_server,
)
from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.workers.worker_executor import (
    DirectWorkerExecutor,
    DockerWorkerExecutor,
    WorkerConfig,
    WorkerExecutor,
)

# Global registry of pool managers for atexit cleanup
# Uses weak references to avoid preventing garbage collection
_pool_manager_registry: weakref.WeakSet["WorkerPoolManager"] = weakref.WeakSet()

# Flag to disable atexit cleanup (set when pools are stopped gracefully)
_atexit_cleanup_disabled = False


def _atexit_cleanup_all_pools():
    """Emergency cleanup of all pool managers on process exit.

    This function is registered with atexit and ensures all workers are
    stopped when the process exits, preventing orphan worker processes.

    Note: This function avoids using the logging module during cleanup because
    the logging module might be in an inconsistent state during interpreter
    shutdown, which can cause errors or hangs.
    """
    global _atexit_cleanup_disabled

    # Skip if cleanup was already done gracefully
    if _atexit_cleanup_disabled:
        return

    try:
        # Convert to list first to avoid issues with WeakSet during GC
        managers = list(_pool_manager_registry)
    except Exception:
        # WeakSet might be in a bad state during shutdown
        return

    for manager in managers:
        try:
            # Check if manager is still valid and needs cleanup
            if manager is None:
                continue

            if not getattr(manager, "running", False):
                continue

            # Use print to stderr instead of logging (more reliable during shutdown)
            try:
                import sys

                worker_count = sum(len(w) for w in manager.workers.values())
                print(
                    f"[CLX] atexit: Emergency cleanup of {worker_count} worker(s)",
                    file=sys.stderr,
                )
            except Exception:
                pass  # Ignore print errors during shutdown

            manager._emergency_stop()

        except Exception:
            # Silently ignore all errors during atexit cleanup
            # Logging might not be available at this point
            pass


# Register the atexit handler once when module is loaded
atexit.register(_atexit_cleanup_all_pools)

logger = logging.getLogger(__name__)


class WorkerPoolManager:
    """Manages worker pools using different execution modes.

    This class is responsible for:
    - Starting and stopping workers (Docker or direct process)
    - Registering workers in the database
    - Monitoring worker health
    - Restarting hung or dead workers
    """

    def __init__(
        self,
        db_path: Path,
        workspace_path: Path,
        worker_configs: list[WorkerConfig],
        network_name: str | None = None,
        log_level: str = "INFO",
        max_startup_concurrency: int | None = None,
        cache_db_path: Path | None = None,
        data_dir: Path | None = None,
    ):
        """Initialize worker pool manager.

        Args:
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory (output)
            worker_configs: List of worker configurations
            network_name: Docker network name (for docker mode). None = use default bridge
                for better host.docker.internal support on Windows/WSL2
            log_level: Logging level for workers
            max_startup_concurrency: Maximum number of workers to start concurrently.
                Defaults to CLX_MAX_WORKER_STARTUP_CONCURRENCY env var or 10.
            cache_db_path: Path to executed notebook cache database
            data_dir: Path to source data directory (for Docker workers to mount)
        """
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.data_dir = data_dir
        self.worker_configs = worker_configs
        self.network_name = network_name
        self.log_level = log_level
        self.cache_db_path = cache_db_path

        # Determine max startup concurrency
        if max_startup_concurrency is None:
            max_startup_concurrency = int(os.getenv("CLX_MAX_WORKER_STARTUP_CONCURRENCY", "10"))
        self.max_startup_concurrency = max_startup_concurrency

        self.docker_client: Any = None  # docker.DockerClient, lazily initialized
        self.job_queue = JobQueue(db_path)
        self.workers: dict[str, list[dict]] = {}  # worker_type -> [worker_info]
        self.executors: dict[str, WorkerExecutor] = {}  # execution_mode -> executor
        self.running = True
        self.monitor_thread: threading.Thread | None = None

        # Worker API server for Docker communication (started when needed)
        self._api_server: WorkerApiServer | None = None

        # Register this pool manager for atexit cleanup
        # This ensures workers are stopped even if the main process exits unexpectedly
        _pool_manager_registry.add(self)

    def _get_or_create_executor(self, config: WorkerConfig) -> WorkerExecutor:
        """Get or create an executor for the given configuration.

        Args:
            config: Worker configuration

        Returns:
            WorkerExecutor instance for the execution mode
        """
        mode = config.execution_mode

        if mode not in self.executors:
            if mode == "docker":
                # Import docker only when needed
                import docker

                # Validate data_dir is set for Docker mode
                if self.data_dir is None:
                    logger.warning(
                        "Docker mode requested but data_dir is not set. "
                        "Workers will not be able to access source files via mount. "
                        "This may cause 'Input file not found' errors. "
                        "Ensure --data-dir is specified or the spec file is in a valid location."
                    )
                else:
                    logger.debug(f"Docker mode: data_dir={self.data_dir}")

                # Lazily initialize Docker client
                if self.docker_client is None:
                    self.docker_client = docker.from_env()  # type: ignore[attr-defined]

                self.executors[mode] = DockerWorkerExecutor(
                    docker_client=self.docker_client,
                    db_path=self.db_path,
                    workspace_path=self.workspace_path,
                    data_dir=self.data_dir,
                    network_name=self.network_name,
                    log_level=self.log_level,
                )
            elif mode == "direct":
                self.executors[mode] = DirectWorkerExecutor(
                    db_path=self.db_path,
                    workspace_path=self.workspace_path,
                    log_level=self.log_level,
                    cache_db_path=self.cache_db_path,
                )
            else:
                raise ValueError(f"Unknown execution mode: {mode}")

        return self.executors[mode]

    def cleanup_stale_workers(self):
        """Clean up stale worker records from the database.

        This removes worker records for:
        1. Workers stuck in 'created' status (pre-registration failed)
        2. Containers/processes that no longer exist
        3. Workers whose parent process has died (orphans)
        """
        logger.info("Cleaning up stale worker records from database")

        conn = self.job_queue._get_conn()

        # First, clean up workers stuck in 'created' status
        # These are workers that were pre-registered but never activated
        removed_count = self._cleanup_stuck_created_workers(conn)

        cursor = conn.execute("SELECT id, container_id FROM workers")
        workers = cursor.fetchall()

        if not workers:
            if removed_count > 0:
                logger.info(
                    f"Removed {removed_count} stuck 'created' worker(s), no other workers found"
                )
            else:
                logger.info("No existing worker records found")
            return

        logger.info(f"Found {len(workers)} existing worker record(s), checking status...")

        # Use WorkerDiscovery to check worker health (includes process checks)
        from clx.infrastructure.workers.discovery import WorkerDiscovery

        # Use existing executors for health checking
        # NOTE: Don't create new executor instances - they won't have process/container tracking!
        with WorkerDiscovery(self.db_path, executors=self.executors) as discovery:
            healthy_workers = discovery.discover_workers()
        healthy_ids = {w.db_id for w in healthy_workers if w.is_healthy}

        # Initialize Docker client if we need to check containers
        docker_client = None

        removed_count = 0
        for worker_id, container_id in workers:
            # Check if this is a direct worker or docker worker
            is_direct = container_id.startswith("direct-")

            if is_direct:
                # Direct worker - check if it's still healthy using WorkerDiscovery
                if worker_id in healthy_ids:
                    logger.info(
                        f"Worker {worker_id} is direct worker {container_id}, still healthy, keeping it"
                    )
                    continue
                else:
                    logger.info(
                        f"Worker {worker_id} is direct worker {container_id}, not healthy, removing stale record"
                    )
                    conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
                    removed_count += 1
            else:
                # Docker worker - check if container exists
                if docker_client is None:
                    try:
                        import docker

                        docker_client = docker.from_env()  # type: ignore[attr-defined]
                    except Exception as e:
                        logger.warning(f"Could not initialize Docker client: {e}")
                        # Remove record if we can't check
                        conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
                        removed_count += 1
                        continue

                try:
                    import docker

                    # Check if container still exists
                    container = docker_client.containers.get(container_id)
                    container.reload()

                    # If container exists but is not running, remove it
                    if container.status != "running":
                        logger.info(
                            f"Worker {worker_id} container {container_id[:12]} is {container.status}, "
                            f"removing container and worker record"
                        )
                        try:
                            container.stop(timeout=2)
                            container.remove()
                        except docker.errors.NotFound:
                            # Container already removed - that's fine
                            pass
                        except docker.errors.APIError as e:
                            # Docker daemon issue - log but continue cleanup
                            logger.warning(
                                f"Docker API error stopping container {container_id[:12]}: {e}"
                            )
                        except Exception as e:
                            # Unexpected error - log with details
                            logger.error(
                                f"Unexpected error stopping container {container_id[:12]}: {e}",
                                exc_info=True,
                            )

                        conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
                        removed_count += 1
                    else:
                        logger.info(
                            f"Worker {worker_id} container {container_id[:12]} is still running, keeping it"
                        )

                except docker.errors.NotFound:
                    # Container doesn't exist, remove worker record
                    logger.info(
                        f"Worker {worker_id} container {container_id[:12]} not found, removing worker record"
                    )
                    conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
                    removed_count += 1
                except Exception as e:
                    logger.warning(
                        f"Error checking worker {worker_id} container {container_id[:12]}: {e}"
                    )
                    # On error, remove the worker record to be safe
                    conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
                    removed_count += 1

        conn.commit()

        if removed_count > 0:
            logger.info(f"Removed {removed_count} stale worker record(s)")
        else:
            logger.info("No stale workers to remove")

    def _cleanup_stuck_created_workers(self, conn) -> int:
        """Clean up workers stuck in 'created' status.

        Workers in 'created' status were pre-registered by the parent process
        but never activated (transitioned to 'idle'). This can happen if:
        1. The subprocess failed to start
        2. The subprocess crashed before activating
        3. The parent process died before starting the subprocess

        Args:
            conn: SQLite connection

        Returns:
            Number of workers removed
        """
        # Timeout for workers stuck in 'created' status (30 seconds)
        # This is generous to handle slow-starting workers on loaded systems
        CREATED_TIMEOUT_SECONDS = 30

        # Find workers stuck in 'created' status for too long
        cursor = conn.execute(
            """
            SELECT id, container_id, parent_pid, started_at
            FROM workers
            WHERE status = 'created'
            AND started_at < datetime('now', ?)
            """,
            (f"-{CREATED_TIMEOUT_SECONDS} seconds",),
        )
        stuck_workers = cursor.fetchall()

        removed_count = 0
        for worker_id, _container_id, parent_pid, started_at in stuck_workers:
            # Check if parent process is still alive
            parent_alive = self._is_process_alive(parent_pid) if parent_pid else False

            if parent_alive:
                # Parent is alive but worker hasn't activated - something is wrong
                logger.warning(
                    f"Worker {worker_id} stuck in 'created' status since {started_at}, "
                    f"parent {parent_pid} is alive but worker hasn't activated. Removing."
                )
            else:
                # Parent is dead - orphaned pre-registered worker
                logger.info(
                    f"Worker {worker_id} orphaned (parent {parent_pid} dead), "
                    f"stuck in 'created' status since {started_at}. Removing."
                )

            conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
            removed_count += 1

        if removed_count > 0:
            conn.commit()
            logger.info(f"Cleaned up {removed_count} worker(s) stuck in 'created' status")

        return removed_count

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process with the given PID is still running.

        Args:
            pid: Process ID to check

        Returns:
            True if process is running, False otherwise
        """
        import sys

        if sys.platform == "win32":
            # Windows: use ctypes to check process
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        else:
            # Unix: use os.kill with signal 0
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def _ensure_network_exists(self):
        """Ensure Docker network exists, create if needed."""
        import docker
        import docker.errors  # type: ignore[import-not-found]

        # Lazily initialize Docker client if needed
        if self.docker_client is None:
            self.docker_client = docker.from_env()  # type: ignore[attr-defined]

        assert self.docker_client is not None
        try:
            self.docker_client.networks.get(self.network_name)
            logger.info(f"Docker network '{self.network_name}' exists")
        except docker.errors.NotFound:
            logger.info(f"Docker network '{self.network_name}' not found, creating...")
            self.docker_client.networks.create(
                self.network_name, driver="bridge", check_duplicate=True
            )
            logger.info(f"Created Docker network '{self.network_name}'")
        except Exception as e:
            logger.error(f"Error checking/creating Docker network: {e}", exc_info=True)
            raise

    def _start_worker_api_server(self):
        """Start the Worker API server for Docker container communication.

        This server provides a REST API that Docker containers use to communicate
        with the job queue, bypassing SQLite WAL mode issues on Windows.
        """
        if self._api_server is not None and self._api_server.is_running:
            logger.debug("Worker API server already running")
            return

        try:
            self._api_server = start_worker_api_server(self.db_path)
            logger.info(
                f"Worker API server started for Docker communication "
                f"(Docker URL: {self._api_server.docker_url})"
            )
        except Exception as e:
            logger.error(f"Failed to start Worker API server: {e}", exc_info=True)
            raise RuntimeError(
                f"Cannot start Docker workers: Worker API server failed to start: {e}"
            ) from e

    def _stop_worker_api_server(self):
        """Stop the Worker API server if running."""
        if self._api_server is not None:
            try:
                self._api_server.stop()
                logger.info("Worker API server stopped")
            except Exception as e:
                logger.warning(f"Error stopping Worker API server: {e}")
            finally:
                self._api_server = None

    def start_pools(self):
        """Start all worker pools defined in worker_configs with parallel startup."""
        logger.info(f"Starting worker pools with {len(self.worker_configs)} configurations")

        # Check if we need Docker and ensure network exists
        needs_docker = any(c.execution_mode == "docker" for c in self.worker_configs)
        if needs_docker:
            # Only create custom network if explicitly specified
            # Default (None) uses Docker's default bridge for better host.docker.internal support
            if self.network_name:
                self._ensure_network_exists()
            else:
                logger.info(
                    "Using Docker default bridge network for better host.docker.internal "
                    "connectivity on Windows/WSL2"
                )
            self._start_worker_api_server()

        # Clean up any stale worker records first
        self.cleanup_stale_workers()

        # Prepare all worker start tasks
        tasks = []
        for config in self.worker_configs:
            self.workers[config.worker_type] = []
            for i in range(config.count):
                tasks.append((config, i))

        total_workers = len(tasks)
        if total_workers == 0:
            logger.info("No workers to start")
            return

        # Log worker configurations
        for config in self.worker_configs:
            mode_desc = f"mode: {config.execution_mode}"
            if config.execution_mode == "docker":
                mode_desc += f", image: {config.image}, memory: {config.memory_limit}"
            logger.info(f"Configured {config.count} {config.worker_type} workers ({mode_desc})")

        logger.info(
            f"Starting {total_workers} worker(s) in parallel "
            f"(max concurrency: {self.max_startup_concurrency})..."
        )

        # Start workers in parallel with controlled concurrency
        started_workers = []
        failed_workers = []

        start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.max_startup_concurrency) as executor:
            # Submit all start tasks
            future_to_task = {
                executor.submit(self._start_worker, config, i): (config, i) for config, i in tasks
            }

            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_task):
                config, i = future_to_task[future]
                completed += 1

                try:
                    worker_info = future.result()
                    if worker_info:
                        started_workers.append(worker_info)
                        self.workers[config.worker_type].append(worker_info)
                        logger.info(
                            f"✓ Started {config.worker_type}-{i} ({completed}/{total_workers})"
                        )
                    else:
                        failed_workers.append((config.worker_type, i))
                        logger.error(
                            f"✗ Failed to start {config.worker_type}-{i} "
                            f"({completed}/{total_workers})"
                        )
                except Exception as e:
                    failed_workers.append((config.worker_type, i))
                    logger.error(
                        f"✗ Exception starting {config.worker_type}-{i}: {e} "
                        f"({completed}/{total_workers})",
                        exc_info=True,
                    )

        duration = time.time() - start_time

        # Report results
        logger.info(f"Started {len(started_workers)}/{total_workers} worker(s) in {duration:.1f}s")

        if failed_workers:
            logger.error(f"Failed to start {len(failed_workers)} worker(s): {failed_workers}")

    def _pre_register_worker(self, worker_type: str, execution_mode: str) -> tuple[int, str]:
        """Pre-register a worker in the database before starting the subprocess.

        This creates a worker record with status='created' and returns the
        database worker ID and a unique identifier (UUID). The subprocess
        will receive these via environment variables and update status to
        'idle' when ready.

        This eliminates the need to wait for worker self-registration, which
        can take 2-10 seconds due to Python startup and module imports.

        Args:
            worker_type: Type of worker ('notebook', 'plantuml', 'drawio')
            execution_mode: Execution mode ('docker' or 'direct')

        Returns:
            Tuple of (db_worker_id, container_id_uuid)
        """
        # Generate a unique identifier for this worker
        container_id = f"{execution_mode}-{worker_type}-{uuid.uuid4().hex}"

        # Get parent process ID for orphan detection
        parent_pid = os.getpid()

        conn = self.job_queue._get_conn()
        cursor = conn.execute(
            """
            INSERT INTO workers (worker_type, container_id, status, parent_pid)
            VALUES (?, ?, 'created', ?)
            """,
            (worker_type, container_id, parent_pid),
        )
        db_worker_id = cursor.lastrowid
        assert db_worker_id is not None, "INSERT should always return a valid lastrowid"
        # Connection is in autocommit mode, no commit() needed

        logger.debug(
            f"Pre-registered {worker_type} worker {db_worker_id} "
            f"(container_id: {container_id}, parent_pid: {parent_pid})"
        )

        return db_worker_id, container_id

    def _wait_for_worker_registration(self, container_id: str, timeout: int = 10) -> int | None:
        """Wait for a worker to register itself in the database.

        Note: This method is kept for backwards compatibility but is no longer
        used with worker pre-registration. Workers are now pre-registered by
        the parent process before subprocess startup.

        Uses adaptive polling with exponential backoff:
        - Starts with fast polling (50ms) to catch quick registrations
        - Backs off exponentially up to 500ms to reduce database load

        Args:
            container_id: Docker container ID (full or short)
            timeout: Maximum time to wait in seconds

        Returns:
            Worker ID if registered, None if timeout
        """
        start_time = time.time()
        poll_interval = 0.05  # Start with 50ms for fast initial detection
        max_poll_interval = 0.5  # Cap at 500ms
        short_id = container_id[:12]  # Docker HOSTNAME is short ID

        while (time.time() - start_time) < timeout:
            conn = self.job_queue._get_conn()
            # Check for both full and short container ID
            cursor = conn.execute(
                """
                SELECT id FROM workers
                WHERE container_id = ? OR container_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (container_id, short_id),
            )
            row = cursor.fetchone()

            if row:
                return cast(int, row[0])

            time.sleep(poll_interval)
            # Exponential backoff: 50ms -> 100ms -> 200ms -> 400ms -> 500ms (capped)
            poll_interval = min(poll_interval * 2, max_poll_interval)

        return None

    def _start_worker(self, config: WorkerConfig, index: int) -> dict | None:
        """Start a single worker using the appropriate executor.

        Uses worker pre-registration to eliminate startup wait time:
        1. Pre-register worker in database with status='created'
        2. Start subprocess with pre-assigned worker ID
        3. Return immediately (no wait for registration)

        The worker subprocess will update its status from 'created' to 'idle'
        when ready to accept jobs.

        Args:
            config: Worker configuration
            index: Worker index (for naming)

        Returns:
            Dictionary with worker info (executor_id, db_worker_id, config) or None if failed
        """
        db_worker_id = None
        container_id = None

        try:
            # Pre-register worker in database with status='created'
            # This returns immediately with the database ID
            db_worker_id, container_id = self._pre_register_worker(
                config.worker_type, config.execution_mode
            )

            # Get the appropriate executor for this config
            executor = self._get_or_create_executor(config)

            # Start the worker using the executor, passing the pre-assigned IDs
            executor_id = executor.start_worker(
                config.worker_type, index, config, db_worker_id=db_worker_id
            )

            if executor_id is None:
                logger.error(f"Failed to start {config.worker_type} worker {index}")
                # Clean up the pre-registered worker record
                self._cleanup_pre_registered_worker(db_worker_id)
                return None

            # No need to wait for registration - worker will update status when ready
            logger.info(
                f"Worker {db_worker_id} started: {config.worker_type}-{index} "
                f"(executor_id: {executor_id[:12] if len(executor_id) > 12 else executor_id})"
            )

            return {
                "executor_id": executor_id,
                "db_worker_id": db_worker_id,
                "config": config,
                "executor": executor,
                "started_at": datetime.now(),
            }

        except Exception as e:
            logger.error(f"Failed to start worker {config.worker_type}-{index}: {e}", exc_info=True)
            # Clean up the pre-registered worker record if it was created
            if db_worker_id is not None:
                self._cleanup_pre_registered_worker(db_worker_id)
            return None

    def _cleanup_pre_registered_worker(self, db_worker_id: int) -> None:
        """Clean up a pre-registered worker record on startup failure.

        Args:
            db_worker_id: Database worker ID to remove
        """
        try:
            conn = self.job_queue._get_conn()
            conn.execute("DELETE FROM workers WHERE id = ?", (db_worker_id,))
            logger.debug(f"Cleaned up pre-registered worker {db_worker_id}")
        except Exception as e:
            logger.warning(f"Failed to clean up pre-registered worker {db_worker_id}: {e}")

    def start_monitoring(self, check_interval: int = 10):
        """Start health monitoring in a background thread.

        Args:
            check_interval: Time between health checks (seconds)
        """
        if self.monitor_thread and self.monitor_thread.is_alive():
            logger.warning("Monitor thread already running")
            return

        self.monitor_thread = threading.Thread(
            target=self._monitor_health, args=(check_interval,), daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"Started health monitoring (check interval: {check_interval}s)")

    def _monitor_health(self, check_interval: int):
        """Monitor worker health and restart if needed.

        Args:
            check_interval: Time between checks (seconds)
        """
        logger.info("Health monitor started")
        stats_check_counter = 0  # Counter for throttling Docker stats collection

        while self.running:
            try:
                conn = self.job_queue._get_conn()
                cursor = conn.execute(
                    """
                    SELECT id, worker_type, container_id, status, last_heartbeat
                    FROM workers
                    WHERE status IN ('busy', 'idle')
                    """
                )

                # Increment stats check counter for throttling
                stats_check_counter += 1

                for row in cursor.fetchall():
                    worker_id = row[0]
                    worker_type = row[1]
                    executor_id = row[2]  # This is executor_id (container_id or direct-*)
                    status = row[3]
                    last_heartbeat = row[4]

                    # Check if heartbeat is stale (no update in 30 seconds)
                    if self._is_heartbeat_stale(last_heartbeat, 30):
                        logger.warning(
                            f"Worker {worker_id} ({worker_type}) has stale heartbeat "
                            f"(last: {last_heartbeat})"
                        )

                        # Determine executor type and get executor
                        is_direct = executor_id.startswith("direct-")
                        executor_type = "direct" if is_direct else "docker"

                        if executor_type not in self.executors:
                            logger.warning(
                                f"No executor available for type {executor_type}, "
                                f"marking worker as dead"
                            )
                            conn.execute(
                                "UPDATE workers SET status = 'dead' WHERE id = ?", (worker_id,)
                            )
                            conn.commit()
                            continue

                        executor = self.executors[executor_type]

                        # Check if worker process/container is still running
                        try:
                            if not executor.is_worker_running(executor_id):
                                logger.error(
                                    f"Worker {worker_id} ({executor_type}) is not running, "
                                    f"marking as dead"
                                )
                                conn.execute(
                                    "UPDATE workers SET status = 'dead' WHERE id = ?", (worker_id,)
                                )
                                conn.commit()
                                continue

                            # Get worker stats (throttled to every 5th check to reduce Docker API calls)
                            # This reduces overhead by 80% while still detecting hung workers
                            stats = None
                            if stats_check_counter % 5 == 0:
                                stats = executor.get_worker_stats(executor_id)

                            if stats and executor_type == "docker":
                                cpu_percent = stats.get("cpu_percent", 0.0)

                                # If CPU < 1% and status is busy, worker is likely hung
                                if cpu_percent < 1.0 and status == "busy":
                                    logger.error(
                                        f"Worker {worker_id} appears hung "
                                        f"(CPU: {cpu_percent:.1f}%, status: busy)"
                                    )
                                    conn.execute(
                                        "UPDATE workers SET status = 'hung' WHERE id = ?",
                                        (worker_id,),
                                    )
                                    conn.commit()

                                    # Optionally restart hung workers
                                    # self._restart_worker(worker_id, executor_id, worker_type)

                        except Exception as e:
                            logger.error(
                                f"Error checking worker {worker_id} health: {e}", exc_info=True
                            )

                time.sleep(check_interval)

            except Exception as e:
                logger.error(f"Health monitoring error: {e}", exc_info=True)
                time.sleep(check_interval)

        logger.info("Health monitor stopped")

    def _is_heartbeat_stale(self, last_heartbeat: str, threshold_seconds: int) -> bool:
        """Check if heartbeat timestamp is older than threshold.

        Args:
            last_heartbeat: ISO format timestamp string
            threshold_seconds: Maximum age in seconds

        Returns:
            True if heartbeat is stale
        """
        try:
            heartbeat_time = datetime.fromisoformat(last_heartbeat)
            now = datetime.now()
            age = (now - heartbeat_time).total_seconds()
            return age > threshold_seconds
        except Exception as e:
            logger.error(f"Error parsing heartbeat timestamp: {e}")
            return True  # Treat parse errors as stale

    def _calculate_cpu_percent(self, stats: dict) -> float:
        """Calculate CPU usage percentage from Docker stats.

        Args:
            stats: Docker stats dictionary

        Returns:
            CPU usage percentage (0-100)
        """
        try:
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
            )

            if system_delta > 0 and cpu_delta > 0:
                num_cpus = len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
                return float((cpu_delta / system_delta) * num_cpus * 100.0)

            return 0.0

        except (KeyError, ZeroDivisionError) as e:
            logger.debug(f"Error calculating CPU percent: {e}")
            return 0.0

    def _restart_worker(self, worker_id: int, container_id: str, worker_type: str):
        """Restart a worker container.

        Args:
            worker_id: Worker ID in database
            container_id: Docker container ID
            worker_type: Worker type
        """
        logger.info(f"Restarting worker {worker_id} ({worker_type})")

        try:
            # Stop and remove old container
            import docker
            import docker.errors

            assert self.docker_client is not None
            try:
                container = self.docker_client.containers.get(container_id)
                container.stop(timeout=5)
                container.remove()
            except docker.errors.NotFound:
                # Container already removed - that's fine
                logger.debug(f"Container {container_id[:12]} not found (already removed)")
            except docker.errors.APIError as e:
                # Docker daemon issue - log but continue
                logger.warning(f"Docker API error stopping container {container_id[:12]}: {e}")
            except Exception as e:
                # Unexpected error - log with details
                logger.error(
                    f"Unexpected error stopping container {container_id[:12]}: {e}", exc_info=True
                )

            # Delete old worker from database (it will be replaced by a new one)
            conn = self.job_queue._get_conn()
            conn.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
            conn.commit()

            # Find the worker config and restart
            for config in self.worker_configs:
                if config.worker_type == worker_type:
                    # Start new worker
                    worker_info = self._start_worker(config, 0)
                    if worker_info:
                        logger.info(f"Successfully restarted worker for {worker_type}")
                    else:
                        logger.error(f"Failed to restart worker for {worker_type}")
                    break

        except Exception as e:
            logger.error(f"Error restarting worker {worker_id}: {e}", exc_info=True)

    def stop_pools(self):
        """Stop all worker pools gracefully."""
        global _atexit_cleanup_disabled

        logger.info("Stopping worker pools")
        self.running = False

        # Disable atexit cleanup since we're doing graceful shutdown
        _atexit_cleanup_disabled = True

        # Wait for monitor thread to stop
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)

        # Stop all workers
        total_stopped = 0
        for worker_type, workers in self.workers.items():
            logger.info(f"Stopping {len(workers)} {worker_type} workers")

            for worker_info in workers:
                try:
                    executor = worker_info["executor"]
                    executor_id = worker_info["executor_id"]

                    # Stop using executor
                    if executor.stop_worker(executor_id):
                        total_stopped += 1

                    # Delete worker from database on graceful shutdown
                    conn = self.job_queue._get_conn()
                    conn.execute(
                        "DELETE FROM workers WHERE id = ?",
                        (worker_info["db_worker_id"],),
                    )
                    conn.commit()

                except Exception as e:
                    logger.error(f"Error stopping worker: {e}")

        # Clean up all executors
        for executor in self.executors.values():
            try:
                executor.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up executor: {e}")

        # Stop the Worker API server if running
        self._stop_worker_api_server()

        logger.info(f"Stopped {total_stopped} workers")

    def _emergency_stop(self):
        """Emergency stop of all workers without waiting for graceful shutdown.

        This method is called by the atexit handler when the process is exiting.
        It attempts to stop workers as quickly as possible, with minimal waiting.

        Note: This method avoids using the logging module because it might be
        called during interpreter shutdown when logging is unavailable.
        """
        self.running = False

        # Don't wait for monitor thread - we're exiting anyway

        # Stop all workers with minimal timeout
        stopped_count = 0
        try:
            workers_dict = getattr(self, "workers", {})
            for _worker_type, workers in workers_dict.items():
                for worker_info in workers:
                    try:
                        executor = worker_info.get("executor")
                        executor_id = worker_info.get("executor_id")

                        if executor and executor_id:
                            # Try to stop worker (executor handles timeout)
                            if executor.stop_worker(executor_id):
                                stopped_count += 1
                    except Exception:
                        # Silently ignore errors during emergency cleanup
                        pass
        except Exception:
            # Silently ignore errors accessing workers dict
            pass

        # Quick cleanup of executors
        try:
            executors = getattr(self, "executors", {})
            for executor in executors.values():
                try:
                    executor.cleanup()
                except Exception:
                    pass
        except Exception:
            pass

        # Stop the Worker API server if running (silently)
        try:
            api_server = getattr(self, "_api_server", None)
            if api_server is not None:
                api_server.stop(timeout=1.0)
        except Exception:
            pass

    def close(self):
        """Close database connections.

        This method should be called when the pool manager is no longer needed
        to avoid ResourceWarning about unclosed database connections.
        """
        if hasattr(self, "job_queue") and self.job_queue is not None:
            self.job_queue.close()

    def get_worker_stats(self) -> dict:
        """Get statistics about all workers.

        Returns:
            Dictionary with worker statistics by type and status
        """
        conn = self.job_queue._get_conn()
        cursor = conn.execute(
            """
            SELECT worker_type, status, COUNT(*) as count
            FROM workers
            GROUP BY worker_type, status
            """
        )

        stats: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            worker_type = row[0]
            status = row[1]
            count = row[2]

            if worker_type not in stats:
                stats[worker_type] = {}

            stats[worker_type][status] = count

        return stats


if __name__ == "__main__":
    """Example CLI for running worker pools."""
    import os

    # Configure logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Example configuration
    db_path = Path(os.getenv("CLX_DB_PATH", "clx_jobs.db"))
    workspace_path = Path(os.getenv("CLX_WORKSPACE_PATH", os.getcwd()))

    logger.info("Configuration:")
    logger.info(f"  Database path: {db_path.absolute()}")
    logger.info(f"  Workspace path: {workspace_path.absolute()}")

    # Initialize database if it doesn't exist
    from clx.infrastructure.database.schema import init_database

    if not db_path.exists():
        logger.info(f"Initializing database at {db_path}")
        init_database(db_path)
    else:
        logger.info(f"Using existing database at {db_path}")

    # Define worker configurations
    worker_configs = [
        WorkerConfig(
            worker_type="notebook",
            image="mhoelzl/clx-notebook-processor:latest",
            count=2,
            memory_limit="1g",
        ),
        WorkerConfig(
            worker_type="drawio",
            image="mhoelzl/clx-drawio-converter:latest",
            count=1,
            memory_limit="512m",
        ),
        WorkerConfig(
            worker_type="plantuml",
            image="mhoelzl/clx-plantuml-converter:latest",
            count=1,
            memory_limit="512m",
        ),
    ]

    # Create pool manager
    manager = WorkerPoolManager(
        db_path=db_path, workspace_path=workspace_path, worker_configs=worker_configs
    )

    try:
        logger.info("Starting worker pools...")
        manager.start_pools()

        logger.info("Starting health monitoring...")
        manager.start_monitoring(check_interval=10)

        logger.info("Worker pools started. Press Ctrl+C to stop.")

        # Keep running
        import time

        while manager.running:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        manager.stop_pools()
        logger.info("Stopped.")
