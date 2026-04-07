"""Tests for the clm suggest-sync CLI command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.suggest_sync import suggest_sync_cmd


def _init_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    return tmp_path


def _commit_file(repo: Path, file_path: Path, content: str) -> None:
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", str(file_path)], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "test"], cwd=str(repo), capture_output=True, check=True)


BILINGUAL = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Methoden

# %% [markdown] lang="en" tags=["slide"]
# ## Methods

# %% tags=["keep"]
x = 1
"""

BILINGUAL_DE_CHANGED = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Neue Methoden

# %% [markdown] lang="en" tags=["slide"]
# ## Methods

# %% tags=["keep"]
x = 1
"""


class TestSuggestSyncCommand:
    def test_in_sync(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL)

        runner = CliRunner()
        result = runner.invoke(suggest_sync_cmd, [str(slide)])
        assert result.exit_code == 0
        assert "In sync" in result.output

    def test_de_modified(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL)
        slide.write_text(BILINGUAL_DE_CHANGED, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(suggest_sync_cmd, [str(slide)])
        assert result.exit_code == 0
        assert "modified" in result.output.lower()

    def test_source_language_option(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL)
        slide.write_text(BILINGUAL_DE_CHANGED, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(suggest_sync_cmd, [str(slide), "--source-language", "de"])
        assert result.exit_code == 0
        assert "DE" in result.output

    def test_json_output(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL)
        slide.write_text(BILINGUAL_DE_CHANGED, encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(suggest_sync_cmd, [str(slide), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sync_needed"] is True
        assert "suggestions" in data

    def test_nonexistent_file(self):
        runner = CliRunner()
        result = runner.invoke(suggest_sync_cmd, ["/no/such/file.py"])
        assert result.exit_code != 0

    def test_json_in_sync(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        slide = repo / "slides_test.py"
        _commit_file(repo, slide, BILINGUAL)

        runner = CliRunner()
        result = runner.invoke(suggest_sync_cmd, [str(slide), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sync_needed"] is False
        assert data["suggestions"] == []
