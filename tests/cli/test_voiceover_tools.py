"""CLI tests for extract-voiceover and inline-voiceover commands."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.voiceover_tools import extract_voiceover_cmd, inline_voiceover_cmd

SLIDE_WITH_VOICEOVER = """\
# j2 from 'macros.j2' import header
# {{ header("Test", "Test") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Thema Eins

# %% [markdown] lang="de" tags=["voiceover"]
# Voiceover auf Deutsch.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic One

# %% [markdown] lang="en" tags=["voiceover"]
# Voiceover in English.
"""


def test_extract_voiceover(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(extract_voiceover_cmd, [str(slide_file)])

    assert result.exit_code == 0
    assert "2 voiceover cell(s) extracted" in result.output


def test_extract_voiceover_dry_run(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(extract_voiceover_cmd, [str(slide_file), "--dry-run"])

    assert result.exit_code == 0
    assert "[DRY RUN]" in result.output
    assert not (tmp_path / "voiceover_intro.py").exists()


def test_extract_voiceover_json(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(extract_voiceover_cmd, [str(slide_file), "--json"])

    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["cells_extracted"] == 2
    assert "companion_file" in data


def test_extract_no_voiceover(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# ## Test\n', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(extract_voiceover_cmd, [str(slide_file)])

    assert result.exit_code == 0
    assert "No voiceover cells found" in result.output


def test_inline_voiceover(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

    # Extract first
    runner = CliRunner()
    runner.invoke(extract_voiceover_cmd, [str(slide_file)])

    # Then inline
    result = runner.invoke(inline_voiceover_cmd, [str(slide_file)])

    assert result.exit_code == 0
    assert "2 voiceover cell(s) inlined" in result.output


def test_inline_no_companion(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text('# %% [markdown] lang="de" tags=["slide"]\n# ## Test\n', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(inline_voiceover_cmd, [str(slide_file)])

    assert result.exit_code == 0
    assert "No companion file found" in result.output


def test_inline_voiceover_json(tmp_path: Path):
    slide_file = tmp_path / "slides_intro.py"
    slide_file.write_text(SLIDE_WITH_VOICEOVER, encoding="utf-8")

    runner = CliRunner()
    runner.invoke(extract_voiceover_cmd, [str(slide_file)])

    result = runner.invoke(inline_voiceover_cmd, [str(slide_file), "--json"])

    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert data["cells_inlined"] == 2
    assert data["companion_deleted"] is True
