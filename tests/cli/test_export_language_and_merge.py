"""Per-language filtering and ``--include-disabled=merge`` for ``clm export``.

Two behaviours that the rest of the export tests do not cover because the
shared ``tests/test-data`` slides are not split-language decks:

1. **Bilingual split fix.** A split topic ships ``slides_x.de.py`` +
   ``slides_x.en.py``; under ``-L de`` only the ``.de`` half must appear. This
   regressed in every "resolved course" enumeration that skipped
   ``output_language_filter`` (outline flat sections + JSON slides, summary) and
   in the filesystem read for disabled topics (``disabled_topic_files``). The
   fixtures here build real split decks on disk so the filter is exercised
   end-to-end.

2. **``--include-disabled=merge``.** Bare ``--include-disabled`` keeps the
   legacy "marked" behaviour (a ``(disabled)`` marker; disabled whole sections
   appended after the enabled ones for outline/summary). ``=merge`` folds
   disabled content into the normal declared order with no marker.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import CliRunner

from clm.cli.commands._export_shared import resolve_disabled_mode
from clm.cli.main import cli


# ---------------------------------------------------------------------------
# On-disk course builders
# ---------------------------------------------------------------------------
def _split_topic(slides_root: Path, module: str, num: int, topic_id: str, de: str, en: str) -> None:
    """Write a split ``.de.py`` / ``.en.py`` deck pair for *topic_id*."""
    topic_dir = slides_root / module / f"topic_{num}_{topic_id}"
    topic_dir.mkdir(parents=True, exist_ok=True)
    # A markdown cell gives `clm export summary` (client audience) extractable
    # content so the deck shows up in the dry-run listing.
    (topic_dir / f"slides_{topic_id}.de.py").write_text(
        f"# j2 from 'macros.j2' import header_de\n# {{{{ header_de(\"{de}\") }}}}\n\n"
        "# %% [markdown]\n# Intro.\n\n# %%\nprint(1)\n",
        encoding="utf-8",
    )
    (topic_dir / f"slides_{topic_id}.en.py").write_text(
        f"# j2 from 'macros.j2' import header_en\n# {{{{ header_en(\"{en}\") }}}}\n\n"
        "# %% [markdown]\n# Intro.\n\n# %%\nprint(1)\n",
        encoding="utf-8",
    )


def _bilingual_topic(
    slides_root: Path, module: str, num: int, topic_id: str, de: str, en: str
) -> None:
    """Write a single bilingual ``slides_x.py`` deck (no language suffix)."""
    topic_dir = slides_root / module / f"topic_{num}_{topic_id}"
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / f"slides_{topic_id}.py").write_text(
        f'# j2 from \'macros.j2\' import header\n# {{{{ header("{de}", "{en}") }}}}\n\n'
        "# %% [markdown]\n# Intro.\n\n# %%\nprint(1)\n",
        encoding="utf-8",
    )


def _write_spec(tmp_path: Path, body: str) -> Path:
    spec_file = tmp_path / "course-specs" / "spec.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(
            f"""\
            <course>
              <name><de>Kurs</de><en>Course</en></name>
              <prog-lang>python</prog-lang>
              <description><de>.</de><en>.</en></description>
              <certificate><de>.</de><en>.</en></certificate>
              <sections>
            {body}
              </sections>
            </course>
            """
        ),
        encoding="utf-8",
    )
    return spec_file


@pytest.fixture
def flat_split_course(tmp_path: Path) -> Path:
    """Three flat (no-subsection) sections; the middle one disabled.

    Declared order Alpha, Bravo (disabled), Charlie lets us distinguish marked
    (disabled appended last) from merge (declared order). Alpha mixes a split
    topic (``foo``) with a bilingual one (``qux``) to prove bilingual decks
    survive the language filter.
    """
    slides = tmp_path / "slides"
    _split_topic(slides, "module_010_a", 100, "foo", "Foo DE", "Foo EN")
    _bilingual_topic(slides, "module_010_a", 110, "qux", "Qux DE", "Qux EN")
    _split_topic(slides, "module_020_b", 100, "bar", "Bar DE", "Bar EN")
    _split_topic(slides, "module_030_c", 100, "baz", "Baz DE", "Baz EN")
    return _write_spec(
        tmp_path,
        """\
        <section id="alpha"><name><de>Alpha</de><en>Alpha</en></name>
          <topics><topic>foo</topic><topic>qux</topic></topics>
        </section>
        <section id="bravo" enabled="false"><name><de>Bravo</de><en>Bravo</en></name>
          <topics><topic>bar</topic></topics>
        </section>
        <section id="charlie"><name><de>Charlie</de><en>Charlie</en></name>
          <topics><topic>baz</topic></topics>
        </section>""",
    )


@pytest.fixture
def subsection_split_course(tmp_path: Path) -> Path:
    """One week with an enabled and a disabled weekday subsection of split decks."""
    slides = tmp_path / "slides"
    _split_topic(slides, "module_010_a", 100, "foo", "Foo DE", "Foo EN")
    _split_topic(slides, "module_010_a", 110, "bar", "Bar DE", "Bar EN")
    return _write_spec(
        tmp_path,
        """\
        <section><name><de>Woche 1</de><en>Week 1</en></name>
          <topics>
            <subsection weekday="mon"><topic>foo</topic></subsection>
            <subsection weekday="tue" enabled="false"><topic>bar</topic></subsection>
          </topics>
        </section>""",
    )


def _run(*args: str) -> object:
    return CliRunner().invoke(cli, list(args))


# ---------------------------------------------------------------------------
# resolve_disabled_mode unit
# ---------------------------------------------------------------------------
class TestResolveDisabledMode:
    def test_none_excludes(self):
        assert resolve_disabled_mode(None) == (False, False)

    def test_marked(self):
        assert resolve_disabled_mode("marked") == (True, False)

    def test_merge(self):
        assert resolve_disabled_mode("merge") == (True, True)


# ---------------------------------------------------------------------------
# Bilingual split fix — outline
# ---------------------------------------------------------------------------
class TestOutlineSplitLanguage:
    def test_markdown_de_shows_only_de_half(self, flat_split_course):
        result = _run("export", "outline", str(flat_split_course), "-L", "de")
        assert result.exit_code == 0, result.output
        assert "- Foo DE" in result.output
        assert "- Baz DE" in result.output
        assert "- Qux DE" in result.output  # bilingual deck survives
        # The English split companions must NOT leak in.
        assert "Foo EN" not in result.output
        assert "Baz EN" not in result.output
        assert "Qux EN" not in result.output
        # Exactly one bullet per split deck.
        assert result.output.count("- Foo DE") == 1

    def test_markdown_en_shows_only_en_half(self, flat_split_course):
        result = _run("export", "outline", str(flat_split_course), "-L", "en")
        assert result.exit_code == 0, result.output
        assert "- Foo EN" in result.output
        assert "- Baz EN" in result.output
        assert "- Qux EN" in result.output
        assert "Foo DE" not in result.output
        assert "Baz DE" not in result.output

    def test_json_de_one_slide_per_split_topic(self, flat_split_course):
        result = _run("export", "outline", str(flat_split_course), "-L", "de", "-f", "json")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        alpha = next(s for s in data["sections"] if s["name"] == "Alpha")
        foo = next(t for t in alpha["topics"] if t["topic_id"] == "foo")
        titles = [s["title"] for s in foo["slides"]]
        assert titles == ["Foo DE"]


# ---------------------------------------------------------------------------
# Bilingual split fix — summary
# ---------------------------------------------------------------------------
class TestSummarySplitLanguage:
    def test_dry_run_de_no_english_companion(self, flat_split_course):
        result = _run(
            "export",
            "summary",
            str(flat_split_course),
            "--audience",
            "client",
            "--dry-run",
            "-L",
            "de",
        )
        assert result.exit_code == 0, result.output
        assert "Foo DE" in result.output
        assert "Baz DE" in result.output
        assert "Foo EN" not in result.output
        assert "Baz EN" not in result.output


# ---------------------------------------------------------------------------
# Bilingual split fix — schedule (enabled + disabled subsections, Family B)
# ---------------------------------------------------------------------------
class TestScheduleSplitLanguage:
    def test_enabled_subsection_de_single_language(self, subsection_split_course):
        result = _run("export", "schedule", str(subsection_split_course), "-L", "de")
        assert result.exit_code == 0, result.output
        assert "Foo DE" in result.output
        assert "Foo EN" not in result.output

    def test_disabled_subsection_de_single_language(self, subsection_split_course):
        result = _run(
            "export", "schedule", str(subsection_split_course), "-L", "de", "--include-disabled"
        )
        assert result.exit_code == 0, result.output
        # Disabled day surfaced from disk, single language, label tagged.
        assert "Bar DE" in result.output
        assert "Bar EN" not in result.output
        assert "(disabled)" in result.output

    def test_merge_drops_disabled_tag(self, subsection_split_course):
        result = _run(
            "export",
            "schedule",
            str(subsection_split_course),
            "-L",
            "de",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        assert "Bar DE" in result.output
        assert "(disabled)" not in result.output


# ---------------------------------------------------------------------------
# --include-disabled value modes — outline markdown
# ---------------------------------------------------------------------------
class TestOutlineDisabledModes:
    def test_default_excludes_disabled(self, flat_split_course):
        result = _run("export", "outline", str(flat_split_course), "-L", "en")
        assert result.exit_code == 0, result.output
        assert "Bravo" not in result.output

    def test_bare_flag_is_marked_and_appended(self, flat_split_course):
        """Legacy bare --include-disabled: marker + disabled section last."""
        result = _run("export", "outline", str(flat_split_course), "-L", "en", "--include-disabled")
        assert result.exit_code == 0, result.output
        assert "## Bravo (disabled)" in result.output
        # Bravo (disabled) is appended after the enabled Charlie.
        assert result.output.index("## Charlie") < result.output.index("## Bravo (disabled)")
        # Disabled topic read from disk, single language.
        assert "- Bar EN (disabled)" in result.output
        assert "Bar DE" not in result.output

    def test_marked_explicit_value(self, flat_split_course):
        result = _run(
            "export", "outline", str(flat_split_course), "-L", "en", "--include-disabled=marked"
        )
        assert result.exit_code == 0, result.output
        assert "## Bravo (disabled)" in result.output

    def test_merge_interleaves_in_declared_order_without_marker(self, flat_split_course):
        result = _run(
            "export", "outline", str(flat_split_course), "-L", "en", "--include-disabled=merge"
        )
        assert result.exit_code == 0, result.output
        assert "(disabled)" not in result.output
        assert "## Bravo" in result.output
        # Declared order Alpha, Bravo, Charlie.
        assert (
            result.output.index("## Alpha")
            < result.output.index("## Bravo")
            < result.output.index("## Charlie")
        )

    def test_merge_disabled_topic_single_language(self, flat_split_course):
        result = _run(
            "export", "outline", str(flat_split_course), "-L", "de", "--include-disabled=merge"
        )
        assert result.exit_code == 0, result.output
        assert "- Bar DE" in result.output
        assert "Bar EN" not in result.output

    def test_bogus_value_rejected(self, flat_split_course):
        result = _run("export", "outline", str(flat_split_course), "--include-disabled=bogus")
        assert result.exit_code != 0
        assert "not one of" in result.output.lower()


# ---------------------------------------------------------------------------
# --include-disabled value modes — outline JSON
# ---------------------------------------------------------------------------
class TestOutlineJsonDisabledModes:
    def test_marked_appends_disabled_entry(self, flat_split_course):
        result = _run(
            "export",
            "outline",
            str(flat_split_course),
            "-L",
            "en",
            "-f",
            "json",
            "--include-disabled",
        )
        assert result.exit_code == 0, result.output
        names = [s["name"] for s in json.loads(result.output)["sections"]]
        assert names == ["Alpha", "Charlie", "Bravo"]

    def test_merge_interleaves_keeps_disabled_flag(self, flat_split_course):
        result = _run(
            "export",
            "outline",
            str(flat_split_course),
            "-L",
            "en",
            "-f",
            "json",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        sections = json.loads(result.output)["sections"]
        assert [s["name"] for s in sections] == ["Alpha", "Bravo", "Charlie"]
        assert [s["number"] for s in sections] == [1, 2, 3]
        bravo = next(s for s in sections if s["name"] == "Bravo")
        # JSON keeps the disabled bit as metadata; merge changes only placement.
        assert bravo["disabled"] is True
        enabled = next(s for s in sections if s["name"] == "Alpha")
        assert enabled["disabled"] is False


# ---------------------------------------------------------------------------
# --include-disabled value modes — summary
# ---------------------------------------------------------------------------
class TestSummaryDisabledModes:
    def _headings(self, output: str) -> list[str]:
        return [line for line in output.splitlines() if line.startswith("## ")]

    def test_marked_appends_disabled_heading(self, flat_split_course):
        result = _run(
            "export",
            "summary",
            str(flat_split_course),
            "--audience",
            "client",
            "--dry-run",
            "-L",
            "en",
            "--include-disabled",
        )
        assert result.exit_code == 0, result.output
        assert "## Bravo (disabled)" in result.output
        assert self._headings(result.output) == ["## Alpha", "## Charlie", "## Bravo (disabled)"]

    def test_merge_interleaves_without_marker(self, flat_split_course):
        result = _run(
            "export",
            "summary",
            str(flat_split_course),
            "--audience",
            "client",
            "--dry-run",
            "-L",
            "en",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        assert "(disabled)" not in result.output
        assert self._headings(result.output) == ["## Alpha", "## Bravo", "## Charlie"]


# ---------------------------------------------------------------------------
# Merge robustness: duplicate, id-less section names
# ---------------------------------------------------------------------------
@pytest.fixture
def dup_name_course(tmp_path: Path) -> Path:
    """Two enabled sections share a name and have no id, with a disabled one
    between them. A name/id-keyed merge map would collapse the two onto one
    built section; the positional walk must keep each section's own content.
    """
    slides = tmp_path / "slides"
    _split_topic(slides, "module_010_a", 100, "foo", "Foo DE", "Foo EN")
    _split_topic(slides, "module_020_b", 100, "bar", "Bar DE", "Bar EN")
    _split_topic(slides, "module_030_c", 100, "baz", "Baz DE", "Baz EN")
    return _write_spec(
        tmp_path,
        """\
        <section><name><de>Dup</de><en>Dup</en></name>
          <topics><topic>foo</topic></topics>
        </section>
        <section enabled="false"><name><de>Mid</de><en>Mid</en></name>
          <topics><topic>bar</topic></topics>
        </section>
        <section><name><de>Dup</de><en>Dup</en></name>
          <topics><topic>baz</topic></topics>
        </section>""",
    )


class TestMergeDuplicateSectionNames:
    def test_merge_keeps_each_section_content(self, dup_name_course):
        result = _run(
            "export", "outline", str(dup_name_course), "-L", "en", "--include-disabled=merge"
        )
        assert result.exit_code == 0, result.output
        # Each enabled section renders its OWN topic exactly once (a keyed map
        # would have collapsed the two "Dup" sections onto the last one).
        assert result.output.count("- Foo EN") == 1
        assert result.output.count("- Baz EN") == 1
        assert "- Bar EN" in result.output  # disabled middle section, folded in
        assert "(disabled)" not in result.output

    def test_marked_keeps_each_section_content(self, dup_name_course):
        result = _run("export", "outline", str(dup_name_course), "-L", "en", "--include-disabled")
        assert result.exit_code == 0, result.output
        assert result.output.count("- Foo EN") == 1
        assert result.output.count("- Baz EN") == 1


# ---------------------------------------------------------------------------
# Cross-product edges flagged by review
# ---------------------------------------------------------------------------
class TestOutlineJsonMergeLanguage:
    def test_merge_de_disabled_section_slides_single_language(self, flat_split_course):
        result = _run(
            "export",
            "outline",
            str(flat_split_course),
            "-L",
            "de",
            "-f",
            "json",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        bravo = next(s for s in json.loads(result.output)["sections"] if s["name"] == "Bravo")
        titles = [s["title"] for t in bravo["topics"] for s in t["slides"]]
        assert titles == ["Bar DE"]


class TestOutlineDisabledSubsectionMerge:
    def test_markdown_disabled_subsection_unmarked_single_language(self, subsection_split_course):
        result = _run(
            "export",
            "outline",
            str(subsection_split_course),
            "-L",
            "de",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        assert "(disabled)" not in result.output
        assert "**Dienstag**" in result.output  # disabled tue subsection still rendered
        assert "- Bar DE" in result.output
        assert "Bar EN" not in result.output

    def test_markdown_disabled_subsection_marked_by_default(self, subsection_split_course):
        result = _run(
            "export", "outline", str(subsection_split_course), "-L", "de", "--include-disabled"
        )
        assert result.exit_code == 0, result.output
        assert "**Dienstag** (disabled)" in result.output

    def test_json_disabled_subsection_merge_keeps_enabled_flag(self, subsection_split_course):
        result = _run(
            "export",
            "outline",
            str(subsection_split_course),
            "-L",
            "de",
            "-f",
            "json",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        week = json.loads(result.output)["sections"][0]
        tue = next(ss for ss in week["subsections"] if ss["label"] == "Dienstag")
        assert tue["enabled"] is False  # structured flag stays truthful
        titles = [s["title"] for t in tue["topics"] for s in t["slides"]]
        assert titles == ["Bar DE"]


class TestScheduleCsvModes:
    def test_csv_de_split_single_row(self, subsection_split_course):
        result = _run("export", "schedule", str(subsection_split_course), "-L", "de", "-f", "csv")
        assert result.exit_code == 0, result.output
        assert "Foo DE" in result.output
        assert "Foo EN" not in result.output
        assert result.output.count("Foo DE") == 1

    def test_csv_merge_keeps_truthful_disabled_column(self, subsection_split_course):
        result = _run(
            "export",
            "schedule",
            str(subsection_split_course),
            "-L",
            "de",
            "-f",
            "csv",
            "--include-disabled=merge",
        )
        assert result.exit_code == 0, result.output
        # Structured CSV keeps the disabled column + truthful flag under merge;
        # only the human-readable Markdown tag is suppressed.
        assert result.output.splitlines()[0].endswith(",disabled")
        assert ",true" in result.output  # the disabled tue deck row
        assert "Bar DE" in result.output
        assert "Bar EN" not in result.output
