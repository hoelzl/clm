"""Tests for ``clm export agenda`` (issue #1163)."""

import csv
import io
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.export.agenda import render_agenda_csv, render_agenda_markdown
from clm.cli.main import cli
from clm.core.agenda import build_agenda
from clm.core.course_spec import CourseSpec

SPEC_PATH = Path("tests/test-data/course-specs/block-spec.xml")


def _spec() -> CourseSpec:
    return CourseSpec.from_file(SPEC_PATH)


class TestMarkdown:
    def test_german_overview_table(self):
        spec = _spec()
        out = render_agenda_markdown(spec, build_agenda(spec), "de")
        assert out.startswith("# Mein Kurs\n")
        assert "## Überblick" in out
        assert "| Woche | Thema | UE |" in out
        assert "| 1 | Woche 1 | 16 |" in out
        assert "| 2 | Woche 2 | 8 |" in out
        assert "| | **Gesamtumfang** | **24** |" in out
        assert "1 UE = 45 Minuten" in out

    def test_german_week_table(self):
        spec = _spec()
        out = render_agenda_markdown(spec, build_agenda(spec), "de")
        week1 = out.split("## Woche 1 (16 UE)", 1)[1].split("## Woche 2", 1)[0]
        assert "_Grundlagen_" in week1
        assert "| Tag | Thema | Inhalte | Form | UE |" in week1
        assert "| 1 | Einstieg | Werkzeuge, Ablauf | Theorie | 4 |" in week1
        assert "| 1 | Übungen *(geplant)* |  | Praxis | 4 |" in week1
        assert "| 2 | Recht *(anzupassen)* |  | Theorie/Praxis | 6 |" in week1
        assert "| 2 | Lernkontrolle |  | Theorie/Praxis | 2 |" in week1
        assert "| **Summe Woche 1** | | | | **16** |" in week1
        assert "**Ergebnis:** Erste Notebooks" in week1
        # Rows appear in document order.
        assert week1.index("Einstieg") < week1.index("Übungen") < week1.index("Recht")

    def test_english_labels(self):
        spec = _spec()
        out = render_agenda_markdown(spec, build_agenda(spec), "en")
        assert "## Overview" in out
        assert "| Week | Title | Units |" in out
        assert "| Day | Block | Contents | Form | Units |" in out
        assert "| 1 | Getting started | Tools, workflow | Theory | 4 |" in out
        assert "| 1 | Exercises *(planned)* |  | Practice | 4 |" in out
        assert "Theory/Practice" in out
        assert "**Outcome:** First notebooks" in out
        assert "1 unit = 45 minutes" in out
        assert "UE" not in out

    def test_week_without_agenda_has_no_subtitle_or_outcome(self):
        spec = _spec()
        out = render_agenda_markdown(spec, build_agenda(spec), "de")
        week2 = out.split("## Woche 2 (8 UE)", 1)[1]
        assert "Ergebnis" not in week2
        assert "| 1 | Projekt |  | Praxis | 8 |" in week2

    def test_pipe_in_text_is_escaped(self):
        xml = """
        <course><name>C</name><prog-lang>python</prog-lang><sections>
          <section><name>W</name><topics><subsection weekday="mon">
            <block units="8" form="theory"><name>A | B</name></block>
          </subsection></topics></section></sections></course>
        """
        spec = CourseSpec.from_file(io.StringIO(xml))
        out = render_agenda_markdown(spec, build_agenda(spec), "en")
        assert "A \\| B" in out


class TestCsv:
    def test_rows_carry_all_block_attributes(self):
        spec = _spec()
        rows = list(csv.DictReader(io.StringIO(render_agenda_csv(build_agenda(spec), "de"))))
        assert len(rows) == 5
        first = rows[0]
        assert first["week"] == "1"
        assert first["week_title"] == "Woche 1"
        assert first["day"] == "1"
        assert first["weekday"] == "mon"
        assert first["block"] == "Einstieg"
        assert first["contents"] == "Werkzeuge, Ablauf"
        assert first["form"] == "theory"
        assert first["units"] == "4"
        assert first["status"] == "ready"
        assert first["standalone"] == "false"
        assert first["pace"] == "full"
        assert first["topics"] == "some_topic_from_test_1"
        law = rows[2]
        assert (law["status"], law["pace"]) == ("adapt", "recap")
        assert rows[3]["standalone"] == "true"
        assert rows[3]["topics"] == ""


class TestCli:
    def test_markdown_default_german(self):
        result = CliRunner().invoke(cli, ["export", "agenda", str(SPEC_PATH)])
        assert result.exit_code == 0, result.output
        assert "| Tag | Thema | Inhalte | Form | UE |" in result.output

    def test_english_csv_to_file(self, tmp_path):
        out = tmp_path / "agenda.csv"
        result = CliRunner().invoke(
            cli, ["export", "agenda", str(SPEC_PATH), "-L", "en", "-f", "csv", "-o", str(out)]
        )
        assert result.exit_code == 0, result.output
        text = out.read_text(encoding="utf-8")
        assert text.startswith("week,week_title,day,weekday,block,")
        assert "Getting started" in text

    def test_output_dir(self, tmp_path):
        result = CliRunner().invoke(cli, ["export", "agenda", str(SPEC_PATH), "-d", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "Mein Kurs-agenda-de.md").exists()

    def test_spec_without_blocks_warns(self, tmp_path):
        spec = tmp_path / "c.xml"
        spec.write_text(
            "<course><name>C</name><prog-lang>python</prog-lang><sections><section>"
            "<name>W</name><topics><topic>a</topic></topics></section></sections></course>",
            encoding="utf-8",
        )
        result = CliRunner().invoke(cli, ["export", "agenda", str(spec)])
        assert result.exit_code == 0
        assert "no <block>" in result.output

    def test_invalid_spec_is_a_click_error(self, tmp_path):
        spec = tmp_path / "c.xml"
        spec.write_text(
            "<course><name>C</name><prog-lang>python</prog-lang><sections><section>"
            '<name>W</name><topics><block units="2"/></topics></section></sections></course>',
            encoding="utf-8",
        )
        result = CliRunner().invoke(cli, ["export", "agenda", str(spec)])
        assert result.exit_code != 0
        assert "<block>" in result.output
