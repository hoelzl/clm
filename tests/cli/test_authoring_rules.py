"""Tests for the authoring-rules CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.authoring_rules import authoring_rules_cmd


@pytest.fixture()
def data_dir(tmp_path):
    """Minimal data directory with specs, slides, and authoring rules."""
    specs = tmp_path / "course-specs"
    specs.mkdir()

    (specs / "_common.authoring.md").write_text(
        "## Common\n\n- Rule A.\n",
        encoding="utf-8",
    )
    (specs / "my-course.authoring.md").write_text(
        "## My Course\n\n- Rule B.\n",
        encoding="utf-8",
    )
    (specs / "my-course.xml").write_text(
        """\
<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>K</de><en>C</en></name>
    <prog-lang>python</prog-lang>
    <description><de></de><en></en></description>
    <certificate><de></de><en></en></certificate>
    <sections>
        <section>
            <name><de>S</de><en>S</en></name>
            <topics><topic>hello</topic></topics>
        </section>
    </sections>
</course>
""",
        encoding="utf-8",
    )

    slides = tmp_path / "slides"
    t = slides / "module_100_basics" / "topic_010_hello"
    t.mkdir(parents=True)
    (t / "slides_hello.py").write_text("# %% [markdown]\n# ## Hello\n", encoding="utf-8")

    return tmp_path


class TestAuthoringRulesCli:
    def test_by_slug(self, data_dir):
        runner = CliRunner()
        result = runner.invoke(
            authoring_rules_cmd,
            ["--course-spec", "my-course", "--data-dir", str(data_dir)],
        )
        assert result.exit_code == 0
        assert "Common" in result.output
        assert "My Course" in result.output

    def test_json_output(self, data_dir):
        runner = CliRunner()
        result = runner.invoke(
            authoring_rules_cmd,
            ["--course-spec", "my-course", "--data-dir", str(data_dir), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["has_common_rules"] is True
        assert len(data["course_rules"]) == 1

    def test_by_slide_path(self, data_dir):
        slide = data_dir / "slides" / "module_100_basics" / "topic_010_hello" / "slides_hello.py"
        runner = CliRunner()
        result = runner.invoke(
            authoring_rules_cmd,
            ["--slide-path", str(slide), "--data-dir", str(data_dir)],
        )
        assert result.exit_code == 0
        assert "My Course" in result.output

    def test_no_arguments_error(self, data_dir):
        runner = CliRunner()
        result = runner.invoke(
            authoring_rules_cmd,
            ["--data-dir", str(data_dir)],
        )
        assert result.exit_code != 0
        assert "At least one" in result.output

    def test_missing_authoring_file_note(self, data_dir):
        runner = CliRunner()
        result = runner.invoke(
            authoring_rules_cmd,
            ["--course-spec", "nonexistent", "--data-dir", str(data_dir)],
        )
        assert result.exit_code == 0
        assert "NOTE:" in result.output

    def test_json_with_notes(self, data_dir):
        runner = CliRunner()
        result = runner.invoke(
            authoring_rules_cmd,
            ["--course-spec", "nonexistent", "--data-dir", str(data_dir), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "notes" in data
