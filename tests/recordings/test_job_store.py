"""Tests for :class:`JsonFileJobStore`."""

from __future__ import annotations

import json
from pathlib import Path

from clm.recordings.workflow.job_store import JsonFileJobStore
from clm.recordings.workflow.jobs import JobState, ProcessingJob


def _make_job(tmp_path: Path, **overrides: object) -> ProcessingJob:
    """Minimal valid ProcessingJob for tests."""
    kwargs: dict[str, object] = {
        "backend_name": "onnx",
        "raw_path": tmp_path / "lecture--RAW.mp4",
        "final_path": tmp_path / "final" / "lecture.mp4",
        "relative_dir": Path("py/week01"),
    }
    kwargs.update(overrides)
    return ProcessingJob(**kwargs)  # type: ignore[arg-type]


class TestJsonFileJobStoreBasics:
    def test_missing_file_starts_empty(self, tmp_path: Path):
        store = JsonFileJobStore(tmp_path / ".clm" / "jobs.json")
        assert store.load_all() == []

    def test_save_and_load_all(self, tmp_path: Path):
        path = tmp_path / ".clm" / "jobs.json"
        store = JsonFileJobStore(path)

        job = _make_job(tmp_path, progress=0.5, message="Halfway")
        store.save(job)

        assert path.exists()
        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0].id == job.id
        assert loaded[0].progress == 0.5
        assert loaded[0].message == "Halfway"

    def test_save_replaces_existing_by_id(self, tmp_path: Path):
        store = JsonFileJobStore(tmp_path / "jobs.json")

        job = _make_job(tmp_path)
        store.save(job)

        job.state = JobState.COMPLETED
        job.progress = 1.0
        store.save(job)

        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0].state == JobState.COMPLETED
        assert loaded[0].progress == 1.0

    def test_multiple_jobs(self, tmp_path: Path):
        store = JsonFileJobStore(tmp_path / "jobs.json")

        a = _make_job(tmp_path, backend_name="onnx")
        b = _make_job(tmp_path, backend_name="auphonic")
        store.save(a)
        store.save(b)

        loaded_by_id = {j.id: j for j in store.load_all()}
        assert len(loaded_by_id) == 2
        assert loaded_by_id[a.id].backend_name == "onnx"
        assert loaded_by_id[b.id].backend_name == "auphonic"

    def test_delete(self, tmp_path: Path):
        store = JsonFileJobStore(tmp_path / "jobs.json")
        a = _make_job(tmp_path)
        b = _make_job(tmp_path)
        store.save(a)
        store.save(b)

        store.delete(a.id)

        remaining = store.load_all()
        assert len(remaining) == 1
        assert remaining[0].id == b.id

    def test_delete_unknown_id_is_noop(self, tmp_path: Path):
        store = JsonFileJobStore(tmp_path / "jobs.json")
        store.save(_make_job(tmp_path))
        store.delete("does-not-exist")
        assert len(store.load_all()) == 1


class TestJsonFileJobStorePersistence:
    def test_survives_fresh_instance(self, tmp_path: Path):
        """A new store reading the same file sees previously saved jobs."""
        path = tmp_path / ".clm" / "jobs.json"
        store1 = JsonFileJobStore(path)
        job = _make_job(tmp_path, backend_ref="abc-123")
        store1.save(job)

        store2 = JsonFileJobStore(path)
        loaded = store2.load_all()
        assert len(loaded) == 1
        assert loaded[0].id == job.id
        assert loaded[0].backend_ref == "abc-123"

    def test_corrupt_file_loads_empty(self, tmp_path: Path):
        """A garbled JSON file should not crash the store."""
        path = tmp_path / "jobs.json"
        path.write_text("{not valid json", encoding="utf-8")

        store = JsonFileJobStore(path)
        assert store.load_all() == []

        # Can still save new jobs, which replaces the corrupt file.
        job = _make_job(tmp_path)
        store.save(job)
        assert len(store.load_all()) == 1

    def test_skips_invalid_entries(self, tmp_path: Path):
        """Valid + invalid entries coexist; invalid ones are skipped."""
        path = tmp_path / "jobs.json"
        valid = _make_job(tmp_path)
        payload = {
            "version": 1,
            "jobs": [
                valid.model_dump(mode="json"),
                {"backend_name": "bogus"},  # missing required fields
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        store = JsonFileJobStore(path)
        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0].id == valid.id

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path: Path):
        path = tmp_path / "jobs.json"
        store = JsonFileJobStore(path)
        store.save(_make_job(tmp_path))

        tmp_file = path.with_suffix(path.suffix + ".tmp")
        assert not tmp_file.exists()

    def test_serialized_payload_is_versioned_dict(self, tmp_path: Path):
        path = tmp_path / "jobs.json"
        store = JsonFileJobStore(path)
        store.save(_make_job(tmp_path))

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        assert raw["version"] == 1
        assert isinstance(raw["jobs"], list)
        assert len(raw["jobs"]) == 1
