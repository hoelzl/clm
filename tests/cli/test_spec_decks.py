"""Tests for ``clm spec decks`` and ``clm slides referenced-by``."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import CliRunner

from clm.cli.main import cli


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


def _topic(tmp_path: Path, module: str, topic: str, *decks: str) -> Path:
    topic_dir = tmp_path / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    for deck in decks:
        (topic_dir / deck).write_text("# %% [markdown]\n# Hello\n", encoding="utf-8")
    return topic_dir


def _section(name: str, *topics: str, module: str | None = None) -> str:
    topic_xml = "".join(f"<topic>{t}</topic>" for t in topics)
    module_attr = f' module="{module}"' if module else ""
    return (
        f"<section{module_attr}><name><de>{name}</de><en>{name}</en></name>"
        f"<topics>{topic_xml}</topics></section>"
    )


def _json_from_output(output: str) -> dict:
    start = output.index("{")
    end = output.rindex("}") + 1
    return json.loads(output[start:end])


class TestSpecDecksCommand:
    def test_lists_all_decks_in_topic_dir(self, tmp_path):
        _topic(
            tmp_path,
            "module_100_basics",
            "topic_010_props",
            "slides_properties.py",
            "slides_property_setters.py",
        )
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'props')}</sections>")

        result = CliRunner().invoke(
            cli, ["course", "decks", str(spec_file), "--data-dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "slides_properties.py" in result.output
        assert "slides_property_setters.py" in result.output

    def test_json_output(self, tmp_path):
        _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli, ["course", "decks", str(spec_file), "--data-dir", str(tmp_path), "--json"]
        )

        assert result.exit_code == 0
        data = _json_from_output(result.output)
        assert data["deck_count"] == 1
        assert data["lang"] == "both"
        assert any("slides_intro.py" in d for d in data["decks"])

    def test_json_shape_is_flat_not_grouped_by_section(self, tmp_path):
        """The JSON is a flat ``topics[]`` (each with a ``section`` string
        field), NOT a ``sections[]`` grouping. This contract is documented in
        the ``--json`` help and ``clm info commands``; an agent that filters
        against an assumed nested schema gets silent no-output (issue #516)."""
        _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli, ["course", "decks", str(spec_file), "--data-dir", str(tmp_path), "--json"]
        )

        assert result.exit_code == 0
        data = _json_from_output(result.output)
        # Exactly the documented top-level keys — no ``sections`` key.
        assert set(data) == {
            "spec",
            "slides_dir",
            "lang",
            "deck_count",
            "decks",
            "topics",
            "unresolved",
        }
        assert "sections" not in data
        # ``topics`` is a flat list; ``section`` is a plain string field on each.
        assert isinstance(data["topics"], list)
        assert data["topics"], "expected at least one resolved topic"
        for topic in data["topics"]:
            assert isinstance(topic["section"], str)
            assert isinstance(topic["slide_files"], list)

    def test_lang_filter_excludes_other_split_half(self, tmp_path):
        _topic(
            tmp_path,
            "module_100_basics",
            "topic_010_intro",
            "slides_intro.de.py",
            "slides_intro.en.py",
            "slides_bilingual.py",
        )
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>")

        result = CliRunner().invoke(
            cli,
            ["course", "decks", str(spec_file), "--data-dir", str(tmp_path), "--lang", "de"],
        )

        assert result.exit_code == 0
        assert "slides_intro.de.py" in result.output
        assert "slides_intro.en.py" not in result.output
        # Bilingual decks serve both languages, so they survive a --lang filter.
        assert "slides_bilingual.py" in result.output

    def test_unresolved_topic_warns(self, tmp_path):
        _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S', 'intro', 'ghost')}</sections>")

        result = CliRunner().invoke(
            cli, ["course", "decks", str(spec_file), "--data-dir", str(tmp_path)]
        )

        assert result.exit_code == 0
        # stderr is captured separately on Click 8.1 and merged on 8.2+; check both.
        combined = result.output + (result.stderr if result.stderr_bytes else "")
        assert "ghost" in combined
        assert "slides_intro.py" in result.output

    def test_all_specs_annotates_each_deck(self, tmp_path):
        _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        _topic(tmp_path, "module_100_basics", "topic_020_extra", "slides_extra.py")
        _write_spec(tmp_path, f"<sections>{_section('S', 'intro')}</sections>", name="a.xml")
        _write_spec(
            tmp_path,
            f"<sections>{_section('S', 'intro', 'extra')}</sections>",
            name="b.xml",
        )

        result = CliRunner().invoke(
            cli,
            [
                "course",
                "decks",
                "--all-specs",
                str(tmp_path / "course-specs"),
                "--data-dir",
                str(tmp_path),
                "--json",
            ],
        )

        assert result.exit_code == 0
        data = _json_from_output(result.output)
        by_name = {Path(d["path"]).name: d["specs"] for d in data["decks"]}
        assert by_name["slides_intro.py"] == ["a.xml", "b.xml"]
        assert by_name["slides_extra.py"] == ["b.xml"]

    def test_requires_spec_or_all_specs(self, tmp_path):
        result = CliRunner().invoke(cli, ["course", "decks"])
        assert result.exit_code != 0
        assert "SPEC_FILE or --all-specs" in result.output

    def test_rejects_both_spec_and_all_specs(self, tmp_path):
        spec_file = _write_spec(tmp_path, f"<sections>{_section('S')}</sections>")
        result = CliRunner().invoke(
            cli,
            ["course", "decks", str(spec_file), "--all-specs", str(tmp_path / "course-specs")],
        )
        assert result.exit_code != 0
        assert "not both" in result.output


class TestReferencedByCommand:
    def test_finds_referencing_spec(self, tmp_path):
        topic_dir = _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        deck = topic_dir / "slides_intro.py"
        _write_spec(tmp_path, f"<sections>{_section('Intro', 'intro')}</sections>")

        result = CliRunner().invoke(cli, ["slides", "referenced-by", str(deck)])

        assert result.exit_code == 0
        assert "test.xml" in result.output
        assert "intro" in result.output

    def test_unreferenced_deck(self, tmp_path):
        topic_dir = _topic(tmp_path, "module_100_basics", "topic_010_orphan", "slides_orphan.py")
        deck = topic_dir / "slides_orphan.py"
        _write_spec(tmp_path, f"<sections>{_section('S')}</sections>")

        result = CliRunner().invoke(cli, ["slides", "referenced-by", str(deck)])

        assert result.exit_code == 0
        assert "unreferenced" in result.output

    def test_json_output(self, tmp_path):
        topic_dir = _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        deck = topic_dir / "slides_intro.py"
        _write_spec(tmp_path, f"<sections>{_section('Intro', 'intro')}</sections>")

        result = CliRunner().invoke(cli, ["slides", "referenced-by", str(deck), "--json"])

        assert result.exit_code == 0
        data = _json_from_output(result.output)
        assert data["referenced"] is True
        assert data["references"][0]["topic_id"] == "intro"

    def test_missing_specs_dir_errors(self, tmp_path):
        topic_dir = _topic(tmp_path, "module_100_basics", "topic_010_intro", "slides_intro.py")
        deck = topic_dir / "slides_intro.py"
        # No course-specs/ directory created.

        result = CliRunner().invoke(cli, ["slides", "referenced-by", str(deck)])

        assert result.exit_code != 0
        assert "Specs directory not found" in result.output
