"""Worker pool manager for coordinating long-lived workers.

This module provides the WorkerPoolManager class that manages workers
running in different modes (Docker containers or direct processes),
monitors their health, and handles restarts.
"""

from typing import TYPE_CHECKING
import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta

# Import docker only when type checking or when actually needed
if TYPE_CHECKING:
    import docker

from clx.infrastructure.database.job_queue import JobQueue
from clx.infrastructure.workers.worker_executor import (
    WorkerConfig,
    WorkerExecutor,
    DockerWorkerExecutor,
    DirectWorkerExecutor
)

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
        worker_configs: List[WorkerConfig],
        network_name: str = 'clx_app-network',
        log_level: str = 'INFO',
        max_startup_concurrency: Optional[int] = None
    ):
        """Initialize worker pool manager.

        Args:
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory
            worker_configs: List of worker configurations
            network_name: Docker network name (for docker mode)
            log_level: Logging level for workers
            max_startup_concurrency: Maximum number of workers to start concurrently.
                Defaults to CLX_MAX_WORKER_STARTUP_CONCURRENCY env var or 10.
        """
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.worker_configs = worker_configs
        self.network_name = network_name
        self.log_level = log_level

        # Determine max startup concurrency
        if max_startup_concurrency is None:
            max_startup_concurrency = int(
                os.getenv('CLX_MAX_WORKER_STARTUP_CONCURRENCY', '10')
            )
        self.max_startup_concurrency = max_startup_concurrency

        self.docker_client = None  # Lazily initialized if needed
        self.job_queue = JobQueue(db_path)
        self.workers: Dict[str, List[Dict]] = {}  # worker_type -> [worker_info]
        self.executors: Dict[str, WorkerExecutor] = {}  # execution_mode -> executor
        self.running = True
        self.monitor_thread: Optional[threading.Thread] = None

    def _get_or_create_executor(self, config: WorkerConfig) -> WorkerExecutor:
        """Get or create an executor for the given configuration.

        Args:
            config: Worker configuration

        Returns:
            WorkerExecutor instance for the execution mode
        """
        mode = config.execution_mode

        if mode not in self.executors:
            if mode == 'docker':
                # Import docker only when needed
                import docker

                # Lazily initialize Docker client
                if self.docker_client is None:
                    self.docker_client = docker.from_env()

                self.executors[mode] = DockerWorkerExecutor(
                    docker_client=self.docker_client,
                    db_path=self.db_path,
                    workspace_path=self.workspace_path,
                    network_name=self.network_name,
                    log_level=self.log_level
                )
            elif mode == 'direct':
                self.executors[mode] = DirectWorkerExecutor(
                    db_path=self.db_path,
                    workspace_path=self.workspace_path,
                    log_level=self.log_level
                )
            else:
                raise ValueError(f"Unknown execution mode: {mode}")

        return self.executors[mode]

    def cleanup_stale_workers(self):
        """Clean up stale worker records from the database.

        This removes worker records for containers/processes that no longer exist,
        preventing issues on startup.
        """
        logger.info("Cleaning up stale worker records from database")

        conn = self.job_queue._get_conn()
        cursor = conn.execute("SELECT id, container_id FROM workers")
        workers = cursor.fetchall()

        if not workers:
            logger.info("No existing worker records found")
            return

        logger.info(f"Found {len(workers)} existing worker record(s), checking status...")

        # Use WorkerDiscovery to check worker health (includes process checks)
        from clx.infrastructure.workers.discovery import WorkerDiscovery

        # Use existing executors for health checking
        # NOTE: Don't create new executor instances - they won't have process/container tracking!
        discovery = WorkerDiscovery(self.db_path, executors=self.executors)
        healthy_workers = discovery.discover_workers()
        healthy_ids = {w.db_id for w in healthy_workers if w.is_healthy}

        # Initialize Docker client if we need to check containers
        docker_client = None
        has_docker_workers = False

        removed_count = 0
        for worker_id, container_id in workers:
            # Check if this is a direct worker or docker worker
            is_direct = container_id.startswith('direct-')

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
                        docker_client = docker.from_env()
                        has_docker_workers = True
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
                    if container.status != 'running':
                        logger.info(
                            f"Worker {worker_id} container {container_id[:12]} is {container.status}, "
                            f"removing container and worker record"
                        )
                        try:
                            container.stop(timeout=2)
                            container.remove()
                        except Exception:
                            pass  # Container might already be stopped

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

    def _ensure_network_exists(self):
        """Ensure Docker network exists, create if needed."""
        import docker

        # Lazily initialize Docker client if needed
        if self.docker_client is None:
            self.docker_client = docker.from_env()

        try:
            self.docker_client.networks.get(self.network_name)
            logger.info(f"Docker network '{self.network_name}' exists")
        except docker.errors.NotFound:
            logger.info(f"Docker network '{self.network_name}' not found, creating...")
            self.docker_client.networks.create(
                self.network_name,
                driver='bridge',
                check_duplicate=True
            )
            logger.info(f"Created Docker network '{self.network_name}'")
        except Exception as e:
            logger.error(f"Error checking/creating Docker network: {e}", exc_info=True)
            raise

    def start_pools(self):
        """Start all worker pools defined in worker_configs with parallel startup."""
        logger.info(f"Starting worker pools with {len(self.worker_configs)} configurations")

        # Check if we need Docker and ensure network exists
        needs_docker = any(c.execution_mode == 'docker' for c in self.worker_configs)
        if needs_docker:
            self._ensure_network_exists()

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
            if config.execution_mode == 'docker':
                mode_desc += f", image: {config.image}, memory: {config.memory_limit}"
            logger.info(
                f"Configured {config.count} {config.worker_type} workers ({mode_desc})"
            )

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
                executor.submit(self._start_worker, config, i): (config, i)
                for config, i in tasks
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
                            f"✓ Started {config.worker_type}-{i} "
                            f"({completed}/{total_workers})"
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
                        exc_info=True
                    )

        duration = time.time() - start_time

        # Report results
        logger.info(
            f"Started {len(started_workers)}/{total_workers} worker(s) in {duration:.1f}s"
        )

        if failed_workers:
            logger.error(
                f"Failed to start {len(failed_workers)} worker(s): {failed_workers}"
            )

    def _wait_for_worker_registration(
        self,
        container_id: str,
        timeout: int = 10
    ) -> Optional[int]:
        """Wait for a worker to register itself in the database.

        Args:
            container_id: Docker container ID (full or short)
            timeout: Maximum time to wait in seconds

        Returns:
            Worker ID if registered, None if timeout
        """
        start_time = time.time()
        poll_interval = 0.5
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
                (container_id, short_id)
            )
            row = cursor.fetchone()

            if row:
                return row[0]

            time.sleep(poll_interval)

        return None

    def _start_worker(
        self,
        config: WorkerConfig,
        index: int
    ) -> Optional[Dict]:
        """Start a single worker using the appropriate executor.

        Args:
            config: Worker configuration
            index: Worker index (for naming)

        Returns:
            Dictionary with worker info (executor_id, db_worker_id, config) or None if failed
        """
        try:
            # Get the appropriate executor for this config
            executor = self._get_or_create_executor(config)

            # Start the worker using the executor
            executor_id = executor.start_worker(config.worker_type, index, config)

            if executor_id is None:
                logger.error(f"Failed to start {config.worker_type} worker {index}")
                return None

            # Wait for worker to self-register in database (timeout after 10 seconds)
            db_worker_id = self._wait_for_worker_registration(executor_id, timeout=10)

            if db_worker_id is None:
                logger.error(
                    f"Worker {config.worker_type}-{index} (executor_id: {executor_id}) "
                    f"failed to register in database."
                )

                # Try to get debug info
                if config.execution_mode == 'docker':
                    logger.error(f"Check container logs with: docker logs clx-{config.worker_type}-worker-{index}")
                else:
                    logger.error(f"Direct worker failed to register. Check worker logs.")

                # Stop the worker since it failed to register
                executor.stop_worker(executor_id)
                return None

            logger.info(
                f"Worker {db_worker_id} registered: {config.worker_type}-{index} "
                f"(executor_id: {executor_id[:12] if len(executor_id) > 12 else executor_id})"
            )

            return {
                'executor_id': executor_id,
                'db_worker_id': db_worker_id,
                'config': config,
                'executor': executor,
                'started_at': datetime.now()
            }

        except Exception as e:
            logger.error(
                f"Failed to start worker {config.worker_type}-{index}: {e}",
                exc_info=True
            )
            return None

    def start_monitoring(self, check_interval: int = 10):
        """Start health monitoring in a background thread.

        Args:
            check_interval: Time between health checks (seconds)
        """
        if self.monitor_thread and self.monitor_thread.is_alive():
            logger.warning("Monitor thread already running")
            return

        self.monitor_thread = threading.Thread(
            target=self._monitor_health,
            args=(check_interval,),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info(f"Started health monitoring (check interval: {check_interval}s)")

    def _monitor_health(self, check_interval: int):
        """Monitor worker health and restart if needed.

        Args:
            check_interval: Time between checks (seconds)
        """
        logger.info("Health monitor started")

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

                current_time = datetime.now()

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
                        is_direct = executor_id.startswith('direct-')
                        executor_type = 'direct' if is_direct else 'docker'

                        if executor_type not in self.executors:
                            logger.warning(
                                f"No executor available for type {executor_type}, "
                                f"marking worker as dead"
                            )
                            conn.execute(
                                "UPDATE workers SET status = 'dead' WHERE id = ?",
                                (worker_id,)
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
                                    "UPDATE workers SET status = 'dead' WHERE id = ?",
                                    (worker_id,)
                                )
                                conn.commit()
                                continue

                            # Get worker stats
                            stats = executor.get_worker_stats(executor_id)

                            if stats and executor_type == 'docker':
                                cpu_percent = stats.get('cpu_percent', 0.0)

                                # If CPU < 1% and status is busy, worker is likely hung
                                if cpu_percent < 1.0 and status == 'busy':
                                    logger.error(
                                        f"Worker {worker_id} appears hung "
                                        f"(CPU: {cpu_percent:.1f}%, status: busy)"
                                    )
                                    conn.execute(
                                        "UPDATE workers SET status = 'hung' WHERE id = ?",
                                        (worker_id,)
                                    )
                                    conn.commit()

                                    # Optionally restart hung workers
                                    # self._restart_worker(worker_id, executor_id, worker_type)

                        except Exception as e:
                            logger.error(
                                f"Error checking worker {worker_id} health: {e}",
                                exc_info=True
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
                stats['cpu_stats']['cpu_usage']['total_usage'] -
                stats['precpu_stats']['cpu_usage']['total_usage']
            )
            system_delta = (
                stats['cpu_stats']['system_cpu_usage'] -
                stats['precpu_stats']['system_cpu_usage']
            )

            if system_delta > 0 and cpu_delta > 0:
                num_cpus = len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))
                return (cpu_delta / system_delta) * num_cpus * 100.0

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
            try:
                container = self.docker_client.containers.get(container_id)
                container.stop(timeout=5)
                container.remove()
            except Exception as e:
                logger.warning(f"Error stopping container {container_id[:12]}: {e}")

            # Mark old worker as dead
            conn = self.job_queue._get_conn()
            conn.execute(
                "UPDATE workers SET status = 'dead' WHERE id = ?",
                (worker_id,)
            )
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
        logger.info("Stopping worker pools")
        self.running = False

        # Wait for monitor thread to stop
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)

        # Stop all workers
        total_stopped = 0
        for worker_type, workers in self.workers.items():
            logger.info(f"Stopping {len(workers)} {worker_type} workers")

            for worker_info in workers:
                try:
                    executor = worker_info['executor']
                    executor_id = worker_info['executor_id']

                    # Stop using executor
                    if executor.stop_worker(executor_id):
                        total_stopped += 1

                    # Mark as dead in database
                    conn = self.job_queue._get_conn()
                    conn.execute(
                        "UPDATE workers SET status = 'dead' WHERE id = ?",
                        (worker_info['db_worker_id'],)
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

        logger.info(f"Stopped {total_stopped} workers")

    def get_worker_stats(self) -> Dict:
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

        stats = {}
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
    import sys
    import os

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Example configuration
    db_path = Path(os.getenv('CLX_DB_PATH', 'clx_jobs.db'))
    workspace_path = Path(os.getenv('CLX_WORKSPACE_PATH', os.getcwd()))

    logger.info(f"Configuration:")
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
            worker_type='notebook',
            image='mhoelzl/clx-notebook-processor:0.3.1',
            count=2,
            memory_limit='1g'
        ),
        WorkerConfig(
            worker_type='drawio',
            image='mhoelzl/clx-drawio-converter:0.3.1',
            count=1,
            memory_limit='512m'
        ),
        WorkerConfig(
            worker_type='plantuml',
            image='mhoelzl/clx-plantuml-converter:0.3.1',
            count=1,
            memory_limit='512m'
        ),
    ]

    # Create pool manager
    manager = WorkerPoolManager(
        db_path=db_path,
        workspace_path=workspace_path,
        worker_configs=worker_configs
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
