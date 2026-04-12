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

    def test_jobs_list_shows_last_poll_error_only_when_present(self, tmp_path, monkeypatch):
        """The 'Last poll error' column should appear only if any job has one.

        Keeps the healthy-state listing narrow. We assert on the
        presence/absence of the error *content* rather than the header
        string, because CliRunner captures Rich output at a narrow
        terminal width that wraps column headers onto multiple lines.
        """
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        manager = self._install_fake_manager(monkeypatch, tmp_path)

        # Short distinctive marker: the CliRunner captures Rich output
        # at a narrow terminal width that truncates long cell values
        # with an ellipsis, so we need something that fits in ~6 chars.
        distinctive_error = "ZQZQ9"
        healthy = ProcessingJob(
            id="healthy-abcd-abcd-abcd",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "ok.mp4",
            final_path=tmp_path / "final" / "ok.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            progress=0.5,
            message="Processing",
        )
        manager._store_job(healthy)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "list"])
        assert result.exit_code == 0, result.output
        # No transient error yet → the distinctive marker is absent.
        assert distinctive_error not in result.output

        # Now add a transient error and list again — the marker must
        # appear somewhere in the output (i.e. the column is rendering).
        healthy.last_poll_error = distinctive_error
        manager._store_job(healthy)
        result = runner.invoke(recordings_group, ["jobs", "list"])
        assert result.exit_code == 0, result.output
        assert distinctive_error in result.output

    def test_jobs_fail_transitions_and_leaves_backend_alone(self, tmp_path, monkeypatch):
        """jobs fail marks the job failed with the reason; backend untouched."""
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        manager = self._install_fake_manager(monkeypatch, tmp_path)

        stuck = ProcessingJob(
            id="stuck-stuck-stuck-stuck",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "stuck.mp4",
            final_path=tmp_path / "final" / "stuck.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
            backend_ref="remote-xyz",
        )
        manager._store_job(stuck)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "fail", "stuck", "--reason", "poll wedged"],
        )
        assert result.exit_code == 0, result.output
        assert "Marked failed" in result.output
        assert "poll wedged" in result.output
        assert "not" in result.output  # "production was NOT cancelled"

        after = manager.get(stuck.id)
        assert after is not None
        assert after.state == JobState.FAILED
        assert after.error == "poll wedged"
        # backend_ref preserved so the user can still manually retrieve.
        assert after.backend_ref == "remote-xyz"

    def test_jobs_fail_refuses_terminal_job(self, tmp_path, monkeypatch):
        """You can't overwrite a COMPLETED job with a manual failure."""
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
        result = runner.invoke(recordings_group, ["jobs", "fail", "done", "--reason", "nope"])
        assert result.exit_code == 1, result.output
        assert "already completed" in result.output

        after = manager.get(done.id)
        assert after is not None
        assert after.state == JobState.COMPLETED
        assert after.error is None

    def test_jobs_poll_watch_rejects_zero(self, tmp_path, monkeypatch):
        """--watch 0 is nonsense; click should reject it with BadParameter."""
        self._install_fake_manager(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "poll", "--watch", "0"])
        assert result.exit_code != 0, result.output

    def test_jobs_poll_watch_stops_when_in_flight_clears(self, tmp_path, monkeypatch):
        """--watch > 1 should break early when nothing is in-flight anymore.

        With a stub backend whose poll() completes the job on the first
        tick, watch=5 should finish after 1 tick, not sleep 4 times.
        Verified indirectly via wall-clock time (no sleep called).
        """
        from clm.recordings.workflow.backends.base import (
            BackendCapabilities,
            ProcessingBackend,
        )
        from clm.recordings.workflow.directories import ensure_root
        from clm.recordings.workflow.event_bus import EventBus
        from clm.recordings.workflow.job_manager import JobManager
        from clm.recordings.workflow.job_store import JsonFileJobStore
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        class _CompletesOnFirstPoll(ProcessingBackend):
            capabilities = BackendCapabilities(
                name="stub",
                display_name="Stub",
                is_synchronous=False,
            )

            def accepts_file(self, path):
                return True

            def submit(self, raw_path, final_path, *, options, ctx):
                raise NotImplementedError

            def poll(self, job, *, ctx):
                job.state = JobState.COMPLETED
                job.progress = 1.0
                job.message = "Done"
                return job

            def cancel(self, job, *, ctx):
                pass

        ensure_root(tmp_path)
        store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        bus = EventBus()
        manager = JobManager(
            backend=_CompletesOnFirstPoll(),
            root_dir=tmp_path,
            store=store,
            bus=bus,
        )
        job = ProcessingJob(
            id="one-tick-one-tick",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "x.mp4",
            final_path=tmp_path / "final" / "x.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
        )
        manager._store_job(job)

        from clm.cli.commands import recordings as recordings_cli

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

        # Fail the test loudly if the CLI actually sleeps: large watch
        # + tiny timeout, and if we wait even a single second we know
        # the early-stop path is broken.
        import time

        sleep_called = 0

        def fake_sleep(seconds: float) -> None:
            nonlocal sleep_called
            sleep_called += 1

        monkeypatch.setattr(time, "sleep", fake_sleep)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "poll", "--watch", "5", "--interval", "0.01"],
        )
        assert result.exit_code == 0, result.output
        # One tick ran, the job hit COMPLETED, loop broke — no sleeps.
        assert sleep_called == 0
        assert manager.get(job.id).state == JobState.COMPLETED


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
