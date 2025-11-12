"""Worker pool manager for coordinating long-lived worker containers.

This module provides the WorkerPoolManager class that manages Docker containers
running workers, monitors their health, and handles restarts.
"""

import docker
import logging
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from clx_common.database.job_queue import JobQueue

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    """Configuration for a worker pool.

    Attributes:
        worker_type: Type of worker ('notebook', 'drawio', 'plantuml')
        image: Docker image name
        count: Number of worker containers to run
        memory_limit: Memory limit per container (e.g., '1g', '512m')
        max_job_time: Maximum time a job can run before considered hung (seconds)
    """
    worker_type: str
    image: str
    count: int
    memory_limit: str = '1g'
    max_job_time: int = 600


class WorkerPoolManager:
    """Manages worker pools using Docker containers.

    This class is responsible for:
    - Starting and stopping worker containers
    - Registering workers in the database
    - Monitoring worker health
    - Restarting hung or dead workers
    """

    def __init__(
        self,
        db_path: Path,
        workspace_path: Path,
        worker_configs: List[WorkerConfig],
        network_name: str = 'clx_app-network'
    ):
        """Initialize worker pool manager.

        Args:
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory (mounted in containers)
            worker_configs: List of worker configurations
            network_name: Docker network name to connect containers to
        """
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.worker_configs = worker_configs
        self.network_name = network_name
        self.docker_client = docker.from_env()
        self.job_queue = JobQueue(db_path)
        self.workers: Dict[str, List[Dict]] = {}  # worker_type -> [worker_info]
        self.running = True
        self.monitor_thread: Optional[threading.Thread] = None

    def cleanup_stale_workers(self):
        """Clean up stale worker records from the database.

        This removes worker records for containers that no longer exist,
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

        removed_count = 0
        for worker_id, container_id in workers:
            try:
                # Check if container still exists
                container = self.docker_client.containers.get(container_id)
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
        """Start all worker pools defined in worker_configs."""
        logger.info(f"Starting worker pools with {len(self.worker_configs)} configurations")

        # Ensure Docker network exists
        self._ensure_network_exists()

        # Clean up any stale worker records first
        self.cleanup_stale_workers()

        for config in self.worker_configs:
            logger.info(
                f"Starting {config.count} {config.worker_type} workers "
                f"(image: {config.image}, memory: {config.memory_limit})"
            )
            self.workers[config.worker_type] = []

            for i in range(config.count):
                worker_info = self._start_worker(config, i)
                if worker_info:
                    self.workers[config.worker_type].append(worker_info)

        logger.info(
            f"Started {sum(len(workers) for workers in self.workers.values())} workers total"
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
        """Start a single worker container.

        Args:
            config: Worker configuration
            index: Worker index (for naming)

        Returns:
            Dictionary with worker info (container, worker_id, config) or None if failed
        """
        container_name = f"clx-{config.worker_type}-worker-{index}"

        try:
            # Check if container already exists
            try:
                existing = self.docker_client.containers.get(container_name)
                logger.warning(f"Container {container_name} already exists, removing...")
                existing.stop(timeout=5)
                existing.remove()
            except docker.errors.NotFound:
                pass

            # Start container
            # Note: The worker will self-register in the database when it starts
            # Mount the database directory, not the file (Windows compatibility)
            db_dir = self.db_path.parent.absolute()
            db_filename = self.db_path.name

            logger.debug(
                f"Mounting volumes for {container_name}:\n"
                f"  Workspace: {self.workspace_path.absolute()} -> /workspace\n"
                f"  Database:  {db_dir} -> /db\n"
                f"  DB_PATH env: /db/{db_filename}"
            )

            container = self.docker_client.containers.run(
                config.image,
                name=container_name,
                detach=True,
                remove=False,
                mem_limit=config.memory_limit,
                volumes={
                    str(self.workspace_path.absolute()): {'bind': '/workspace', 'mode': 'rw'},
                    str(db_dir): {'bind': '/db', 'mode': 'rw'}
                },
                environment={
                    'WORKER_TYPE': config.worker_type,
                    'DB_PATH': f'/db/{db_filename}',
                    'LOG_LEVEL': 'INFO',
                    'USE_SQLITE_QUEUE': 'true'
                },
                network=self.network_name
            )

            logger.info(f"Started container: {container_name} ({container.id[:12]})")

            # Wait for worker to self-register in database (timeout after 10 seconds)
            worker_id = self._wait_for_worker_registration(container.id, timeout=10)

            if worker_id is None:
                logger.error(
                    f"Worker container {container_name} failed to register in database. "
                    f"Check container logs with: docker logs {container_name}"
                )

                # Get container logs for debugging
                try:
                    container.reload()
                    logs = container.logs(tail=50).decode('utf-8', errors='replace')
                    logger.error(f"Container {container_name} logs:\n{logs}")

                    # Check container status
                    logger.error(
                        f"Container {container_name} status: {container.status}"
                    )
                except Exception as e:
                    logger.error(f"Failed to get container logs: {e}")

                # Container is running but worker didn't register - likely crashed
                try:
                    container.stop(timeout=5)
                    container.remove()
                except Exception as e:
                    logger.warning(f"Error stopping/removing container: {e}")

                return None

            logger.info(
                f"Worker {worker_id} registered: {container_name} ({container.id[:12]})"
            )

            return {
                'container': container,
                'worker_id': worker_id,
                'config': config,
                'started_at': datetime.now()
            }

        except Exception as e:
            logger.error(f"Failed to start worker {container_name}: {e}", exc_info=True)
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
                    container_id = row[2]
                    status = row[3]
                    last_heartbeat = row[4]

                    # Check if heartbeat is stale (no update in 30 seconds)
                    if self._is_heartbeat_stale(last_heartbeat, 30):
                        logger.warning(
                            f"Worker {worker_id} ({worker_type}) has stale heartbeat "
                            f"(last: {last_heartbeat})"
                        )

                        # Check container health
                        try:
                            container = self.docker_client.containers.get(container_id)
                            container.reload()

                            # Check if container is still running
                            if container.status != 'running':
                                logger.error(
                                    f"Worker {worker_id} container is {container.status}, "
                                    f"marking as dead"
                                )
                                conn.execute(
                                    "UPDATE workers SET status = 'dead' WHERE id = ?",
                                    (worker_id,)
                                )
                                conn.commit()
                                continue

                            # Get container stats
                            stats = container.stats(stream=False)
                            cpu_percent = self._calculate_cpu_percent(stats)

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
                                # self._restart_worker(worker_id, container_id, worker_type)

                        except docker.errors.NotFound:
                            logger.error(
                                f"Worker {worker_id} container {container_id[:12]} not found, "
                                f"marking as dead"
                            )
                            conn.execute(
                                "UPDATE workers SET status = 'dead' WHERE id = ?",
                                (worker_id,)
                            )
                            conn.commit()

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

        # Stop all containers
        total_stopped = 0
        for worker_type, workers in self.workers.items():
            logger.info(f"Stopping {len(workers)} {worker_type} workers")

            for worker_info in workers:
                try:
                    container = worker_info['container']
                    container.reload()

                    if container.status == 'running':
                        container.stop(timeout=10)

                    container.remove()
                    total_stopped += 1

                    # Mark as dead in database
                    conn = self.job_queue._get_conn()
                    conn.execute(
                        "UPDATE workers SET status = 'dead' WHERE id = ?",
                        (worker_info['worker_id'],)
                    )
                    conn.commit()

                except Exception as e:
                    logger.error(f"Error stopping worker: {e}")

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
    from clx_common.database.schema import init_database
    if not db_path.exists():
        logger.info(f"Initializing database at {db_path}")
        init_database(db_path)
    else:
        logger.info(f"Using existing database at {db_path}")

    # Define worker configurations
    worker_configs = [
        WorkerConfig(
            worker_type='notebook',
            image='notebook-processor:0.2.2',
            count=2,
            memory_limit='1g'
        ),
        WorkerConfig(
            worker_type='drawio',
            image='drawio-converter:0.2.2',
            count=1,
            memory_limit='512m'
        ),
        WorkerConfig(
            worker_type='plantuml',
            image='plantuml-converter:0.2.2',
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
