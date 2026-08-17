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


class TestOwnershipDoesNotSelfAuthorize:
    """A build must not hand the next one permission to delete.

    The provenance manifest *is* the ownership evidence. Writing it into
    a root clm could not verify meant: build 1 declines to delete (exit 0,
    stale files kept) and writes the manifest; build 2 sees an "owned"
    root and deletes everything the build did not write. The end-to-end
    proof — two real builds, including the ``--no-sweep`` and
    ``--incremental`` paths where no sweep runs at all — is in
    ``tests/build/test_output_ownership_e2e.py``; these pin the seams.
    """

    def test_the_snapshot_verdict_is_recorded_before_any_sweep_decision(
        self, tmp_path: Path
    ) -> None:
        """Keyed on the evidence, not on a sweep having refused.

        ``--no-sweep`` / ``--incremental`` / a build with errors never
        reach a refusal, so keying the manifest suppression on one left
        exactly those flows marking an unverified tree as clm's.
        """
        from clm.build.engine import _record_unowned_roots

        root = tmp_path / "documents"
        _make_file(root / "thesis.txt")
        config = _config(tmp_path, sweep=False)

        _record_unowned_roots(config, snapshot_output_ownership([root]))

        assert config.unowned_output_roots == (root,)

    def test_the_override_flag_adopts_the_roots(self, tmp_path: Path) -> None:
        from clm.build.engine import _record_unowned_roots

        root = tmp_path / "documents"
        _make_file(root / "thesis.txt")
        config = _config(tmp_path, allow_unowned_output=True)

        _record_unowned_roots(config, snapshot_output_ownership([root]))

        assert config.unowned_output_roots == ()

    def test_a_sweep_that_clears_a_root_adopts_it(self, tmp_path: Path) -> None:
        """Registry evidence: the sweep found nothing unexpected.

        That is the evidence the snapshot could not offer, so the root
        gets its manifest after all — otherwise a tree whose contents the
        build fully accounts for would stay unverified forever.
        """
        from clm.build.engine import _maybe_run_sweep, _record_unowned_roots

        root = tmp_path / "output" / "course-en"
        written = _make_file(root / "lecture.html")
        registry = OutputWriteRegistry()
        registry.record_write(written, content=b"x", source=written)

        config = _config(tmp_path, sweep=True)
        ownership = snapshot_output_ownership([root])
        _record_unowned_roots(config, ownership)
        assert config.unowned_output_roots == (root,)

        _maybe_run_sweep(
            config=config,
            root_dirs=[root],
            backend=SimpleNamespace(output_write_registry=registry, image_registry=ImageRegistry()),
            build_reporter=MagicMock(errors=[]),
            only_sections_mode=False,
            ownership=ownership,
        )

        assert config.unowned_output_roots == ()

    def test_a_sweep_that_could_not_look_does_not_adopt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreadable tree plans exactly like a clean one — empty.

        Clearing the root on that basis would mark it as clm's without
        anything having seen it, which is the one asymmetry left in the
        evidence logic after the round-2 fix.
        """
        import os

        from clm.build.engine import _maybe_run_sweep, _record_unowned_roots

        root = tmp_path / "output" / "course-en"
        _make_file(root / "unreadable.html")
        config = _config(tmp_path, sweep=True)
        ownership = snapshot_output_ownership([root])
        _record_unowned_roots(config, ownership)

        real_scandir = os.scandir

        def refuse_scandir(path, *args, **kwargs):
            if Path(path) == root:
                raise PermissionError(13, "Permission denied")
            return real_scandir(path, *args, **kwargs)

        monkeypatch.setattr("clm.build.output_sweep.os.scandir", refuse_scandir)

        _maybe_run_sweep(
            config=config,
            root_dirs=[root],
            backend=SimpleNamespace(
                output_write_registry=OutputWriteRegistry(),
                image_registry=ImageRegistry(),
            ),
            build_reporter=MagicMock(errors=[]),
            only_sections_mode=False,
            ownership=ownership,
        )

        assert config.unowned_output_roots == (root,)

    def test_a_refused_sweep_keeps_the_root_unowned(self, tmp_path: Path) -> None:
        from clm.build.engine import _maybe_run_sweep, _record_unowned_roots

        root = tmp_path / "documents"
        _make_file(root / "thesis.txt")
        config = _config(tmp_path, sweep=True)
        ownership = snapshot_output_ownership([root])
        _record_unowned_roots(config, ownership)

        _maybe_run_sweep(
            config=config,
            root_dirs=[root],
            backend=SimpleNamespace(
                output_write_registry=OutputWriteRegistry(),
                image_registry=ImageRegistry(),
            ),
            build_reporter=MagicMock(errors=[]),
            only_sections_mode=False,
            ownership=ownership,
        )

        assert config.unowned_output_roots == (root,)

    def test_target_root_over_an_unowned_root_is_skipped(self, tmp_path: Path) -> None:
        from clm.build.engine import _manifest_roots_to_skip

        target_root = tmp_path / "output" / "shared"
        other_root = tmp_path / "output" / "trainer"
        course = SimpleNamespace(
            output_targets=[
                SimpleNamespace(output_root=target_root),
                SimpleNamespace(output_root=other_root),
            ]
        )
        config = _config(tmp_path, unowned_output_roots=(target_root / "course-en",))

        # Only the unverified tier loses its manifest — suppressing every
        # target would leave the healthy ones unmarked, and they would be
        # refused on the next build in turn.
        assert _manifest_roots_to_skip(course, config) == {target_root}

    def test_only_the_closest_covering_target_is_skipped(self, tmp_path: Path) -> None:
        """Nested targets: an outer tree that swept cleanly keeps its own."""
        from clm.build.engine import _manifest_roots_to_skip

        outer = tmp_path / "output"
        inner = tmp_path / "output" / "students"
        course = SimpleNamespace(
            output_targets=[
                SimpleNamespace(output_root=outer),
                SimpleNamespace(output_root=inner),
            ]
        )
        config = _config(tmp_path, unowned_output_roots=(inner / "course-en",))

        assert _manifest_roots_to_skip(course, config) == {inner}

    def test_nothing_unowned_skips_nothing(self, tmp_path: Path) -> None:
        from clm.build.engine import _manifest_roots_to_skip

        course = SimpleNamespace(output_targets=[SimpleNamespace(output_root=tmp_path / "out")])
        assert _manifest_roots_to_skip(course, _config(tmp_path)) == set()

    def test_write_provenance_manifests_honours_skip_roots(self, tmp_path: Path) -> None:
        from clm.core.provenance_manifest import write_provenance_manifests

        out_root = tmp_path / "output" / "shared"
        out_root.mkdir(parents=True)
        course = MagicMock()
        course.output_targets = [SimpleNamespace(output_root=out_root)]

        written = write_provenance_manifests(
            course,
            source_commit=None,
            source_dirty=None,
            built_at="2026-08-18T00:00:00+00:00",
            skip_roots=[out_root],
        )

        assert written == []
        assert not (out_root / MANIFEST_FILENAME).exists()


class TestOwnershipSnapshot:
    def test_missing_root_is_owned(self, tmp_path: Path) -> None:
        ownership = snapshot_output_ownership([tmp_path / "nope"])
        assert ownership.is_owned(tmp_path / "nope")
        assert ownership.unowned_roots == ()

    def test_empty_root_is_owned(self, tmp_path: Path) -> None:
        """The ``--snapshot`` case.

        ``--snapshot DIR`` builds into ``DIR`` and already refuses a
        non-empty one (``clm build`` entry point), so the empty-at-start
        rule is the evidence it needs — which is the only evidence it
        *can* offer, since the flow suppresses the provenance manifest.

        ``--verify-against`` is deliberately not covered here: it builds
        into the regular output tree, so it is gated exactly like any
        other build (see the migration note).
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

    def test_a_manifest_directory_is_not_evidence(self, tmp_path: Path) -> None:
        """``mkdir .clm-manifest.json`` must not authorize deletion."""
        _make_file(tmp_path / "notes.txt")
        (tmp_path / MANIFEST_FILENAME).mkdir()
        assert not snapshot_output_ownership([tmp_path]).is_owned(tmp_path)

    def test_a_root_that_is_a_file_is_left_alone(self, tmp_path: Path) -> None:
        """Neither operation deletes through a non-directory root.

        The sweep skips it with a warning and ``rmtree(..., ignore_errors)``
        is a no-op on a file, so refusing would turn a pre-existing broken
        setup into a new hard build failure.
        """
        root = _make_file(tmp_path / "not-a-dir")
        ownership = snapshot_output_ownership([root])
        assert ownership.is_owned(root)
        assert "not a directory" in (ownership.evidence_for(root) or "")

    def test_an_unreadable_root_is_not_owned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Path.exists()`` swallows a permission error and answers False.

        Reporting "did not exist at build start" for a root that is there
        but unreadable would call it owned — exactly backwards.
        """
        root = tmp_path / "locked"
        root.mkdir()
        real_stat = Path.stat

        def fake_stat(self: Path, *args, **kwargs):
            if self == root:
                raise PermissionError(13, "Permission denied")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", fake_stat)
        ownership = snapshot_output_ownership([root])
        assert not ownership.is_owned(root)
        assert "inspect" in (ownership.reason_for(root) or "")

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

    def test_a_repeated_root_is_refused_once(
        self, tmp_path: Path, registry: OutputWriteRegistry
    ) -> None:
        """``root_dirs`` legitimately repeats a directory.

        An explicit target whose kinds span the public and the private
        branch derives the same path twice, and reporting one directory
        as two refused roots is a lie about the damage.
        """
        _make_file(tmp_path / "precious.txt")
        report = sweep_stray_files([tmp_path, tmp_path], registry, unowned_roots=[tmp_path])
        assert report.refused_roots == [tmp_path]

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

    def test_cli_renders_the_refusal_instead_of_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``main_build`` converts the engine's typed refusal.

        Without the conversion the carefully worded remedy is buried
        under a raw Python traceback — Click only renders
        ``ClickException``/``Abort`` itself.
        """
        import click

        from clm.build.errors import UnownedOutputRootError
        from clm.cli.commands import build as build_module

        async def fake_run_build(config, **kwargs):
            raise UnownedOutputRootError("--clean would delete …; pass --allow-unowned-output")

        monkeypatch.setattr(build_module, "run_build", fake_run_build)
        spec_file = tmp_path / "spec.xml"
        spec_file.write_text("<course/>", encoding="utf-8")

        with pytest.raises(click.ClickException, match="allow-unowned-output"):
            asyncio.run(
                build_module.main_build(
                    None,
                    spec_file,
                    tmp_path / "data",
                    tmp_path / "out",
                    False,  # watch
                    "fast",  # watch_mode
                    0.3,  # debounce
                    False,  # print_correlation_ids
                    "INFO",  # log_level
                    tmp_path / "cache.db",
                    tmp_path / "jobs.db",
                    False,  # ignore_cache
                    False,  # clear_cache
                    True,  # clean
                    False,  # incremental
                    False,  # no_sweep
                    (),  # only_sections
                    None,  # workers
                    None,  # notebook_workers
                    None,  # plantuml_workers
                    None,  # drawio_workers
                    None,  # max_workers
                    None,  # notebook_image
                    None,  # plantuml_image
                    None,  # drawio_image
                    "default",  # output_mode
                    False,  # no_progress
                    False,  # no_color
                    False,  # verbose_logging
                    None,  # language
                    False,  # speaker_only
                    None,  # targets
                    False,  # force_execute
                    "disabled",  # http_replay
                    "duplicated",  # image_mode
                    "png",  # image_format
                    False,  # inline_images
                )
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
