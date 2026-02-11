"""Tests for the configuration system."""

import os
import tempfile
from pathlib import Path

import pytest

from clm.infrastructure.config import (
    ClmConfig,
    create_example_config,
    find_config_files,
    get_config,
    get_config_file_locations,
    write_example_config,
)


class TestConfigDefaults:
    """Test default configuration values."""

    def test_default_paths(self, monkeypatch):
        """Test default path configuration."""
        # Clear any environment variables that might interfere
        for var in [
            "CLM_PATHS__CACHE_DB_PATH",
            "CLM_PATHS__JOBS_DB_PATH",
            "CLM_PATHS__WORKSPACE_PATH",
        ]:
            monkeypatch.delenv(var, raising=False)

        config = ClmConfig()
        assert config.paths.cache_db_path == "clx_cache.db"
        assert config.paths.jobs_db_path == "clx_jobs.db"
        assert config.paths.workspace_path == ""

    def test_default_logging(self, monkeypatch):
        """Test default logging configuration."""
        # Clear any environment variables that might interfere
        for var in ["CLM_LOGGING__LOG_LEVEL", "CLM_LOGGING__ENABLE_TEST_LOGGING"]:
            monkeypatch.delenv(var, raising=False)

        config = ClmConfig()
        assert config.logging.log_level == "INFO"
        assert config.logging.enable_test_logging is False
        assert config.logging.testing.e2e_progress_interval == 10
        assert config.logging.testing.e2e_long_job_threshold == 60
        assert config.logging.testing.e2e_show_worker_details is False

    def test_default_external_tools(self, monkeypatch):
        """Test default external tools configuration."""
        # Clear any environment variables that might interfere
        for var in ["PLANTUML_JAR", "DRAWIO_EXECUTABLE"]:
            monkeypatch.delenv(var, raising=False)

        config = ClmConfig()
        assert config.external_tools.plantuml_jar == ""
        assert config.external_tools.drawio_executable == ""

    def test_default_jupyter(self, monkeypatch):
        """Test default Jupyter configuration."""
        # Clear any environment variables that might interfere
        for var in ["JINJA_LINE_STATEMENT_PREFIX", "JINJA_TEMPLATES_PATH", "LOG_CELL_PROCESSING"]:
            monkeypatch.delenv(var, raising=False)

        config = ClmConfig()
        assert config.jupyter.jinja_line_statement_prefix == "# j2"
        assert config.jupyter.jinja_templates_path == "templates"
        assert config.jupyter.log_cell_processing is False

    def test_default_workers(self, monkeypatch):
        """Test default workers configuration."""
        # Clear any environment variables that might interfere
        for var in ["WORKER_TYPE", "WORKER_ID", "USE_SQLITE_QUEUE"]:
            monkeypatch.delenv(var, raising=False)

        config = ClmConfig()
        assert config.workers.worker_type == ""
        assert config.workers.worker_id == ""
        assert config.workers.use_sqlite_queue is True


class TestEnvironmentVariables:
    """Test environment variable configuration."""

    def test_clx_prefixed_env_vars(self, monkeypatch):
        """Test CLM_ prefixed environment variables."""
        monkeypatch.setenv("CLM_PATHS__CACHE_DB_PATH", "/tmp/cache.db")
        monkeypatch.setenv("CLM_PATHS__JOBS_DB_PATH", "/tmp/jobs.db")
        monkeypatch.setenv("CLM_LOGGING__LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("CLM_LOGGING__ENABLE_TEST_LOGGING", "true")

        config = ClmConfig()
        assert config.paths.cache_db_path == "/tmp/cache.db"
        assert config.paths.jobs_db_path == "/tmp/jobs.db"
        assert config.logging.log_level == "DEBUG"
        assert config.logging.enable_test_logging is True

    def test_nested_env_vars(self, monkeypatch):
        """Test nested environment variables with double underscores."""
        monkeypatch.setenv("CLM_LOGGING__TESTING__E2E_PROGRESS_INTERVAL", "5")
        monkeypatch.setenv("CLM_LOGGING__TESTING__E2E_LONG_JOB_THRESHOLD", "30")
        monkeypatch.setenv("CLM_LOGGING__TESTING__E2E_SHOW_WORKER_DETAILS", "true")

        config = ClmConfig()
        assert config.logging.testing.e2e_progress_interval == 5
        assert config.logging.testing.e2e_long_job_threshold == 30
        assert config.logging.testing.e2e_show_worker_details is True

    def test_legacy_env_vars(self, monkeypatch):
        """Test legacy environment variables without CLM_ prefix."""
        monkeypatch.setenv("PLANTUML_JAR", "/usr/local/share/plantuml.jar")
        monkeypatch.setenv("DRAWIO_EXECUTABLE", "/usr/local/bin/drawio")
        monkeypatch.setenv("WORKER_TYPE", "notebook")
        monkeypatch.setenv("WORKER_ID", "worker-1")

        config = ClmConfig()
        assert config.external_tools.plantuml_jar == "/usr/local/share/plantuml.jar"
        assert config.external_tools.drawio_executable == "/usr/local/bin/drawio"
        assert config.workers.worker_type == "notebook"
        assert config.workers.worker_id == "worker-1"

    def test_jupyter_env_vars(self, monkeypatch):
        """Test Jupyter-related environment variables."""
        monkeypatch.setenv("JINJA_LINE_STATEMENT_PREFIX", "# custom")
        monkeypatch.setenv("JINJA_TEMPLATES_PATH", "/custom/templates")
        monkeypatch.setenv("LOG_CELL_PROCESSING", "true")

        config = ClmConfig()
        assert config.jupyter.jinja_line_statement_prefix == "# custom"
        assert config.jupyter.jinja_templates_path == "/custom/templates"
        assert config.jupyter.log_cell_processing is True

    def test_case_insensitive_env_vars(self, monkeypatch):
        """Test that environment variables are case-insensitive."""
        monkeypatch.setenv("clm_logging__log_level", "warning")

        config = ClmConfig()
        assert config.logging.log_level == "WARNING"  # Validator uppercases it

    def test_log_level_validation(self):
        """Test log level validation."""
        # Valid log levels
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = ClmConfig(logging={"log_level": level})
            assert config.logging.log_level == level

        # Invalid log level should raise ValueError
        with pytest.raises(ValueError, match="Log level must be one of"):
            ClmConfig(logging={"log_level": "INVALID"})


class TestConfigurationFiles:
    """Test configuration file loading."""

    def test_toml_file_loading(self, tmp_path, monkeypatch):
        """Test loading configuration from TOML file."""
        # Clear environment variables that might interfere
        for var in ["PLANTUML_JAR", "DRAWIO_EXECUTABLE"]:
            monkeypatch.delenv(var, raising=False)

        # Create a temporary config file
        config_file = tmp_path / "clm.toml"
        config_file.write_text("""
[paths]
cache_db_path = "/tmp/cache.db"
jobs_db_path = "/tmp/jobs.db"

[logging]
log_level = "WARNING"

[external_tools]
plantuml_jar = "/custom/plantuml.jar"
""")

        # Change to temp directory so config file is found
        monkeypatch.chdir(tmp_path)

        config = ClmConfig()
        assert config.paths.cache_db_path == "/tmp/cache.db"
        assert config.paths.jobs_db_path == "/tmp/jobs.db"
        assert config.logging.log_level == "WARNING"
        assert config.external_tools.plantuml_jar == "/custom/plantuml.jar"

    def test_dotclx_directory_config(self, tmp_path, monkeypatch):
        """Test loading configuration from .clx/config.toml."""
        # Create .clx directory with config file
        clx_dir = tmp_path / ".clx"
        clx_dir.mkdir()
        config_file = clx_dir / "config.toml"
        config_file.write_text("""
[paths]
cache_db_path = "/tmp/dotclx_cache.db"
jobs_db_path = "/tmp/dotclx_jobs.db"
""")

        monkeypatch.chdir(tmp_path)

        config = ClmConfig()
        assert config.paths.cache_db_path == "/tmp/dotclx_cache.db"
        assert config.paths.jobs_db_path == "/tmp/dotclx_jobs.db"

    def test_config_file_priority(self, tmp_path, monkeypatch):
        """Test that .clx/config.toml has priority over clx.toml."""
        # Create both config files
        (tmp_path / "clm.toml").write_text("""
[paths]
cache_db_path = "/tmp/clx_cache.db"
jobs_db_path = "/tmp/clx_jobs.db"
""")

        clx_dir = tmp_path / ".clx"
        clx_dir.mkdir()
        (clx_dir / "config.toml").write_text("""
[paths]
cache_db_path = "/tmp/dotclx_cache.db"
jobs_db_path = "/tmp/dotclx_jobs.db"
""")

        monkeypatch.chdir(tmp_path)

        config = ClmConfig()
        # .clx/config.toml should take priority
        assert config.paths.cache_db_path == "/tmp/dotclx_cache.db"
        assert config.paths.jobs_db_path == "/tmp/dotclx_jobs.db"

    def test_env_overrides_config_file(self, tmp_path, monkeypatch):
        """Test that environment variables override config files."""
        config_file = tmp_path / "clm.toml"
        config_file.write_text("""
[paths]
cache_db_path = "/tmp/config_cache.db"
jobs_db_path = "/tmp/config_jobs.db"

[logging]
log_level = "INFO"
""")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLM_PATHS__CACHE_DB_PATH", "/tmp/env_cache.db")
        monkeypatch.setenv("CLM_PATHS__JOBS_DB_PATH", "/tmp/env_jobs.db")
        monkeypatch.setenv("CLM_LOGGING__LOG_LEVEL", "ERROR")

        config = ClmConfig()
        # Environment variables should override config file
        assert config.paths.cache_db_path == "/tmp/env_cache.db"
        assert config.paths.jobs_db_path == "/tmp/env_jobs.db"
        assert config.logging.log_level == "ERROR"

    def test_legacy_env_overrides_config_file(self, tmp_path, monkeypatch):
        """Test that legacy env vars override config files."""
        config_file = tmp_path / "clm.toml"
        config_file.write_text("""
[external_tools]
plantuml_jar = "/config/plantuml.jar"
""")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PLANTUML_JAR", "/env/plantuml.jar")

        config = ClmConfig()
        # Environment variable should override config file
        assert config.external_tools.plantuml_jar == "/env/plantuml.jar"


class TestConfigHelpers:
    """Test configuration helper functions."""

    def test_find_config_files_empty(self, tmp_path, monkeypatch):
        """Test find_config_files when no config files exist."""
        monkeypatch.chdir(tmp_path)

        config_files = find_config_files()
        assert config_files["system"] is None
        assert config_files["user"] is None
        assert config_files["project"] is None

    def test_find_config_files_project(self, tmp_path, monkeypatch):
        """Test find_config_files with project config."""
        config_file = tmp_path / "clm.toml"
        config_file.write_text("")

        monkeypatch.chdir(tmp_path)

        config_files = find_config_files()
        assert config_files["project"] == config_file

    def test_get_config_file_locations(self):
        """Test get_config_file_locations returns expected paths."""
        locations = get_config_file_locations()

        assert "system" in locations
        assert "user" in locations
        assert "project" in locations

        # Check that paths are Path objects
        assert all(isinstance(p, Path) for p in locations.values())

        # System config should be in /etc on Unix (or \etc on Windows)
        # Use Path comparison to handle platform differences
        assert locations["system"] == Path("/etc/clx/config.toml")

    def test_create_example_config(self):
        """Test create_example_config returns valid TOML."""
        example = create_example_config()

        # Should be non-empty string
        assert isinstance(example, str)
        assert len(example) > 0

        # Should contain expected sections
        assert "[paths]" in example
        assert "[logging]" in example
        assert "[external_tools]" in example
        assert "[jupyter]" in example
        assert "[workers]" in example

        # Should contain comments
        assert "#" in example

    def test_write_example_config(self, tmp_path, monkeypatch):
        """Test write_example_config creates a valid config file."""
        # Create a fake user config directory
        user_config_dir = tmp_path / "config" / "clx"

        # Patch platformdirs to return our temp directory
        def mock_user_config_dir(appname, appauthor=None):
            return str(user_config_dir)

        monkeypatch.setattr(
            "clm.infrastructure.config.platformdirs.user_config_dir", mock_user_config_dir
        )

        # Write example config
        config_path = write_example_config(location="user")

        # Check file was created
        assert config_path.exists()
        assert config_path.is_file()

        # Check content is valid
        content = config_path.read_text()
        assert "[paths]" in content
        assert "[logging]" in content

    def test_write_example_config_project(self, tmp_path, monkeypatch):
        """Test write_example_config for project location."""
        monkeypatch.chdir(tmp_path)

        config_path = write_example_config(location="project")

        assert config_path == tmp_path / ".clx" / "config.toml"
        assert config_path.exists()

    def test_write_example_config_invalid_location(self):
        """Test write_example_config with invalid location."""
        with pytest.raises(ValueError, match="Invalid location"):
            write_example_config(location="invalid")


class TestGetConfig:
    """Test get_config singleton function."""

    def test_get_config_returns_singleton(self):
        """Test that get_config returns the same instance."""
        config1 = get_config()
        config2 = get_config()

        assert config1 is config2

    def test_get_config_reload(self, monkeypatch):
        """Test that get_config reload parameter works."""
        # Get initial config
        config1 = get_config()
        initial_cache_db_path = config1.paths.cache_db_path

        # Set environment variable
        monkeypatch.setenv("CLM_PATHS__CACHE_DB_PATH", "/tmp/reloaded.db")

        # Get config again without reload - should be same instance
        config2 = get_config(reload=False)
        assert config2.paths.cache_db_path == initial_cache_db_path

        # Get config with reload - should have new value
        config3 = get_config(reload=True)
        assert config3.paths.cache_db_path == "/tmp/reloaded.db"


class TestConfigIntegration:
    """Integration tests for configuration system."""

    def test_complete_priority_chain(self, tmp_path, monkeypatch):
        """Test complete priority chain: env > project > user > system > defaults."""
        # Create project config
        (tmp_path / "clm.toml").write_text("""
[paths]
cache_db_path = "/project/cache.db"
jobs_db_path = "/project/jobs.db"

[logging]
log_level = "WARNING"

[external_tools]
plantuml_jar = "/project/plantuml.jar"
""")

        monkeypatch.chdir(tmp_path)

        # Set some environment variables (should override config file)
        monkeypatch.setenv("CLM_LOGGING__LOG_LEVEL", "ERROR")
        monkeypatch.setenv("PLANTUML_JAR", "/env/plantuml.jar")

        config = ClmConfig()

        # From project config (no env var override)
        assert config.paths.cache_db_path == "/project/cache.db"
        assert config.paths.jobs_db_path == "/project/jobs.db"

        # From environment variable (overrides project config)
        assert config.logging.log_level == "ERROR"
        assert config.external_tools.plantuml_jar == "/env/plantuml.jar"

        # Default value (not in config or env)
        assert config.logging.enable_test_logging is False

    def test_all_config_options(self, tmp_path, monkeypatch):
        """Test that all configuration options can be set."""
        # Clear environment variables that might interfere
        env_vars_to_clear = [
            "PLANTUML_JAR",
            "DRAWIO_EXECUTABLE",
            "WORKER_TYPE",
            "WORKER_ID",
            "USE_SQLITE_QUEUE",
            "JINJA_LINE_STATEMENT_PREFIX",
            "JINJA_TEMPLATES_PATH",
            "LOG_CELL_PROCESSING",
        ]
        for var in env_vars_to_clear:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "clm.toml"
        config_file.write_text("""
[paths]
cache_db_path = "/test/cache.db"
jobs_db_path = "/test/jobs.db"
workspace_path = "/test/workspace"

[external_tools]
plantuml_jar = "/test/plantuml.jar"
drawio_executable = "/test/drawio"

[logging]
log_level = "DEBUG"
enable_test_logging = true

[logging.testing]
e2e_progress_interval = 5
e2e_long_job_threshold = 30
e2e_show_worker_details = true

[jupyter]
jinja_line_statement_prefix = "## custom"
jinja_templates_path = "/test/templates"
log_cell_processing = true

[workers]
worker_type = "plantuml"
worker_id = "test-worker-1"
use_sqlite_queue = false
""")

        monkeypatch.chdir(tmp_path)

        config = ClmConfig()

        # Verify all settings were loaded
        assert config.paths.cache_db_path == "/test/cache.db"
        assert config.paths.jobs_db_path == "/test/jobs.db"
        assert config.paths.workspace_path == "/test/workspace"
        assert config.external_tools.plantuml_jar == "/test/plantuml.jar"
        assert config.external_tools.drawio_executable == "/test/drawio"
        assert config.logging.log_level == "DEBUG"
        assert config.logging.enable_test_logging is True
        assert config.logging.testing.e2e_progress_interval == 5
        assert config.logging.testing.e2e_long_job_threshold == 30
        assert config.logging.testing.e2e_show_worker_details is True
        assert config.jupyter.jinja_line_statement_prefix == "## custom"
        assert config.jupyter.jinja_templates_path == "/test/templates"
        assert config.jupyter.log_cell_processing is True
        assert config.workers.worker_type == "plantuml"
        assert config.workers.worker_id == "test-worker-1"
        assert config.workers.use_sqlite_queue is False


class TestWorkerManagementConfig:
    """Test worker management configuration."""

    def test_worker_management_defaults(self, monkeypatch):
        """Test default worker management configuration."""
        config = ClmConfig()

        assert config.worker_management.default_execution_mode == "direct"
        assert config.worker_management.default_worker_count == 1
        assert config.worker_management.auto_start is True
        assert config.worker_management.auto_stop is True
        assert config.worker_management.reuse_workers is True

    def test_worker_management_from_env(self, monkeypatch):
        """Test loading worker management config from environment."""
        monkeypatch.setenv("CLM_WORKER_MANAGEMENT__DEFAULT_EXECUTION_MODE", "docker")
        monkeypatch.setenv("CLM_WORKER_MANAGEMENT__DEFAULT_WORKER_COUNT", "3")
        monkeypatch.setenv("CLM_WORKER_MANAGEMENT__AUTO_START", "false")

        config = ClmConfig()

        assert config.worker_management.default_execution_mode == "docker"
        assert config.worker_management.default_worker_count == 3
        assert config.worker_management.auto_start is False

    def test_get_worker_config_direct(self, monkeypatch):
        """Test getting worker configuration for direct mode."""
        from clm.infrastructure.config import WorkersManagementConfig

        config = WorkersManagementConfig(default_execution_mode="direct")
        worker_config = config.get_worker_config("notebook")

        assert worker_config.worker_type == "notebook"
        assert worker_config.execution_mode == "direct"
        assert worker_config.count == 1
        assert worker_config.image is None

    def test_get_worker_config_docker(self, monkeypatch):
        """Test getting worker configuration for docker mode."""
        from clm.infrastructure.config import WorkersManagementConfig

        config = WorkersManagementConfig(default_execution_mode="docker")
        worker_config = config.get_worker_config("plantuml")

        assert worker_config.worker_type == "plantuml"
        assert worker_config.execution_mode == "docker"
        assert worker_config.image == "mhoelzl/clx-plantuml-converter:latest"

    def test_get_worker_config_with_override(self, monkeypatch):
        """Test per-type configuration overrides."""
        from clm.infrastructure.config import WorkersManagementConfig, WorkerTypeConfig

        config = WorkersManagementConfig(
            default_execution_mode="direct",
            plantuml=WorkerTypeConfig(execution_mode="docker", count=3),
        )

        # PlantUML should use override
        plantuml_config = config.get_worker_config("plantuml")
        assert plantuml_config.execution_mode == "docker"
        assert plantuml_config.count == 3

        # Notebook should use defaults
        notebook_config = config.get_worker_config("notebook")
        assert notebook_config.execution_mode == "direct"
        assert notebook_config.count == 1
