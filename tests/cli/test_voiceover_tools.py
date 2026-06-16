"""CLI tests for extract-voiceover and inline-voiceover commands."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.voiceover import extract_voiceover_cmd, inline_voiceover_cmd

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


# ---------------------------------------------------------------------------
# Paired extract (auto-pair on a split half) — §8 'F later'
# ---------------------------------------------------------------------------

_DE_HALF = (
    '# %% [markdown] lang="de" tags=["slide"]\n# ## Thema\n\n'
    '# %% [markdown] lang="de" tags=["voiceover"]\n# VO DE\n'
)
_EN_HALF = (
    '# %% [markdown] lang="en" tags=["slide"]\n# ## Topic\n\n'
    '# %% [markdown] lang="en" tags=["voiceover"]\n# VO EN\n'
)


def _write_split_pair(tmp_path: Path) -> tuple[Path, Path]:
    de = tmp_path / "slides_x.de.py"
    en = tmp_path / "slides_x.en.py"
    de.write_text(_DE_HALF, encoding="utf-8")
    en.write_text(_EN_HALF, encoding="utf-8")
    return de, en


class TestExtractPaired:
    def test_auto_pairs_on_split_half(self, tmp_path: Path):
        de, _en = _write_split_pair(tmp_path)
        result = CliRunner().invoke(extract_voiceover_cmd, [str(de), "--layout", "sibling"])
        assert result.exit_code == 0, result.output
        assert "paired extract" in result.output
        assert (tmp_path / "voiceover_x.de.py").exists()
        assert (tmp_path / "voiceover_x.en.py").exists()

    def test_single_opts_out(self, tmp_path: Path):
        de, _en = _write_split_pair(tmp_path)
        result = CliRunner().invoke(
            extract_voiceover_cmd, [str(de), "--single", "--layout", "sibling"]
        )
        assert result.exit_code == 0, result.output
        assert "paired extract" not in result.output
        assert (tmp_path / "voiceover_x.de.py").exists()
        assert not (tmp_path / "voiceover_x.en.py").exists()

    def test_both_and_single_mutually_exclusive(self, tmp_path: Path):
        de, _en = _write_split_pair(tmp_path)
        result = CliRunner().invoke(extract_voiceover_cmd, [str(de), "--both", "--single"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_both_without_twin_errors(self, tmp_path: Path):
        # A bilingual deck has no .de/.en twin; --both cannot pair.
        bilingual = tmp_path / "slides_x.py"
        bilingual.write_text(_DE_HALF + _EN_HALF, encoding="utf-8")
        result = CliRunner().invoke(extract_voiceover_cmd, [str(bilingual), "--both"])
        assert result.exit_code != 0
        assert "no" in result.output.lower() and "twin" in result.output.lower()

    def test_paired_json_shape(self, tmp_path: Path):
        import json

        de, _en = _write_split_pair(tmp_path)
        result = CliRunner().invoke(extract_voiceover_cmd, [str(de), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["paired"] is True
        assert len(data["companions"]) == 2
        assert all("cells_extracted" in c for c in data["companions"])

    def test_bilingual_stays_flat_json(self, tmp_path: Path):
        # Backward compat: a bilingual (twin-less) deck keeps the flat shape.
        import json

        bilingual = tmp_path / "slides_x.py"
        bilingual.write_text(_DE_HALF + _EN_HALF, encoding="utf-8")
        result = CliRunner().invoke(extract_voiceover_cmd, [str(bilingual), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "paired" not in data
        assert "cells_extracted" in data
