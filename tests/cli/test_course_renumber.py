"""End-to-end tests for ``clm course renumber`` (issue #589).

The command is invoked directly with an explicit ``obj`` carrying the DB paths
the ``clm`` entry point would resolve — mirroring the production contract that
the command never guesses database locations.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import CliRunner

from clm.cli.commands.course.renumber import renumber_cmd
from clm.infrastructure.database.db_operations import DatabaseManager
from clm.infrastructure.messaging.base_classes import Result

_META = "completed:python:en:html"


class _Res(Result):
    data: str
    metadata: str

    def result_bytes(self) -> bytes:
        return self.data.encode("utf-8")

    def output_metadata(self):
        return self.metadata


def _seed_cache(db: Path, file_path: Path) -> None:
    result = _Res(
        output_file="o",
        input_file="i",
        content_hash="h",
        data="payload",
        metadata=_META,
        correlation_id="c",
    )
    with DatabaseManager(db) as m:
        m.store_latest_result(str(file_path), "h1", "corr", result, retain_count=3)


@pytest.fixture
def course(tmp_path: Path) -> dict:
    """A scratch course: two topics whose ordinals disagree with spec order."""
    module_dir = tmp_path / "slides" / "module_100_m"
    for name in ("topic_310_a", "topic_050_b"):
        topic = module_dir / name
        topic.mkdir(parents=True)
        (topic / "slides_010_x.py").write_text("# %%\n", encoding="utf-8")
    spec_file = tmp_path / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True)
    spec_file.write_text(
        dedent(
            """\
            <course>
                <name><de>K</de><en>C</en></name>
                <prog-lang>python</prog-lang>
                <sections>
                    <section>
                        <name><de>S</de><en>S</en></name>
                        <topics><topic>a</topic><topic>b</topic></topics>
                    </section>
                </sections>
            </course>
            """
        ),
        encoding="utf-8",
    )
    return {
        "root": tmp_path,
        "module_dir": module_dir,
        "spec": spec_file,
        "cache_db": tmp_path / "clm_cache.db",
        "jobs_db": tmp_path / "clm_jobs.db",
        "obj": {
            "CACHE_DB_PATH": tmp_path / "clm_cache.db",
            "JOBS_DB_PATH": tmp_path / "clm_jobs.db",
        },
    }


def _invoke(course: dict, *args: str):
    return CliRunner().invoke(
        renumber_cmd,
        ["--spec", str(course["spec"]), *args],
        obj=course["obj"],
        catch_exceptions=False,
    )


def test_renumber_moves_dirs_and_migrates_cache_to_live_hits(course):
    old_a = course["module_dir"] / "topic_310_a" / "slides_010_x.py"
    old_b = course["module_dir"] / "topic_050_b" / "slides_010_x.py"
    _seed_cache(course["cache_db"], old_a)
    _seed_cache(course["cache_db"], old_b)

    result = _invoke(course, "--json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["report_only"] is False
    (module,) = report["modules"]
    assert [(r["old"], r["new"]) for r in module["renames"]] == [
        ("topic_310_a", "topic_010_a"),
        ("topic_050_b", "topic_020_b"),
    ]
    assert report["cache"]["rows_rewritten"] >= 2

    # Directories moved...
    assert (course["module_dir"] / "topic_010_a" / "slides_010_x.py").exists()
    assert not (course["module_dir"] / "topic_310_a").exists()
    # ...and the cache HITS at the new paths, MISSES at the old (the point).
    new_a = course["module_dir"] / "topic_010_a" / "slides_010_x.py"
    with DatabaseManager(course["cache_db"]) as m:
        assert m.get_result(str(new_a), "h1", _META) is not None
        assert m.get_result(str(old_a), "h1", _META) is None


def test_report_only_touches_nothing_but_reports_cache_counts(course):
    old_a = course["module_dir"] / "topic_310_a" / "slides_010_x.py"
    _seed_cache(course["cache_db"], old_a)

    result = _invoke(course, "--report-only", "--json")

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["report_only"] is True
    assert report["cache"]["rows_rewritten"] >= 1  # dry-run counts
    # Nothing moved on disk, nothing moved in the DB.
    assert (course["module_dir"] / "topic_310_a").exists()
    with DatabaseManager(course["cache_db"]) as m:
        assert m.get_result(str(old_a), "h1", _META) is not None


def test_no_cache_migrate_moves_dirs_but_leaves_db(course):
    old_a = course["module_dir"] / "topic_310_a" / "slides_010_x.py"
    _seed_cache(course["cache_db"], old_a)

    result = _invoke(course, "--no-cache-migrate", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["cache"] is None
    assert (course["module_dir"] / "topic_010_a").exists()
    with DatabaseManager(course["cache_db"]) as m:
        assert m.get_result(str(old_a), "h1", _META) is not None  # stale by choice


def test_active_build_refuses_without_force(course):
    conn = sqlite3.connect(str(course["jobs_db"]))
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO jobs (status) VALUES ('processing')")
    conn.commit()
    conn.close()

    refused = _invoke(course, "--json")
    assert refused.exit_code == 2
    assert "active" in json.loads(refused.output)["error"]
    assert (course["module_dir"] / "topic_310_a").exists()  # untouched

    forced = _invoke(course, "--force", "--json")
    assert forced.exit_code == 0, forced.output
    assert (course["module_dir"] / "topic_010_a").exists()


def test_validation_error_exits_2_with_json_error(course):
    result = _invoke(course, "unknown_module", "--json")

    assert result.exit_code == 2
    assert "no spec-referenced topics" in json.loads(result.output)["error"]


def test_noop_when_already_in_spec_order(course):
    ok = _invoke(course, "--json")
    assert ok.exit_code == 0

    again = _invoke(course, "--json")
    assert again.exit_code == 0, again.output
    report = json.loads(again.output)
    assert report["modules"][0]["renames"] == []
    assert report["cache"] is None  # no renames -> no migration attempted


def test_human_report_names_the_ordering_spec(course):
    result = _invoke(course, "--report-only")

    assert result.exit_code == 0, result.output
    assert "order: test.xml" in result.output
    assert "topic_310_a  ->  topic_010_a" in result.output
