"""Tests for the Phase C recordings CLI subcommands.

Covers:
- ``clm recordings backends`` — capability table.
- ``clm recordings submit`` — happy path + explicit ``--root``.
- ``clm recordings jobs list`` / ``jobs cancel``.
- ``clm recordings auphonic preset sync`` — idempotent create vs update.

The tests exercise the commands via :class:`click.testing.CliRunner`, so
the full Click wiring is verified. All config lookups are patched to
use a throwaway :class:`RecordingsConfig` pointing at a tmp directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from clm.cli.commands.recordings import recordings_group
from clm.infrastructure.config import AuphonicConfig, RecordingsConfig
from clm.recordings.workflow.directories import ensure_root

# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


@pytest.fixture()
def rec_root(tmp_path: Path) -> Path:
    """A tmp recordings tree with the standard subdirectories."""
    root = tmp_path / "rec"
    ensure_root(root)
    return root


def _patch_build_config(monkeypatch: pytest.MonkeyPatch, config: RecordingsConfig) -> None:
    """Force the CLI helpers to use *config* instead of reading from disk."""
    monkeypatch.setattr("clm.cli.commands.recordings._build_recordings_config", lambda: config)


# ---------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------


class TestBackendsCommand:
    def test_lists_all_three_backends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_build_config(monkeypatch, RecordingsConfig())

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["backends"])

        assert result.exit_code == 0, result.output
        assert "onnx" in result.output
        assert "external" in result.output
        assert "auphonic" in result.output

    def test_marks_active_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_build_config(
            monkeypatch,
            RecordingsConfig(
                processing_backend="auphonic",
                auphonic=AuphonicConfig(api_key="k"),
            ),
        )
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["backends"])

        assert result.exit_code == 0, result.output
        # The active row includes a checkmark and the backend name.
        assert "auphonic" in result.output
        assert "Active backend from config: auphonic" in result.output


# ---------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------


class TestSubmitCommand:
    def test_submit_sync_backend_completes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        rec_root: Path,
    ) -> None:
        # Use the external backend because its submit() path is simple:
        # wait for matching video + audio pair and mux. We fake both by
        # monkeypatching assemble_one to always succeed.
        from clm.recordings.workflow.directories import to_process_dir

        topic_dir = to_process_dir(rec_root) / "course" / "week01"
        topic_dir.mkdir(parents=True, exist_ok=True)
        video = topic_dir / "topic--RAW.mp4"
        video.write_bytes(b"v")
        audio = topic_dir / "topic--RAW.wav"
        audio.write_bytes(b"a")

        # Patch the assembler to avoid ffmpeg.
        from clm.recordings.workflow.assembler import AssemblyResult

        def _fake_assemble_one(pair, final, archive, **kwargs):
            out = final / pair.relative_dir / f"{pair.base_name}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"muxed")
            return AssemblyResult(video=pair.video, output_file=out, success=True)

        monkeypatch.setattr(
            "clm.recordings.workflow.backends.external.assemble_one",
            _fake_assemble_one,
        )

        _patch_build_config(
            monkeypatch,
            RecordingsConfig(processing_backend="external", root_dir=str(rec_root)),
        )

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["submit", str(audio), "--root", str(rec_root)],
        )

        assert result.exit_code == 0, result.output
        assert "completed" in result.output.lower() or "Done" in result.output

    def test_submit_without_root_flag_uses_configured_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        rec_root: Path,
    ) -> None:
        from clm.recordings.workflow.assembler import AssemblyResult
        from clm.recordings.workflow.directories import to_process_dir

        topic_dir = to_process_dir(rec_root) / "course" / "week01"
        topic_dir.mkdir(parents=True, exist_ok=True)
        video = topic_dir / "topic--RAW.mp4"
        video.write_bytes(b"v")
        audio = topic_dir / "topic--RAW.wav"
        audio.write_bytes(b"a")

        def _fake_assemble_one(pair, final, archive, **kwargs):
            out = final / pair.relative_dir / f"{pair.base_name}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"muxed")
            return AssemblyResult(video=pair.video, output_file=out, success=True)

        monkeypatch.setattr(
            "clm.recordings.workflow.backends.external.assemble_one",
            _fake_assemble_one,
        )

        config = RecordingsConfig(
            processing_backend="external",
            root_dir=str(rec_root),
        )
        _patch_build_config(monkeypatch, config)

        runner = CliRunner()
        # No --root flag: should fall through to config's root_dir.
        result = runner.invoke(recordings_group, ["submit", str(audio)])

        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------
# jobs list / cancel
# ---------------------------------------------------------------------


class TestJobsCommands:
    def test_jobs_list_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rec_root: Path,
    ) -> None:
        _patch_build_config(
            monkeypatch,
            RecordingsConfig(processing_backend="onnx", root_dir=str(rec_root)),
        )
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "list", "--root", str(rec_root)])
        assert result.exit_code == 0, result.output
        assert "No jobs found" in result.output

    def test_jobs_list_shows_persisted_jobs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rec_root: Path,
    ) -> None:
        # Pre-populate the job store with one persisted job.
        from clm.recordings.workflow.job_store import DEFAULT_JOBS_FILE, JsonFileJobStore
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        store = JsonFileJobStore(rec_root / DEFAULT_JOBS_FILE)
        store.save(
            ProcessingJob(
                backend_name="onnx",
                raw_path=Path("/tmp/lecture--RAW.mp4"),
                final_path=Path("/tmp/lecture.mp4"),
                relative_dir=Path(),
                state=JobState.COMPLETED,
                progress=1.0,
                message="Done",
            )
        )

        _patch_build_config(
            monkeypatch,
            RecordingsConfig(processing_backend="onnx", root_dir=str(rec_root)),
        )

        runner = CliRunner()
        # --all is required to show terminal jobs.
        result = runner.invoke(
            recordings_group,
            ["jobs", "list", "--root", str(rec_root), "--all"],
        )

        assert result.exit_code == 0, result.output
        assert "lecture--RAW.mp4" in result.output
        assert "completed" in result.output.lower()

    def test_jobs_cancel_unknown_prefix_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rec_root: Path,
    ) -> None:
        _patch_build_config(
            monkeypatch,
            RecordingsConfig(processing_backend="onnx", root_dir=str(rec_root)),
        )
        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "cancel", "deadbeef", "--root", str(rec_root)],
        )
        assert result.exit_code != 0
        assert "No job matching" in result.output


# ---------------------------------------------------------------------
# auphonic preset sync
# ---------------------------------------------------------------------


class _FakeAuphonicClient:
    """Records calls for the preset sync command tests."""

    def __init__(self, *, existing: list | None = None) -> None:
        from clm.recordings.workflow.backends.auphonic_client import AuphonicPreset

        self._existing = existing or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self._AuphonicPreset = AuphonicPreset

    def list_presets(self):
        return list(self._existing)

    def create_preset(self, *, preset_data):
        self.created.append(preset_data)
        return self._AuphonicPreset(uuid="new-uuid", preset_name=preset_data["preset_name"])

    def update_preset(self, uuid, *, preset_data):
        self.updated.append((uuid, preset_data))
        return self._AuphonicPreset(uuid=uuid, preset_name=preset_data["preset_name"])


class TestAuphonicPresetSync:
    def test_creates_preset_when_missing(
        self, monkeypatch: pytest.MonkeyPatch, rec_root: Path
    ) -> None:
        fake = _FakeAuphonicClient(existing=[])
        monkeypatch.setattr("clm.cli.commands.recordings._build_auphonic_client", lambda: fake)
        _patch_build_config(
            monkeypatch,
            RecordingsConfig(
                processing_backend="auphonic",
                auphonic=AuphonicConfig(api_key="k"),
                root_dir=str(rec_root),
            ),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["auphonic", "preset", "sync"])

        assert result.exit_code == 0, result.output
        assert len(fake.created) == 1
        assert fake.created[0]["preset_name"] == "CLM Lecture Recording"
        assert not fake.updated
        assert "Created" in result.output

    def test_updates_existing_preset(self, monkeypatch: pytest.MonkeyPatch, rec_root: Path) -> None:
        from clm.recordings.workflow.backends.auphonic_client import AuphonicPreset

        fake = _FakeAuphonicClient(
            existing=[
                AuphonicPreset(uuid="old", preset_name="CLM Lecture Recording"),
            ],
        )
        monkeypatch.setattr("clm.cli.commands.recordings._build_auphonic_client", lambda: fake)
        _patch_build_config(
            monkeypatch,
            RecordingsConfig(
                processing_backend="auphonic",
                auphonic=AuphonicConfig(api_key="k"),
                root_dir=str(rec_root),
            ),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["auphonic", "preset", "sync"])

        assert result.exit_code == 0, result.output
        assert not fake.created
        assert len(fake.updated) == 1
        uuid, payload = fake.updated[0]
        assert uuid == "old"
        assert payload["preset_name"] == "CLM Lecture Recording"
        assert "Updated" in result.output

    def test_preset_list_command(self, monkeypatch: pytest.MonkeyPatch, rec_root: Path) -> None:
        from clm.recordings.workflow.backends.auphonic_client import AuphonicPreset

        fake = _FakeAuphonicClient(
            existing=[
                AuphonicPreset(uuid="u1", preset_name="My Preset", short_name="mine"),
            ],
        )
        monkeypatch.setattr("clm.cli.commands.recordings._build_auphonic_client", lambda: fake)
        _patch_build_config(
            monkeypatch,
            RecordingsConfig(
                processing_backend="onnx",
                root_dir=str(rec_root),
            ),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["auphonic", "preset", "list"])

        assert result.exit_code == 0, result.output
        assert "My Preset" in result.output
