"""Tests for the clm language-view CLI command."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.commands.language_view import language_view_cmd


def _write_slide(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


BILINGUAL = """\
# j2 from 'macros.j2' import header
# {{ header("Titel", "Title") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Einführung

# %% [markdown] lang="en" tags=["slide"]
# ## Introduction

# %% tags=["keep"]
x = 1
"""


class TestLanguageViewCommand:
    def test_de_view(self, tmp_path):
        p = _write_slide(tmp_path, "slides_test.py", BILINGUAL)
        runner = CliRunner()
        result = runner.invoke(language_view_cmd, [str(p), "de"])
        assert result.exit_code == 0
        assert "Einführung" in result.output
        assert "Introduction" not in result.output
        assert "x = 1" in result.output

    def test_en_view(self, tmp_path):
        p = _write_slide(tmp_path, "slides_test.py", BILINGUAL)
        runner = CliRunner()
        result = runner.invoke(language_view_cmd, [str(p), "en"])
        assert result.exit_code == 0
        assert "Introduction" in result.output
        assert "Einführung" not in result.output

    def test_invalid_language(self, tmp_path):
        p = _write_slide(tmp_path, "slides_test.py", BILINGUAL)
        runner = CliRunner()
        result = runner.invoke(language_view_cmd, [str(p), "fr"])
        assert result.exit_code != 0

    def test_nonexistent_file(self):
        runner = CliRunner()
        result = runner.invoke(language_view_cmd, ["/no/such/file.py", "de"])
        assert result.exit_code != 0

    def test_include_voiceover_flag(self, tmp_path):
        content = """\
        # %% [markdown] lang="de" tags=["slide"]
        # ## Thema

        # %% [markdown] lang="de" tags=["voiceover"]
        # VO text.
        """
        p = _write_slide(tmp_path, "slides_vo.py", content)
        runner = CliRunner()

        # Without flag
        result = runner.invoke(language_view_cmd, [str(p), "de"])
        assert "VO text" not in result.output

        # With flag
        result = runner.invoke(language_view_cmd, [str(p), "de", "--include-voiceover"])
        assert "VO text" in result.output

    def test_include_notes_flag(self, tmp_path):
        content = """\
        # %% [markdown] lang="en" tags=["slide"]
        # ## Topic

        # %% [markdown] lang="en" tags=["notes"]
        # Speaker notes.
        """
        p = _write_slide(tmp_path, "slides_notes.py", content)
        runner = CliRunner()

        result = runner.invoke(language_view_cmd, [str(p), "en", "--include-notes"])
        assert "Speaker notes" in result.output

    def test_line_annotations_in_output(self, tmp_path):
        p = _write_slide(tmp_path, "slides_test.py", BILINGUAL)
        runner = CliRunner()
        result = runner.invoke(language_view_cmd, [str(p), "de"])
        assert "# [original line" in result.output
