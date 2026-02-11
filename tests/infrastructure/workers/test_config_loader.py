"""Tests for config_loader module.

This module tests worker configuration loading including:
- Base config loading from files/environment
- CLI overrides for various settings
- Config-style overrides
- Per-worker-type overrides
"""

from unittest.mock import MagicMock, patch

import pytest

from clm.infrastructure.workers.config_loader import load_worker_config


@pytest.fixture
def mock_base_config():
    """Create a mock base configuration."""
    config = MagicMock()
    config.default_execution_mode = "direct"
    config.default_worker_count = 1
    config.auto_start = True
    config.auto_stop = True
    config.reuse_workers = True

    # Create mock per-type configs
    for worker_type in ["notebook", "plantuml", "drawio"]:
        type_config = MagicMock()
        type_config.count = 1
        setattr(config, worker_type, type_config)

    return config


@pytest.fixture
def mock_get_config(mock_base_config):
    """Mock the get_config function."""
    with patch("clm.infrastructure.workers.config_loader.get_config") as mock:
        mock_clx_config = MagicMock()
        mock_clx_config.worker_management = mock_base_config
        mock.return_value = mock_clx_config
        yield mock


class TestLoadWorkerConfigBasic:
    """Test basic config loading without overrides."""

    def test_load_config_returns_config_object(self, mock_get_config, mock_base_config):
        """Should return a configuration object."""
        config = load_worker_config()
        assert config is mock_base_config

    def test_load_config_without_overrides(self, mock_get_config, mock_base_config):
        """Should return base config when no overrides provided."""
        config = load_worker_config()

        # Should not modify any settings
        assert config.default_execution_mode == "direct"
        assert config.default_worker_count == 1
        assert config.auto_start is True
        assert config.auto_stop is True
        assert config.reuse_workers is True

    def test_load_config_with_none_overrides(self, mock_get_config, mock_base_config):
        """Should handle None overrides dict."""
        config = load_worker_config(cli_overrides=None)
        assert config is mock_base_config

    def test_load_config_with_empty_overrides(self, mock_get_config, mock_base_config):
        """Should handle empty overrides dict."""
        config = load_worker_config(cli_overrides={})
        assert config is mock_base_config


class TestExecutionModeOverride:
    """Test execution mode (workers) override."""

    def test_cli_workers_override(self, mock_get_config, mock_base_config):
        """CLI 'workers' should override execution mode."""
        config = load_worker_config(cli_overrides={"workers": "docker"})
        assert config.default_execution_mode == "docker"

    def test_config_default_execution_mode_override(self, mock_get_config, mock_base_config):
        """Config-style 'default_execution_mode' should override."""
        config = load_worker_config(cli_overrides={"default_execution_mode": "docker"})
        assert config.default_execution_mode == "docker"

    def test_cli_workers_takes_precedence(self, mock_get_config, mock_base_config):
        """CLI-style 'workers' should take precedence over config-style."""
        config = load_worker_config(
            cli_overrides={"workers": "docker", "default_execution_mode": "direct"}
        )
        assert config.default_execution_mode == "docker"


class TestWorkerCountOverride:
    """Test worker count override."""

    def test_cli_worker_count_override(self, mock_get_config, mock_base_config):
        """CLI 'worker_count' should override default worker count."""
        config = load_worker_config(cli_overrides={"worker_count": 5})
        assert config.default_worker_count == 5

    def test_config_default_worker_count_override(self, mock_get_config, mock_base_config):
        """Config-style 'default_worker_count' should override."""
        config = load_worker_config(cli_overrides={"default_worker_count": 5})
        assert config.default_worker_count == 5

    def test_cli_worker_count_takes_precedence(self, mock_get_config, mock_base_config):
        """CLI-style 'worker_count' should take precedence."""
        config = load_worker_config(cli_overrides={"worker_count": 10, "default_worker_count": 5})
        assert config.default_worker_count == 10

    def test_worker_count_zero_is_valid(self, mock_get_config, mock_base_config):
        """Zero worker count should be accepted."""
        config = load_worker_config(cli_overrides={"worker_count": 0})
        assert config.default_worker_count == 0


class TestAutoStartOverride:
    """Test auto_start override."""

    def test_no_auto_start_cli_override(self, mock_get_config, mock_base_config):
        """CLI 'no_auto_start' should disable auto_start."""
        config = load_worker_config(cli_overrides={"no_auto_start": True})
        assert config.auto_start is False

    def test_auto_start_config_override_true(self, mock_get_config, mock_base_config):
        """Config-style 'auto_start' True should enable."""
        mock_base_config.auto_start = False
        config = load_worker_config(cli_overrides={"auto_start": True})
        assert config.auto_start is True

    def test_auto_start_config_override_false(self, mock_get_config, mock_base_config):
        """Config-style 'auto_start' False should disable."""
        config = load_worker_config(cli_overrides={"auto_start": False})
        assert config.auto_start is False

    def test_no_auto_start_takes_precedence(self, mock_get_config, mock_base_config):
        """CLI 'no_auto_start' should take precedence over 'auto_start'."""
        config = load_worker_config(cli_overrides={"no_auto_start": True, "auto_start": True})
        assert config.auto_start is False


class TestAutoStopOverride:
    """Test auto_stop override."""

    def test_no_auto_stop_cli_override(self, mock_get_config, mock_base_config):
        """CLI 'no_auto_stop' should disable auto_stop."""
        config = load_worker_config(cli_overrides={"no_auto_stop": True})
        assert config.auto_stop is False

    def test_auto_stop_config_override_true(self, mock_get_config, mock_base_config):
        """Config-style 'auto_stop' True should enable."""
        mock_base_config.auto_stop = False
        config = load_worker_config(cli_overrides={"auto_stop": True})
        assert config.auto_stop is True

    def test_auto_stop_config_override_false(self, mock_get_config, mock_base_config):
        """Config-style 'auto_stop' False should disable."""
        config = load_worker_config(cli_overrides={"auto_stop": False})
        assert config.auto_stop is False

    def test_no_auto_stop_takes_precedence(self, mock_get_config, mock_base_config):
        """CLI 'no_auto_stop' should take precedence over 'auto_stop'."""
        config = load_worker_config(cli_overrides={"no_auto_stop": True, "auto_stop": True})
        assert config.auto_stop is False


class TestReuseWorkersOverride:
    """Test reuse_workers override."""

    def test_fresh_workers_cli_override(self, mock_get_config, mock_base_config):
        """CLI 'fresh_workers' should disable reuse_workers."""
        config = load_worker_config(cli_overrides={"fresh_workers": True})
        assert config.reuse_workers is False

    def test_reuse_workers_config_override_true(self, mock_get_config, mock_base_config):
        """Config-style 'reuse_workers' True should enable."""
        mock_base_config.reuse_workers = False
        config = load_worker_config(cli_overrides={"reuse_workers": True})
        assert config.reuse_workers is True

    def test_reuse_workers_config_override_false(self, mock_get_config, mock_base_config):
        """Config-style 'reuse_workers' False should disable."""
        config = load_worker_config(cli_overrides={"reuse_workers": False})
        assert config.reuse_workers is False

    def test_fresh_workers_takes_precedence(self, mock_get_config, mock_base_config):
        """CLI 'fresh_workers' should take precedence over 'reuse_workers'."""
        config = load_worker_config(cli_overrides={"fresh_workers": True, "reuse_workers": True})
        assert config.reuse_workers is False


class TestPerWorkerTypeOverrides:
    """Test per-worker-type count overrides."""

    def test_notebook_workers_cli_override(self, mock_get_config, mock_base_config):
        """CLI 'notebook_workers' should override notebook count."""
        config = load_worker_config(cli_overrides={"notebook_workers": 5})
        assert config.notebook.count == 5

    def test_plantuml_workers_cli_override(self, mock_get_config, mock_base_config):
        """CLI 'plantuml_workers' should override plantuml count."""
        config = load_worker_config(cli_overrides={"plantuml_workers": 3})
        assert config.plantuml.count == 3

    def test_drawio_workers_cli_override(self, mock_get_config, mock_base_config):
        """CLI 'drawio_workers' should override drawio count."""
        config = load_worker_config(cli_overrides={"drawio_workers": 2})
        assert config.drawio.count == 2

    def test_notebook_count_config_override(self, mock_get_config, mock_base_config):
        """Config-style 'notebook_count' should override notebook count."""
        config = load_worker_config(cli_overrides={"notebook_count": 5})
        assert config.notebook.count == 5

    def test_plantuml_count_config_override(self, mock_get_config, mock_base_config):
        """Config-style 'plantuml_count' should override plantuml count."""
        config = load_worker_config(cli_overrides={"plantuml_count": 3})
        assert config.plantuml.count == 3

    def test_drawio_count_config_override(self, mock_get_config, mock_base_config):
        """Config-style 'drawio_count' should override drawio count."""
        config = load_worker_config(cli_overrides={"drawio_count": 2})
        assert config.drawio.count == 2

    def test_workers_suffix_takes_precedence_over_count(self, mock_get_config, mock_base_config):
        """CLI-style 'X_workers' should take precedence over 'X_count'."""
        config = load_worker_config(cli_overrides={"notebook_workers": 10, "notebook_count": 5})
        assert config.notebook.count == 10

    def test_multiple_worker_types_override(self, mock_get_config, mock_base_config):
        """Should handle overrides for multiple worker types."""
        config = load_worker_config(
            cli_overrides={"notebook_workers": 5, "plantuml_workers": 3, "drawio_workers": 2}
        )
        assert config.notebook.count == 5
        assert config.plantuml.count == 3
        assert config.drawio.count == 2

    def test_zero_workers_is_valid(self, mock_get_config, mock_base_config):
        """Zero workers for a type should be accepted."""
        config = load_worker_config(cli_overrides={"notebook_workers": 0})
        assert config.notebook.count == 0


class TestCombinedOverrides:
    """Test combinations of multiple overrides."""

    def test_all_cli_overrides(self, mock_get_config, mock_base_config):
        """Should handle all CLI overrides together."""
        config = load_worker_config(
            cli_overrides={
                "workers": "docker",
                "worker_count": 5,
                "notebook_workers": 10,
                "plantuml_workers": 5,
                "drawio_workers": 3,
            }
        )

        assert config.default_execution_mode == "docker"
        assert config.default_worker_count == 5
        assert config.notebook.count == 10
        assert config.plantuml.count == 5
        assert config.drawio.count == 3

    def test_all_config_style_overrides(self, mock_get_config, mock_base_config):
        """Should handle all config-style overrides together."""
        config = load_worker_config(
            cli_overrides={
                "default_execution_mode": "docker",
                "default_worker_count": 5,
                "auto_start": False,
                "auto_stop": False,
                "reuse_workers": False,
                "notebook_count": 10,
                "plantuml_count": 5,
                "drawio_count": 3,
            }
        )

        assert config.default_execution_mode == "docker"
        assert config.default_worker_count == 5
        assert config.auto_start is False
        assert config.auto_stop is False
        assert config.reuse_workers is False
        assert config.notebook.count == 10
        assert config.plantuml.count == 5
        assert config.drawio.count == 3


class TestLogging:
    """Test that overrides are logged."""

    def test_logs_execution_mode_override(self, mock_get_config, mock_base_config, caplog):
        """Should log execution mode override."""
        import logging

        with caplog.at_level(logging.INFO, logger="clm.infrastructure.workers.config_loader"):
            load_worker_config(cli_overrides={"workers": "docker"})
        assert "default_execution_mode" in caplog.text

    def test_logs_worker_count_override(self, mock_get_config, mock_base_config, caplog):
        """Should log worker count override."""
        import logging

        with caplog.at_level(logging.INFO, logger="clm.infrastructure.workers.config_loader"):
            load_worker_config(cli_overrides={"worker_count": 5})
        assert "default_worker_count" in caplog.text

    def test_logs_auto_start_override(self, mock_get_config, mock_base_config, caplog):
        """Should log auto_start override."""
        import logging

        with caplog.at_level(logging.INFO, logger="clm.infrastructure.workers.config_loader"):
            load_worker_config(cli_overrides={"auto_start": False})
        assert "auto_start" in caplog.text

    def test_logs_per_type_override(self, mock_get_config, mock_base_config, caplog):
        """Should log per-type worker count override."""
        import logging

        with caplog.at_level(logging.INFO, logger="clm.infrastructure.workers.config_loader"):
            load_worker_config(cli_overrides={"notebook_workers": 5})
        assert "notebook.count" in caplog.text
