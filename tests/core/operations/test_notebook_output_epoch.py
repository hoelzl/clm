"""Guard for ``NOTEBOOK_OUTPUT_EPOCH`` (issue #1109).

The notebook cache keys carry an explicit output epoch instead of the clm
release version, so an output-neutral release keeps every cache warm. The
price is that a change which *does* alter notebook output must bump the
epoch by hand. This test makes that decision unavoidable: it fails whenever
a guarded source differs from the committed snapshot.
"""

import importlib.util
import json
from pathlib import Path

from clm.core.operations import process_notebook
from clm.core.operations.process_notebook import (
    NOTEBOOK_OUTPUT_EPOCH,
    NOTEBOOK_OUTPUT_SOURCES,
    _hash_template_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_PATH = Path(__file__).with_name("notebook_output_snapshot.json")
SRC_ROOT = Path(process_notebook.__file__).resolve().parents[3]


def _load_update_script():
    spec = importlib.util.spec_from_file_location(
        "update_notebook_output_snapshot",
        REPO_ROOT / "scripts" / "update_notebook_output_snapshot.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


class TestNotebookOutputEpochGuard:
    def test_guarded_sources_match_snapshot(self):
        current = _load_update_script().current_source_hashes(SRC_ROOT)
        recorded = _snapshot()["sources"]
        changed = sorted(p for p in current.keys() & recorded.keys() if current[p] != recorded[p])
        added = sorted(current.keys() - recorded.keys())
        removed = sorted(recorded.keys() - current.keys())
        assert not (changed or added or removed), (
            "Sources that shape notebook output changed "
            f"(changed={changed}, added={added}, removed={removed}).\n"
            "If the change can alter executed or rendered notebook output, bump "
            "NOTEBOOK_OUTPUT_EPOCH in src/clm/core/operations/process_notebook.py "
            "(this invalidates every notebook cache once). Otherwise leave it.\n"
            "Either way, then run: python scripts/update_notebook_output_snapshot.py"
        )

    def test_snapshot_epoch_matches_constant(self):
        assert _snapshot()["epoch"] == NOTEBOOK_OUTPUT_EPOCH, (
            "NOTEBOOK_OUTPUT_EPOCH changed without refreshing the snapshot; run "
            "python scripts/update_notebook_output_snapshot.py"
        )

    def test_every_pattern_matches_something(self):
        for pattern in NOTEBOOK_OUTPUT_SOURCES:
            assert any(SRC_ROOT.glob(pattern)), f"{pattern} matches no file"


class TestFingerprintIgnoresReleaseVersion:
    def test_release_version_does_not_change_fingerprint(self, monkeypatch, tmp_path):
        import clm

        before = _hash_template_dir("python", tmp_path)
        monkeypatch.setattr(clm, "__version__", "999.0.0")
        assert _hash_template_dir("python", tmp_path) == before

    def test_epoch_changes_fingerprint(self, monkeypatch, tmp_path):
        before = _hash_template_dir("python", tmp_path)
        monkeypatch.setattr(process_notebook, "NOTEBOOK_OUTPUT_EPOCH", NOTEBOOK_OUTPUT_EPOCH + 1)
        assert _hash_template_dir("python", tmp_path) != before
