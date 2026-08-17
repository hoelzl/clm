"""Spec-driven write containment: layer 2 (destructive-op ownership gate).

Adversarial-review finding **S11** (umbrella #798). ``sweep_stray_files``
and the ``--clean`` wipe delete everything they find under the build's
output roots. Layer 1 (``tests/core/test_spec_write_containment.py``)
stops a spec from *pointing* those operations at the course sources;
this layer stops them from running at all in a directory CLM cannot
prove it owns.

A root is CLM-owned when it was empty or absent at build start, when it
carries a ``.clm-manifest.json`` provenance index (written by default
since #295), or — for the sweep, whose registry describes the whole
intended tree — when the walk finds nothing that was not written by this
build. Anything else is refused with the directory named; the operator's
escape hatch is the explicit ``--allow-unowned-output`` flag, never
``--clean`` (which is the dangerous operation being gated).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clm.build.output_ownership import snapshot_output_ownership
from clm.build.output_sweep import sweep_stray_files
from clm.core.image_registry import ImageRegistry
from clm.core.output_write_registry import OutputWriteRegistry
from clm.core.provenance_manifest import MANIFEST_FILENAME


def _make_file(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _mark(directory: Path) -> Path:
    """Write a provenance manifest marking *directory* as CLM-owned."""
    directory.mkdir(parents=True, exist_ok=True)
    return _make_file(directory / MANIFEST_FILENAME, b"{}")


def _config(tmp_path: Path, **overrides):
    """A minimal ``BuildConfig``; overrides win over the defaults."""
    from clm.build.config import BuildConfig

    defaults: dict = {
        "spec_file": tmp_path / "spec.xml",
        "data_dir": tmp_path / "data",
        "output_dir": tmp_path / "out",
        "log_level": "INFO",
        "cache_db_path": tmp_path / "cache.db",
        "jobs_db_path": tmp_path / "jobs.db",
        "ignore_cache": False,
        "clear_cache": False,
        "watch": False,
        "print_correlation_ids": False,
        "workers": None,
        "notebook_workers": None,
        "plantuml_workers": None,
        "drawio_workers": None,
        "notebook_image": None,
        "plantuml_image": None,
        "drawio_image": None,
    }
    defaults.update(overrides)
    return BuildConfig(**defaults)


@pytest.fixture
def registry() -> OutputWriteRegistry:
    return OutputWriteRegistry()


class TestOwnershipSnapshot:
    def test_missing_root_is_owned(self, tmp_path: Path) -> None:
        ownership = snapshot_output_ownership([tmp_path / "nope"])
        assert ownership.is_owned(tmp_path / "nope")
        assert ownership.unowned_roots == ()

    def test_empty_root_is_owned(self, tmp_path: Path) -> None:
        """The ``--snapshot`` / ``--verify-against`` case.

        Those flows suppress the provenance manifest, so the empty-at-
        start rule is the only evidence they can offer — and ``--snapshot``
        already refuses a non-empty target directory.
        """
        root = tmp_path / "snapshot-baseline"
        root.mkdir()
        assert snapshot_output_ownership([root]).is_owned(root)

    def test_populated_unmarked_root_is_not_owned(self, tmp_path: Path) -> None:
        _make_file(tmp_path / "notes.txt")
        ownership = snapshot_output_ownership([tmp_path])
        assert not ownership.is_owned(tmp_path)
        assert ownership.unowned_roots == (tmp_path,)

    def test_manifest_in_the_root_marks_it_owned(self, tmp_path: Path) -> None:
        _make_file(tmp_path / "notes.txt")
        _mark(tmp_path)
        assert snapshot_output_ownership([tmp_path]).is_owned(tmp_path)

    def test_manifest_in_a_declared_target_root_marks_children_owned(self, tmp_path: Path) -> None:
        """The manifest lives at the *target* root; the swept roots are deeper."""
        target_root = tmp_path / "output" / "shared"
        root = target_root / "course-en"
        _make_file(root / "notes.txt")
        _mark(target_root)
        ownership = snapshot_output_ownership([root], manifest_roots=[target_root])
        assert ownership.is_owned(root)

    def test_manifest_outside_the_declared_roots_does_not_count(self, tmp_path: Path) -> None:
        """No unbounded walk upwards: a manifest in some ancestor that is
        not a declared output target root proves nothing about this root."""
        _mark(tmp_path)
        root = tmp_path / "documents" / "important"
        _make_file(root / "thesis.txt")
        ownership = snapshot_output_ownership([root], manifest_roots=[tmp_path / "output"])
        assert not ownership.is_owned(root)

    def test_reason_names_the_evidence(self, tmp_path: Path) -> None:
        _make_file(tmp_path / "notes.txt")
        reason = snapshot_output_ownership([tmp_path]).reason_for(tmp_path)
        assert reason and "notes.txt" not in reason
        assert MANIFEST_FILENAME in reason


class TestSweepRefusesUnownedRoots:
    def test_stray_file_in_unowned_root_is_not_deleted(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        stray = _make_file(tmp_path / "precious.txt")
        report = sweep_stray_files([tmp_path], registry, unowned_roots=[tmp_path])
        assert stray.exists()
        assert report.deleted_files == []
        assert report.refused_roots == [tmp_path]

    def test_unowned_root_with_only_expected_files_is_swept_silently(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        """Registry evidence: nothing unexpected on disk ⇒ nothing to refuse."""
        written = _make_file(tmp_path / "section" / "lecture.html")
        registry.record_write(written, content=b"x", source=written)
        report = sweep_stray_files([tmp_path], registry, unowned_roots=[tmp_path])
        assert written.exists()
        assert report.refused_roots == []

    def test_owned_root_still_sweeps(self, tmp_path: Path, registry: OutputWriteRegistry) -> None:
        stray = _make_file(tmp_path / "stale.html")
        report = sweep_stray_files([tmp_path], registry)
        assert not stray.exists()
        assert report.deleted_files == [stray]
        assert report.refused_roots == []

    def test_refusal_is_per_root(self, tmp_path: Path, registry: OutputWriteRegistry) -> None:
        owned = tmp_path / "owned"
        unowned = tmp_path / "unowned"
        owned_stray = _make_file(owned / "stale.html")
        kept = _make_file(unowned / "precious.txt")

        report = sweep_stray_files([owned, unowned], registry, unowned_roots=[unowned])

        assert not owned_stray.exists()
        assert kept.exists()
        assert report.refused_roots == [unowned]

    def test_dry_run_never_deletes_in_a_refused_root(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        stray = _make_file(tmp_path / "precious.txt")
        report = sweep_stray_files([tmp_path], registry, unowned_roots=[tmp_path], dry_run=True)
        assert stray.exists()
        assert report.deleted_files == []
        assert report.refused_roots == [tmp_path]

    def test_empty_dirs_survive_a_refusal(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        """A refused root keeps its directory structure, not just its files."""
        _make_file(tmp_path / "precious.txt")
        stale_dir = tmp_path / "old-section"
        stale_dir.mkdir()
        sweep_stray_files([tmp_path], registry, unowned_roots=[tmp_path])
        assert stale_dir.exists()


class TestSweepRegressionsUnderPlanThenExecute:
    """The refusal needs a plan-then-execute walk; keep the old contract."""

    def test_nested_git_subtree_is_skipped(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        nested = tmp_path / "vendored"
        (nested / ".git").mkdir(parents=True)
        inner = _make_file(nested / "file.txt")
        report = sweep_stray_files([tmp_path], registry)
        assert inner.exists()
        assert report.skipped_subtrees == [nested]

    def test_git_dir_at_root_survives(self, tmp_path: Path, registry: OutputWriteRegistry) -> None:
        git_file = _make_file(tmp_path / ".git" / "HEAD")
        sweep_stray_files([tmp_path], registry)
        assert git_file.exists()

    def test_emptied_directories_are_removed(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        stale = _make_file(tmp_path / "old" / "stale.html")
        report = sweep_stray_files([tmp_path], registry)
        assert not stale.exists()
        assert tmp_path / "old" in report.removed_dirs
        assert not (tmp_path / "old").exists()


class TestEnforceOwnedRoots:
    def test_raises_naming_the_directory_and_the_remedy(self, tmp_path: Path) -> None:
        from clm.build.errors import UnownedOutputRootError
        from clm.build.output_ownership import enforce_owned_roots

        _make_file(tmp_path / "precious.txt")
        ownership = snapshot_output_ownership([tmp_path])

        with pytest.raises(UnownedOutputRootError) as excinfo:
            enforce_owned_roots(ownership, operation="--clean", allow_unowned=False)

        message = str(excinfo.value)
        assert str(tmp_path) in message
        assert "--allow-unowned-output" in message

    def test_override_flag_permits_the_operation(self, tmp_path: Path) -> None:
        from clm.build.output_ownership import enforce_owned_roots

        _make_file(tmp_path / "precious.txt")
        ownership = snapshot_output_ownership([tmp_path])
        enforce_owned_roots(ownership, operation="--clean", allow_unowned=True)

    def test_owned_roots_pass(self, tmp_path: Path) -> None:
        from clm.build.output_ownership import enforce_owned_roots

        ownership = snapshot_output_ownership([tmp_path / "fresh"])
        enforce_owned_roots(ownership, operation="--clean", allow_unowned=False)


class TestSweepGateWiring:
    """``_maybe_run_sweep`` forwards the snapshot's unowned roots."""

    def _spy_sweep(self, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        from clm.build import output_sweep as sweep_module

        calls: list[dict] = []

        def recorder(root_dirs, registry, image_registry=None, *, unowned_roots=(), **kwargs):
            calls.append({"root_dirs": list(root_dirs), "unowned_roots": tuple(unowned_roots)})
            return sweep_module.SweepReport()

        monkeypatch.setattr(sweep_module, "sweep_stray_files", recorder)
        return calls

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config_overrides):
        from clm.build.engine import _maybe_run_sweep

        calls = self._spy_sweep(monkeypatch)
        config = _config(tmp_path, clean=False, sweep=True, **config_overrides)
        _maybe_run_sweep(
            config=config,
            root_dirs=[tmp_path / "roots"],
            backend=SimpleNamespace(
                output_write_registry=OutputWriteRegistry(),
                image_registry=ImageRegistry(),
            ),
            build_reporter=MagicMock(errors=[]),
            only_sections_mode=False,
            ownership=snapshot_output_ownership([tmp_path / "roots"]),
        )
        return calls

    def test_unowned_root_is_forwarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "roots"
        _make_file(root / "precious.txt")
        calls = self._run(tmp_path, monkeypatch)
        assert calls[0]["unowned_roots"] == (root,)

    def test_owned_root_forwards_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The marked-tree rebuild: identical to the pre-gate behavior."""
        root = tmp_path / "roots"
        _make_file(root / "lecture.html")
        _mark(root)
        calls = self._run(tmp_path, monkeypatch)
        assert calls[0]["unowned_roots"] == ()

    def test_override_flag_disables_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "roots"
        _make_file(root / "precious.txt")
        calls = self._run(tmp_path, monkeypatch, allow_unowned_output=True)
        assert calls[0]["unowned_roots"] == ()


class TestCleanWipeIsGated:
    """Wiring proof: ``--clean`` consults the gate before deleting."""

    def _course(self):
        course = MagicMock()
        course.output_targets = []
        course.count_jupyterlite_operations.return_value = 0
        return course

    def _backend(self):
        return SimpleNamespace(
            output_write_registry=OutputWriteRegistry(),
            image_registry=ImageRegistry(),
        )

    def test_clean_refuses_and_deletes_nothing(self, tmp_path: Path) -> None:
        from clm.build.engine import process_course_with_backend
        from clm.build.errors import UnownedOutputRootError

        root = tmp_path / "documents"
        precious = _make_file(root / "thesis.txt")

        with pytest.raises(UnownedOutputRootError):
            asyncio.run(
                process_course_with_backend(
                    self._course(),
                    [root],
                    self._backend(),
                    _config(tmp_path, clean=True),
                    0.0,
                    MagicMock(errors=[]),
                )
            )

        assert precious.exists()
