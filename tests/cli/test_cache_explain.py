"""Tests for ``clm cache explain`` (issue #328).

Read-only inspection: for one slide source file, show the cache-key
components a build would compute, the resulting hashes, and the hit/miss
state of every cache layer. The tests run against the committed
single-notebook test course and seed real cache rows keyed by the hashes
the command itself reports — pinning that explain's keys are the same keys
the build machinery stores under.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.main import cli

DATA_DIR = Path(__file__).parent.parent / "test-data"
SPEC = DATA_DIR / "course-specs" / "test-spec-3.xml"
SLIDE = (
    DATA_DIR
    / "slides"
    / "module_030_single_notebook"
    / "topic_100_simple_notebook"
    / "slides_simple_notebook.py"
)


def _explain(tmp_path: Path, *extra_args: str):
    runner = CliRunner()
    return runner.invoke(
        cli,
        [
            "--cache-db-path",
            str(tmp_path / "cache.db"),
            "--jobs-db-path",
            str(tmp_path / "jobs.db"),
            "cache",
            "explain",
            str(SLIDE),
            "--spec",
            str(SPEC),
            "-L",
            "en",
            "--format",
            "html",
            *extra_args,
        ],
        catch_exceptions=False,
    )


def _explain_json(tmp_path: Path) -> dict:
    result = _explain(tmp_path, "--json")
    assert result.exit_code == 0, result.output
    # Locate the JSON object in the output (logging lines may precede it).
    return json.loads(result.output[result.output.index("{") :])


def _artifact(data: dict, output_metadata: str) -> dict:
    matches = [a for a in data["artifacts"] if a["output_metadata"] == output_metadata]
    assert matches, (
        f"no artifact {output_metadata} in {[a['output_metadata'] for a in data['artifacts']]}"
    )
    return matches[0]


class TestCacheExplainCold:
    def test_cold_caches_report_miss_and_execute(self, tmp_path):
        data = _explain_json(tmp_path)

        assert data["components"]["schema_version"] >= 2
        assert data["components"]["template_fingerprint"]
        assert data["components"]["worker_image_identity"].startswith(("direct", "docker:"))
        assert data["cache_db_exists"] is False
        assert data["artifacts"]

        for artifact in data["artifacts"]:
            assert artifact["caches"]["processed_files"] is None
            assert artifact["caches"]["executed_notebooks"] is None
            assert artifact["caches"]["results_cache"] is None
            assert artifact["verdict"] == "will execute"

    def test_execution_hash_is_kind_agnostic_content_hash_is_not(self, tmp_path):
        """Speaker/Completed share the executed notebook, so their
        execution key must agree while their content keys differ."""
        data = _explain_json(tmp_path)
        artifacts = data["artifacts"]
        assert len({a["execution_cache_hash"] for a in artifacts}) == 1
        assert len({a["content_hash"] for a in artifacts}) == len(artifacts)

    def test_human_output_shows_components_and_verdicts(self, tmp_path):
        result = _explain(tmp_path)
        assert result.exit_code == 0, result.output
        assert "Key components" in result.output
        assert "template fingerprint" in result.output
        assert "worker image" in result.output
        assert "verdict" in result.output
        assert "will execute" in result.output

    def test_file_not_in_course_fails_helpfully(self, tmp_path):
        bogus = tmp_path / "not_in_course.py"
        bogus.write_text("# not part of any course\n")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["cache", "explain", str(bogus), "--spec", str(SPEC)],
        )
        assert result.exit_code != 0
        assert "not part of the course" in result.output


class TestCacheExplainSeeded:
    """Seed real cache rows under the hashes explain reports, re-run, and
    assert the hit/verdict transitions — pinning key agreement between
    explain and the storing machinery."""

    def test_processed_files_hit_reports_replay(self, tmp_path):
        from clm.infrastructure.database.db_operations import DatabaseManager
        from clm.infrastructure.messaging.notebook_classes import NotebookResult

        cold = _explain_json(tmp_path)
        artifact = _artifact(cold, "completed:python:en:html")

        with DatabaseManager(tmp_path / "cache.db") as dm:
            dm.store_result(
                file_path=cold["source_file"],
                content_hash=artifact["content_hash"],
                correlation_id="cid-test",
                result=NotebookResult(
                    correlation_id="cid-test",
                    output_file=artifact["output_file"],
                    input_file=cold["source_file"],
                    content_hash=artifact["content_hash"],
                    result="<html>cached</html>",
                    output_metadata_tags=("completed", "python", "en", "html"),
                ),
            )

        warm = _explain_json(tmp_path)
        hit = _artifact(warm, "completed:python:en:html")
        assert hit["caches"]["processed_files"] is not None
        assert hit["verdict"].startswith("replays stored result")
        # Other artifacts stay cold.
        other = _artifact(warm, "code-along:python:en:html")
        assert other["verdict"] == "will execute"

    def test_recording_hit_with_cold_execution_cache_reports_producer_gate(self, tmp_path):
        """Mirrors SqliteBackend._can_replay_from_cache: a Recording HTML
        processed_files hit does NOT replay while executed_notebooks is
        cold — the worker runs to repopulate the Stage-4 producer cache."""
        from clm.infrastructure.database.db_operations import DatabaseManager
        from clm.infrastructure.messaging.notebook_classes import NotebookResult

        cold = _explain_json(tmp_path)
        artifact = _artifact(cold, "recording:python:en:html")

        with DatabaseManager(tmp_path / "cache.db") as dm:
            dm.store_result(
                file_path=cold["source_file"],
                content_hash=artifact["content_hash"],
                correlation_id="cid-test",
                result=NotebookResult(
                    correlation_id="cid-test",
                    output_file=artifact["output_file"],
                    input_file=cold["source_file"],
                    content_hash=artifact["content_hash"],
                    result="<html>cached</html>",
                    output_metadata_tags=("recording", "python", "en", "html"),
                ),
            )

        warm = _explain_json(tmp_path)
        hit = _artifact(warm, "recording:python:en:html")
        assert hit["caches"]["processed_files"] is not None
        assert "execution cache is cold" in hit["verdict"]

    def test_executed_notebooks_hit_reports_reuse(self, tmp_path):
        from clm.infrastructure.database.executed_notebook_cache import (
            ExecutedNotebookCache,
        )

        cold = _explain_json(tmp_path)
        artifact = _artifact(cold, "completed:python:en:html")

        with ExecutedNotebookCache(tmp_path / "cache.db") as nb_cache:
            nb_cache.store(
                input_file=cold["source_file"],
                content_hash=artifact["execution_cache_hash"],
                language="en",
                prog_lang="python",
                executed_notebook={"cells": []},  # opaque blob, content irrelevant
            )

        warm = _explain_json(tmp_path)
        hit = _artifact(warm, "completed:python:en:html")
        assert hit["caches"]["executed_notebooks"] is not None
        assert "reusing the cached execution" in hit["verdict"]

    def test_results_cache_hit_with_missing_output_reports_execute(self, tmp_path):
        from clm.infrastructure.database.job_queue import JobQueue
        from clm.infrastructure.database.schema import init_database

        cold = _explain_json(tmp_path)
        artifact = _artifact(cold, "completed:python:en:html")

        init_database(tmp_path / "jobs.db")
        jq = JobQueue(tmp_path / "jobs.db")
        try:
            jq.add_to_cache(artifact["output_file"], artifact["content_hash"], {"format": "html"})
        finally:
            jq.close()

        warm = _explain_json(tmp_path)
        hit = _artifact(warm, "completed:python:en:html")
        assert hit["caches"]["results_cache"] is not None
        # The committed test-data tree is normally unbuilt, but tolerate a
        # leftover output tree from a local build: the verdict flips between
        # the two results_cache outcomes on output existence.
        if hit["output_exists"]:
            assert "skips execution" in hit["verdict"]
        else:
            assert "output file is missing" in hit["verdict"]
