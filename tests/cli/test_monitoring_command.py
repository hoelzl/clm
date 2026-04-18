"""Tests for the ``clm monitor`` and ``clm serve`` CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import monitoring as monitoring_module
from clm.cli.commands.monitoring import monitor, serve


class TestMonitorCommand:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(monitor, ["--help"])
        assert result.exit_code == 0
        assert "--refresh" in result.output
        assert "--jobs-db-path" in result.output
        assert "--log-file" in result.output

    def test_refresh_range_validated(self):
        runner = CliRunner()
        # Click's IntRange validator should reject values outside [1, 10].
        result = runner.invoke(monitor, ["--refresh", "0"])
        assert result.exit_code != 0

        result = runner.invoke(monitor, ["--refresh", "11"])
        assert result.exit_code != 0

    def test_missing_db_exits_with_code_2(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Stub CLMMonitorApp import so the try/except doesn't fail;
        # the command should exit with code 2 before it runs.
        fake_app_cls = MagicMock(name="CLMMonitorApp")
        fake_module = MagicMock()
        fake_module.CLMMonitorApp = fake_app_cls
        monkeypatch.setitem(__import__("sys").modules, "clm.cli.monitor.app", fake_module)

        missing_db = tmp_path / "does_not_exist.db"

        runner = CliRunner()
        result = runner.invoke(monitor, ["--jobs-db-path", str(missing_db)])

        assert result.exit_code == 2
        assert "not found" in result.output

    def test_runs_app_when_db_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_app = MagicMock()
        fake_app_cls = MagicMock(return_value=fake_app)
        fake_module = MagicMock()
        fake_module.CLMMonitorApp = fake_app_cls
        monkeypatch.setitem(__import__("sys").modules, "clm.cli.monitor.app", fake_module)

        runner = CliRunner()
        result = runner.invoke(monitor, ["--jobs-db-path", str(db_path), "--refresh", "3"])

        assert result.exit_code == 0, result.output
        fake_app_cls.assert_called_once()
        kwargs = fake_app_cls.call_args.kwargs
        assert kwargs["db_path"] == db_path
        assert kwargs["refresh_interval"] == 3
        fake_app.run.assert_called_once()

    def test_auto_detects_db_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "auto_detected.db"
        db_path.touch()

        fake_app = MagicMock()
        fake_module = MagicMock()
        fake_module.CLMMonitorApp = MagicMock(return_value=fake_app)
        monkeypatch.setitem(__import__("sys").modules, "clm.cli.monitor.app", fake_module)

        # Patch StatusCollector so the module's .db_path lookup returns our db.
        fake_collector_cls = MagicMock()
        fake_collector = MagicMock()
        fake_collector.db_path = db_path
        fake_collector_cls.return_value = fake_collector
        collector_module = MagicMock()
        collector_module.StatusCollector = fake_collector_cls
        monkeypatch.setitem(__import__("sys").modules, "clm.cli.status.collector", collector_module)

        runner = CliRunner()
        result = runner.invoke(monitor, [])

        assert result.exit_code == 0, result.output
        fake_module.CLMMonitorApp.assert_called_once()
        assert fake_module.CLMMonitorApp.call_args.kwargs["db_path"] == db_path

    def test_import_error_reports_tui_extra(self, monkeypatch: pytest.MonkeyPatch):
        import sys

        fake_module = MagicMock()
        del fake_module.CLMMonitorApp  # Accessing it should raise AttributeError.

        monkeypatch.setitem(sys.modules, "clm.cli.monitor.app", None)  # triggers ImportError

        runner = CliRunner()
        result = runner.invoke(monitor, [])

        assert result.exit_code == 1
        assert "TUI dependencies" in result.output

    def test_app_run_exception_reports_and_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_app = MagicMock()
        fake_app.run.side_effect = RuntimeError("boom")
        fake_module = MagicMock()
        fake_module.CLMMonitorApp = MagicMock(return_value=fake_app)
        monkeypatch.setitem(__import__("sys").modules, "clm.cli.monitor.app", fake_module)

        runner = CliRunner()
        result = runner.invoke(monitor, ["--jobs-db-path", str(db_path)])

        assert result.exit_code == 1
        assert "Error running monitor" in result.output
        assert "boom" in result.output

    def test_log_file_option_mentioned_in_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        db_path = tmp_path / "jobs.db"
        db_path.touch()
        log_file = tmp_path / "err.log"

        fake_app = MagicMock()
        fake_app.run.side_effect = RuntimeError("crash")
        fake_module = MagicMock()
        fake_module.CLMMonitorApp = MagicMock(return_value=fake_app)
        monkeypatch.setitem(__import__("sys").modules, "clm.cli.monitor.app", fake_module)

        runner = CliRunner()
        result = runner.invoke(
            monitor,
            ["--jobs-db-path", str(db_path), "--log-file", str(log_file)],
        )

        assert result.exit_code == 1
        assert "See" in result.output
        assert str(log_file) in result.output


class TestServeCommand:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(serve, ["--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--no-browser" in result.output
        assert "--cors-origin" in result.output

    def test_starts_server_with_existing_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_uvicorn = MagicMock()
        fake_create_app = MagicMock(return_value="fastapi_app")
        fake_web_app_module = MagicMock()
        fake_web_app_module.create_app = fake_create_app

        import sys

        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        monkeypatch.setitem(sys.modules, "clm.web.app", fake_web_app_module)

        runner = CliRunner()
        result = runner.invoke(
            serve,
            [
                "--jobs-db-path",
                str(db_path),
                "--port",
                "9001",
                "--host",
                "127.0.0.1",
                "--no-browser",
            ],
        )

        assert result.exit_code == 0, result.output
        fake_create_app.assert_called_once()
        # create_app call includes db path and cors origins.
        kwargs = fake_create_app.call_args.kwargs
        assert kwargs["db_path"] == db_path
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9001
        assert kwargs["cors_origins"] is None
        fake_uvicorn.run.assert_called_once()

    def test_warns_when_db_missing_but_still_starts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        missing_db = tmp_path / "nope.db"

        fake_uvicorn = MagicMock()
        fake_web_app_module = MagicMock()
        fake_web_app_module.create_app = MagicMock(return_value="app")

        import sys

        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        monkeypatch.setitem(sys.modules, "clm.web.app", fake_web_app_module)

        runner = CliRunner()
        result = runner.invoke(serve, ["--jobs-db-path", str(missing_db), "--no-browser"])

        assert result.exit_code == 0, result.output
        assert "Warning" in result.output
        assert "not found" in result.output
        fake_uvicorn.run.assert_called_once()

    def test_cors_origin_list_passed_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_create_app = MagicMock(return_value="app")
        fake_web_app_module = MagicMock()
        fake_web_app_module.create_app = fake_create_app

        import sys

        monkeypatch.setitem(sys.modules, "uvicorn", MagicMock())
        monkeypatch.setitem(sys.modules, "clm.web.app", fake_web_app_module)

        runner = CliRunner()
        result = runner.invoke(
            serve,
            [
                "--jobs-db-path",
                str(db_path),
                "--no-browser",
                "--cors-origin",
                "https://a.example",
                "--cors-origin",
                "https://b.example",
            ],
        )

        assert result.exit_code == 0
        kwargs = fake_create_app.call_args.kwargs
        assert kwargs["cors_origins"] == ["https://a.example", "https://b.example"]

    def test_opens_browser_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_webbrowser = MagicMock()
        fake_web_app_module = MagicMock()
        fake_web_app_module.create_app = MagicMock(return_value="app")

        import sys

        monkeypatch.setitem(sys.modules, "uvicorn", MagicMock())
        monkeypatch.setitem(sys.modules, "clm.web.app", fake_web_app_module)
        monkeypatch.setitem(sys.modules, "webbrowser", fake_webbrowser)

        runner = CliRunner()
        result = runner.invoke(
            serve,
            ["--jobs-db-path", str(db_path), "--host", "127.0.0.1", "--port", "4242"],
        )

        assert result.exit_code == 0
        fake_webbrowser.open.assert_called_once_with("http://127.0.0.1:4242")

    def test_no_browser_skips_webbrowser(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_webbrowser = MagicMock()
        fake_web_app_module = MagicMock()
        fake_web_app_module.create_app = MagicMock(return_value="app")

        import sys

        monkeypatch.setitem(sys.modules, "uvicorn", MagicMock())
        monkeypatch.setitem(sys.modules, "clm.web.app", fake_web_app_module)
        monkeypatch.setitem(sys.modules, "webbrowser", fake_webbrowser)

        runner = CliRunner()
        result = runner.invoke(serve, ["--jobs-db-path", str(db_path), "--no-browser"])

        assert result.exit_code == 0
        fake_webbrowser.open.assert_not_called()

    def test_uvicorn_error_reports_and_exits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db_path = tmp_path / "jobs.db"
        db_path.touch()

        fake_uvicorn = MagicMock()
        fake_uvicorn.run.side_effect = RuntimeError("port in use")
        fake_web_app_module = MagicMock()
        fake_web_app_module.create_app = MagicMock(return_value="app")

        import sys

        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        monkeypatch.setitem(sys.modules, "clm.web.app", fake_web_app_module)

        runner = CliRunner()
        result = runner.invoke(serve, ["--jobs-db-path", str(db_path), "--no-browser"])

        assert result.exit_code == 1
        assert "Error running server" in result.output
        assert "port in use" in result.output


def test_module_logger_name():
    # Confirm the module exposes a logger with the expected name.
    assert monitoring_module.logger.name == "clm.cli.commands.monitoring"
