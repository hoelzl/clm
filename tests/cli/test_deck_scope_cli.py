"""Tests for the deck-scoping flags on ``assign-ids`` and ``normalize`` (gap #4)."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.main import cli

BI = (
    '# %% [markdown] lang="de" tags=["slide"]\n# ## Einfuehrung\n\n'
    '# %% [markdown] lang="en" tags=["slide"]\n# ## Introduction\n'
)
HALF_DE = '# %% [markdown] lang="de" tags=["slide"]\n# ## Einfuehrung\n'
HALF_EN = '# %% [markdown] lang="en" tags=["slide"]\n# ## Introduction\n'


def _tree(tmp_path: Path) -> Path:
    """A slides tree with a bilingual deck, a split pair, and an archived deck."""
    s = tmp_path / "slides" / "module_100_x"
    (s / "topic_010_a").mkdir(parents=True)
    (s / "topic_010_a" / "slides_bi.py").write_text(BI, encoding="utf-8")
    (s / "topic_020_b").mkdir(parents=True)
    (s / "topic_020_b" / "slides_x.de.py").write_text(HALF_DE, encoding="utf-8")
    (s / "topic_020_b" / "slides_x.en.py").write_text(HALF_EN, encoding="utf-8")
    (s / "_archive" / "topic_900_old").mkdir(parents=True)
    (s / "_archive" / "topic_900_old" / "slides_old.py").write_text(BI, encoding="utf-8")
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


def _ids_in(path: Path) -> int:
    return path.read_text(encoding="utf-8").count("slide_id=")


class TestAssignIdsScope:
    def test_only_bilingual_excludes_splits(self, tmp_path):
        slides = _tree(tmp_path)
        result = CliRunner().invoke(
            cli,
            [
                "slides",
                "assign-ids",
                str(slides),
                "--accept-content-derived",
                "--only",
                "bilingual",
            ],
        )
        assert result.exit_code == 0
        # Bilingual + archived bilingual minted; split halves untouched.
        assert _ids_in(slides / "module_100_x" / "topic_010_a" / "slides_bi.py") == 2
        assert _ids_in(slides / "module_100_x" / "topic_020_b" / "slides_x.de.py") == 0

    def test_exclude_archive(self, tmp_path):
        slides = _tree(tmp_path)
        CliRunner().invoke(
            cli,
            [
                "slides",
                "assign-ids",
                str(slides),
                "--accept-content-derived",
                "--only",
                "bilingual",
                "--exclude",
                "_archive",
            ],
        )
        assert _ids_in(slides / "module_100_x" / "topic_010_a" / "slides_bi.py") == 2
        assert (
            _ids_in(slides / "module_100_x" / "_archive" / "topic_900_old" / "slides_old.py") == 0
        )

    def test_only_split_touches_pair_only(self, tmp_path):
        slides = _tree(tmp_path)
        CliRunner().invoke(
            cli,
            ["slides", "assign-ids", str(slides), "--accept-content-derived", "--only", "split"],
        )
        assert _ids_in(slides / "module_100_x" / "topic_020_b" / "slides_x.de.py") >= 1
        assert _ids_in(slides / "module_100_x" / "topic_010_a" / "slides_bi.py") == 0

    def test_shipping_only(self, tmp_path):
        slides = _tree(tmp_path)
        _spec(tmp_path, "a")  # only topic_010_a ("a") ships
        CliRunner().invoke(
            cli,
            ["slides", "assign-ids", str(slides), "--accept-content-derived", "--shipping-only"],
        )
        assert _ids_in(slides / "module_100_x" / "topic_010_a" / "slides_bi.py") == 2
        # topic_020_b and _archive are not in the spec → untouched.
        assert _ids_in(slides / "module_100_x" / "topic_020_b" / "slides_x.de.py") == 0
        assert (
            _ids_in(slides / "module_100_x" / "_archive" / "topic_900_old" / "slides_old.py") == 0
        )

    def test_scope_on_single_file_errors(self, tmp_path):
        slides = _tree(tmp_path)
        f = slides / "module_100_x" / "topic_010_a" / "slides_bi.py"
        result = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--only", "bilingual"])
        assert result.exit_code != 0
        assert "apply to a directory" in result.output

    def test_report_only_json_respects_scope(self, tmp_path):
        slides = _tree(tmp_path)
        result = CliRunner().invoke(
            cli,
            [
                "slides",
                "assign-ids",
                str(slides),
                "--accept-content-derived",
                "--report-only",
                "--only",
                "bilingual",
                "--exclude",
                "_archive",
                "--json",
            ],
        )
        data = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
        assert data["files_visited"] == 1


class TestNormalizeScope:
    def test_only_bilingual_dry_run(self, tmp_path):
        slides = _tree(tmp_path)
        result = CliRunner().invoke(
            cli,
            [
                "slides",
                "normalize",
                str(slides),
                "--operations",
                "slide_ids",
                "--dry-run",
                "--only",
                "bilingual",
                "--exclude",
                "_archive",
                "--json",
            ],
        )
        data = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
        # Only the single bilingual deck's cells are in scope.
        files = {c["file"] for c in data["changes"]}
        assert all("slides_bi.py" in f for f in files)

    def test_shipping_only_applies(self, tmp_path):
        slides = _tree(tmp_path)
        _spec(tmp_path, "a")
        CliRunner().invoke(
            cli,
            ["slides", "normalize", str(slides), "--operations", "slide_ids", "--shipping-only"],
        )
        assert _ids_in(slides / "module_100_x" / "topic_010_a" / "slides_bi.py") == 2
        assert _ids_in(slides / "module_100_x" / "topic_020_b" / "slides_x.de.py") == 0

    def test_scope_on_spec_errors(self, tmp_path):
        _tree(tmp_path)
        spec = _spec(tmp_path, "a")
        result = CliRunner().invoke(cli, ["slides", "normalize", str(spec), "--only", "bilingual"])
        assert result.exit_code != 0
        assert "apply to a directory" in result.output
