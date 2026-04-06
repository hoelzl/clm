"""Tests for the clm resolve-topic CLI command."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from clm.cli.main import cli


@pytest.fixture()
def course_tree(tmp_path):
    """Create a minimal course tree with slides/ directory."""
    slides = tmp_path / "slides"

    m1 = slides / "module_100_basics"
    t1 = m1 / "topic_010_intro"
    t1.mkdir(parents=True)
    (t1 / "slides_intro.py").write_text("# intro", encoding="utf-8")

    t2 = m1 / "topic_020_variables"
    t2.mkdir(parents=True)
    (t2 / "slides_variables.py").write_text("# vars", encoding="utf-8")

    return tmp_path


class TestResolveTopicCommand:
    def test_basic_resolve(self, course_tree):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["resolve-topic", "intro", "--data-dir", str(course_tree)],
        )
        assert result.exit_code == 0
        assert "topic_010_intro" in result.output

    def test_not_found(self, course_tree):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["resolve-topic", "nonexistent", "--data-dir", str(course_tree)],
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_json_output(self, course_tree):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["resolve-topic", "intro", "--data-dir", str(course_tree), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["topic_id"] == "intro"
        assert data["path"] is not None
        assert "slides_intro.py" in str(data["slide_files"])

    def test_glob_match(self, course_tree):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["resolve-topic", "*", "--data-dir", str(course_tree)],
        )
        assert result.exit_code == 0
        assert "intro" in result.output
        assert "variables" in result.output

    def test_glob_no_match(self, course_tree):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["resolve-topic", "zzz*", "--data-dir", str(course_tree)],
        )
        assert result.exit_code != 0
