"""Tests for ``clm recordings`` CLI commands.

Complements ``test_cli_recordings.py`` (which covers the jobs subcommand
group) by exercising the remaining commands: check, process, batch,
status, compare, assemble, serve, backends, and the internal config
resolution helpers. Mocks at narrow seams so the tests don't require
ffmpeg, OBS, uvicorn, or the Auphonic API.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import recordings as recordings_module
from clm.cli.commands.recordings import (
    _build_recordings_config,
    _get_auphonic_config,
    _get_obs_config,
    _get_raw_suffix,
    _get_watcher_config,
    _load_pipeline_config,
    _resolve_recordings_root,
    recordings_group,
)

# ---------------------------------------------------------------------------
# _get_*_config helpers — fallback paths (config system unavailable).
# ---------------------------------------------------------------------------


class TestGetObsConfig:
    def test_returns_config_values_when_available(self, monkeypatch: pytest.MonkeyPatch):
        fake_config_module = MagicMock()
        fake_cfg = MagicMock()
        fake_cfg.recordings.obs_host = "host.internal"
        fake_cfg.recordings.obs_port = 4456
        fake_cfg.recordings.obs_password = "secret"
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        host, port, password = _get_obs_config()
        assert (host, port, password) == ("host.internal", 4456, "secret")

    def test_falls_back_to_defaults_on_exception(self, monkeypatch: pytest.MonkeyPatch):
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        host, port, password = _get_obs_config()
        assert (host, port, password) == ("localhost", 4455, "")


class TestGetRawSuffix:
    def test_returns_configured_value(self, monkeypatch: pytest.MonkeyPatch):
        fake_config_module = MagicMock()
        fake_cfg = MagicMock()
        fake_cfg.recordings.raw_suffix = "--MY-RAW"
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        assert _get_raw_suffix() == "--MY-RAW"

    def test_falls_back_to_default_when_empty(self, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX

        fake_config_module = MagicMock()
        fake_cfg = MagicMock()
        fake_cfg.recordings.raw_suffix = ""
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        assert _get_raw_suffix() == DEFAULT_RAW_SUFFIX

    def test_falls_back_to_default_on_exception(self, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX

        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        assert _get_raw_suffix() == DEFAULT_RAW_SUFFIX


class TestGetWatcherConfig:
    def test_returns_config_values(self, monkeypatch: pytest.MonkeyPatch):
        fake_cfg = MagicMock()
        fake_cfg.recordings.processing_backend = "external"
        fake_cfg.recordings.stability_check_interval = 5.5
        fake_cfg.recordings.stability_check_count = 7
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        backend, interval, count = _get_watcher_config()
        assert backend == "external"
        assert interval == 5.5
        assert count == 7

    def test_fallback_on_exception(self, monkeypatch: pytest.MonkeyPatch):
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        assert _get_watcher_config() == ("onnx", 2.0, 3)


class TestGetAuphonicConfig:
    def test_returns_config_values(self, monkeypatch: pytest.MonkeyPatch):
        fake_cfg = MagicMock()
        fake_cfg.recordings.auphonic.api_key = "abc"
        fake_cfg.recordings.auphonic.preset = "my-preset"
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        api_key, preset = _get_auphonic_config()
        assert api_key == "abc"
        assert preset == "my-preset"

    def test_fallback_on_exception(self, monkeypatch: pytest.MonkeyPatch):
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        assert _get_auphonic_config() == ("", "")


class TestLoadPipelineConfig:
    def test_loads_from_json_file(self, tmp_path: Path):
        config_file = tmp_path / "pipeline.json"
        config_file.write_text(
            '{"denoise_atten_lim": 40.0, "sample_rate": 44100, "output_extension": "mkv"}'
        )

        config = _load_pipeline_config(config_file)

        assert config.denoise_atten_lim == 40.0
        assert config.sample_rate == 44100
        assert config.output_extension == "mkv"

    def test_uses_clm_config_when_no_file(self, monkeypatch: pytest.MonkeyPatch):
        fake_cfg = MagicMock()
        fake_rec = MagicMock()
        fake_rec.denoise_atten_lim = 33.0
        fake_rec.sample_rate = 48000
        fake_rec.audio_bitrate = "192k"
        fake_rec.video_codec = "copy"
        fake_rec.output_extension = "mp4"
        fake_rec.highpass_freq = 100
        fake_rec.loudnorm_target = -14
        fake_cfg.recordings.processing = fake_rec

        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        config = _load_pipeline_config(None)

        assert config.denoise_atten_lim == 33.0
        assert config.sample_rate == 48000

    def test_falls_back_to_defaults(self, monkeypatch: pytest.MonkeyPatch):
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(side_effect=RuntimeError)
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        config = _load_pipeline_config(None)

        # Defaults from PipelineConfig() — at least keys are present.
        assert config.sample_rate > 0
        assert isinstance(config.output_extension, str)


# ---------------------------------------------------------------------------
# _resolve_recordings_root / _build_recordings_config
# ---------------------------------------------------------------------------


class TestResolveRecordingsRoot:
    def test_cli_root_takes_precedence(self, tmp_path: Path):
        assert _resolve_recordings_root(tmp_path) == tmp_path

    def test_uses_configured_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake_rec = MagicMock()
        fake_rec.root_dir = str(tmp_path)
        monkeypatch.setattr(recordings_module, "_build_recordings_config", lambda: fake_rec)

        assert _resolve_recordings_root(None) == tmp_path

    def test_raises_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch):
        fake_rec = MagicMock()
        fake_rec.root_dir = ""
        monkeypatch.setattr(recordings_module, "_build_recordings_config", lambda: fake_rec)

        import click

        with pytest.raises(click.ClickException, match="No recordings root"):
            _resolve_recordings_root(None)


class TestBuildRecordingsConfig:
    def test_returns_real_recordings_config_on_success(self, monkeypatch: pytest.MonkeyPatch):
        from clm.infrastructure.config import RecordingsConfig

        fake_cfg = MagicMock()
        fake_rec = RecordingsConfig()
        fake_cfg.recordings = fake_rec
        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(return_value=fake_cfg)
        fake_config_module.RecordingsConfig = RecordingsConfig
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        assert _build_recordings_config() is fake_rec

    def test_returns_defaults_on_error(self, monkeypatch: pytest.MonkeyPatch):
        from clm.infrastructure.config import RecordingsConfig

        fake_config_module = MagicMock()
        fake_config_module.get_config = MagicMock(side_effect=RuntimeError)
        fake_config_module.RecordingsConfig = RecordingsConfig
        monkeypatch.setitem(sys.modules, "clm.infrastructure.config", fake_config_module)

        result = _build_recordings_config()
        assert isinstance(result, RecordingsConfig)


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    def test_all_dependencies_found(self, monkeypatch: pytest.MonkeyPatch):
        fake_utils = MagicMock()
        fake_utils.check_dependencies = MagicMock(
            return_value={"ffmpeg": "/usr/bin/ffmpeg", "onnxruntime": "1.17"}
        )
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.utils", fake_utils)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["check"])

        assert result.exit_code == 0, result.output
        assert "All dependencies found" in result.output

    def test_missing_dependencies_exit_1(self, monkeypatch: pytest.MonkeyPatch):
        fake_utils = MagicMock()
        fake_utils.check_dependencies = MagicMock(
            return_value={"ffmpeg": "/usr/bin/ffmpeg", "onnxruntime": None}
        )
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.utils", fake_utils)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["check"])

        assert result.exit_code == 1
        assert "Some dependencies are missing" in result.output


# ---------------------------------------------------------------------------
# process command
# ---------------------------------------------------------------------------


def _install_fake_pipeline(monkeypatch, success: bool, error: str | None = None):
    """Install a fake ProcessingPipeline that short-circuits .process()."""
    fake_pipeline_module = MagicMock()

    from clm.recordings.processing.pipeline import ProcessingResult

    class FakePipeline:
        def __init__(self, _config):
            pass

        def process(self, input_file: Path, output_file: Path, *, on_step=None):
            if on_step is not None:
                on_step(1, "doing_step", 1)
            # Create output file so stat().st_size works in the CLI.
            if success:
                output_file.write_bytes(b"output")
            return ProcessingResult(
                input_file=input_file,
                output_file=output_file,
                success=success,
                duration_seconds=5.0,
                error=error,
            )

    fake_pipeline_module.ProcessingPipeline = FakePipeline
    monkeypatch.setitem(sys.modules, "clm.recordings.processing.pipeline", fake_pipeline_module)


class TestProcessCommand:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_file = tmp_path / "lecture.mkv"
        input_file.write_bytes(b"input")

        _install_fake_pipeline(monkeypatch, success=True)
        # Avoid the config lookup doing real work.
        monkeypatch.setattr(
            recordings_module,
            "_load_pipeline_config",
            lambda _: __import__(
                "clm.recordings.processing.config", fromlist=["PipelineConfig"]
            ).PipelineConfig(),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["process", str(input_file)])

        assert result.exit_code == 0, result.output
        assert "Done in" in result.output
        # Output file was placed next to input with default suffix.
        assert (tmp_path / "lecture_final.mp4").is_file()

    def test_failure_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_file = tmp_path / "lecture.mkv"
        input_file.write_bytes(b"input")

        _install_fake_pipeline(monkeypatch, success=False, error="mux failed")
        monkeypatch.setattr(
            recordings_module,
            "_load_pipeline_config",
            lambda _: __import__(
                "clm.recordings.processing.config", fromlist=["PipelineConfig"]
            ).PipelineConfig(),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["process", str(input_file)])

        assert result.exit_code == 1
        assert "Failed" in result.output
        assert "mux failed" in result.output

    def test_custom_output_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_file = tmp_path / "lecture.mkv"
        input_file.write_bytes(b"input")
        output_file = tmp_path / "custom.mp4"

        _install_fake_pipeline(monkeypatch, success=True)
        monkeypatch.setattr(
            recordings_module,
            "_load_pipeline_config",
            lambda _: __import__(
                "clm.recordings.processing.config", fromlist=["PipelineConfig"]
            ).PipelineConfig(),
        )

        runner = CliRunner()
        result = runner.invoke(
            recordings_group, ["process", str(input_file), "-o", str(output_file)]
        )

        assert result.exit_code == 0, result.output
        assert output_file.is_file()

    def test_keep_temp_flag_sets_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_file = tmp_path / "lecture.mkv"
        input_file.write_bytes(b"input")

        captured_configs = []

        from clm.recordings.processing.pipeline import ProcessingResult

        class FakePipeline:
            def __init__(self, config):
                captured_configs.append(config)

            def process(self, input_file: Path, output_file: Path, *, on_step=None):
                output_file.write_bytes(b"x")
                return ProcessingResult(
                    input_file=input_file,
                    output_file=output_file,
                    success=True,
                )

        fake_pipeline_module = MagicMock()
        fake_pipeline_module.ProcessingPipeline = FakePipeline
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.pipeline", fake_pipeline_module)

        from clm.recordings.processing.config import PipelineConfig

        monkeypatch.setattr(recordings_module, "_load_pipeline_config", lambda _: PipelineConfig())

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["process", str(input_file), "--keep-temp"])

        assert result.exit_code == 0, result.output
        assert captured_configs[0].keep_temp is True


# ---------------------------------------------------------------------------
# batch command
# ---------------------------------------------------------------------------


class TestBatchCommand:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_dir = tmp_path / "in"
        input_dir.mkdir()

        from clm.recordings.processing.batch import BatchResult

        fake_batch_module = MagicMock()
        fake_batch_module.process_batch = MagicMock(return_value=BatchResult())
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.batch", fake_batch_module)
        monkeypatch.setattr(
            recordings_module,
            "_load_pipeline_config",
            lambda _: __import__(
                "clm.recordings.processing.config", fromlist=["PipelineConfig"]
            ).PipelineConfig(),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["batch", str(input_dir)])

        assert result.exit_code == 0, result.output
        # Default output is input_dir / processed
        kwargs = fake_batch_module.process_batch.call_args.kwargs
        assert kwargs["recursive"] is False
        assert callable(kwargs["on_file"])
        assert callable(kwargs["on_step"])

    def test_failure_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_dir = tmp_path / "in"
        input_dir.mkdir()

        from clm.recordings.processing.batch import BatchResult
        from clm.recordings.processing.pipeline import ProcessingResult

        fake_batch_module = MagicMock()
        fake_batch_module.process_batch = MagicMock(
            return_value=BatchResult(
                failed=[
                    ProcessingResult(
                        input_file=input_dir / "x.mkv",
                        output_file=tmp_path / "x.mp4",
                        success=False,
                        error="boom",
                    )
                ]
            )
        )
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.batch", fake_batch_module)
        monkeypatch.setattr(
            recordings_module,
            "_load_pipeline_config",
            lambda _: __import__(
                "clm.recordings.processing.config", fromlist=["PipelineConfig"]
            ).PipelineConfig(),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["batch", str(input_dir)])

        assert result.exit_code == 1

    def test_recursive_flag_passed_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        input_dir = tmp_path / "in"
        input_dir.mkdir()

        from clm.recordings.processing.batch import BatchResult

        fake_batch_module = MagicMock()
        fake_batch_module.process_batch = MagicMock(return_value=BatchResult())
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.batch", fake_batch_module)
        monkeypatch.setattr(
            recordings_module,
            "_load_pipeline_config",
            lambda _: __import__(
                "clm.recordings.processing.config", fromlist=["PipelineConfig"]
            ).PipelineConfig(),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["batch", str(input_dir), "--recursive"])

        assert result.exit_code == 0
        assert fake_batch_module.process_batch.call_args.kwargs["recursive"] is True


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_no_state_for_course_exits_1(self, monkeypatch: pytest.MonkeyPatch):
        fake_state_module = MagicMock()
        fake_state_module.load_state = MagicMock(return_value=None)
        monkeypatch.setitem(sys.modules, "clm.recordings.state", fake_state_module)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["status", "my-course"])

        assert result.exit_code == 1
        assert "No recording state" in result.output

    def test_displays_lecture_table(self, monkeypatch: pytest.MonkeyPatch):
        # Build a fake state with different lecture statuses.
        part_processed = MagicMock(status="processed")
        part_failed = MagicMock(status="failed")
        part_processing = MagicMock(status="processing")
        part_pending = MagicMock(status="pending")

        def _lecture(lecture_id: str, display_name: str, parts: list):
            lec = MagicMock()
            lec.lecture_id = lecture_id
            lec.display_name = display_name
            lec.parts = parts
            return lec

        lectures = [
            _lecture("L1", "Intro", [part_processed]),  # all processed
            _lecture("L2", "Two", [part_failed]),  # any failed
            _lecture("L3", "Three", [part_processing]),  # any processing
            _lecture("L4", "Four", [part_pending]),  # pending fallback
            _lecture("L5", "Five", []),  # unrecorded
        ]

        fake_state = MagicMock()
        fake_state.progress = (2, 5)
        fake_state.continue_current_lecture = True
        fake_state.lectures = lectures
        fake_state.next_lecture_index = 2  # "L3" should be marked

        fake_state_module = MagicMock()
        fake_state_module.load_state = MagicMock(return_value=fake_state)
        monkeypatch.setitem(sys.modules, "clm.recordings.state", fake_state_module)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["status", "my-course"])

        assert result.exit_code == 0, result.output
        assert "Progress:" in result.output
        assert "2/5" in result.output
        # At least each lecture id should appear somewhere.
        for lid in ["L1", "L2", "L3", "L4", "L5"]:
            assert lid in result.output
        # The next-lecture marker is on L3 (index 2).
        assert "*" in result.output


# ---------------------------------------------------------------------------
# compare command
# ---------------------------------------------------------------------------


class TestCompareCommand:
    def test_writes_html(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        version_a = tmp_path / "a.mp4"
        version_a.write_bytes(b"a")
        version_b = tmp_path / "b.mp4"
        version_b.write_bytes(b"b")
        out_html = tmp_path / "cmp.html"

        fake_compare = MagicMock()
        fake_compare.audio_to_base64 = MagicMock(return_value="BASE64")
        fake_compare.extract_audio_segment = MagicMock()
        fake_compare.generate_comparison_html = MagicMock(return_value="<html>hi</html>")
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.compare", fake_compare)

        fake_utils = MagicMock()
        fake_utils.find_ffmpeg = MagicMock(return_value=Path("/mock/ffmpeg"))
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.utils", fake_utils)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["compare", str(version_a), str(version_b), "-o", str(out_html)],
        )

        assert result.exit_code == 0, result.output
        assert out_html.is_file()
        assert "<html>" in out_html.read_text()
        # generate_comparison_html called with both labels + base64.
        kwargs = fake_compare.generate_comparison_html.call_args.kwargs
        assert kwargs["label_a"] == "Version A"
        assert kwargs["label_b"] == "Version B"
        assert kwargs["original_b64"] is None

    def test_includes_original(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        version_a = tmp_path / "a.mp4"
        version_a.write_bytes(b"a")
        version_b = tmp_path / "b.mp4"
        version_b.write_bytes(b"b")
        original = tmp_path / "orig.mp4"
        original.write_bytes(b"orig")
        out_html = tmp_path / "cmp.html"

        fake_compare = MagicMock()
        fake_compare.audio_to_base64 = MagicMock(return_value="BASE64")
        fake_compare.extract_audio_segment = MagicMock()
        fake_compare.generate_comparison_html = MagicMock(return_value="<html></html>")
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.compare", fake_compare)

        fake_utils = MagicMock()
        fake_utils.find_ffmpeg = MagicMock(return_value=Path("/mock/ffmpeg"))
        monkeypatch.setitem(sys.modules, "clm.recordings.processing.utils", fake_utils)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            [
                "compare",
                str(version_a),
                str(version_b),
                "--original",
                str(original),
                "-o",
                str(out_html),
                "--label-a",
                "Old",
                "--label-b",
                "New",
            ],
        )

        assert result.exit_code == 0, result.output
        kwargs = fake_compare.generate_comparison_html.call_args.kwargs
        assert kwargs["label_a"] == "Old"
        assert kwargs["label_b"] == "New"
        assert kwargs["original_b64"] == "BASE64"


# ---------------------------------------------------------------------------
# assemble command
# ---------------------------------------------------------------------------


class TestAssembleCommand:
    def test_validation_errors_exit_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake_directories = MagicMock()
        fake_directories.validate_root = MagicMock(return_value=["missing to-process"])
        fake_directories.to_process_dir = MagicMock(return_value=tmp_path / "tp")
        fake_directories.find_pending_pairs = MagicMock(return_value=[])
        monkeypatch.setitem(sys.modules, "clm.recordings.workflow.directories", fake_directories)
        monkeypatch.setattr(recordings_module, "_get_raw_suffix", lambda: "--RAW")

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["assemble", str(tmp_path)])

        assert result.exit_code == 1
        assert "missing to-process" in result.output

    def test_no_pending_pairs_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake_directories = MagicMock()
        fake_directories.validate_root = MagicMock(return_value=[])
        fake_directories.to_process_dir = MagicMock(return_value=tmp_path / "tp")
        fake_directories.find_pending_pairs = MagicMock(return_value=[])
        monkeypatch.setitem(sys.modules, "clm.recordings.workflow.directories", fake_directories)
        monkeypatch.setattr(recordings_module, "_get_raw_suffix", lambda: "--RAW")

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["assemble", str(tmp_path)])

        assert result.exit_code == 0
        assert "No pending" in result.output

    def test_dry_run_lists_pairs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        pair = MagicMock()
        pair.relative_dir = Path("week1")
        pair.video = MagicMock()
        pair.video.name = "t1--RAW.mp4"

        fake_directories = MagicMock()
        fake_directories.validate_root = MagicMock(return_value=[])
        fake_directories.to_process_dir = MagicMock(return_value=tmp_path / "tp")
        fake_directories.find_pending_pairs = MagicMock(return_value=[pair])
        monkeypatch.setitem(sys.modules, "clm.recordings.workflow.directories", fake_directories)

        fake_assembler = MagicMock()
        fake_assembler.assemble_all = MagicMock()
        monkeypatch.setitem(sys.modules, "clm.recordings.workflow.assembler", fake_assembler)

        monkeypatch.setattr(recordings_module, "_get_raw_suffix", lambda: "--RAW")

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["assemble", str(tmp_path), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        fake_assembler.assemble_all.assert_not_called()

    def test_failures_exit_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        pair = MagicMock()
        pair.relative_dir = Path("week1")
        pair.video = MagicMock()
        pair.video.name = "t1--RAW.mp4"

        fake_directories = MagicMock()
        fake_directories.validate_root = MagicMock(return_value=[])
        fake_directories.to_process_dir = MagicMock(return_value=tmp_path / "tp")
        fake_directories.find_pending_pairs = MagicMock(return_value=[pair])
        monkeypatch.setitem(sys.modules, "clm.recordings.workflow.directories", fake_directories)

        fake_result = MagicMock()
        fake_result.failed = ["something"]
        fake_result.summary = MagicMock(return_value="failed 1")

        fake_assembler = MagicMock()
        fake_assembler.assemble_all = MagicMock(return_value=fake_result)
        monkeypatch.setitem(sys.modules, "clm.recordings.workflow.assembler", fake_assembler)

        monkeypatch.setattr(recordings_module, "_get_raw_suffix", lambda: "--RAW")

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["assemble", str(tmp_path)])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# serve_recordings command (uvicorn/create_app mocked)
# ---------------------------------------------------------------------------


class TestServeRecordingsCommand:
    def _install_stubs(self, monkeypatch):
        fake_uvicorn = MagicMock()
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

        fake_app_module = MagicMock()
        fake_app_module.create_app = MagicMock(return_value="app_instance")
        monkeypatch.setitem(sys.modules, "clm.recordings.web.app", fake_app_module)

        fake_webbrowser = MagicMock()
        monkeypatch.setitem(sys.modules, "webbrowser", fake_webbrowser)

        monkeypatch.setattr(
            recordings_module,
            "_get_obs_config",
            lambda: ("cfg-host", 4455, ""),
        )
        monkeypatch.setattr(recordings_module, "_get_raw_suffix", lambda: "--RAW")
        monkeypatch.setattr(
            recordings_module,
            "_get_watcher_config",
            lambda: ("onnx", 2.0, 3),
        )
        monkeypatch.setattr(
            recordings_module,
            "_get_auphonic_config",
            lambda: ("", ""),
        )
        return fake_uvicorn, fake_app_module, fake_webbrowser

    def test_starts_server_with_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake_uvicorn, fake_app_module, _ = self._install_stubs(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["serve", str(tmp_path), "--no-browser"])

        assert result.exit_code == 0, result.output
        fake_app_module.create_app.assert_called_once()
        # OBS fallback from config was used.
        kwargs = fake_app_module.create_app.call_args.kwargs
        assert kwargs["obs_host"] == "cfg-host"
        fake_uvicorn.run.assert_called_once()

    def test_cli_obs_flags_override_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _, fake_app_module, _ = self._install_stubs(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            [
                "serve",
                str(tmp_path),
                "--no-browser",
                "--obs-host",
                "cli-host",
                "--obs-port",
                "4456",
                "--obs-password",
                "cli-pw",
            ],
        )

        assert result.exit_code == 0, result.output
        kwargs = fake_app_module.create_app.call_args.kwargs
        assert kwargs["obs_host"] == "cli-host"
        assert kwargs["obs_port"] == 4456
        assert kwargs["obs_password"] == "cli-pw"

    def test_opens_browser_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _, _, fake_webbrowser = self._install_stubs(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["serve", str(tmp_path)])

        assert result.exit_code == 0, result.output
        fake_webbrowser.open.assert_called_once()

    def test_server_exception_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        fake_uvicorn, _, _ = self._install_stubs(monkeypatch)
        fake_uvicorn.run.side_effect = RuntimeError("boom")

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["serve", str(tmp_path), "--no-browser"])

        assert result.exit_code == 1
        assert "Server error" in result.output


# ---------------------------------------------------------------------------
# backends command (list_backends)
# ---------------------------------------------------------------------------


class TestListBackendsCommand:
    def test_lists_all_backends(self, monkeypatch: pytest.MonkeyPatch):
        # Override the active backend setter.
        fake_config = MagicMock()
        fake_config.processing_backend = "onnx"
        monkeypatch.setattr(recordings_module, "_build_recordings_config", lambda: fake_config)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["backends"])

        assert result.exit_code == 0, result.output
        # All three backend names appear in the table.
        assert "onnx" in result.output
        assert "external" in result.output
        assert "auphonic" in result.output
        # And the active-backend hint is printed.
        assert "Active backend" in result.output


# ---------------------------------------------------------------------------
# wait_job command
# ---------------------------------------------------------------------------


class TestWaitJobCommand:
    def _install_fake_manager(self, monkeypatch, tmp_path: Path):
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
                is_synchronous=False,
            )

            def accepts_file(self, path: Path) -> bool:
                return True

            def submit(self, raw_path, final_path, *, options, ctx):
                raise NotImplementedError

            def poll(self, job, *, ctx):
                # Transition to COMPLETED on first poll.
                from clm.recordings.workflow.jobs import JobState

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

        # Patch time.sleep so the wait loop doesn't actually sleep.
        import time as _time

        monkeypatch.setattr(_time, "sleep", lambda _: None)
        return manager

    def test_wait_on_terminal_job_returns_immediately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
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
        result = runner.invoke(recordings_group, ["jobs", "wait", "done", "--interval", "0"])

        assert result.exit_code == 0, result.output
        assert "already completed" in result.output

    def test_wait_for_completion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        manager = self._install_fake_manager(monkeypatch, tmp_path)

        job = ProcessingJob(
            id="wait-wait-wait-wait",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "x.mp4",
            final_path=tmp_path / "final" / "x.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
        )
        manager._store_job(job)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "wait", "wait", "--interval", "0"])

        assert result.exit_code == 0, result.output
        assert "Done" in result.output

    def test_wait_for_failure_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.backends.base import (
            BackendCapabilities,
            ProcessingBackend,
        )
        from clm.recordings.workflow.directories import ensure_root
        from clm.recordings.workflow.event_bus import EventBus
        from clm.recordings.workflow.job_manager import JobManager
        from clm.recordings.workflow.job_store import JsonFileJobStore
        from clm.recordings.workflow.jobs import JobState, ProcessingJob

        class _FailingBackend(ProcessingBackend):
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
                job.state = JobState.FAILED
                job.error = "broken"
                return job

            def cancel(self, job, *, ctx):
                pass

        ensure_root(tmp_path)
        store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        bus = EventBus()
        manager = JobManager(
            backend=_FailingBackend(),
            root_dir=tmp_path,
            store=store,
            bus=bus,
        )

        from clm.cli.commands import recordings as recordings_cli

        monkeypatch.setattr(recordings_cli, "_make_job_manager_for_root", lambda root: manager)
        monkeypatch.setattr(recordings_cli, "_resolve_recordings_root", lambda cli_root: tmp_path)

        import time as _time

        monkeypatch.setattr(_time, "sleep", lambda _: None)

        job = ProcessingJob(
            id="fail-fail-fail-fail",
            backend_name="stub",
            raw_path=tmp_path / "to-process" / "x.mp4",
            final_path=tmp_path / "final" / "x.mp4",
            relative_dir=Path(),
            state=JobState.PROCESSING,
        )
        manager._store_job(job)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "wait", "fail", "--interval", "0"])

        assert result.exit_code == 1
        assert "Failed" in result.output
