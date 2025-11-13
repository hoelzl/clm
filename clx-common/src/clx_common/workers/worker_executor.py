"""Worker executor abstractions for different execution modes.

This module provides abstract and concrete implementations for executing workers
in different modes (Docker containers or direct processes).
"""

from typing import TYPE_CHECKING
import os
import sys
import uuid
import signal
import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass

# Import docker only when type checking or when actually needed
if TYPE_CHECKING:
    import docker

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    """Configuration for a worker pool.

    Attributes:
        worker_type: Type of worker ('notebook', 'drawio', 'plantuml')
        count: Number of worker instances to run
        execution_mode: Execution mode ('docker' or 'direct')
        image: Docker image name (required for docker mode)
        memory_limit: Memory limit per container (e.g., '1g', '512m') - Docker only
        max_job_time: Maximum time a job can run before considered hung (seconds)
    """
    worker_type: str
    count: int
    execution_mode: str = 'docker'
    image: Optional[str] = None
    memory_limit: str = '1g'
    max_job_time: int = 600

    def __post_init__(self):
        """Validate configuration."""
        if self.execution_mode not in ('docker', 'direct'):
            raise ValueError(f"Invalid execution_mode: {self.execution_mode}")

        if self.execution_mode == 'docker' and not self.image:
            raise ValueError(f"Docker execution mode requires 'image' to be specified")


class WorkerExecutor(ABC):
    """Abstract base class for worker executors.

    This defines the interface that both Docker and direct process executors
    must implement.
    """

    @abstractmethod
    def start_worker(
        self,
        worker_type: str,
        index: int,
        config: WorkerConfig
    ) -> Optional[str]:
        """Start a worker and return its unique identifier.

        Args:
            worker_type: Type of worker to start
            index: Worker index for naming
            config: Worker configuration

        Returns:
            Unique worker identifier if successful, None otherwise
        """
        pass

    @abstractmethod
    def stop_worker(self, worker_id: str) -> bool:
        """Stop a specific worker.

        Args:
            worker_id: Identifier returned by start_worker

        Returns:
            True if stopped successfully, False otherwise
        """
        pass

    @abstractmethod
    def is_worker_running(self, worker_id: str) -> bool:
        """Check if a worker is currently running.

        Args:
            worker_id: Worker identifier

        Returns:
            True if running, False otherwise
        """
        pass

    @abstractmethod
    def get_worker_stats(self, worker_id: str) -> Optional[Dict]:
        """Get resource usage statistics for a worker.

        Args:
            worker_id: Worker identifier

        Returns:
            Dictionary with stats (cpu_percent, memory_mb, etc.) or None
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up all workers managed by this executor."""
        pass


class DockerWorkerExecutor(WorkerExecutor):
    """Executor for running workers in Docker containers."""

    def __init__(
        self,
        docker_client: "docker.DockerClient",
        db_path: Path,
        workspace_path: Path,
        network_name: str = 'clx_app-network',
        log_level: str = 'INFO'
    ):
        """Initialize Docker executor.

        Args:
            docker_client: Docker client instance
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory
            network_name: Docker network name
            log_level: Logging level for workers
        """
        self.docker_client = docker_client
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.network_name = network_name
        self.log_level = log_level
        self.containers: Dict[str, "docker.models.containers.Container"] = {}

    def start_worker(
        self,
        worker_type: str,
        index: int,
        config: WorkerConfig
    ) -> Optional[str]:
        """Start a worker in a Docker container."""
        import docker

        container_name = f"clx-{worker_type}-worker-{index}"

        try:
            # Check if container already exists
            try:
                existing = self.docker_client.containers.get(container_name)
                logger.warning(f"Container {container_name} already exists, removing...")
                existing.stop(timeout=5)
                existing.remove()
            except docker.errors.NotFound:
                pass

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
                    'WORKER_TYPE': worker_type,
                    'DB_PATH': f'/db/{db_filename}',
                    'LOG_LEVEL': self.log_level,
                    'USE_SQLITE_QUEUE': 'true'
                },
                network=self.network_name
            )

            logger.info(f"Started container: {container_name} ({container.id[:12]})")

            # Store container reference using container ID as worker_id
            self.containers[container.id] = container
            return container.id

        except Exception as e:
            logger.error(f"Failed to start worker {container_name}: {e}", exc_info=True)
            return None

    def stop_worker(self, worker_id: str) -> bool:
        """Stop a Docker container worker."""
        import docker

        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            container.reload()
            if container.status == 'running':
                container.stop(timeout=10)

            container.remove()

            if worker_id in self.containers:
                del self.containers[worker_id]

            logger.info(f"Stopped worker container: {worker_id[:12]}")
            return True

        except docker.errors.NotFound:
            logger.warning(f"Container {worker_id[:12]} not found")
            if worker_id in self.containers:
                del self.containers[worker_id]
            return False
        except Exception as e:
            logger.error(f"Error stopping worker {worker_id[:12]}: {e}", exc_info=True)
            return False

    def is_worker_running(self, worker_id: str) -> bool:
        """Check if Docker container is running."""
        import docker

        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            container.reload()
            return container.status == 'running'

        except docker.errors.NotFound:
            return False
        except Exception as e:
            logger.error(f"Error checking worker status: {e}")
            return False

    def get_worker_stats(self, worker_id: str) -> Optional[Dict]:
        """Get Docker container resource statistics."""
        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            stats = container.stats(stream=False)

            # Calculate CPU percentage
            cpu_delta = (
                stats['cpu_stats']['cpu_usage']['total_usage'] -
                stats['precpu_stats']['cpu_usage']['total_usage']
            )
            system_delta = (
                stats['cpu_stats']['system_cpu_usage'] -
                stats['precpu_stats']['system_cpu_usage']
            )

            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                num_cpus = len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))
                cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0

            # Calculate memory usage in MB
            memory_mb = stats['memory_stats'].get('usage', 0) / (1024 * 1024)

            return {
                'cpu_percent': cpu_percent,
                'memory_mb': memory_mb
            }

        except Exception as e:
            logger.error(f"Error getting worker stats: {e}")
            return None

    def cleanup(self) -> None:
        """Stop and remove all managed containers."""
        logger.info(f"Cleaning up {len(self.containers)} Docker workers")

        for worker_id in list(self.containers.keys()):
            self.stop_worker(worker_id)


class DirectWorkerExecutor(WorkerExecutor):
    """Executor for running workers as direct processes."""

    # Map worker types to their Python module entry points
    MODULE_MAP = {
        'notebook': 'nb',
        'drawio': 'drawio_converter',
        'plantuml': 'plantuml_converter'
    }

    def __init__(
        self,
        db_path: Path,
        workspace_path: Path,
        log_level: str = 'INFO'
    ):
        """Initialize direct process executor.

        Args:
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory
            log_level: Logging level for workers
        """
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.log_level = log_level
        self.processes: Dict[str, subprocess.Popen] = {}
        self.worker_info: Dict[str, Dict] = {}  # worker_id -> {type, index, etc.}

    def start_worker(
        self,
        worker_type: str,
        index: int,
        config: WorkerConfig
    ) -> Optional[str]:
        """Start a worker as a direct subprocess."""
        # Generate unique worker ID
        worker_id = f"direct-{worker_type}-{index}-{uuid.uuid4().hex[:8]}"

        try:
            # Get the module to run
            if worker_type not in self.MODULE_MAP:
                logger.error(f"Unknown worker type: {worker_type}")
                return None

            module = self.MODULE_MAP[worker_type]

            # Prepare environment variables
            env = os.environ.copy()
            env.update({
                'WORKER_TYPE': worker_type,
                'WORKER_ID': worker_id,  # Explicit ID for direct execution
                'DB_PATH': str(self.db_path.absolute()),
                'WORKSPACE_PATH': str(self.workspace_path.absolute()),
                'LOG_LEVEL': self.log_level,
                'USE_SQLITE_QUEUE': 'true'
            })

            # Build command
            cmd = [sys.executable, '-m', module]

            logger.info(f"Starting direct worker: {worker_id}")
            logger.debug(f"Command: {' '.join(cmd)}")
            logger.debug(f"Environment: WORKER_TYPE={worker_type}, WORKER_ID={worker_id}")

            # Start the process
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid if sys.platform != 'win32' else None
            )

            # Store process and info
            self.processes[worker_id] = process
            self.worker_info[worker_id] = {
                'type': worker_type,
                'index': index,
                'pid': process.pid
            }

            logger.info(
                f"Started direct worker: {worker_id} (PID: {process.pid})"
            )

            return worker_id

        except Exception as e:
            logger.error(f"Failed to start direct worker {worker_type}-{index}: {e}", exc_info=True)
            return None

    def stop_worker(self, worker_id: str) -> bool:
        """Stop a direct process worker."""
        try:
            if worker_id not in self.processes:
                logger.warning(f"Worker {worker_id} not found in process list")
                return False

            process = self.processes[worker_id]

            # Check if already terminated
            if process.poll() is not None:
                logger.info(f"Worker {worker_id} already terminated")
                del self.processes[worker_id]
                del self.worker_info[worker_id]
                return True

            # Send SIGTERM for graceful shutdown
            logger.info(f"Stopping worker {worker_id} (PID: {process.pid})")

            if sys.platform != 'win32':
                # On Unix, kill the process group to handle any child processes
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                # On Windows, just terminate the process
                process.terminate()

            # Wait for process to exit (with timeout)
            try:
                process.wait(timeout=10)
                logger.info(f"Worker {worker_id} stopped gracefully")
            except subprocess.TimeoutExpired:
                logger.warning(f"Worker {worker_id} did not stop gracefully, killing")
                if sys.platform != 'win32' and hasattr(signal, 'SIGKILL'):
                    # Use SIGKILL on Unix if available
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    # Fall back to process.kill() on Windows or if SIGKILL not available
                    process.kill()
                process.wait()

            # Clean up
            del self.processes[worker_id]
            del self.worker_info[worker_id]

            return True

        except Exception as e:
            logger.error(f"Error stopping worker {worker_id}: {e}", exc_info=True)
            # Clean up even on error
            if worker_id in self.processes:
                del self.processes[worker_id]
            if worker_id in self.worker_info:
                del self.worker_info[worker_id]
            return False

    def is_worker_running(self, worker_id: str) -> bool:
        """Check if direct process worker is running."""
        if worker_id not in self.processes:
            return False

        process = self.processes[worker_id]
        return process.poll() is None

    def get_worker_stats(self, worker_id: str) -> Optional[Dict]:
        """Get resource usage statistics for a direct process worker.

        Note: This is a simplified version. For production use, consider
        using psutil for more accurate resource monitoring.
        """
        if worker_id not in self.processes:
            return None

        try:
            process = self.processes[worker_id]

            # Basic check - just return if process is alive
            # For more detailed stats, would need psutil library
            is_alive = process.poll() is None

            return {
                'cpu_percent': 0.0,  # Would need psutil for accurate CPU
                'memory_mb': 0.0,     # Would need psutil for accurate memory
                'is_alive': is_alive,
                'pid': process.pid
            }

        except Exception as e:
            logger.error(f"Error getting worker stats: {e}")
            return None

    def cleanup(self) -> None:
        """Stop all managed worker processes."""
        logger.info(f"Cleaning up {len(self.processes)} direct workers")

        for worker_id in list(self.processes.keys()):
            self.stop_worker(worker_id)
