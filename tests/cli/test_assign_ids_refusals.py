"""Tests for ``clm slides assign-ids --report-refusals [--context]`` (gap #5)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.main import cli

# The hard-refusal cell is an HTML comment: an alt-less <img src> stopped
# hard-refusing with the #233 filename-stem fallback.
DECK = (
    '# %% [markdown] lang="en" tags=["slide"]\n'
    "# ## Introduction\n"
    "\n"
    '# %% [markdown] lang="en" tags=["slide"]\n'
    "# <!-- placeholder-diagram -->\n"
)


def _deck(tmp_path: Path) -> Path:
    f = tmp_path / "slides_x.py"
    f.write_text(DECK, encoding="utf-8")
    return f


def test_report_refusals_lists_hard(tmp_path):
    f = _deck(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--report-refusals"])
    assert r.exit_code == 2  # hard refusal present
    assert "[hard]" in r.output
    assert "1 hard refusal(s)" in r.output
    # Without --context, no cell body is shown.
    assert "placeholder-diagram" not in r.output


def test_report_refusals_does_not_write(tmp_path):
    f = _deck(tmp_path)
    before = f.read_text(encoding="utf-8")
    CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--report-refusals", "--report-only"])
    assert f.read_text(encoding="utf-8") == before


def test_context_implies_report_refusals_and_shows_body(tmp_path):
    f = _deck(tmp_path)
    # --context alone (no explicit --report-refusals) still produces the worklist.
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--context", "--report-only"])
    assert "[hard]" in r.output
    assert "placeholder-diagram" in r.output
    assert 'heading "Introduction"' in r.output


def test_report_refusals_json(tmp_path):
    f = _deck(tmp_path)
    r = CliRunner().invoke(
        cli, ["slides", "assign-ids", str(f), "--context", "--json", "--report-only"]
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert data["hard_refusals"] == 1
    ctx = data["refusals"][0]["context"]
    assert ctx["preceding_heading"] == "Introduction"
    assert "placeholder-diagram" in ctx["body"]


def test_clean_deck_reports_no_refusals(tmp_path):
    f = tmp_path / "slides_ok.py"
    f.write_text(
        '# %% [markdown] lang="en" tags=["slide"]\n# ## Introduction\n',
        encoding="utf-8",
    )
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--report-refusals"])
    assert r.exit_code == 0
    assert "No refusals" in r.output


def test_report_refusals_directory_scope(tmp_path):
    # The worklist reflects scoping: --only bilingual drops the split half.
    s = tmp_path / "slides" / "topic_010_a"
    s.mkdir(parents=True)
    (s / "slides_bi.py").write_text(DECK, encoding="utf-8")
    half = '# %% [markdown] lang="de" tags=["slide"]\n# <img src="img/de.png">\n'
    (s / "slides_y.de.py").write_text(half, encoding="utf-8")
    (s / "slides_y.en.py").write_text(
        '# %% [markdown] lang="en" tags=["slide"]\n# <img src="img/en.png">\n',
        encoding="utf-8",
    )
    r = CliRunner().invoke(
        cli,
        [
            "slides",
            "assign-ids",
            str(tmp_path / "slides"),
            "--report-refusals",
            "--report-only",
            "--only",
            "bilingual",
        ],
    )
    assert "slides_bi.py" in r.output
    assert "slides_y.de.py" not in r.output
