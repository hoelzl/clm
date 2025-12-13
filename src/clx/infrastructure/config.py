"""Configuration management for CLX.

This module provides a unified configuration system that supports:
- Configuration files in TOML format
- Environment variables
- Multiple configuration file locations (project, user, system)
- Type-safe configuration using Pydantic

Configuration Priority (highest to lowest):
1. Environment variables
2. Project configuration file (.clx/config.toml or clx.toml)
3. User configuration file (~/.config/clx/config.toml)
4. System configuration file (/etc/clx/config.toml)
5. Default values

Environment Variable Naming:
- Flat fields: CLX_<FIELD_NAME> (e.g., CLX_DB_PATH)
- Nested fields: CLX_<SECTION>__<FIELD> (e.g., CLX_LOGGING__LOG_LEVEL)
- External tools: <TOOL_NAME> (e.g., PLANTUML_JAR, DRAWIO_EXECUTABLE)
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs
from pydantic import BaseModel, Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

if TYPE_CHECKING:
    from clx.infrastructure.workers.worker_executor import WorkerConfig

logger = logging.getLogger(__name__)


class LegacyEnvSettingsSource(PydanticBaseSettingsSource):
    """Custom settings source to handle legacy environment variables.

    This source handles environment variables that don't follow the CLX_ prefix
    convention, such as PLANTUML_JAR, DRAWIO_EXECUTABLE, etc.
    """

    # Map of field paths to environment variable names (without prefix)
    LEGACY_ENV_VARS = {
        ("external_tools", "plantuml_jar"): "PLANTUML_JAR",
        ("external_tools", "drawio_executable"): "DRAWIO_EXECUTABLE",
        ("jupyter", "jinja_line_statement_prefix"): "JINJA_LINE_STATEMENT_PREFIX",
        ("jupyter", "jinja_templates_path"): "JINJA_TEMPLATES_PATH",
        ("jupyter", "log_cell_processing"): "LOG_CELL_PROCESSING",
        ("workers", "worker_type"): "WORKER_TYPE",
        ("workers", "worker_id"): "WORKER_ID",
        ("workers", "use_sqlite_queue"): "USE_SQLITE_QUEUE",
    }

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Get field value from environment variables."""
        # This method is called for each field in the model
        # Return (value, key, is_complex) or raise ValueError if not found
        raise ValueError(f"Field {field_name} not found in legacy environment")

    def __call__(self) -> dict[str, Any]:
        """Build settings from legacy environment variables."""
        data: dict[str, Any] = {}

        for field_path, env_var in self.LEGACY_ENV_VARS.items():
            env_value = os.getenv(env_var)

            if env_value is not None:
                # Build nested dict structure
                current = data
                for part in field_path[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

                # Set the value, converting booleans
                final_key = field_path[-1]
                if env_var in ("LOG_CELL_PROCESSING", "USE_SQLITE_QUEUE"):
                    # Boolean conversion
                    current[final_key] = env_value.lower() in ("true", "1", "yes")
                else:
                    current[final_key] = env_value

        return data


class PathsConfig(BaseModel):
    """Path-related configuration."""

    cache_db_path: str = Field(
        default="clx_cache.db",
        description="Path to the cache database (stores processed file results)",
    )

    jobs_db_path: str = Field(
        default="clx_jobs.db",
        description="Path to the job queue database (stores jobs, workers, events)",
    )

    workspace_path: str = Field(
        default="",
        description="Workspace path for workers (usually derived from output directory)",
    )


class ExternalToolsConfig(BaseModel):
    """External tool paths configuration."""

    plantuml_jar: str = Field(
        default="",
        description="Path to PlantUML JAR file",
    )

    drawio_executable: str = Field(
        default="",
        description="Path to Draw.io executable",
    )


class LoggingTestingConfig(BaseModel):
    """Test-specific logging configuration."""

    e2e_progress_interval: int = Field(
        default=10,
        description="Progress update interval for E2E tests (seconds)",
    )

    e2e_long_job_threshold: int = Field(
        default=60,
        description="Long job warning threshold (seconds)",
    )

    e2e_show_worker_details: bool = Field(
        default=False,
        description="Show worker details in E2E tests",
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    enable_test_logging: bool = Field(
        default=False,
        description="Enable logging for tests",
    )

    testing: LoggingTestingConfig = Field(
        default_factory=LoggingTestingConfig,
        description="Test-specific logging settings",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}, got {v}")
        return v_upper


class JupyterConfig(BaseModel):
    """Jupyter notebook processing configuration."""

    jinja_line_statement_prefix: str = Field(
        default="# j2",
        description="Jinja2 line statement prefix",
    )

    jinja_templates_path: str = Field(
        default="templates",
        description="Jinja2 templates path",
    )

    log_cell_processing: bool = Field(
        default=False,
        description="Log cell processing in notebook processor",
    )


class WorkersConfig(BaseModel):
    """Worker configuration."""

    worker_type: str = Field(
        default="",
        description="Worker type (notebook, plantuml, drawio)",
    )

    worker_id: str = Field(
        default="",
        description="Worker ID",
    )

    use_sqlite_queue: bool = Field(
        default=True,
        description="Use SQLite queue for job orchestration",
    )


class WorkerTypeConfig(BaseModel):
    """Configuration for a specific worker type."""

    execution_mode: str | None = Field(
        default=None,
        description="Execution mode: 'direct' or 'docker' (overrides global default)",
    )

    count: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Number of workers to start (overrides global default)",
    )

    image: str | None = Field(
        default=None,
        description="Docker image name (required for docker mode)",
    )

    memory_limit: str = Field(
        default="1g",
        description="Memory limit per worker (Docker only, e.g., '1g', '512m')",
    )

    max_job_time: int = Field(
        default=600,
        ge=10,
        le=3600,
        description="Maximum time a job can run before considered hung (seconds)",
    )


class WorkersManagementConfig(BaseModel):
    """Worker lifecycle management configuration."""

    # Global defaults
    default_execution_mode: str = Field(
        default="direct",
        description="Default execution mode: 'direct' or 'docker'",
    )

    default_worker_count: int = Field(
        default=1,
        ge=1,
        le=20,
        description="Default number of workers per type",
    )

    auto_start: bool = Field(
        default=True,
        description="Automatically start workers with 'clx build'",
    )

    auto_stop: bool = Field(
        default=True,
        description="Automatically stop workers after 'clx build' completes",
    )

    reuse_workers: bool = Field(
        default=True,
        description="Reuse existing healthy workers instead of starting new ones",
    )

    # Network configuration (Docker)
    network_name: str = Field(
        default="clx_app-network",
        description="Docker network name for worker containers",
    )

    # Worker startup
    startup_timeout: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Seconds to wait for worker registration",
    )

    startup_parallel: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of workers to start in parallel",
    )

    # Per-worker-type configurations
    notebook: WorkerTypeConfig = Field(
        default_factory=WorkerTypeConfig,
        description="Notebook worker configuration",
    )

    plantuml: WorkerTypeConfig = Field(
        default_factory=WorkerTypeConfig,
        description="PlantUML worker configuration",
    )

    drawio: WorkerTypeConfig = Field(
        default_factory=WorkerTypeConfig,
        description="Draw.io worker configuration",
    )

    @field_validator("default_execution_mode")
    @classmethod
    def validate_execution_mode(cls, v: str) -> str:
        """Validate execution mode."""
        if v not in ("direct", "docker"):
            raise ValueError(f"Execution mode must be 'direct' or 'docker', got '{v}'")
        return v

    def get_worker_config(self, worker_type: str) -> "WorkerConfig":
        """Get effective configuration for a worker type.

        This merges per-type config with global defaults.

        Args:
            worker_type: Worker type ('notebook', 'plantuml', 'drawio')

        Returns:
            WorkerConfig with effective settings

        Raises:
            ValueError: If worker_type is invalid
        """
        # Import here to avoid circular dependency
        from clx.infrastructure.workers.worker_executor import WorkerConfig

        if worker_type not in ("notebook", "plantuml", "drawio"):
            raise ValueError(f"Unknown worker type: {worker_type}")

        type_config = getattr(self, worker_type)

        # Determine effective values
        execution_mode = type_config.execution_mode or self.default_execution_mode
        count = type_config.count if type_config.count is not None else self.default_worker_count

        # Determine image
        image = type_config.image
        if execution_mode == "docker" and not image:
            # Use default images
            default_images = {
                "notebook": "mhoelzl/clx-notebook-processor:latest",
                "plantuml": "mhoelzl/clx-plantuml-converter:latest",
                "drawio": "mhoelzl/clx-drawio-converter:latest",
            }
            image = default_images.get(worker_type)

        return WorkerConfig(
            worker_type=worker_type,
            execution_mode=execution_mode,
            count=count,
            image=image,
            memory_limit=type_config.memory_limit,
            max_job_time=type_config.max_job_time,
        )

    def get_all_worker_configs(self) -> list["WorkerConfig"]:
        """Get configurations for all worker types.

        Returns:
            List of WorkerConfig for notebook, plantuml, and drawio
        """
        return [
            self.get_worker_config("notebook"),
            self.get_worker_config("plantuml"),
            self.get_worker_config("drawio"),
        ]


class ClxConfig(BaseSettings):
    """Main CLX configuration.

    This class manages all configuration for CLX, loading from multiple sources
    in priority order: environment variables > project config > user config >
    system config > defaults.

    Environment Variables:
        - CLX_PATHS__DB_PATH: Database path
        - CLX_LOGGING__LOG_LEVEL: Logging level
        - PLANTUML_JAR: PlantUML JAR path (no CLX_ prefix)
        - DRAWIO_EXECUTABLE: Draw.io executable path (no CLX_ prefix)
        - And many more (see nested config classes)
    """

    model_config = SettingsConfigDict(
        env_prefix="CLX_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    paths: PathsConfig = Field(
        default_factory=PathsConfig,
        description="Path-related configuration",
    )

    external_tools: ExternalToolsConfig = Field(
        default_factory=ExternalToolsConfig,
        description="External tool paths",
    )

    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        description="Logging configuration",
    )

    jupyter: JupyterConfig = Field(
        default_factory=JupyterConfig,
        description="Jupyter notebook processing configuration",
    )

    workers: WorkersConfig = Field(
        default_factory=WorkersConfig,
        description="Worker configuration",
    )

    worker_management: WorkersManagementConfig = Field(
        default_factory=WorkersManagementConfig,
        description="Worker lifecycle management configuration",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize the sources and their priority for settings.

        Priority order (highest to lowest):
        1. Environment variables (both CLX_ prefixed and legacy)
        2. Project configuration file
        3. User configuration file
        4. System configuration file
        5. Init settings (programmatic)
        """
        config_files = find_config_files()

        # Create TOML sources for each config file (in reverse priority order)
        # because pydantic-settings applies sources from left to right, with
        # left sources having higher priority
        toml_sources = []

        # System config (lowest priority TOML file)
        if config_files["system"]:
            try:
                toml_sources.append(
                    TomlConfigSettingsSource(
                        settings_cls,
                        toml_file=config_files["system"],
                    )
                )
                logger.debug(f"Loaded system config: {config_files['system']}")
            except Exception as e:
                logger.debug(f"Could not load system config: {e}")

        # User config (medium priority TOML file)
        if config_files["user"]:
            try:
                toml_sources.append(
                    TomlConfigSettingsSource(
                        settings_cls,
                        toml_file=config_files["user"],
                    )
                )
                logger.debug(f"Loaded user config: {config_files['user']}")
            except Exception as e:
                logger.debug(f"Could not load user config: {e}")

        # Project config (highest priority TOML file)
        if config_files["project"]:
            try:
                toml_sources.append(
                    TomlConfigSettingsSource(
                        settings_cls,
                        toml_file=config_files["project"],
                    )
                )
                logger.debug(f"Loaded project config: {config_files['project']}")
            except Exception as e:
                logger.debug(f"Could not load project config: {e}")

        # Create legacy environment variable source
        legacy_env_settings = LegacyEnvSettingsSource(settings_cls)

        # Return sources in priority order (left = highest priority)
        # 1. Standard environment variables with CLX_ prefix (highest)
        # 2. Legacy environment variables (PLANTUML_JAR, etc.)
        # 3. Project TOML file
        # 4. User TOML file
        # 5. System TOML file
        # 6. Init settings (lowest, for programmatic overrides)
        return (
            env_settings,
            legacy_env_settings,
            *reversed(toml_sources),  # Reverse to get project > user > system
            init_settings,
        )


def find_config_files() -> dict[str, Path | None]:
    """Find configuration files in standard locations.

    Returns:
        Dictionary with keys 'system', 'user', 'project', each containing
        a Path to the config file if it exists, or None otherwise.
    """
    config_files: dict[str, Path | None] = {
        "system": None,
        "user": None,
        "project": None,
    }

    # System config (Linux/Unix only)
    if Path("/etc").exists():
        system_config = Path("/etc/clx/config.toml")
        if system_config.exists():
            config_files["system"] = system_config

    # User config (using platformdirs for cross-platform support)
    user_config_dir = Path(platformdirs.user_config_dir("clx", appauthor=False))
    user_config = user_config_dir / "config.toml"
    if user_config.exists():
        config_files["user"] = user_config

    # Project config (in current working directory or .clx subdirectory)
    # Check .clx/config.toml first, then clx.toml
    cwd = Path.cwd()
    project_configs = [
        cwd / ".clx" / "config.toml",
        cwd / "clx.toml",
    ]

    for project_config in project_configs:
        if project_config.exists():
            config_files["project"] = project_config
            break

    return config_files


def get_config_file_locations() -> dict[str, Path]:
    """Get the standard configuration file locations.

    Returns:
        Dictionary with keys 'system', 'user', 'project', each containing
        the Path where the config file should be located (may not exist).
    """
    locations: dict[str, Path] = {}

    # System config location (Linux/Unix)
    locations["system"] = Path("/etc/clx/config.toml")

    # User config location (cross-platform)
    user_config_dir = Path(platformdirs.user_config_dir("clx", appauthor=False))
    locations["user"] = user_config_dir / "config.toml"

    # Project config location (in current working directory)
    locations["project"] = Path.cwd() / ".clx" / "config.toml"

    return locations


# Global configuration instance
# This will be lazily initialized on first access
_config: ClxConfig | None = None


def get_config(reload: bool = False) -> ClxConfig:
    """Get the global configuration instance.

    Args:
        reload: If True, reload the configuration from files and environment.

    Returns:
        The global ClxConfig instance.
    """
    global _config

    if _config is None or reload:
        _config = ClxConfig()

    return _config


def create_example_config() -> str:
    """Create an example configuration file content.

    Returns:
        String containing an example TOML configuration with all options
        documented.
    """
    return """# CLX Configuration File
#
# This file configures the CLX course content processing system.
# Configuration files are loaded from (in priority order):
#   1. .clx/config.toml or clx.toml (project directory)
#   2. ~/.config/clx/config.toml (user directory)
#   3. /etc/clx/config.toml (system directory, Linux/Unix only)
#
# Environment variables can override any setting (highest priority).
# Nested settings use double underscores: CLX_<SECTION>__<KEY>
#
# Examples:
#   CLX_PATHS__CACHE_DB_PATH=/tmp/cache.db
#   CLX_PATHS__JOBS_DB_PATH=/tmp/jobs.db
#   CLX_LOGGING__LOG_LEVEL=DEBUG
#   PLANTUML_JAR=/usr/local/share/plantuml.jar
#   DRAWIO_EXECUTABLE=/usr/local/bin/drawio

[paths]
# Path to the cache database (stores processed file results)
cache_db_path = "clx_cache.db"

# Path to the job queue database (stores jobs, workers, events)
jobs_db_path = "clx_jobs.db"

# Workspace path for workers (optional, usually derived from output directory)
workspace_path = ""

[external_tools]
# Path to PlantUML JAR file
# Environment variable: PLANTUML_JAR (no CLX_ prefix)
# Example: plantuml_jar = "/usr/local/share/plantuml-1.2024.6.jar"
plantuml_jar = ""

# Path to Draw.io executable
# Environment variable: DRAWIO_EXECUTABLE (no CLX_ prefix)
# Example: drawio_executable = "/usr/local/bin/drawio"
drawio_executable = ""

[logging]
# Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL
# Environment variable: CLX_LOGGING__LOG_LEVEL
log_level = "INFO"

# Enable logging for tests
# Environment variable: CLX_LOGGING__ENABLE_TEST_LOGGING
enable_test_logging = false

[logging.testing]
# Progress update interval for E2E tests (seconds)
# Environment variable: CLX_LOGGING__TESTING__E2E_PROGRESS_INTERVAL
e2e_progress_interval = 10

# Long job warning threshold (seconds)
# Environment variable: CLX_LOGGING__TESTING__E2E_LONG_JOB_THRESHOLD
e2e_long_job_threshold = 60

# Show worker details in E2E tests
# Environment variable: CLX_LOGGING__TESTING__E2E_SHOW_WORKER_DETAILS
e2e_show_worker_details = false

[jupyter]
# Jinja2 line statement prefix for template processing
# Environment variable: JINJA_LINE_STATEMENT_PREFIX (no CLX_ prefix)
jinja_line_statement_prefix = "# j2"

# Jinja2 templates path
# Environment variable: JINJA_TEMPLATES_PATH (no CLX_ prefix)
jinja_templates_path = "templates"

# Log cell processing in notebook processor
# Environment variable: LOG_CELL_PROCESSING (no CLX_ prefix)
log_cell_processing = false

[workers]
# Worker type (notebook, plantuml, drawio)
# Environment variable: WORKER_TYPE (no CLX_ prefix)
# Usually set automatically, not needed in config file
worker_type = ""

# Worker ID
# Environment variable: WORKER_ID (no CLX_ prefix)
# Usually set automatically, not needed in config file
worker_id = ""

# Use SQLite queue for job orchestration
# Environment variable: USE_SQLITE_QUEUE (no CLX_ prefix)
use_sqlite_queue = true

[worker_management]
# Worker lifecycle management configuration

# Global defaults
# Default execution mode: "direct" or "docker"
# Environment variable: CLX_WORKER_MANAGEMENT__DEFAULT_EXECUTION_MODE
default_execution_mode = "direct"

# Default number of workers per type
# Environment variable: CLX_WORKER_MANAGEMENT__DEFAULT_WORKER_COUNT
default_worker_count = 1

# Automatically start workers with 'clx build'
# Environment variable: CLX_WORKER_MANAGEMENT__AUTO_START
auto_start = true

# Automatically stop workers after 'clx build' completes
# Environment variable: CLX_WORKER_MANAGEMENT__AUTO_STOP
auto_stop = true

# Reuse existing healthy workers instead of starting new ones
# Environment variable: CLX_WORKER_MANAGEMENT__REUSE_WORKERS
reuse_workers = true

# Docker network name for worker containers
# Environment variable: CLX_WORKER_MANAGEMENT__NETWORK_NAME
network_name = "clx_app-network"

# Seconds to wait for worker registration
# Environment variable: CLX_WORKER_MANAGEMENT__STARTUP_TIMEOUT
startup_timeout = 30

# Number of workers to start in parallel
# Environment variable: CLX_WORKER_MANAGEMENT__STARTUP_PARALLEL
startup_parallel = 5

# Per-worker-type configuration
[worker_management.notebook]
# Execution mode for notebook workers (overrides global default)
# execution_mode = "direct"

# Number of notebook workers (overrides global default)
# count = 2

# Docker image for notebook workers (required for docker mode)
# image = "mhoelzl/clx-notebook-processor:latest"

# Memory limit per notebook worker (Docker only)
memory_limit = "1g"

# Maximum time a notebook job can run (seconds)
max_job_time = 600

[worker_management.plantuml]
# Execution mode for PlantUML workers
# execution_mode = "docker"

# Number of PlantUML workers
# count = 1

# Docker image for PlantUML workers
# image = "mhoelzl/clx-plantuml-converter:latest"

# Memory limit per PlantUML worker (Docker only)
memory_limit = "512m"

# Maximum time a PlantUML job can run (seconds)
max_job_time = 300

[worker_management.drawio]
# Execution mode for Draw.io workers
# execution_mode = "direct"

# Number of Draw.io workers
# count = 1

# Docker image for Draw.io workers
# image = "mhoelzl/clx-drawio-converter:latest"

# Memory limit per Draw.io worker (Docker only)
memory_limit = "512m"

# Maximum time a Draw.io job can run (seconds)
max_job_time = 300
"""


def write_example_config(location: str = "user") -> Path:
    """Write an example configuration file to a standard location.

    Args:
        location: Where to write the config file. One of:
            - "user": User config directory (~/.config/clx/config.toml)
            - "project": Project config directory (.clx/config.toml)
            - "system": System config directory (/etc/clx/config.toml)

    Returns:
        Path to the created configuration file.

    Raises:
        ValueError: If location is invalid.
        PermissionError: If cannot write to the location.
    """
    locations = get_config_file_locations()

    if location not in locations:
        raise ValueError(f"Invalid location '{location}'. Must be one of: user, project, system")

    config_path = locations[location]

    # Create parent directory if it doesn't exist
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the example config
    config_path.write_text(create_example_config())

    logger.info(f"Created example configuration at: {config_path}")

    return config_path
