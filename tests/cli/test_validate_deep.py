"""Tests for ``clm validate`` deep / summary / shipping-only modes (gap #2)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli

CLEAN_DECK = dedent(
    """\
    # %% [markdown] lang="de" tags=["slide"] slide_id="title"
    #
    # ## Titel

    # %% [markdown] lang="en" tags=["slide"] slide_id="title"
    #
    # ## Title
    """
)

# Missing slide_id on the slide cells — a *content* error (since 1.8) that
# structure-only spec validation does not catch.
MISSING_ID_DECK = dedent(
    """\
    # %% [markdown] lang="de" tags=["slide"]
    # ## Titel

    # %% [markdown] lang="en" tags=["slide"]
    # ## Title
    """
)


def _write_spec(tmp_path: Path, sections_xml: str, name: str = "test.xml") -> Path:
    spec_file = tmp_path / "course-specs" / name
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


def _deck(tmp_path: Path, module: str, topic: str, name: str, content: str) -> Path:
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    p = topic_dir / name
    p.write_text(content, encoding="utf-8")
    return p


def _section(name: str, *topics: str) -> str:
    topic_xml = "".join(f"<topic>{t}</topic>" for t in topics)
    return f"<section><name><de>{name}</de><en>{name}</en></name><topics>{topic_xml}</topics></section>"


def _json(output: str) -> dict:
    return json.loads(output[output.index("{") : output.rindex("}") + 1])


class TestDeep:
    def test_structure_ok_but_deck_content_error(self, tmp_path):
        # Spec structure is fine (topic resolves); the deck has a content error.
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", MISSING_ID_DECK)
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        runner = CliRunner()
        shallow = runner.invoke(cli, ["validate", str(spec_file), "--data-dir", str(tmp_path)])
        deep = runner.invoke(
            cli, ["validate", str(spec_file), "--data-dir", str(tmp_path), "--deep"]
        )

        # Structure-only passes; deep catches the missing slide_id and fails.
        assert shallow.exit_code == 0
        assert deep.exit_code == 1
        assert "slide_id" in deep.output

    def test_deep_clean_passes(self, tmp_path):
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", CLEAN_DECK)
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli, ["validate", str(spec_file), "--data-dir", str(tmp_path), "--deep"]
        )

        assert result.exit_code == 0
        assert "Spec structure: OK" in result.output

    def test_deep_json_has_spec_and_slides(self, tmp_path):
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", MISSING_ID_DECK)
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli,
            ["validate", str(spec_file), "--data-dir", str(tmp_path), "--deep", "--json"],
        )

        data = _json(result.output)
        assert data["kind"] == "deep"
        assert "spec" in data and "slides" in data
        assert data["slides"]["findings"]

    def test_deep_on_slides_path_errors(self, tmp_path):
        p = _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", CLEAN_DECK)
        result = CliRunner().invoke(cli, ["validate", str(p), "--deep"])
        assert result.exit_code != 0
        assert "--deep applies to a spec" in result.output


class TestSummary:
    def test_summary_on_spec_implies_deep(self, tmp_path):
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", MISSING_ID_DECK)
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli, ["validate", str(spec_file), "--data-dir", str(tmp_path), "--summary"]
        )

        assert "By category:" in result.output
        assert "By kind:" in result.output
        assert result.exit_code == 1  # the missing-id error still fails

    def test_summary_json(self, tmp_path):
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", MISSING_ID_DECK)
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli,
            ["validate", str(spec_file), "--data-dir", str(tmp_path), "--summary", "--json"],
        )
        data = _json(result.output)
        assert "summary" in data
        assert data["summary"]["total"] >= 1


class TestShippingOnly:
    def test_skips_unreferenced_decks(self, tmp_path):
        # Referenced clean deck + an UNreferenced deck with a content error.
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", CLEAN_DECK)
        _deck(tmp_path, "module_100_basics", "topic_900_archive", "slides_old.py", MISSING_ID_DECK)
        _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        slides_dir = tmp_path / "slides"
        runner = CliRunner()
        full = runner.invoke(cli, ["validate", str(slides_dir)])
        shipped = runner.invoke(cli, ["validate", str(slides_dir), "--shipping-only"])

        # The whole-tree walk sees the archived deck's error; shipping-only skips it.
        assert full.exit_code == 1
        assert shipped.exit_code == 0
        assert "OK" in shipped.output

    def test_shipping_only_requires_directory(self, tmp_path):
        p = _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", CLEAN_DECK)
        _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")
        result = CliRunner().invoke(cli, ["validate", str(p), "--shipping-only"])
        assert result.exit_code != 0

    def test_shipping_only_explicit_specs_dir(self, tmp_path):
        _deck(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py", CLEAN_DECK)
        _deck(tmp_path, "module_100_basics", "topic_900_archive", "slides_old.py", MISSING_ID_DECK)
        specs = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>").parent

        result = CliRunner().invoke(
            cli,
            ["validate", str(tmp_path / "slides"), "--shipping-only", "--specs-dir", str(specs)],
        )
        assert result.exit_code == 0
