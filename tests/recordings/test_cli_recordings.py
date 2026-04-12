"""Tests for the recordings CLI command registration and basic help."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group


class TestRecordingsGroup:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["--help"])
        assert result.exit_code == 0
        assert "recordings" in result.output.lower() or "Manage video" in result.output

    def test_subcommands_listed(self):
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["--help"])
        assert result.exit_code == 0
        assert "check" in result.output
        assert "process" in result.output
        assert "batch" in result.output
        assert "status" in result.output
        assert "compare" in result.output

    def test_recordings_registered_in_cli(self):
        from clm.cli.main import cli

        command_names = list(cli.commands)
        assert "recordings" in command_names


class TestJobsSubcommands:
    """Smoke tests for the new jobs subcommands (poll/prune/wait).

    These verify click-level registration and the main safety guards.
    Deep logic lives on :class:`JobManager` and has its own unit tests;
    here we just want to make sure the CLI wires up correctly and
    refuses dangerous operations (prune of in-flight jobs).
    """

    def test_jobs_subcommands_registered(self):
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "--help"])
        assert result.exit_code == 0
        # The four jobs subcommands should all be listed.
        assert "cancel" in result.output
        assert "list" in result.output
        assert "poll" in result.output
        assert "prune" in result.output
        assert "wait" in result.output

    def _install_fake_manager(self, monkeypatch, tmp_path: Path):
        """Return a pre-seeded JobManager and make the CLI use it.

        Builds a real JobManager with a stub backend so we don't touch
        the real user config, then monkeypatches the CLI's factory to
        return that instance regardless of --root.
        """
        from clm.cli.commands import recordings as recordings_cli
        from clm.recordings.workflow.backends.base import (
            BackendCapabilities,
            ProcessingBackend,
        )
        from clm.recordings.workflow.directories import ensure_root
        from clm.recordings.workflow.event_bus import EventBus
        from clm.recordings.workflow.job_manager import JobManager
        from clm.recordings.workflow.job_store import JsonFileJobStore

        class _StubBackend(ProcessingBackend):
            capabilities = BackendCapabilities(
                name="stub",
                display_name="Stub",
                is_synchronous=True,
            )

            def accepts_file(self, path: Path) -> bool:
                return True

            def submit(self, raw_path, final_path, *, options, ctx):
                raise NotImplementedError

            def poll(self, job, *, ctx):
                return job

            def cancel(self, job, *, ctx):
                pass

        ensure_root(tmp_path)
        store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        bus = EventBus()
        manager = JobManager(
            backend=_StubBackend(),
            root_dir=tmp_path,
            store=store,
            bus=bus,
        )
        monkeypatch.setattr(
            recordings_cli,
            "_make_job_manager_for_root",
            lambda root: manager,
        )
        monkeypatch.setattr(
            recordings_cli,
            "_resolve_recordings_root",
            lambda cli_root: tmp_path,
        )
        return manager

    def test_jobs_poll_with_no_jobs_reports_empty(self, tmp_path, monkeypatch):
        self._install_fake_manager(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "poll"])
        assert result.exit_code == 0, result.output
        assert "No in-flight jobs" in result.output

    def test_jobs_prune_deletes_failed_job(self, tmp_path, monkeypatch):
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        manager = self._install_fake_manager(monkeypatch, tmp_path)

        failed = ProcessingJob(
            id="dead-dead-dead-dead",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "dead.mp4",
            final_path=tmp_path / "final" / "dead.mp4",
            relative_dir=Path(),
            state=JobState.FAILED,
            error="something broke",
        )
        manager._store_job(failed)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "prune", "--yes", "--state", "failed"],
        )
        assert result.exit_code == 0, result.output
        assert "Pruned 1 job" in result.output
        assert manager.get(failed.id) is None

    def test_jobs_prune_refuses_in_flight_job_by_id(self, tmp_path, monkeypatch):
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        manager = self._install_fake_manager(monkeypatch, tmp_path)

        in_flight = ProcessingJob(
            id="live-live-live-live",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "live.mp4",
            final_path=tmp_path / "final" / "live.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
        )
        manager._store_job(in_flight)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "prune", "--id", "live", "--yes"],
        )
        # Exits non-zero with a "cancel it first" warning.
        assert result.exit_code == 1, result.output
        assert "Refusing to prune in-flight job" in result.output
        # Job is still in the store.
        assert manager.get(in_flight.id) is not None

    def test_jobs_poll_on_terminal_job_is_noop(self, tmp_path, monkeypatch):
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        manager = self._install_fake_manager(monkeypatch, tmp_path)

        done = ProcessingJob(
            id="done-done-done-done",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "done.mp4",
            final_path=tmp_path / "final" / "done.mp4",
            relative_dir=Path(),
            state=JobState.COMPLETED,
            progress=1.0,
            message="Done",
        )
        manager._store_job(done)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "poll", "done"])
        assert result.exit_code == 0, result.output
        assert "already completed" in result.output


class TestRecordingsConfig:
    def test_recordings_config_in_clm_config(self):
        """RecordingsConfig should be accessible via CLM's config system."""
        from clm.infrastructure.config import ClmConfig, RecordingsConfig

        config = ClmConfig()
        assert isinstance(config.recordings, RecordingsConfig)
        assert config.recordings.auto_process is False
        assert config.recordings.active_course == ""
        assert config.recordings.processing.denoise_atten_lim == 35.0
        assert config.recordings.processing.sample_rate == 48000
