"""Tests for ``clm course gate``."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli

HEADING_PAIR = (
    '# %% [markdown] lang="de" tags=["slide"]\n# ## Einfuehrung\n\n'
    '# %% [markdown] lang="en" tags=["slide"]\n# ## Introduction\n'
)
NON_EXTRACTABLE = '# %% [markdown] lang="de" tags=["slide"]\n#\n'


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


def _deck(tmp_path: Path, topic: str, name: str, content: str) -> Path:
    d = tmp_path / "slides" / "module_100_x" / topic
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


def _section(name: str, *topics: str) -> str:
    t = "".join(f"<topic>{x}</topic>" for x in topics)
    return f"<section><name><de>{name}</de><en>{name}</en></name><topics>{t}</topics></section>"


class TestCourseGateCommand:
    def test_dry_run_mechanical_is_clean_exit0(self, tmp_path):
        _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)
        spec = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(cli, ["course", "gate", str(spec), "--data-dir", str(tmp_path)])

        assert result.exit_code == 0
        assert "MECHANICALLY CLEAN" in result.output
        assert "slide_ids: 2" in result.output

    def test_needs_author_exit1(self, tmp_path):
        _deck(tmp_path, "topic_010_intro", "slides_bad.py", NON_EXTRACTABLE)
        spec = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(cli, ["course", "gate", str(spec), "--data-dir", str(tmp_path)])

        assert result.exit_code == 1
        assert "NEEDS AUTHOR" in result.output
        assert "slide_id_hard_refusal" in result.output

    def test_apply_writes(self, tmp_path):
        p = _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)
        spec = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli, ["course", "gate", str(spec), "--data-dir", str(tmp_path), "--apply"]
        )

        assert result.exit_code == 0
        assert 'slide_id="' in p.read_text(encoding="utf-8")
        assert "Residual after apply" in result.output

    def test_dry_run_does_not_write(self, tmp_path):
        p = _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)
        spec = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")
        before = p.read_text(encoding="utf-8")

        CliRunner().invoke(cli, ["course", "gate", str(spec), "--data-dir", str(tmp_path)])

        assert p.read_text(encoding="utf-8") == before

    def test_json_output(self, tmp_path):
        _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)
        spec = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli, ["course", "gate", str(spec), "--data-dir", str(tmp_path), "--json"]
        )

        data = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
        assert data["deck_count"] == 1
        assert data["mechanical"]["by_operation"]["slide_ids"] == 2
        assert data["is_clean"] is True

    def test_directory_target(self, tmp_path):
        _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)

        result = CliRunner().invoke(cli, ["course", "gate", str(tmp_path / "slides")])

        assert result.exit_code == 0
        assert "Scope: 1 deck" in result.output

    def test_operations_filter(self, tmp_path):
        # Only tag_migration requested → the id-less pair is NOT minted.
        _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)
        spec = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli,
            [
                "course",
                "gate",
                str(spec),
                "--data-dir",
                str(tmp_path),
                "--operations",
                "tag_migration",
            ],
        )

        assert "slide_ids:" not in result.output

    def test_bad_operation_errors(self, tmp_path):
        _deck(tmp_path, "topic_010_intro", "slides_intro.py", HEADING_PAIR)
        result = CliRunner().invoke(
            cli, ["course", "gate", str(tmp_path / "slides"), "--operations", "nope"]
        )
        assert result.exit_code != 0
        assert "Unknown operation" in result.output
