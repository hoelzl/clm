"""Tests for --polish-level on CLI commands and --mode deprecation on voiceover sync."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands.polish import polish
from clm.cli.commands.voiceover import voiceover_group

# ---------------------------------------------------------------------------
# clm polish --polish-level
# ---------------------------------------------------------------------------


def _make_slide_group(index: int, has_notes: bool, notes_text: str = "", title: str = ""):
    sg = MagicMock()
    sg.index = index
    sg.has_notes = has_notes
    sg.notes_text = notes_text
    sg.text_content = f"slide content {index}"
    sg.title = title or f"Slide {index}"
    return sg


class TestPolishCommandPolishLevel:
    def _setup_mocks(self, monkeypatch, groups, polish_mock, tmp_path):
        """Wire up standard monkeypatches for the polish command."""
        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr(
            "clm.notebooks.slide_writer.write_narrative",
            MagicMock(return_value=tmp_path / "out.py"),
        )
        monkeypatch.setattr("clm.notebooks.polish.polish_text", polish_mock)

    def test_default_polish_level_is_standard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")
        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        polish_mock = AsyncMock(return_value="polished")
        self._setup_mocks(monkeypatch, groups, polish_mock, tmp_path)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de"])

        assert result.exit_code == 0, result.output
        call_kwargs = polish_mock.call_args.kwargs
        # When --polish-level is absent, the default "standard" is resolved
        # and passed as polish_level kwarg via _polish_all.
        from clm.notebooks.polish_levels import PolishLevel

        assert call_kwargs.get("polish_level") == PolishLevel.standard

    def test_explicit_polish_level_heavy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")
        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        polish_mock = AsyncMock(return_value="polished")
        self._setup_mocks(monkeypatch, groups, polish_mock, tmp_path)

        from clm.notebooks.polish_levels import PolishLevel

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de", "--polish-level", "heavy"])

        assert result.exit_code == 0, result.output
        call_kwargs = polish_mock.call_args.kwargs
        assert call_kwargs.get("polish_level") == PolishLevel.heavy

    def test_explicit_polish_level_light(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")
        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        polish_mock = AsyncMock(return_value="polished")
        self._setup_mocks(monkeypatch, groups, polish_mock, tmp_path)

        from clm.notebooks.polish_levels import PolishLevel

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de", "--polish-level", "light"])

        assert result.exit_code == 0, result.output
        call_kwargs = polish_mock.call_args.kwargs
        assert call_kwargs.get("polish_level") == PolishLevel.light

    def test_invalid_polish_level_rejected(self, tmp_path: Path):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de", "--polish-level", "nuclear"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# clm voiceover sync --mode deprecation
# ---------------------------------------------------------------------------


class TestVoiceoverSyncModeDeprecation:
    def test_mode_polished_emits_deprecation_warning(self, tmp_path: Path):
        slides = tmp_path / "deck.py"
        slides.write_text("# slide")
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        runner = CliRunner()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # This will fail (no actual voiceover deps) but the deprecation
            # warning should still be emitted during the option-processing phase.
            runner.invoke(
                voiceover_group,
                ["sync", str(slides), str(video), "--lang", "de", "--mode", "polished"],
                catch_exceptions=True,
            )

        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any(
            "--mode" in str(w.message) or "deprecated" in str(w.message).lower()
            for w in dep_warnings
        ), (
            f"Expected a DeprecationWarning about --mode; got: {[str(w.message) for w in dep_warnings]}"
        )

    def test_mode_verbatim_emits_deprecation_warning(self, tmp_path: Path):
        slides = tmp_path / "deck.py"
        slides.write_text("# slide")
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        runner = CliRunner()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runner.invoke(
                voiceover_group,
                [
                    "sync",
                    str(slides),
                    str(video),
                    "--lang",
                    "de",
                    "--mode",
                    "verbatim",
                    "--overwrite",
                ],
                catch_exceptions=True,
            )

        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any(
            "--mode" in str(w.message) or "deprecated" in str(w.message).lower()
            for w in dep_warnings
        )

    def test_mode_and_polish_level_together_error(self, tmp_path: Path):
        slides = tmp_path / "deck.py"
        slides.write_text("# slide")
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        runner = CliRunner()

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = runner.invoke(
                voiceover_group,
                [
                    "sync",
                    str(slides),
                    str(video),
                    "--lang",
                    "de",
                    "--mode",
                    "polished",
                    "--polish-level",
                    "heavy",
                ],
                catch_exceptions=True,
            )

        assert result.exit_code != 0

    def test_polish_level_standard_accepted_without_mode(self, tmp_path: Path):
        """--polish-level standard should not trigger any deprecation warning."""
        slides = tmp_path / "deck.py"
        slides.write_text("# slide")
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        runner = CliRunner()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runner.invoke(
                voiceover_group,
                [
                    "sync",
                    str(slides),
                    str(video),
                    "--lang",
                    "de",
                    "--polish-level",
                    "standard",
                ],
                catch_exceptions=True,
            )

        dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        mode_warnings = [w for w in dep_warnings if "--mode" in str(w.message)]
        assert not mode_warnings, f"Unexpected --mode DeprecationWarning: {mode_warnings}"
