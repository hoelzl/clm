"""Tests for the ``clm query affected-specs`` CLI command (issue #350)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.main import cli

SPEC_HEADER = """\
<name><de>Test</de><en>Test</en></name>
<prog-lang>python</prog-lang>
<description><de></de><en></en></description>
<certificate><de></de><en></en></certificate>
"""


def _write_spec(specs_dir: Path, name: str, body: str) -> Path:
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_file = specs_dir / f"{name}.xml"
    spec_file.write_text(
        f"<course>\n{SPEC_HEADER}\n{body}\n</course>\n",
        encoding="utf-8",
    )
    return spec_file


def _section(name: str, *topics: str, extra: str = "") -> str:
    topic_xml = "".join(f"<topic>{t}</topic>" for t in topics)
    return (
        f"<section><name><de>{name}</de><en>{name}</en></name>"
        f"{extra}<topics>{topic_xml}</topics></section>"
    )


@pytest.fixture()
def course_tree(tmp_path: Path) -> Path:
    """Two specs sharing one topic, plus an include, a dir-group, a file topic.

    ``alpha`` references topics ``intro`` + ``variables`` and includes the
    shared header next to the topic dirs. ``beta`` references ``variables`` +
    the single-file topic ``meta`` (which imports a sibling module) and
    declares a dir-group over ``examples/``.
    """
    slides = tmp_path / "slides"

    m1 = slides / "module_100_basics"
    (m1 / "topic_010_intro").mkdir(parents=True)
    (m1 / "topic_010_intro" / "slides_intro.py").write_text("# intro", encoding="utf-8")
    (m1 / "topic_020_variables").mkdir()
    (m1 / "topic_020_variables" / "slides_variables.py").write_text("# vars", encoding="utf-8")
    (m1 / "topic_020_variables" / "data.csv").write_text("a,b\n", encoding="utf-8")
    (m1 / "topic_030_unused").mkdir()
    (m1 / "topic_030_unused" / "slides_unused.py").write_text("# unused", encoding="utf-8")
    (m1 / "shared.py").write_text("X = 1\n", encoding="utf-8")

    m2 = slides / "module_200_advanced"
    m2.mkdir()
    (m2 / "topic_040_meta.py").write_text(
        "import helper\n# %% [markdown]\n# meta\n", encoding="utf-8"
    )
    (m2 / "helper.py").write_text("def f(): pass\n", encoding="utf-8")

    archive = slides / "_archive" / "module_100_basics" / "topic_010_intro"
    archive.mkdir(parents=True)
    (archive / "slides_intro.py").write_text("# parked", encoding="utf-8")

    examples = tmp_path / "examples" / "Demo"
    examples.mkdir(parents=True)
    (examples / "demo.py").write_text("print('demo')\n", encoding="utf-8")

    specs_dir = tmp_path / "course-specs"
    _write_spec(
        specs_dir,
        "alpha",
        "<sections>"
        + _section(
            "Week 1",
            "intro",
            "variables",
            extra='<include source="slides/module_100_basics/shared.py"/>',
        )
        + "</sections>",
    )
    _write_spec(
        specs_dir,
        "beta",
        "<sections>"
        + _section("Week 1", "variables", "meta")
        + "</sections>"
        + "<dir-groups><dir-group><name>Examples</name>"
        "<path>examples</path></dir-group></dir-groups>",
    )
    return tmp_path


def _run(course_tree: Path, *args: str, input: str | None = None):
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "query",
            "affected-specs",
            "--spec-dir",
            str(course_tree / "course-specs"),
            *args,
        ],
        input=input,
    )


def _run_json(course_tree: Path, *args: str, input: str | None = None) -> dict:
    result = _run(course_tree, "--json", *args, input=input)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


class TestClaimedPaths:
    def test_topic_file_maps_to_single_spec(self, course_tree):
        data = _run_json(course_tree, "slides/module_100_basics/topic_010_intro/slides_intro.py")
        assert data == {
            "specs": ["alpha"],
            "all": False,
            "paths": [
                {
                    "path": "slides/module_100_basics/topic_010_intro/slides_intro.py",
                    "status": "claimed",
                    "specs": ["alpha"],
                }
            ],
        }

    def test_shared_topic_maps_to_both_specs(self, course_tree):
        data = _run_json(course_tree, "slides/module_100_basics/topic_020_variables/data.csv")
        assert data["specs"] == ["alpha", "beta"]
        assert data["all"] is False

    def test_include_source_is_claimed(self, course_tree):
        data = _run_json(course_tree, "slides/module_100_basics/shared.py")
        assert data["specs"] == ["alpha"]

    def test_dir_group_subtree_is_claimed(self, course_tree):
        data = _run_json(course_tree, "examples/Demo/demo.py")
        assert data["specs"] == ["beta"]

    def test_file_topic_claims_itself_and_imported_sibling(self, course_tree):
        data = _run_json(
            course_tree,
            "slides/module_200_advanced/topic_040_meta.py",
            "slides/module_200_advanced/helper.py",
        )
        assert data["specs"] == ["beta"]
        assert all(p["status"] == "claimed" for p in data["paths"])

    def test_spec_file_maps_to_its_own_spec(self, course_tree):
        data = _run_json(course_tree, "course-specs/alpha.xml")
        assert data["specs"] == ["alpha"]
        assert data["all"] is False


class TestIrrelevantAndUnreferencedPaths:
    def test_ci_config_and_top_level_docs_are_ignored(self, course_tree):
        data = _run_json(course_tree, ".github/workflows/ci.yml", "README.md")
        assert data["specs"] == []
        assert data["all"] is False
        assert {p["status"] for p in data["paths"]} == {"ignored"}

    def test_unreferenced_topic_affects_nothing(self, course_tree):
        data = _run_json(course_tree, "slides/module_100_basics/topic_030_unused/slides_unused.py")
        assert data["specs"] == []
        assert data["paths"][0]["status"] == "unreferenced"

    def test_archived_content_affects_nothing(self, course_tree):
        data = _run_json(
            course_tree, "slides/_archive/module_100_basics/topic_010_intro/slides_intro.py"
        )
        assert data["specs"] == []
        assert data["paths"][0]["status"] == "unreferenced"


class TestFailOpen:
    def test_unclaimed_path_affects_all_specs(self, course_tree):
        (course_tree / "jinja").mkdir()
        data = _run_json(course_tree, "jinja/macros.j2")
        assert data["all"] is True
        assert data["specs"] == ["alpha", "beta"]
        assert data["paths"][0]["status"] == "unknown"

    def test_unclaimed_spec_dir_content_affects_all_specs(self, course_tree):
        data = _run_json(course_tree, "course-specs/deleted-course.xml")
        assert data["all"] is True
        assert data["specs"] == ["alpha", "beta"]

    def test_loose_module_file_without_claim_fails_open(self, course_tree):
        (course_tree / "slides" / "module_100_basics" / "loose.txt").write_text(
            "x", encoding="utf-8"
        )
        data = _run_json(course_tree, "slides/module_100_basics/loose.txt")
        assert data["all"] is True

    def test_broken_spec_is_affected_by_any_relevant_change(self, course_tree):
        (course_tree / "course-specs" / "broken.xml").write_text(
            "<course><unclosed>", encoding="utf-8"
        )
        result = _run(
            course_tree,
            "--json",
            "slides/module_100_basics/topic_010_intro/slides_intro.py",
        )
        assert result.exit_code == 0
        # Depending on the click version, CliRunner mixes stderr into output
        # (8.1) or captures it separately (8.2+) — check both streams.
        combined = result.output + (result.stderr if result.stderr_bytes is not None else "")
        assert "broken.xml" in combined
        # Extract the JSON block (a stderr warning may precede it when mixed).
        data = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
        assert data["specs"] == ["alpha", "broken"]
        assert data["all"] is False


class TestCliBehavior:
    def test_stdin_input(self, course_tree):
        data = _run_json(
            course_tree,
            "--stdin",
            input="slides/module_100_basics/topic_010_intro/slides_intro.py\n\nexamples/Demo/demo.py\n",
        )
        assert data["specs"] == ["alpha", "beta"]
        assert len(data["paths"]) == 2

    def test_empty_input_is_data_not_error(self, course_tree):
        data = _run_json(course_tree)
        assert data == {"specs": [], "all": False, "paths": []}

    def test_duplicate_and_dot_prefixed_paths_are_normalized(self, course_tree):
        data = _run_json(
            course_tree,
            "./examples/Demo/demo.py",
            "examples/Demo/demo.py",
        )
        assert len(data["paths"]) == 1
        assert data["specs"] == ["beta"]

    def test_human_readable_output(self, course_tree):
        result = _run(course_tree, "slides/module_100_basics/topic_020_variables/data.csv")
        assert result.exit_code == 0
        assert "alpha, beta" in result.output
        assert "Affected specs (2/2)" in result.output

    def test_no_specs_found_is_an_error(self, tmp_path):
        empty = tmp_path / "course-specs"
        empty.mkdir()
        runner = CliRunner()
        result = runner.invoke(cli, ["query", "affected-specs", "--spec-dir", str(empty), "x.py"])
        assert result.exit_code != 0
        assert "No *.xml specs" in result.output


class TestModuleBindings:
    def test_module_bound_topic_resolves_only_in_bound_module(self, tmp_path):
        slides = tmp_path / "slides"
        live = slides / "module_100_live" / "topic_010_intro"
        live.mkdir(parents=True)
        (live / "slides_intro.py").write_text("# live", encoding="utf-8")
        frozen = slides / "module_900_cohort" / "topic_010_intro"
        frozen.mkdir(parents=True)
        (frozen / "slides_intro.py").write_text("# frozen", encoding="utf-8")

        specs_dir = tmp_path / "course-specs"
        _write_spec(specs_dir, "live", "<sections>" + _section("W1", "intro") + "</sections>")
        _write_spec(
            specs_dir,
            "cohort",
            '<sections><section module="module_900_cohort">'
            "<name><de>W1</de><en>W1</en></name>"
            "<topics><topic>intro</topic></topics></section></sections>",
        )

        # The module-bound 'cohort' spec claims only its bound copy; the
        # unbound 'live' spec conservatively claims both copies (resolution
        # is first-occurrence-wins, which can flip when copies come and go).
        data = _run_json(tmp_path, "slides/module_900_cohort/topic_010_intro/slides_intro.py")
        assert data["specs"] == ["cohort", "live"]
        data = _run_json(tmp_path, "slides/module_100_live/topic_010_intro/slides_intro.py")
        assert data["specs"] == ["live"]
