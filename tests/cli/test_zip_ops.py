"""Tests for ZIP archive operations."""

import zipfile
from pathlib import Path

import pytest

from clm.cli.commands.zip_ops import zip_directory


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """Create a sample directory tree for zipping."""
    root = tmp_path / "MyCourse"
    root.mkdir()

    # Regular files
    (root / "file1.txt").write_text("hello")
    (root / "file2.html").write_text("<html></html>")

    # Subdirectory with files
    sub = root / "Slides"
    sub.mkdir()
    (sub / "slide1.html").write_text("<h1>Title</h1>")
    (sub / "slide2.html").write_text("<h1>End</h1>")

    # Nested subdirectory
    img = root / "img"
    img.mkdir()
    (img / "diagram.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

    # Directories that should be excluded
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]")

    pycache = root / "__pycache__"
    pycache.mkdir()
    (pycache / "module.cpython-312.pyc").write_bytes(b"\x00")

    # .pyc file in regular dir
    (sub / "temp.pyc").write_bytes(b"\x00")

    return root


class TestZipDirectory:
    def test_creates_zip_file(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        result = zip_directory(sample_tree, archive)

        assert result == archive
        assert archive.exists()

    def test_archive_contains_top_level_directory(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            # All entries should be under MyCourse/
            assert all(name.startswith("MyCourse/") for name in names)

    def test_archive_contains_expected_files(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            assert "MyCourse/file1.txt" in names
            assert "MyCourse/file2.html" in names
            assert "MyCourse/Slides/slide1.html" in names
            assert "MyCourse/Slides/slide2.html" in names
            assert "MyCourse/img/diagram.png" in names

    def test_excludes_git_directory(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            assert not any(".git" in name for name in names)

    def test_excludes_pycache_directory(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            assert not any("__pycache__" in name for name in names)

    def test_excludes_pyc_files(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            assert not any(name.endswith(".pyc") for name in names)

    def test_uses_deflated_compression(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                assert info.compress_type == zipfile.ZIP_DEFLATED

    def test_deterministic_ordering(self, sample_tree: Path, tmp_path: Path):
        archive1 = tmp_path / "output1.zip"
        archive2 = tmp_path / "output2.zip"

        zip_directory(sample_tree, archive1)
        zip_directory(sample_tree, archive2)

        with zipfile.ZipFile(archive1) as zf1, zipfile.ZipFile(archive2) as zf2:
            assert zf1.namelist() == zf2.namelist()

    def test_files_within_directories_are_sorted(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            # Group entries by their parent directory and verify each group is sorted
            from itertools import groupby

            for _dir, group_entries in groupby(names, key=lambda n: str(Path(n).parent)):
                entries = list(group_entries)
                assert entries == sorted(entries)

    def test_preserves_file_contents(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive)

        with zipfile.ZipFile(archive) as zf:
            assert zf.read("MyCourse/file1.txt") == b"hello"
            assert zf.read("MyCourse/img/diagram.png") == b"\x89PNG" + b"\x00" * 100

    def test_dry_run_does_not_create_file(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "output.zip"
        zip_directory(sample_tree, archive, dry_run=True)

        assert not archive.exists()

    def test_raises_on_nonexistent_directory(self, tmp_path: Path):
        archive = tmp_path / "output.zip"
        with pytest.raises(Exception):
            zip_directory(tmp_path / "nonexistent", archive)

    def test_creates_parent_directories(self, sample_tree: Path, tmp_path: Path):
        archive = tmp_path / "nested" / "dirs" / "output.zip"
        zip_directory(sample_tree, archive)

        assert archive.exists()
