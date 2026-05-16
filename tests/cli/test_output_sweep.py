"""Tests for the post-build stray-file sweep.

The sweep removes files under each output root that the build did not
record in either the :class:`OutputWriteRegistry` or the
:class:`ImageRegistry`. The protected-paths surface is intentionally
small (``.git/**`` only) so most of these tests focus on the boundary
between "registry-tracked" and "stray".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from clm.cli.output_sweep import (
    DEFAULT_KEEP_PATTERNS,
    SweepReport,
    sweep_stray_files,
)
from clm.core.image_registry import ImageRegistry
from clm.core.output_write_registry import OutputWriteRegistry


def _record(registry: OutputWriteRegistry, path: Path, content: bytes = b"x") -> None:
    """Convenience: record ``path`` in the write registry with given bytes."""
    registry.record_write(path, content=content, source=path)


def _make_file(path: Path, content: bytes = b"x") -> Path:
    """Create the file (and parents) with the given content; return path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _can_symlink_on_windows() -> bool:
    """Probe whether the current process can create a symlink.

    On Windows, ``os.symlink`` requires elevated privileges or
    Developer Mode. The probe creates and deletes a tiny test
    symlink; failures are silently treated as "cannot symlink".
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "_t"
        link = tmp_path / "_l"
        target.write_bytes(b"")
        try:
            link.symlink_to(target)
            link.unlink()
            return True
        except OSError:
            return False


@pytest.fixture
def empty_registry() -> OutputWriteRegistry:
    return OutputWriteRegistry()


@pytest.fixture
def empty_image_registry() -> ImageRegistry:
    return ImageRegistry()


class TestSweepDefaults:
    """Cheapest happy paths — empty / single-file trees."""

    def test_empty_registry_and_empty_tree_is_noop(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        report = sweep_stray_files([tmp_path], empty_registry)
        assert isinstance(report, SweepReport)
        assert report.deleted_files == []
        assert report.removed_dirs == []
        assert report.kept_due_to_pattern == 0
        assert not report.skipped

    def test_registry_path_present_is_kept(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        kept = _make_file(tmp_path / "section" / "lecture_01.html")
        _record(empty_registry, kept)
        report = sweep_stray_files([tmp_path], empty_registry)
        assert kept.exists()
        assert report.deleted_files == []
        assert report.removed_dirs == []

    def test_default_keep_patterns_is_only_git(self):
        assert DEFAULT_KEEP_PATTERNS == (".git/**",)


class TestStrayFileRemoval:
    """The core behavior: registry-absent files get deleted."""

    def test_stray_file_at_root_is_deleted(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        stray = _make_file(tmp_path / "abandoned.html")
        report = sweep_stray_files([tmp_path], empty_registry)
        assert not stray.exists()
        assert stray in report.deleted_files

    def test_stray_file_in_subdir_is_deleted(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        stray = _make_file(tmp_path / "old_section" / "lecture_99.html")
        report = sweep_stray_files([tmp_path], empty_registry)
        assert not stray.exists()
        assert stray in report.deleted_files

    def test_mixed_kept_and_stray(self, tmp_path: Path, empty_registry: OutputWriteRegistry):
        kept = _make_file(tmp_path / "section" / "current.html")
        stray = _make_file(tmp_path / "section" / "obsolete.html")
        _record(empty_registry, kept)
        report = sweep_stray_files([tmp_path], empty_registry)
        assert kept.exists()
        assert not stray.exists()
        assert report.deleted_files == [stray]

    def test_auxiliary_files_at_root_are_swept(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        """``.gitignore``/``README.md`` at root must be swept — the
        principle is that the output tree is exclusively CLM's."""
        gitignore = _make_file(tmp_path / ".gitignore", b"*.pyc\n")
        readme = _make_file(tmp_path / "README.md", b"hand-edited")
        report = sweep_stray_files([tmp_path], empty_registry)
        assert not gitignore.exists()
        assert not readme.exists()
        assert gitignore in report.deleted_files
        assert readme in report.deleted_files


class TestGitDirectoryPreservation:
    """``.git`` and its contents are never touched."""

    def test_git_directory_at_root_is_preserved(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        git_dir = tmp_path / ".git"
        git_config = _make_file(git_dir / "config", b"[core]")
        objects = _make_file(git_dir / "objects" / "ab" / "cd1234", b"blob")
        sweep_stray_files([tmp_path], empty_registry)
        assert git_dir.is_dir()
        assert git_config.exists()
        assert objects.exists()

    def test_nested_git_subtree_is_preserved(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        """A directory containing a nested ``.git/`` is treated as
        opaque — every file inside survives, even those the sweep
        would otherwise delete."""
        nested_root = tmp_path / "vendored_repo"
        _make_file(nested_root / ".git" / "HEAD", b"ref: refs/heads/main\n")
        nested_file = _make_file(nested_root / "src" / "file.py", b"print()")
        unrelated_stray = _make_file(tmp_path / "other_stray.txt")

        report = sweep_stray_files([tmp_path], empty_registry)
        assert nested_root.is_dir()
        assert nested_file.exists()
        assert not unrelated_stray.exists()
        assert nested_root in report.skipped_subtrees

    def test_git_directory_does_not_block_sibling_sweep(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        _make_file(tmp_path / ".git" / "config", b"[core]")
        stray = _make_file(tmp_path / "section" / "stray.html")
        report = sweep_stray_files([tmp_path], empty_registry)
        assert not stray.exists()
        assert (tmp_path / ".git" / "config").exists()
        assert stray in report.deleted_files


class TestEmptyDirectoryCleanup:
    """Directories left empty by the sweep are removed bottom-up."""

    def test_empty_dir_after_all_files_deleted_is_removed(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        section_dir = tmp_path / "old_section"
        _make_file(section_dir / "a.html")
        _make_file(section_dir / "b.html")
        report = sweep_stray_files([tmp_path], empty_registry)
        assert not section_dir.exists()
        assert section_dir in report.removed_dirs

    def test_nested_empty_dirs_removed_bottom_up(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        deep = tmp_path / "outer" / "inner" / "deepest"
        _make_file(deep / "a.html")
        report = sweep_stray_files([tmp_path], empty_registry)
        assert not deep.exists()
        assert not deep.parent.exists()
        assert not deep.parent.parent.exists()
        # All three intermediate directories should be reported as removed.
        assert len(report.removed_dirs) == 3

    def test_root_directory_itself_is_never_removed(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        _make_file(tmp_path / "stray.html")
        sweep_stray_files([tmp_path], empty_registry)
        assert tmp_path.is_dir()

    def test_non_empty_dir_is_preserved(self, tmp_path: Path, empty_registry: OutputWriteRegistry):
        keeper = _make_file(tmp_path / "section" / "current.html")
        _record(empty_registry, keeper)
        report = sweep_stray_files([tmp_path], empty_registry)
        assert keeper.exists()
        assert keeper.parent.is_dir()
        assert report.removed_dirs == []


class TestImageRegistryUnion:
    """Image paths must be considered tracked via ``ImageRegistry``."""

    def test_image_in_image_registry_is_kept(
        self,
        tmp_path: Path,
        empty_registry: OutputWriteRegistry,
        empty_image_registry: ImageRegistry,
    ):
        img = _make_file(tmp_path / "img" / "logo.png", b"\x89PNG")
        empty_image_registry.record_output_write(img)
        report = sweep_stray_files([tmp_path], empty_registry, image_registry=empty_image_registry)
        assert img.exists()
        assert report.deleted_files == []

    def test_image_without_image_registry_is_swept(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        """Without an image registry the image looks stray to the sweep.
        This is the failure mode the union prevents — the test pins it."""
        img = _make_file(tmp_path / "img" / "logo.png", b"\x89PNG")
        report = sweep_stray_files([tmp_path], empty_registry, image_registry=None)
        assert not img.exists()
        assert img in report.deleted_files

    def test_stray_image_not_in_registry_is_swept(
        self,
        tmp_path: Path,
        empty_registry: OutputWriteRegistry,
        empty_image_registry: ImageRegistry,
    ):
        kept_img = _make_file(tmp_path / "img" / "current.png", b"\x89PNG")
        stray_img = _make_file(tmp_path / "img" / "removed.png", b"\x89PNG")
        empty_image_registry.record_output_write(kept_img)
        report = sweep_stray_files([tmp_path], empty_registry, image_registry=empty_image_registry)
        assert kept_img.exists()
        assert not stray_img.exists()
        assert report.deleted_files == [stray_img]


class TestSkipReasons:
    """When the caller passes ``skip_reason``, the sweep is a no-op."""

    def test_skip_reason_short_circuits(self, tmp_path: Path, empty_registry: OutputWriteRegistry):
        stray = _make_file(tmp_path / "stray.html")
        report = sweep_stray_files([tmp_path], empty_registry, skip_reason="stage errored")
        assert stray.exists()
        assert report.skipped is True
        assert report.skip_reason == "stage errored"
        assert report.deleted_files == []


class TestDryRun:
    """Dry-run lists what would be done without touching disk."""

    def test_dry_run_lists_but_does_not_delete(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        stray = _make_file(tmp_path / "stray.html")
        empty_section = tmp_path / "old_section"
        _make_file(empty_section / "a.html")

        report = sweep_stray_files([tmp_path], empty_registry, dry_run=True)
        assert stray.exists()
        assert empty_section.exists()
        assert report.dry_run is True
        assert stray in report.deleted_files
        assert empty_section in report.removed_dirs


class TestSymlinks:
    """Symlinks under the root: the link is removed, not the target."""

    @pytest.mark.skipif(
        sys.platform == "win32" and not _can_symlink_on_windows(),
        reason="Windows requires admin or Developer Mode to create symlinks",
    )
    def test_stray_symlink_link_deleted_target_untouched(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        target_dir = tmp_path.parent / "sweep_symlink_target"
        target_dir.mkdir(exist_ok=True)
        target = target_dir / "preserved.txt"
        target.write_bytes(b"keep me")
        try:
            link = tmp_path / "stray_link.txt"
            link.symlink_to(target)

            report = sweep_stray_files([tmp_path], empty_registry)
            assert not link.exists()
            assert not link.is_symlink()
            assert target.exists()
            assert target.read_bytes() == b"keep me"
            assert link in report.deleted_files
        finally:
            try:
                target.unlink()
            except OSError:
                pass
            try:
                target_dir.rmdir()
            except OSError:
                pass


class TestMultipleRoots:
    """Multiple roots are walked independently."""

    def test_per_root_isolation(self, tmp_path: Path, empty_registry: OutputWriteRegistry):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        kept_in_a = _make_file(root_a / "kept.html")
        stray_in_a = _make_file(root_a / "stray.html")
        stray_in_b = _make_file(root_b / "stray.html")
        _record(empty_registry, kept_in_a)

        report = sweep_stray_files([root_a, root_b], empty_registry)
        assert kept_in_a.exists()
        assert not stray_in_a.exists()
        assert not stray_in_b.exists()
        assert set(report.deleted_files) == {stray_in_a, stray_in_b}

    def test_missing_root_is_silently_skipped(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        missing = tmp_path / "does_not_exist"
        report = sweep_stray_files([missing], empty_registry)
        assert report.deleted_files == []
        assert report.removed_dirs == []


class TestCustomKeepPatterns:
    """Callers can extend ``keep_patterns`` to spare specific paths."""

    def test_explicit_keep_pattern_spares_file(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        kept = _make_file(tmp_path / "manually_pinned.txt")
        report = sweep_stray_files(
            [tmp_path],
            empty_registry,
            keep_patterns=(".git/**", "manually_pinned.txt"),
        )
        assert kept.exists()
        assert report.deleted_files == []
        assert report.kept_due_to_pattern == 1

    def test_keep_pattern_glob_matches_subpath(
        self, tmp_path: Path, empty_registry: OutputWriteRegistry
    ):
        kept = _make_file(tmp_path / "auxiliary" / "vendor.css")
        report = sweep_stray_files(
            [tmp_path],
            empty_registry,
            keep_patterns=(".git/**", "auxiliary/**"),
        )
        assert kept.exists()
        assert report.kept_due_to_pattern == 1


def test_module_does_not_pull_in_build_command() -> None:
    """Importing the sweep module must not transitively import
    ``clm.cli.commands.build`` — the dependency goes build → sweep,
    never the reverse."""
    sweep_module = sys.modules["clm.cli.output_sweep"]
    assert sweep_module is not None
    assert "clm.cli.commands.build" not in (
        getattr(attr, "__module__", "") for attr in vars(sweep_module).values()
    )
