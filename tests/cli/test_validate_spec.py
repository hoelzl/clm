"""Tests for the ``clm validate-spec`` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli


def _write_spec(tmp_path: Path, sections_xml: str) -> Path:
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>Test</de><en>Test</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          {sections_xml}
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


def _make_topic(tmp_path: Path, module: str, topic: str) -> None:
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hello\n")


class TestValidateSpecCommand:
    def test_clean_spec(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-spec", str(spec_file), "--data-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "OK" in result.output
        assert "1 topics" in result.output

    def test_error_exits_nonzero(self, tmp_path):
        (tmp_path / "slides").mkdir(parents=True)
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>nonexistent</topic></topics>
            </section></sections>""",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-spec", str(spec_file), "--data-dir", str(tmp_path)])

        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_json_output(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["validate-spec", str(spec_file), "--data-dir", str(tmp_path), "--json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["topics_total"] == 1
        assert data["findings"] == []

    def test_json_output_with_errors(self, tmp_path):
        (tmp_path / "slides").mkdir(parents=True)
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>missing</topic></topics>
            </section></sections>""",
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["validate-spec", str(spec_file), "--data-dir", str(tmp_path), "--json"],
        )

        assert result.exit_code == 0  # JSON mode doesn't set exit code
        data = json.loads(result.output)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["type"] == "unresolved_topic"

    def test_inferred_data_dir(self, tmp_path):
        _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        runner = CliRunner()
        # Without --data-dir, should infer from spec file location:
        # spec is at tmp_path/course-specs/test.xml → slides at tmp_path/slides
        result = runner.invoke(cli, ["validate-spec", str(spec_file)])

        assert result.exit_code == 0
        assert "OK" in result.output
