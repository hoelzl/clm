"""Tests for ``clm spec orphans`` (gap #7)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli

DECK = '# %% [markdown] lang="en" tags=["slide"]\n# ## Intro\n'


def _course(tmp_path: Path) -> Path:
    """A course with one shipping deck and three orphans; return the course root."""
    slides = tmp_path / "slides" / "module_100_x"
    ref = slides / "topic_0000_a"
    ref.mkdir(parents=True)
    (ref / "slides_a.py").write_text(DECK, encoding="utf-8")
    orph = slides / "topic_900_b"
    orph.mkdir(parents=True)
    (orph / "slides_b_old.py").write_text(DECK, encoding="utf-8")
    (orph / "slides_b_part1.py").write_text(DECK, encoding="utf-8")
    (orph / "slides_b_misc.py").write_text(DECK, encoding="utf-8")
    ck = ref / ".ipynb_checkpoints"
    ck.mkdir()
    (ck / "slides_a-checkpoint.py").write_text(DECK, encoding="utf-8")

    specs = tmp_path / "course-specs"
    specs.mkdir()
    (specs / "c.xml").write_text(
        dedent("""\
        <course><name><de>C</de><en>C</en></name><prog-lang>python</prog-lang>
        <description><de></de><en></en></description><certificate><de></de><en></en></certificate>
        <sections><section><name><de>S</de><en>S</en></name>
        <topics><topic>a</topic></topics></section></sections></course>
        """),
        encoding="utf-8",
    )
    return tmp_path


def test_lists_orphans_grouped(tmp_path):
    _course(tmp_path)
    r = CliRunner().invoke(cli, ["spec", "orphans", str(tmp_path / "course-specs")])
    assert r.exit_code == 0
    assert "slides_b_old.py" in r.output
    assert "slides_b_part1.py" in r.output
    assert "slides_b_misc.py" in r.output
    # The shipping deck is not listed.
    assert "slides_a.py" not in r.output
    # The checkpoint dir is surfaced as cruft.
    assert ".ipynb_checkpoints" in r.output


def test_kind_filter(tmp_path):
    _course(tmp_path)
    r = CliRunner().invoke(
        cli, ["spec", "orphans", str(tmp_path / "course-specs"), "--kind", "superseded", "--json"]
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    kinds = {o["kind"] for o in data["orphans"]}
    assert kinds == {"superseded"}


def test_json_shape(tmp_path):
    _course(tmp_path)
    r = CliRunner().invoke(cli, ["spec", "orphans", str(tmp_path / "course-specs"), "--json"])
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert data["orphan_count"] == 3
    assert data["by_kind"] == {"superseded": 1, "alternate": 1, "unknown": 1}
    assert len(data["checkpoints"]) == 1


def test_clean_checkpoints_removes(tmp_path):
    _course(tmp_path)
    ck = tmp_path / "slides" / "module_100_x" / "topic_0000_a" / ".ipynb_checkpoints"
    assert ck.exists()
    r = CliRunner().invoke(
        cli, ["spec", "orphans", str(tmp_path / "course-specs"), "--clean-checkpoints"]
    )
    assert "Removed 1" in r.output
    assert not ck.exists()


def test_clean_checkpoints_json_records_removed(tmp_path):
    _course(tmp_path)
    r = CliRunner().invoke(
        cli,
        ["spec", "orphans", str(tmp_path / "course-specs"), "--clean-checkpoints", "--json"],
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert len(data["checkpoints_removed"]) == 1


def test_slides_dir_override(tmp_path):
    _course(tmp_path)
    # Move specs somewhere whose parent has no slides/ — must use --slides-dir.
    far = tmp_path / "elsewhere" / "specs"
    far.mkdir(parents=True)
    (far / "c.xml").write_text(
        (tmp_path / "course-specs" / "c.xml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    r = CliRunner().invoke(
        cli,
        ["spec", "orphans", str(far), "--slides-dir", str(tmp_path / "slides"), "--json"],
    )
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert data["orphan_count"] == 3


def test_missing_slides_dir_errors(tmp_path):
    specs = tmp_path / "course-specs"
    specs.mkdir()
    (specs / "c.xml").write_text(
        dedent("""\
        <course><name><de>C</de><en>C</en></name><prog-lang>python</prog-lang>
        <description><de></de><en></en></description><certificate><de></de><en></en></certificate>
        <sections><section><name><de>S</de><en>S</en></name>
        <topics><topic>a</topic></topics></section></sections></course>
        """),
        encoding="utf-8",
    )
    r = CliRunner().invoke(cli, ["spec", "orphans", str(specs)])
    assert r.exit_code != 0
    assert "Slides directory not found" in r.output


def test_no_specs_errors(tmp_path):
    empty = tmp_path / "course-specs"
    empty.mkdir()
    (tmp_path / "slides").mkdir()
    r = CliRunner().invoke(cli, ["spec", "orphans", str(empty)])
    assert r.exit_code != 0
    assert "No *.xml specs" in r.output
