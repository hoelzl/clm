"""Tests for ``clm slides coverage-report`` (gap #8)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli


def _bi(de: int, en: int) -> str:
    cells = [f'# %% [markdown] lang="de" tags=["slide"]\n# ## De {i}\n' for i in range(de)]
    cells += [f'# %% [markdown] lang="en" tags=["slide"]\n# ## En {i}\n' for i in range(en)]
    return "\n".join(cells)


def _tree(tmp_path: Path) -> Path:
    s = tmp_path / "slides" / "module_100_x"
    t1 = s / "topic_010_a"
    t1.mkdir(parents=True)
    (t1 / "slides_bal.py").write_text(_bi(2, 2), encoding="utf-8")
    (t1 / "slides_imb.py").write_text(_bi(2, 1), encoding="utf-8")
    t2 = s / "topic_020_b"
    t2.mkdir(parents=True)
    (t2 / "slides_de.de.py").write_text(_bi(1, 0), encoding="utf-8")
    arch = s / "_archive"
    arch.mkdir(parents=True)
    (arch / "slides_old.py").write_text(_bi(1, 0), encoding="utf-8")
    return tmp_path / "slides"


def _spec(tmp_path: Path, *topics: str) -> Path:
    t = "".join(f"<topic>{x}</topic>" for x in topics)
    spec = tmp_path / "course-specs" / "c.xml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        dedent(f"""\
        <course><name><de>C</de><en>C</en></name><prog-lang>python</prog-lang>
        <description><de></de><en></en></description><certificate><de></de><en></en></certificate>
        <sections><section><name><de>S</de><en>S</en></name>
        <topics>{t}</topics></section></sections></course>
        """),
        encoding="utf-8",
    )
    return spec


def test_directory_report(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "coverage-report", str(slides)])
    assert r.exit_code == 0
    assert "slides_imb.py" in r.output  # imbalanced bilingual
    assert "slides_de.de.py" in r.output  # de-only split half
    assert "slides_bal.py" not in r.output  # balanced not listed


def test_json_by_status(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "coverage-report", str(slides), "--json"])
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert data["by_status"]["de_only"] >= 1
    assert data["by_status"]["imbalanced"] >= 1


def test_status_filter(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(
        cli, ["slides", "coverage-report", str(slides), "--status", "de_only", "--json"]
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert {d["status"] for d in data["decks"]} == {"de_only"}


def test_exclude_archive(tmp_path):
    slides = _tree(tmp_path)
    r = CliRunner().invoke(
        cli, ["slides", "coverage-report", str(slides), "--exclude", "_archive", "--json"]
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    names = {Path(d["label"]).name for d in data["decks"]}
    assert "slides_old.py" not in names


def test_shipping_only(tmp_path):
    slides = _tree(tmp_path)
    _spec(tmp_path, "a")  # only topic_010_a ships
    r = CliRunner().invoke(
        cli, ["slides", "coverage-report", str(slides), "--shipping-only", "--json"]
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    names = {Path(d["label"]).name for d in data["decks"]}
    assert names == {"slides_bal.py", "slides_imb.py"}


def test_spec_path(tmp_path):
    _tree(tmp_path)
    spec = _spec(tmp_path, "a")
    r = CliRunner().invoke(cli, ["slides", "coverage-report", str(spec)])
    assert r.exit_code == 0
    assert "slides_imb.py" in r.output


def test_scope_on_spec_errors(tmp_path):
    _tree(tmp_path)
    spec = _spec(tmp_path, "a")
    r = CliRunner().invoke(cli, ["slides", "coverage-report", str(spec), "--exclude", "_archive"])
    assert r.exit_code != 0
    assert "directory" in r.output


def test_all_balanced_message(tmp_path):
    s = tmp_path / "slides" / "topic_010_a"
    s.mkdir(parents=True)
    (s / "slides_ok.py").write_text(_bi(2, 2), encoding="utf-8")
    r = CliRunner().invoke(cli, ["slides", "coverage-report", str(tmp_path / "slides")])
    assert "balanced" in r.output
