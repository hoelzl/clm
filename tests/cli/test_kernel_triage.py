"""Tests for ``clm kernel-triage`` (issue #330).

The triage-spec rewriting and outcome classification are tested directly;
the CLI is exercised in ``--report-only`` mode against the committed
single-notebook test course with a seeded telemetry database (re-execution
runs a full ``clm build`` subprocess and is exactly the production build
path, which the build test suite already covers).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.kernel_triage import (
    TriageDeck,
    _classify_rerun_outcomes,
    _extract_build_json,
    write_triage_spec,
)
from clm.cli.main import cli
from clm.infrastructure.database.execution_telemetry import (
    ExecutionTelemetryStore,
    TelemetryEvent,
)

DATA_DIR = Path(__file__).parent.parent / "test-data"
SPEC = DATA_DIR / "course-specs" / "test-spec-3.xml"
SLIDE = (
    DATA_DIR
    / "slides"
    / "module_030_single_notebook"
    / "topic_100_simple_notebook"
    / "slides_simple_notebook.py"
)

TRIAGE_SOURCE_SPEC = """\
<course>
    <name><de>K</de><en>C</en></name>
    <prog-lang>cpp</prog-lang>
    <output-targets>
        <target name="student" path="out/student"/>
    </output-targets>
    <sections>
        <section>
            <name><de>Eins</de><en>One</en></name>
            <topics>
                <topic evaluate="no">algorithms</topic>
                <topic>untouched_topic</topic>
            </topics>
        </section>
        <section>
            <name><de>Zwei</de><en>Two</en></name>
            <topics>
                <subsection weekday="mon">
                    <topic id="clean_code" evaluate="no" skip-errors="true"/>
                </subsection>
                <subsection weekday="tue">
                    <topic>dropped_topic</topic>
                </subsection>
            </topics>
        </section>
        <section>
            <name><de>Drei</de><en>Three</en></name>
            <topics>
                <topic>another_dropped_topic</topic>
            </topics>
        </section>
    </sections>
</course>
"""


class TestWriteTriageSpec:
    def _rewrite(self, tmp_path: Path, target_ids: set[str]) -> ET.Element:
        source = tmp_path / "course.xml"
        source.write_text(TRIAGE_SOURCE_SPEC, encoding="utf-8")
        out = tmp_path / ".clm-triage.xml"
        write_triage_spec(source, target_ids, out)
        return ET.parse(out).getroot()

    def test_keeps_targets_and_strips_evaluate(self, tmp_path):
        root = self._rewrite(tmp_path, {"algorithms", "clean_code"})
        topics = list(root.iter("topic"))
        ids = {(t.attrib.get("id") or (t.text or "").strip()) for t in topics}
        assert ids == {"algorithms", "clean_code"}
        for topic in topics:
            assert "evaluate" not in topic.attrib

    def test_other_attributes_survive(self, tmp_path):
        root = self._rewrite(tmp_path, {"clean_code"})
        topic = next(root.iter("topic"))
        assert topic.attrib.get("skip-errors") == "true"

    def test_sections_without_topics_are_disabled(self, tmp_path):
        root = self._rewrite(tmp_path, {"algorithms"})
        sections = list(root.iter("section"))
        assert sections[0].attrib.get("enabled") != "false"
        assert sections[1].attrib.get("enabled") == "false"
        assert sections[2].attrib.get("enabled") == "false"

    def test_empty_subsections_are_disabled(self, tmp_path):
        root = self._rewrite(tmp_path, {"algorithms", "clean_code"})
        subsections = {
            s.attrib["weekday"]: s.attrib.get("enabled") for s in root.iter("subsection")
        }
        assert subsections["mon"] != "false"
        assert subsections["tue"] == "false"

    def test_output_targets_are_dropped(self, tmp_path):
        root = self._rewrite(tmp_path, {"algorithms"})
        assert list(root.iter("output-targets")) == []


class TestExtractBuildJson:
    def test_finds_summary_after_noise(self):
        stdout = (
            "Loading course specification...\n"
            "some {brace} noise\n"
            + json.dumps({"status": "success", "errors": []}, indent=2)
            + "\ntrailing line\n"
        )
        assert _extract_build_json(stdout) == {"status": "success", "errors": []}

    def test_returns_none_without_summary(self):
        assert _extract_build_json("no json here") is None


class TestClassifyRerunOutcomes:
    def _decks(self) -> list[TriageDeck]:
        return [
            TriageDeck(path="C:/c/slides_lifted.py", topic_id="a", is_workaround=True),
            TriageDeck(path="C:/c/slides_flaky.py", topic_id="b", is_workaround=False),
            TriageDeck(path="C:/c/slides_broken.py", topic_id="c", is_workaround=True),
        ]

    def test_outcomes_and_recommendations(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(
            TelemetryEvent(
                input_file="C:/c/slides_broken.py",
                outcome="failed",
                classification="deterministic",
                attempts=2,
                failure_type="dead_kernel",
                failing_cell_index=12,
            )
        )
        summary_json = {
            "status": "failed",
            "errors": [
                {"file_path": "C:/c/slides_broken.py", "message": "Kernel died\nmore detail"}
            ],
            "flaky_files": [
                {
                    "file_path": "C:/c/slides_flaky.py",
                    "max_attempts": 2,
                    "failure_types": ["startup_timeout"],
                }
            ],
        }

        decks = self._decks()
        _classify_rerun_outcomes(decks, summary_json, store, since_run := "2000-01-01T00:00:00")
        assert since_run  # readability binding

        by_path = {d.path: d for d in decks}
        lifted = by_path["C:/c/slides_lifted.py"]
        assert lifted.outcome == "passed"
        assert "can be lifted" in lifted.recommendation

        flaky = by_path["C:/c/slides_flaky.py"]
        assert flaky.outcome == "flaky"
        assert "still flaky" in flaky.recommendation
        assert "startup_timeout" in flaky.details

        broken = by_path["C:/c/slides_broken.py"]
        assert broken.outcome == "failed"
        assert "deterministic dead_kernel at cell 12" in broken.details
        assert 'keep evaluate="no"' in broken.recommendation

    def test_no_summary_yields_unknown(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        decks = self._decks()
        _classify_rerun_outcomes(decks, None, store, "2000-01-01T00:00:00")
        assert all(d.outcome == "unknown" for d in decks)


class TestKernelTriageCli:
    def _invoke(self, tmp_path: Path, *args: str):
        runner = CliRunner()
        return runner.invoke(
            cli,
            [
                "--cache-db-path",
                str(tmp_path / "cache.db"),
                "--jobs-db-path",
                str(tmp_path / "jobs.db"),
                "--telemetry-db-path",
                str(tmp_path / "telemetry.db"),
                "kernel-triage",
                str(SPEC),
                "--report-only",
                *args,
            ],
            catch_exceptions=False,
        )

    def test_nothing_to_triage(self, tmp_path):
        result = self._invoke(tmp_path)
        assert result.exit_code == 0, result.output
        assert "Nothing to triage" in result.output

    def test_report_only_lists_flaky_deck_from_telemetry(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(
            TelemetryEvent(
                input_file=str(SLIDE),
                outcome="passed_after_retry",
                classification="flaky",
                attempts=2,
                failure_type="dead_kernel",
                language="en",
            )
        )

        result = self._invoke(tmp_path)
        assert result.exit_code == 0, result.output
        assert "known-flaky decks from telemetry (1)" in result.output
        assert "slides_simple_notebook.py" in result.output
        assert "passed_after_retry" in result.output

    def test_stale_telemetry_is_reported_not_triaged(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(
            TelemetryEvent(
                input_file="C:/gone/slides_removed.py",
                outcome="failed",
                classification="deterministic",
                attempts=6,
            )
        )

        result = self._invoke(tmp_path)
        assert result.exit_code == 0, result.output
        assert "stale telemetry" in result.output
        assert "slides_removed.py" in result.output

    def test_json_output(self, tmp_path):
        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(
            TelemetryEvent(
                input_file=str(SLIDE),
                outcome="failed",
                classification="deterministic",
                attempts=6,
                failure_type="cell_execution_error",
                failing_cell_index=3,
            )
        )

        result = self._invoke(tmp_path, "--json")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output[result.output.index("{") :])
        assert data["mode"] == "report-only"
        assert len(data["decks"]) == 1
        deck = data["decks"][0]
        assert deck["is_workaround"] is False
        assert deck["history"][0]["failure_type"] == "cell_execution_error"

    def test_old_telemetry_outside_window_is_ignored(self, tmp_path):
        import sqlite3

        store = ExecutionTelemetryStore(tmp_path / "telemetry.db")
        store.record_event(
            TelemetryEvent(
                input_file=str(SLIDE),
                outcome="failed",
                classification="deterministic",
                attempts=6,
            )
        )
        # Backdate the event beyond any realistic lookback window.
        with sqlite3.connect(tmp_path / "telemetry.db") as conn:
            conn.execute("UPDATE execution_telemetry SET created_at = '2020-01-01T00:00:00.000Z'")

        result = self._invoke(tmp_path, "--since-days", "30")
        assert result.exit_code == 0, result.output
        assert "Nothing to triage" in result.output
