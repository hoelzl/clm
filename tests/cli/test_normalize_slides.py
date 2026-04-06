"""Tests for the normalize-slides CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.normalize_slides import normalize_slides_cmd


def _write_slide(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestNormalizeSlidesCmd:
    def test_no_changes_exit_0(self, tmp_path):
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% [markdown] tags=["slide"]\n# Title\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path)])
        assert result.exit_code == 0
        assert "No changes needed" in result.output

    def test_tag_migration(self, tmp_path):
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path)])
        assert result.exit_code == 0
        assert "tag_migration" in result.output

    def test_dry_run(self, tmp_path):
        text = '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert path.read_text(encoding="utf-8") == text

    def test_json_output(self, tmp_path):
        path = _write_slide(
            tmp_path / "slides_test.py",
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "applied"
        assert len(data["changes"]) == 1

    def test_review_items_exit_1(self, tmp_path):
        # Count mismatch produces review items → exit 1
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n# Folie 1\n\n'
            '# %% [markdown] lang="de" tags=["subslide"]\n# Extra\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# Slide 1\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(path)])
        # Has review items but no changes → exit 2
        assert result.exit_code == 2

    def test_operations_filter(self, tmp_path):
        text = (
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "# ## Workshop: Test\n"
            "\n"
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n'
        )
        path = _write_slide(tmp_path / "slides_test.py", text)
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd,
            [str(path), "--operations", "tag_migration", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        ops = {c["operation"] for c in data["changes"]}
        assert "tag_migration" in ops
        assert "workshop_tags" not in ops

    def test_invalid_operation(self, tmp_path):
        path = _write_slide(tmp_path / "slides_test.py", "# %%\nx = 1\n")
        runner = CliRunner()
        result = runner.invoke(
            normalize_slides_cmd,
            [str(path), "--operations", "bogus"],
        )
        assert result.exit_code != 0
        assert "Unknown operation" in result.output

    def test_directory_input(self, tmp_path):
        topic = tmp_path / "topic_010_test"
        topic.mkdir()
        _write_slide(
            topic / "slides_test.py",
            '# %% tags=["start"]\nx = 1\n\n# %% tags=["alt"]\nx = 2\n',
        )
        runner = CliRunner()
        result = runner.invoke(normalize_slides_cmd, [str(topic)])
        assert result.exit_code == 0
        assert "tag_migration" in result.output
