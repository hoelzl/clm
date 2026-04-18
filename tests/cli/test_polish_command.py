"""Tests for the ``clm polish`` CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import polish as polish_module
from clm.cli.commands.polish import _parse_range, polish


class TestParseRange:
    def test_single_number(self):
        assert _parse_range("7") == (7, 7)

    def test_range_pair(self):
        assert _parse_range("5-10") == (5, 10)

    def test_range_single_digit(self):
        assert _parse_range("1-2") == (1, 2)


def _make_slide_group(index: int, has_notes: bool, notes_text: str = "", title: str = ""):
    """Build a minimal SlideGroup-like object for polish tests."""
    sg = MagicMock()
    sg.index = index
    sg.has_notes = has_notes
    sg.notes_text = notes_text
    sg.text_content = f"slide content {index}"
    sg.title = title or f"Slide {index}"
    return sg


class TestPolishCommand:
    def test_no_notes_found_early_return(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text('# %% [markdown] lang="de" tags=["slide"]\n# Hello\n')

        groups = [_make_slide_group(1, has_notes=False)]
        fake_parse_slides = MagicMock(return_value=groups)
        fake_write_narrative = MagicMock()
        fake_polish_text = AsyncMock(return_value="should-not-run")

        monkeypatch.setattr("clm.notebooks.slide_parser.parse_slides", fake_parse_slides)
        monkeypatch.setattr("clm.notebooks.slide_writer.write_narrative", fake_write_narrative)
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de"])

        assert result.exit_code == 0, result.output
        assert "No notes found" in result.output
        fake_polish_text.assert_not_called()
        fake_write_narrative.assert_not_called()

    def test_polishes_each_slide_with_notes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        groups = [
            _make_slide_group(1, has_notes=True, notes_text="raw notes 1"),
            _make_slide_group(2, has_notes=False),
            _make_slide_group(3, has_notes=True, notes_text="raw notes 3"),
        ]
        fake_parse_slides = MagicMock(return_value=groups)
        fake_polish_text = AsyncMock(side_effect=lambda notes, content, **_: f"polished({notes})")
        fake_write_narrative = MagicMock(return_value=slides)

        monkeypatch.setattr("clm.notebooks.slide_parser.parse_slides", fake_parse_slides)
        monkeypatch.setattr("clm.notebooks.slide_writer.write_narrative", fake_write_narrative)
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "en"])

        assert result.exit_code == 0, result.output
        # Only slides 1 and 3 have notes; slide 2 should not have been polished.
        polished_args = [call.args[0] for call in fake_polish_text.call_args_list]
        assert polished_args == ["raw notes 1", "raw notes 3"]

        written_map = fake_write_narrative.call_args.args[1]
        assert written_map == {
            1: "polished(raw notes 1)",
            3: "polished(raw notes 3)",
        }

    def test_slides_range_filters(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        groups = [
            _make_slide_group(i, has_notes=True, notes_text=f"notes {i}") for i in [1, 3, 5, 7]
        ]
        fake_polish_text = AsyncMock(side_effect=lambda notes, content, **_: notes.upper())
        fake_write_narrative = MagicMock(return_value=slides)

        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr("clm.notebooks.slide_writer.write_narrative", fake_write_narrative)
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "en", "--slides-range", "3-5"])

        assert result.exit_code == 0, result.output
        # Only slides 3 and 5 fall in range.
        polished_args = [call.args[0] for call in fake_polish_text.call_args_list]
        assert polished_args == ["notes 3", "notes 5"]

    def test_single_number_range(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        groups = [_make_slide_group(i, has_notes=True, notes_text=f"notes {i}") for i in [1, 2, 3]]
        fake_polish_text = AsyncMock(side_effect=lambda notes, content, **_: notes.upper())
        fake_write_narrative = MagicMock(return_value=slides)

        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr("clm.notebooks.slide_writer.write_narrative", fake_write_narrative)
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "en", "--slides-range", "2"])

        assert result.exit_code == 0, result.output
        polished_args = [call.args[0] for call in fake_polish_text.call_args_list]
        assert polished_args == ["notes 2"]

    def test_dry_run_skips_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        fake_write_narrative = MagicMock()
        fake_polish_text = AsyncMock(return_value="polished hello")

        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr("clm.notebooks.slide_writer.write_narrative", fake_write_narrative)
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        fake_write_narrative.assert_not_called()
        fake_polish_text.assert_awaited_once()

    def test_model_option_passed_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        fake_polish_text = AsyncMock(return_value="polished")

        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr(
            "clm.notebooks.slide_writer.write_narrative", MagicMock(return_value=slides)
        )
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(
            polish,
            [str(slides), "--lang", "de", "--model", "gpt-fake"],
        )

        assert result.exit_code == 0, result.output
        call_kwargs = fake_polish_text.call_args.kwargs
        assert call_kwargs == {"model": "gpt-fake"}

    def test_no_model_option_gives_empty_kwargs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        fake_polish_text = AsyncMock(return_value="polished")

        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr(
            "clm.notebooks.slide_writer.write_narrative", MagicMock(return_value=slides)
        )
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "de"])

        assert result.exit_code == 0, result.output
        # No --model flag → polish_text called without a "model" kwarg.
        call_kwargs = fake_polish_text.call_args.kwargs
        assert call_kwargs == {}

    def test_output_path_forwarded_to_writer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")
        output_path = tmp_path / "out.py"

        groups = [_make_slide_group(1, has_notes=True, notes_text="hello")]
        fake_write_narrative = MagicMock(return_value=output_path)
        fake_polish_text = AsyncMock(return_value="polished")

        monkeypatch.setattr(
            "clm.notebooks.slide_parser.parse_slides", MagicMock(return_value=groups)
        )
        monkeypatch.setattr("clm.notebooks.slide_writer.write_narrative", fake_write_narrative)
        monkeypatch.setattr("clm.notebooks.polish.polish_text", fake_polish_text)

        runner = CliRunner()
        result = runner.invoke(
            polish,
            [str(slides), "--lang", "de", "-o", str(output_path)],
        )

        assert result.exit_code == 0, result.output
        kwargs = fake_write_narrative.call_args.kwargs
        assert kwargs["output_path"] == output_path
        assert kwargs["tag"] == "notes"

    def test_rejects_invalid_lang(self, tmp_path: Path):
        slides = tmp_path / "deck.py"
        slides.write_text("placeholder")

        runner = CliRunner()
        result = runner.invoke(polish, [str(slides), "--lang", "fr"])

        assert result.exit_code != 0

    def test_missing_slides_path_errors(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(polish, [str(tmp_path / "missing.py"), "--lang", "en"])

        assert result.exit_code != 0


def test_module_exposes_logger_and_console():
    assert polish_module.logger.name == "clm.cli.commands.polish"
    # console is a rich.console.Console instance
    from rich.console import Console

    assert isinstance(polish_module.console, Console)
