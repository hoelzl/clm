"""Worker executor abstractions for different execution modes.

This module provides abstract and concrete implementations for executing workers
in different modes (Docker containers or direct processes).
"""

import glob
import logging
import os
import signal
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# Note: docker package is optional - may not be installed
# Type annotations use string literals to avoid import errors

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
    execution_mode: str = "docker"
    image: str | None = None
    memory_limit: str = "1g"
    max_job_time: int = 600

    def __post_init__(self):
        """Validate configuration."""
        if self.execution_mode not in ("docker", "direct"):
            raise ValueError(f"Invalid execution_mode: {self.execution_mode}")

        if self.execution_mode == "docker" and not self.image:
            raise ValueError("Docker execution mode requires 'image' to be specified")


class WorkerExecutor(ABC):
    """Abstract base class for worker executors.

    This defines the interface that both Docker and direct process executors
    must implement.
    """

    @abstractmethod
    def start_worker(self, worker_type: str, index: int, config: WorkerConfig) -> str | None:
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
    def get_worker_stats(self, worker_id: str) -> dict | None:
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

    def get_container_logs(self, worker_id: str, tail: int = 100) -> str | None:
        """Get logs from a worker (Docker only).

        This is a non-abstract method with a default implementation that
        returns None. Docker executor overrides this to return actual logs.

        Args:
            worker_id: Worker identifier
            tail: Number of lines to get from end

        Returns:
            Log output as string, or None if unavailable
        """
        return None


class DockerWorkerExecutor(WorkerExecutor):
    """Executor for running workers in Docker containers."""

    def __init__(
        self,
        docker_client: Any,  # docker.DockerClient when docker is installed
        db_path: Path,
        workspace_path: Path,
        data_dir: Path | None = None,
        network_name: str = "clx_app-network",
        log_level: str = "INFO",
    ):
        """Initialize Docker executor.

        Args:
            docker_client: Docker client instance
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory (output, mounted at /workspace)
            data_dir: Path to source data directory (mounted at /source, read-only)
            network_name: Docker network name
            log_level: Logging level for workers
        """
        self.docker_client = docker_client
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.data_dir = data_dir
        self.network_name = network_name
        self.log_level = log_level
        self.containers: dict[str, Any] = {}  # Container objects when docker is installed

    def start_worker(self, worker_type: str, index: int, config: WorkerConfig) -> str | None:
        """Start a worker in a Docker container."""
        import docker
        import docker.errors  # type: ignore[import-not-found]

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

            # Workers communicate via REST API instead of direct SQLite access
            # This solves the SQLite WAL mode issues on Windows Docker
            from clx.infrastructure.api.server import DEFAULT_PORT

            api_url = f"http://host.docker.internal:{DEFAULT_PORT}"

            # Build volume mounts
            volumes = {
                str(self.workspace_path.absolute()): {"bind": "/workspace", "mode": "rw"},
            }

            # Build environment variables
            environment = {
                "WORKER_TYPE": worker_type,
                "CLX_API_URL": api_url,  # Use REST API instead of direct SQLite
                "CLX_HOST_WORKSPACE": str(
                    self.workspace_path.absolute()
                ),  # For output path conversion
                "LOG_LEVEL": self.log_level,
                "PYTHONUNBUFFERED": "1",  # Enable immediate log output
            }

            # Mount source directory if provided (for reading input files)
            if self.data_dir:
                volumes[str(self.data_dir.absolute())] = {"bind": "/source", "mode": "ro"}
                environment["CLX_HOST_DATA_DIR"] = str(self.data_dir.absolute())

            log_mounts = f"  Workspace: {self.workspace_path.absolute()} -> /workspace (rw)"
            if self.data_dir:
                log_mounts += f"\n  Source: {self.data_dir.absolute()} -> /source (ro)"

            logger.debug(
                f"Starting container {container_name}:\n{log_mounts}\n  API URL: {api_url}"
            )

            # On Linux, host.docker.internal doesn't work by default.
            # We need to add it as an extra host pointing to the host gateway.
            # This is equivalent to: docker run --add-host=host.docker.internal:host-gateway
            extra_hosts = {"host.docker.internal": "host-gateway"}

            container = self.docker_client.containers.run(
                config.image,
                name=container_name,
                detach=True,
                remove=False,
                mem_limit=config.memory_limit,
                volumes=volumes,
                environment=environment,
                network=self.network_name,
                extra_hosts=extra_hosts,
            )

            container_id = cast(str, container.id)
            logger.info(f"Started container: {container_name} ({container_id[:12]})")

            # Store container reference using container ID as worker_id
            self.containers[container_id] = container
            return container_id

        except Exception as e:
            logger.error(f"Failed to start worker {container_name}: {e}", exc_info=True)
            return None

    def stop_worker(self, worker_id: str) -> bool:
        """Stop a Docker container worker."""
        import docker
        import docker.errors

        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            container.reload()
            if container.status == "running":
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

    def get_container_logs(self, worker_id: str, tail: int = 100) -> str | None:
        """Get logs from a Docker container.

        Args:
            worker_id: Container ID
            tail: Number of lines to get from end

        Returns:
            Log output as string, or None if unavailable
        """
        import docker.errors

        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            logs = container.logs(tail=tail, timestamps=False)
            if isinstance(logs, bytes):
                return logs.decode("utf-8", errors="replace")
            return str(logs)

        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.error(f"Error getting container logs: {e}")
            return None

    def is_worker_running(self, worker_id: str) -> bool:
        """Check if Docker container is running."""
        import docker
        import docker.errors

        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            container.reload()
            return cast(bool, container.status == "running")

        except docker.errors.NotFound:
            return False
        except Exception as e:
            logger.error(f"Error checking worker status: {e}")
            return False

    def get_worker_stats(self, worker_id: str) -> dict | None:
        """Get Docker container resource statistics."""
        try:
            if worker_id in self.containers:
                container = self.containers[worker_id]
            else:
                container = self.docker_client.containers.get(worker_id)

            stats = container.stats(stream=False)

            # Calculate CPU percentage
            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
            )

            cpu_percent = 0.0
            if system_delta > 0 and cpu_delta > 0:
                num_cpus = len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
                cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0

            # Calculate memory usage in MB
            memory_mb = stats["memory_stats"].get("usage", 0) / (1024 * 1024)

            return {"cpu_percent": cpu_percent, "memory_mb": memory_mb}

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
        "notebook": "clx.workers.notebook",
        "drawio": "clx.workers.drawio",
        "plantuml": "clx.workers.plantuml",
    }

    def __init__(
        self,
        db_path: Path,
        workspace_path: Path,
        log_level: str = "INFO",
        cache_db_path: Path | None = None,
    ):
        """Initialize direct process executor.

        Args:
            db_path: Path to SQLite database
            workspace_path: Path to workspace directory
            log_level: Logging level for workers
            cache_db_path: Path to executed notebook cache database
        """
        self.db_path = db_path
        self.workspace_path = workspace_path
        self.log_level = log_level
        self.cache_db_path = cache_db_path
        self.processes: dict[str, subprocess.Popen] = {}
        self.worker_info: dict[str, dict] = {}  # worker_id -> {type, index, etc.}

    def start_worker(self, worker_type: str, index: int, config: WorkerConfig) -> str | None:
        """Start a worker as a direct subprocess."""
        # Generate unique worker ID
        worker_id = f"direct-{worker_type}-{index}-{uuid.uuid4().hex[:8]}"

        try:
            # Get the module to run
            if worker_type not in self.MODULE_MAP:
                logger.error(f"Unknown worker type: {worker_type}")
                return None

            module = self.MODULE_MAP[worker_type]

            # Check if worker module is available
            import importlib.util

            spec = importlib.util.find_spec(module)
            if spec is None:
                error_msg = (
                    f"\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Worker '{worker_type}' not available in direct mode\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"\n"
                    f"To use {worker_type} worker in direct execution mode:\n"
                    f"\n"
                    f"  pip install clx[{worker_type}]\n"
                    f"\n"
                    f"Or install all workers:\n"
                    f"\n"
                    f"  pip install clx[all-workers]\n"
                    f"\n"
                    f"Or use Docker mode instead (no extra installation needed):\n"
                    f"\n"
                    f"  clx build --execution-mode docker <course.yaml>\n"
                    f"\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                )
                logger.error(error_msg)
                return None

            # Prepare environment variables
            env = os.environ.copy()
            env.update(
                {
                    "WORKER_TYPE": worker_type,
                    "WORKER_ID": worker_id,  # Explicit ID for direct execution
                    "DB_PATH": str(self.db_path.absolute()),
                    "WORKSPACE_PATH": str(self.workspace_path.absolute()),
                    "LOG_LEVEL": self.log_level,
                    "USE_SQLITE_QUEUE": "true",
                }
            )

            # Pass cache database path for notebook workers
            if self.cache_db_path is not None:
                env["CACHE_DB_PATH"] = str(self.cache_db_path.absolute())
                logger.debug(f"Passing CACHE_DB_PATH={env['CACHE_DB_PATH']} to worker")

            # Ensure converter-specific environment variables are passed through
            # These are needed by PlantUML and Draw.io converters
            for var in ["PLANTUML_JAR", "DRAWIO_EXECUTABLE"]:
                if var in os.environ:
                    env[var] = os.environ[var]
                    logger.debug(f"Passing {var}={env[var]} to worker")

            # Build command
            cmd = [sys.executable, "-m", module]

            logger.info(f"Starting direct worker: {worker_id}")
            logger.debug(f"Command: {' '.join(cmd)}")
            logger.debug(f"Environment: WORKER_TYPE={worker_type}, WORKER_ID={worker_id}")

            # Get log file path for this worker
            from clx.infrastructure.logging.log_paths import get_worker_log_path

            log_file_path = get_worker_log_path(worker_type, index)
            logger.debug(f"Worker {worker_id} logs: {log_file_path}")

            # Open log file for worker output
            # Use append mode so logs persist across restarts
            log_file = open(log_file_path, "a", encoding="utf-8", buffering=1)

            # Start the process with output redirected to log file
            # Use getattr for os.setsid since it's only available on Unix
            preexec_fn = getattr(os, "setsid", None) if sys.platform != "win32" else None

            # On Windows, use CREATE_NEW_PROCESS_GROUP to prevent CTRL_C_EVENT
            # from propagating between parent and child processes. Without this,
            # worker subprocess termination or SQLite lock contention can trigger
            # spurious SIGINT in the parent process, causing "Aborted!" messages.
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                preexec_fn=preexec_fn,
                creationflags=creationflags,
            )

            # Store process and info
            self.processes[worker_id] = process
            self.worker_info[worker_id] = {
                "type": worker_type,
                "index": index,
                "pid": process.pid,
                "log_file": log_file,
                "log_path": log_file_path,
            }

            logger.info(f"Started direct worker: {worker_id} (PID: {process.pid})")

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
            worker_info = self.worker_info.get(worker_id, {})

            # Check if already terminated
            if process.poll() is not None:
                logger.info(f"Worker {worker_id} already terminated")
                # Close log file if open
                self._close_worker_log_file(worker_info)
                del self.processes[worker_id]
                if worker_id in self.worker_info:
                    del self.worker_info[worker_id]
                return True

            # Send SIGTERM for graceful shutdown
            logger.info(f"Stopping worker {worker_id} (PID: {process.pid})")

            if sys.platform != "win32":
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
                if sys.platform != "win32" and hasattr(signal, "SIGKILL"):
                    # Use SIGKILL on Unix if available
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    # Fall back to process.kill() on Windows or if SIGKILL not available
                    process.kill()
                process.wait()

            # Close log file if open
            self._close_worker_log_file(worker_info)

            # Clean up
            del self.processes[worker_id]
            if worker_id in self.worker_info:
                del self.worker_info[worker_id]

            return True

        except Exception as e:
            logger.error(f"Error stopping worker {worker_id}: {e}", exc_info=True)
            # Clean up even on error
            if worker_id in self.processes:
                del self.processes[worker_id]
            if worker_id in self.worker_info:
                # Try to close log file
                worker_info = self.worker_info.get(worker_id, {})
                self._close_worker_log_file(worker_info)
                del self.worker_info[worker_id]
            return False

    def _close_worker_log_file(self, worker_info: dict) -> None:
        """Close the log file for a worker if it exists.

        Args:
            worker_info: Worker info dict that may contain 'log_file' key
        """
        log_file = worker_info.get("log_file")
        if log_file is not None:
            try:
                log_file.close()
            except Exception as e:
                logger.debug(f"Error closing worker log file: {e}")

    def is_worker_running(self, worker_id: str) -> bool:
        """Check if direct process worker is running.

        This method checks system-wide for the worker process, not just
        in the local process dict. This allows worker reuse across
        different executor instances.
        """
        # First check if we have it in our local process dict (fast path)
        if worker_id in self.processes:
            process = self.processes[worker_id]
            return process.poll() is None

        # If not in our dict, check if process exists system-wide
        # This handles the case where a different executor instance started it
        try:
            # Try using psutil if available (cross-platform)
            import psutil  # type: ignore[import-untyped]

            for proc in psutil.process_iter(["pid", "environ"]):
                try:
                    env = proc.environ()
                    if env.get("WORKER_ID") == worker_id:
                        return bool(proc.is_running())
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except ImportError:
            # psutil not available, fall back to /proc on Linux
            if sys.platform.startswith("linux"):
                try:
                    for proc_dir in glob.glob("/proc/[0-9]*/environ"):
                        try:
                            with open(proc_dir, "rb") as f:
                                environ_data = f.read()
                                # Environment variables are null-separated
                                environ_str = environ_data.decode("utf-8", errors="ignore")
                                if (
                                    f"WORKER_ID={worker_id}\x00" in environ_str
                                    or f"WORKER_ID={worker_id}" in environ_str
                                ):
                                    # Process exists
                                    return True
                        except (FileNotFoundError, PermissionError, OSError):
                            # Process disappeared or no permission
                            continue
                except Exception as e:
                    logger.debug(f"Error checking /proc for worker {worker_id}: {e}")

        # Could not verify - assume not running
        return False

    def get_worker_stats(self, worker_id: str) -> dict | None:
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
                "cpu_percent": 0.0,  # Would need psutil for accurate CPU
                "memory_mb": 0.0,  # Would need psutil for accurate memory
                "is_alive": is_alive,
                "pid": process.pid,
            }

        except Exception as e:
            logger.error(f"Error getting worker stats: {e}")
            return None

    def cleanup(self) -> None:
        """Stop all managed worker processes."""
        logger.info(f"Cleaning up {len(self.processes)} direct workers")

        for worker_id in list(self.processes.keys()):
            self.stop_worker(worker_id)
