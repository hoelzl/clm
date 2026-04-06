"""Tests for the ``clm validate-slides`` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli


def _write_slide(tmp_path: Path, name: str, content: str) -> Path:
    """Write a slide file and return its path."""
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


class TestValidateSlidesCommand:
    def test_clean_file(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title

            # %% tags=["keep"]
            x = 1
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p)])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_errors_exit_nonzero(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p)])

        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_json_output(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p), "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["files_checked"] == 1
        assert data["findings"] == []

    def test_json_output_with_errors(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_bad.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p), "--json"])

        assert result.exit_code == 0  # JSON mode doesn't set exit code
        data = json.loads(result.output)
        assert len(data["findings"]) == 1
        assert data["findings"][0]["category"] == "tags"

    def test_quick_mode(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel

            # %% [markdown] lang="en" tags=["slide"]
            # ## Title
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p), "--quick"])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_quick_catches_unclosed_start(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_unclosed.py",
            """\
            # %% tags=["start"]
            # starter code
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p), "--quick"])

        assert result.exit_code == 1
        assert "start" in result.output

    def test_checks_filter(self, tmp_path):
        # File with tag error but no format error
        p = _write_slide(
            tmp_path,
            "slides_tags.py",
            """\
            # %% tags=["bogus_tag"]
            x = 1
            """,
        )

        runner = CliRunner()
        # Only run format checks — should not catch tag issue
        result = runner.invoke(cli, ["validate-slides", str(p), "--checks", "format"])

        assert result.exit_code == 0

    def test_directory_validation(self, tmp_path):
        topic_dir = tmp_path / "topic_010_intro"
        topic_dir.mkdir()
        (topic_dir / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n',
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(topic_dir)])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_course_spec_validation(self, tmp_path):
        # Set up a minimal course tree
        slides = tmp_path / "slides"
        m1 = slides / "module_100_basics"
        t1 = m1 / "topic_010_intro"
        t1.mkdir(parents=True)
        (t1 / "slides_intro.py").write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Titel\n'
            '# %% [markdown] lang="en" tags=["slide"]\n# ## Title\n',
            encoding="utf-8",
        )

        specs = tmp_path / "course-specs"
        specs.mkdir()
        spec_path = specs / "test.xml"
        spec_path.write_text(
            dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <course>
                <name><de>Test</de><en>Test</en></name>
                <prog-lang>python</prog-lang>
                <description><de></de><en></en></description>
                <certificate><de></de><en></en></certificate>
                <sections><section>
                    <name><de>S</de><en>S</en></name>
                    <topics><topic>intro</topic></topics>
                </section></sections>
            </course>
            """),
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(spec_path)])

        assert result.exit_code == 0
        assert "OK" in result.output

    def test_invalid_check_name(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_ok.py",
            """\
            # %% [markdown] lang="de" tags=["slide"]
            # ## Titel
            """,
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["validate-slides", str(p), "--checks", "nonexistent"])

        assert result.exit_code != 0
        assert "Unknown check" in result.output

    def test_review_material_in_json(self, tmp_path):
        p = _write_slide(
            tmp_path,
            "slides_print.py",
            """\
            # %% tags=["keep"]
            print(42)
            """,
        )

        runner = CliRunner()
        result = runner.invoke(
            cli, ["validate-slides", str(p), "--checks", "code_quality", "--json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "review_material" in data
        assert "code_quality" in data["review_material"]
