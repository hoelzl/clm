"""``--json`` output for the recordings CLI surface (#965, surface half).

The read-side recordings commands (``status``, ``jobs list/cancel/fail/
poll/wait/prune``, ``backends``, ``check``) historically rendered Rich
tables only, with truncated values (``id[:8]`` prefixes, ``[:60]``
messages) — unusable for an agent driving the workflow. These tests pin
the machine-readable payloads: full ids, full messages, full paths, and
the load-bearing exit codes (0 ok / 1 failure / 2 timeout).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import recordings as recordings_module
from clm.cli.commands.recordings import recordings_group


def _parse_json(result) -> dict | list:
    """Parse the JSON document from a CLI run's **stdout only**.

    ``result.output`` mixes stderr in write order (click's StreamMixer),
    so a stray log line would break parsing; ``result.stdout`` is the
    stream the --json contract promises to keep parseable.
    """
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _install_fake_manager(monkeypatch, tmp_path: Path):
    """Return a pre-seeded JobManager wired to a stub backend.

    Same pattern as ``test_cli_recordings.py``: a real JobManager with a
    stub backend, monkeypatched into the CLI factory seams.
    """
    from clm.recordings.workflow.backends.base import (
        BackendCapabilities,
        ProcessingBackend,
    )
    from clm.recordings.workflow.directories import ensure_root
    from clm.recordings.workflow.event_bus import EventBus
    from clm.recordings.workflow.job_manager import JobManager
    from clm.recordings.workflow.job_store import JsonFileJobStore

    class _StubBackend(ProcessingBackend):
        capabilities = BackendCapabilities(
            name="stub",
            display_name="Stub",
            is_synchronous=False,
        )

        def accepts_file(self, path: Path) -> bool:
            return True

        def submit(self, raw_path, final_path, *, options, ctx):
            raise NotImplementedError

        def poll(self, job, *, ctx):
            return job

        def cancel(self, job, *, ctx):
            pass

    ensure_root(tmp_path)
    store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
    bus = EventBus()
    manager = JobManager(
        backend=_StubBackend(),
        root_dir=tmp_path,
        store=store,
        bus=bus,
    )
    monkeypatch.setattr(recordings_module, "_make_job_manager_for_root", lambda root: manager)
    monkeypatch.setattr(recordings_module, "_resolve_recordings_root", lambda cli_root: tmp_path)
    return manager


def _make_job(tmp_path: Path, job_id: str = "aaaa-bbbb-cccc-dddd", state=None, **kwargs):
    from clm.recordings.workflow.jobs import JobState, ProcessingJob

    if state is None:
        state = JobState.PROCESSING
    defaults: dict = {
        "id": job_id,
        "backend_name": "stub",
        "raw_path": tmp_path / "to-process" / "x.mp4",
        "final_path": tmp_path / "final" / "x.mp4",
        "relative_dir": Path(),
        "state": state,
    }
    defaults.update(kwargs)
    return ProcessingJob(**defaults)


# ---------------------------------------------------------------------------
# status --json
# ---------------------------------------------------------------------------


class TestStatusJson:
    def _fake_state_module(self, monkeypatch, state):
        fake_state_module = MagicMock()
        fake_state_module.load_state = MagicMock(return_value=state)
        monkeypatch.setitem(__import__("sys").modules, "clm.recordings.state", fake_state_module)

    def test_payload_has_full_lecture_rows(self, monkeypatch: pytest.MonkeyPatch):
        part_processed = MagicMock(status="processed")
        part_failed = MagicMock(status="failed")

        def _lecture(lecture_id, name, parts):
            lec = MagicMock()
            lec.lecture_id = lecture_id
            lec.display_name = name
            lec.parts = parts
            return lec

        lectures = [
            _lecture("L1", "Intro", [part_processed]),
            _lecture("L2", "Two", [part_failed]),
            _lecture("L5", "Five", []),
        ]
        fake_state = MagicMock()
        fake_state.progress = (1, 3)
        fake_state.continue_current_lecture = True
        fake_state.lectures = lectures
        fake_state.next_lecture_index = 1
        self._fake_state_module(monkeypatch, fake_state)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["status", "my-course", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["course_id"] == "my-course"
        assert payload["recorded"] == 1
        assert payload["total"] == 3
        assert payload["continue_current_lecture"] is True
        assert payload["next_lecture_index"] == 1
        rows = payload["lectures"]
        assert [r["lecture_id"] for r in rows] == ["L1", "L2", "L5"]
        assert rows[0]["status"] == "processed"
        assert rows[1]["status"] == "failed"
        assert rows[1]["next"] is True
        assert rows[2]["status"] == "unrecorded"
        assert rows[2]["parts"] == 0
        assert rows[0]["part_statuses"] == ["processed"]

    def test_no_state_still_exits_1(self, monkeypatch: pytest.MonkeyPatch):
        self._fake_state_module(monkeypatch, None)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["status", "nope", "--json"])
        assert result.exit_code == 1
        # Diagnostics go to stderr in JSON mode; stdout stays empty so a
        # parser never sees interleaved plain text (review finding #4).
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# check --json
# ---------------------------------------------------------------------------


def _set_backend(monkeypatch, backend: str, api_key: str = ""):
    from clm.infrastructure.config import AuphonicConfig, RecordingsConfig

    cfg = RecordingsConfig.model_construct(
        processing_backend=backend,
        auphonic=AuphonicConfig(api_key=api_key),
    )
    monkeypatch.setattr(recordings_module, "_build_recordings_config", lambda: cfg)
    monkeypatch.setattr(recordings_module, "_get_auphonic_config", lambda: (api_key, ""))


def _install_fake_check_deps(monkeypatch, mapping: dict):
    fake_utils = MagicMock()
    fake_utils.check_dependencies_for_backend = MagicMock(return_value=mapping)
    monkeypatch.setitem(__import__("sys").modules, "clm.recordings.processing.utils", fake_utils)
    return fake_utils


class TestCheckJson:
    def test_ok_payload_exits_0(self, monkeypatch: pytest.MonkeyPatch):
        _set_backend(monkeypatch, "onnx")
        _install_fake_check_deps(
            monkeypatch,
            {"ffmpeg": "/usr/bin/ffmpeg", "onnxruntime": "1.17"},
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["check", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["backend"] == "onnx"
        assert payload["ok"] is True
        assert payload["dependencies"]["ffmpeg"] == {
            "found": True,
            "info": "/usr/bin/ffmpeg",
        }
        assert "auphonic" not in payload

    def test_missing_dependency_exits_1_with_payload(self, monkeypatch: pytest.MonkeyPatch):
        _set_backend(monkeypatch, "onnx")
        _install_fake_check_deps(monkeypatch, {"ffmpeg": None, "onnxruntime": "1.17"})

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["check", "--json"])

        assert result.exit_code == 1
        payload = _parse_json(result)
        assert payload["ok"] is False
        assert payload["dependencies"]["onnxruntime"]["found"] is True
        assert payload["dependencies"]["ffmpeg"]["found"] is False

    def test_auphonic_section_present(self, monkeypatch: pytest.MonkeyPatch):
        _set_backend(monkeypatch, "auphonic", api_key="secret")
        _install_fake_check_deps(monkeypatch, {})
        monkeypatch.setattr(
            recordings_module,
            "_build_auphonic_client",
            MagicMock(side_effect=AssertionError("offline test")),
        )

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["check", "--json", "--offline"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["auphonic"]["ok"] is True
        assert payload["dependencies"] == {}


# ---------------------------------------------------------------------------
# backends --json
# ---------------------------------------------------------------------------


class TestBackendsJson:
    def test_lists_all_backends_with_active_flag(self, monkeypatch: pytest.MonkeyPatch):
        fake_config = MagicMock()
        fake_config.processing_backend = "onnx"
        monkeypatch.setattr(recordings_module, "_build_recordings_config", lambda: fake_config)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["backends", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["active"] == "onnx"
        names = {b["name"] for b in payload["backends"]}
        assert names == {"onnx", "external", "auphonic"}
        onnx = next(b for b in payload["backends"] if b["name"] == "onnx")
        assert onnx["active"] is True
        assert onnx["synchronous"] is True
        auphonic = next(b for b in payload["backends"] if b["name"] == "auphonic")
        assert auphonic["active"] is False
        assert auphonic["requires_api_key"] is True
        assert isinstance(auphonic["features"], list)


# ---------------------------------------------------------------------------
# jobs list --json
# ---------------------------------------------------------------------------


class TestJobsListJson:
    def test_rows_are_untruncated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        manager = _install_fake_manager(monkeypatch, tmp_path)
        long_error = "x" * 120
        job = _make_job(tmp_path, error=None, message=long_error)
        manager._store_job(job)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "list", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["root"] == str(tmp_path)
        rows = payload["jobs"]
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == "aaaa-bbbb-cccc-dddd"  # full id, not [:8]
        assert row["state"] == "processing"
        assert row["progress"] == 0.0
        assert row["message"] == long_error  # untruncated
        assert row["input"] == str(tmp_path / "to-process" / "x.mp4")
        assert row["output"] == str(tmp_path / "final" / "x.mp4")
        assert row["error"] is None
        assert row["last_poll_error"] is None

    def test_all_and_limit_flags_respected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        manager._store_job(_make_job(tmp_path, "done-done-done-done", JobState.COMPLETED))
        manager._store_job(_make_job(tmp_path, "live-live-live-live", JobState.PROCESSING))
        manager._store_job(_make_job(tmp_path, "more-more-more-more", JobState.PROCESSING))

        runner = CliRunner()

        result = runner.invoke(recordings_group, ["jobs", "list", "--json"])
        assert {r["id"] for r in _parse_json(result)["jobs"]} == {
            "live-live-live-live",
            "more-more-more-more",
        }

        result = runner.invoke(recordings_group, ["jobs", "list", "--json", "--all"])
        assert len(_parse_json(result)["jobs"]) == 3

        result = runner.invoke(recordings_group, ["jobs", "list", "--json", "--limit", "1"])
        assert len(_parse_json(result)["jobs"]) == 1

    def test_empty_list_is_valid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _install_fake_manager(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "list", "--json", "--all"])
        assert result.exit_code == 0, result.output
        assert _parse_json(result)["jobs"] == []


# ---------------------------------------------------------------------------
# jobs cancel/fail --json
# ---------------------------------------------------------------------------


class TestJobsMutationsJson:
    def test_cancel_emits_updated_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        job = _make_job(tmp_path)
        manager._store_job(job)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "cancel", "aaaa", "--json"])

        assert result.exit_code == 0, result.output
        row = _parse_json(result)
        assert row["id"] == job.id
        assert row["state"] == JobState.CANCELLED.value
        assert manager.get(job.id).state == JobState.CANCELLED

    def test_fail_emits_updated_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        job = _make_job(tmp_path)
        manager._store_job(job)

        runner = CliRunner()
        result = runner.invoke(
            recordings_group, ["jobs", "fail", "aaaa", "--reason", "poll wedged", "--json"]
        )

        assert result.exit_code == 0, result.output
        row = _parse_json(result)
        assert row["state"] == "failed"
        assert row["error"] == "poll wedged"

    def test_fail_refuses_terminal_with_exit_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        manager._store_job(_make_job(tmp_path, "done-done-done-done", JobState.COMPLETED))

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "fail", "done", "--json"])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# jobs poll --json
# ---------------------------------------------------------------------------


class TestJobsPollJson:
    def test_single_tick_is_one_document(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        manager = _install_fake_manager(monkeypatch, tmp_path)
        manager._store_job(_make_job(tmp_path))

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "poll", "--json"])

        assert result.exit_code == 0, result.output
        rows = _parse_json(result)
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["id"] == "aaaa-bbbb-cccc-dddd"

    def test_no_in_flight_jobs_emits_empty_array(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _install_fake_manager(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "poll", "--json"])
        assert result.exit_code == 0, result.output
        assert _parse_json(result) == []

    def test_target_id_emits_that_jobs_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        manager._store_job(_make_job(tmp_path, "done-done-done-done", JobState.COMPLETED))

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "poll", "done", "--json"])

        assert result.exit_code == 0, result.output
        row = _parse_json(result)
        assert row["id"] == "done-done-done-done"
        assert row["state"] == "completed"

    def test_target_id_honors_watch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """--watch must apply in target mode too, not be silently ignored.

        Regression test for the review finding that ``poll ID --json
        --watch N`` polled exactly once regardless of N.
        """
        manager = _install_fake_manager(monkeypatch, tmp_path)
        manager._store_job(_make_job(tmp_path))

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "poll", "aaaa", "--json", "--watch", "3", "--interval", "0"],
        )

        assert result.exit_code == 0, result.output
        documents = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert len(documents) == 3
        assert all(d["id"] == "aaaa-bbbb-cccc-dddd" for d in documents)

    def test_watch_emits_one_document_per_tick(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        manager = _install_fake_manager(monkeypatch, tmp_path)
        manager._store_job(_make_job(tmp_path))

        runner = CliRunner()
        result = runner.invoke(
            recordings_group,
            ["jobs", "poll", "--json", "--watch", "2", "--interval", "0"],
        )

        assert result.exit_code == 0, result.output
        documents = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert len(documents) == 2


# ---------------------------------------------------------------------------
# jobs wait --json
# ---------------------------------------------------------------------------


class TestJobsWaitJson:
    def _install(self, monkeypatch, tmp_path, poll_behavior):
        from clm.recordings.workflow.backends.base import (
            BackendCapabilities,
            ProcessingBackend,
        )
        from clm.recordings.workflow.directories import ensure_root
        from clm.recordings.workflow.event_bus import EventBus
        from clm.recordings.workflow.job_manager import JobManager
        from clm.recordings.workflow.job_store import JsonFileJobStore

        class _Backend(ProcessingBackend):
            capabilities = BackendCapabilities(
                name="stub", display_name="Stub", is_synchronous=False
            )

            def accepts_file(self, path):
                return True

            def submit(self, raw_path, final_path, *, options, ctx):
                raise NotImplementedError

            def poll(self, job, *, ctx):
                return poll_behavior(job)

            def cancel(self, job, *, ctx):
                pass

        ensure_root(tmp_path)
        manager = JobManager(
            backend=_Backend(),
            root_dir=tmp_path,
            store=JsonFileJobStore(tmp_path / ".clm" / "jobs.json"),
            bus=EventBus(),
        )
        monkeypatch.setattr(recordings_module, "_make_job_manager_for_root", lambda root: manager)
        monkeypatch.setattr(
            recordings_module, "_resolve_recordings_root", lambda cli_root: tmp_path
        )
        import time as _time

        monkeypatch.setattr(_time, "sleep", lambda _: None)
        return manager

    def test_completed_emits_final_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        def complete(job):
            job.state = JobState.COMPLETED
            job.progress = 1.0
            job.message = "Done"
            return job

        manager = self._install(monkeypatch, tmp_path, complete)
        job = _make_job(tmp_path, "wait-wait-wait-wait")
        manager._store_job(job)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "wait", "wait", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["outcome"] == "completed"
        assert payload["job"]["id"] == "wait-wait-wait-wait"
        assert payload["job"]["state"] == "completed"

    def test_failed_exits_1_with_payload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        def fail(job):
            job.state = JobState.FAILED
            job.error = "broken"
            return job

        manager = self._install(monkeypatch, tmp_path, fail)
        manager._store_job(_make_job(tmp_path, "fail-fail-fail-fail"))

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "wait", "fail", "--json"])

        assert result.exit_code == 1
        payload = _parse_json(result)
        assert payload["outcome"] == "failed"
        assert payload["job"]["error"] == "broken"

    def test_timeout_exits_2_with_payload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        manager = self._install(monkeypatch, tmp_path, lambda job: job)
        manager._store_job(_make_job(tmp_path, "slow-slow-slow-slow"))

        runner = CliRunner()
        result = runner.invoke(
            recordings_group, ["jobs", "wait", "slow", "--json", "--timeout", "0"]
        )

        assert result.exit_code == 2
        payload = _parse_json(result)
        assert payload["outcome"] == "timeout"
        assert payload["job"]["state"] == "processing"

    def test_already_terminal_emits_row_exit_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from clm.recordings.workflow.jobs import JobState

        manager = self._install(monkeypatch, tmp_path, lambda job: job)
        manager._store_job(_make_job(tmp_path, "done-done-done-done", JobState.COMPLETED))

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "wait", "done", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["outcome"] == "completed"
        assert payload["job"]["state"] == "completed"

    def test_already_failed_exits_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """An already-FAILED job must exit 1, matching the in-wait failure.

        Regression test for the review finding that the already-terminal
        branch exited 0 for every state, contradicting the documented
        outcome→exit-code map (0 success / 1 failure / 2 timeout).
        """
        from clm.recordings.workflow.jobs import JobState

        manager = self._install(monkeypatch, tmp_path, lambda job: job)
        manager._store_job(_make_job(tmp_path, "dead-dead-dead-dead", JobState.FAILED))

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "wait", "dead", "--json"])

        assert result.exit_code == 1
        payload = _parse_json(result)
        assert payload["outcome"] == "failed"
        assert payload["job"]["state"] == "failed"


# ---------------------------------------------------------------------------
# jobs prune --json
# ---------------------------------------------------------------------------


class TestJobsPruneJson:
    def test_emits_deleted_count_and_rows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        dead = _make_job(tmp_path, "dead-dead-dead-dead", JobState.FAILED, error="broke")
        manager._store_job(dead)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "prune", "--yes", "--json"])

        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["pruned"] == 1
        assert payload["jobs"][0]["id"] == "dead-dead-dead-dead"
        assert manager.get(dead.id) is None

    def test_no_victims_emits_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _install_fake_manager(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "prune", "--yes", "--json"])
        assert result.exit_code == 0, result.output
        payload = _parse_json(result)
        assert payload["pruned"] == 0
        assert payload["jobs"] == []

    def test_json_without_yes_is_a_usage_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """--json without --yes fails loudly instead of prompting.

        The interactive prompt (and its input echo) cannot be kept off
        stdout, so JSON mode demands explicit --yes; a usage error is
        the loud failure an agent needs. Nothing is deleted either way.
        """
        from clm.recordings.workflow.jobs import JobState

        manager = _install_fake_manager(monkeypatch, tmp_path)
        dead = _make_job(tmp_path, "dead-dead-dead-dead", JobState.FAILED)
        manager._store_job(dead)

        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "prune", "--json"])

        assert result.exit_code == 2, result.output
        assert "--json requires --yes" in result.output
        assert result.stdout.strip() == ""  # no JSON document, no prompt on stdout
        assert manager.get(dead.id) is not None  # nothing deleted

    def test_json_with_no_victims_needs_no_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """With nothing to delete there is no prompt, so --json alone is fine."""
        _install_fake_manager(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(recordings_group, ["jobs", "prune", "--json"])
        assert result.exit_code == 0, result.output
        assert _parse_json(result) == {"pruned": 0, "jobs": []}
