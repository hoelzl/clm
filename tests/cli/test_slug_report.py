"""Tests for ``clm slides slug-report`` (gap #6)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli


def _deck(*slide_ids: str) -> str:
    cells = [
        f'# %% [markdown] lang="en" tags=["slide"] slide_id="{sid}"\n# ## Heading {i}\n'
        for i, sid in enumerate(slide_ids)
    ]
    return "\n".join(cells)


def _tree(tmp_path: Path) -> Path:
    s = tmp_path / "slides" / "module_100_x" / "topic_010_a"
    s.mkdir(parents=True)
    (s / "slides_bi.py").write_text(_deck("introduction-to-x", "df", "data"), encoding="utf-8")
    arch = tmp_path / "slides" / "module_100_x" / "_archive"
    arch.mkdir(parents=True)
    (arch / "slides_old.py").write_text(_deck("cp"), encoding="utf-8")
    return tmp_path / "slides"


def _spec(tmp_path: Path, *topics: str) -> Path:
    t = "".join(f"<topic>{x}</topic>" for x in topics)
    spec = tmp_path / "course-specs" / "c.xml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        dedent(f"""\
        <course>
          <name><de>C</de><en>C</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          <sections><section><name><de>S</de><en>S</en></name>
          <topics>{t}</topics></section></sections>
        </course>
        """),
        encoding="utf-8",
    )
    return spec


def test_directory_scan_flags(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(slides)])
    assert r.exit_code == 0
    assert 'slide_id="df"' in r.output
    assert 'slide_id="data"' in r.output
    # The clean multi-token id is not flagged.
    assert 'slide_id="introduction-to-x"' not in r.output


def test_min_severity_high(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(slides), "--min-severity", "high"])
    assert 'slide_id="df"' in r.output  # very_short = high
    # introduction-to-x isn't flagged at all; a low single_token would be hidden.


def test_json_output(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(slides), "--json"])
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert data["by_issue"]["very_short"] >= 1
    assert data["by_issue"]["generic"] >= 1


def test_exclude_archive(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(
        cli, ["slides", "slug-report", str(slides), "--exclude", "_archive", "--json"]
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    files = {Path(f["file"]).name for f in data["findings"]}
    assert "slides_old.py" not in files  # the archived "cp" deck was excluded
    assert "slides_bi.py" in files


def test_shipping_only(tmp_path):
    slides = _tree(tmp_path)
    _spec(tmp_path, "a")  # only topic_010_a ships
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(slides), "--shipping-only", "--json"])
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    files = {Path(f["file"]).name for f in data["findings"]}
    assert files == {"slides_bi.py"}  # the archived deck does not ship


def test_spec_path_resolves_decks(tmp_path):
    _tree(tmp_path)
    spec = _spec(tmp_path, "a")
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(spec)])
    assert r.exit_code == 0
    assert 'slide_id="df"' in r.output


def test_scope_on_spec_errors(tmp_path):
    _tree(tmp_path)
    spec = _spec(tmp_path, "a")
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(spec), "--exclude", "_archive"])
    assert r.exit_code != 0
    assert "directory" in r.output


def test_clean_tree_message(tmp_path):
    s = tmp_path / "slides" / "topic_010_a"
    s.mkdir(parents=True)
    (s / "slides_ok.py").write_text(_deck("introduction-to-functions"), encoding="utf-8")
    r = CliRunner().invoke(cli, ["slides", "slug-report", str(tmp_path / "slides")])
    assert "all look fine" in r.output
