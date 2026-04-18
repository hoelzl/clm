"""Tests for ZIP archive operations."""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from clm.cli.commands import zip_ops as zip_ops_module
from clm.cli.commands.zip_ops import (
    OutputDirectory,
    _archive_name,
    find_output_directories,
    zip_directory,
    zip_group,
)


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


class TestOutputDirectory:
    def test_display_name_combines_target_and_language(self, tmp_path: Path):
        od = OutputDirectory(path=tmp_path, target_name="public", language="de")
        assert od.display_name == "public/de"

    def test_exists_true_when_directory_present(self, tmp_path: Path):
        (tmp_path / "built").mkdir()
        od = OutputDirectory(path=tmp_path / "built", target_name="x", language="en")
        assert od.exists is True

    def test_exists_false_when_missing(self, tmp_path: Path):
        od = OutputDirectory(path=tmp_path / "never_created", target_name="x", language="en")
        assert od.exists is False

    def test_exists_false_when_path_is_file(self, tmp_path: Path):
        file_path = tmp_path / "a_file.txt"
        file_path.write_text("not a dir")
        od = OutputDirectory(path=file_path, target_name="x", language="en")
        assert od.exists is False


class TestArchiveName:
    def test_archive_name_format(self, tmp_path: Path):
        od = OutputDirectory(
            path=tmp_path / "my-course-de",
            target_name="public",
            language="de",
        )
        assert _archive_name(od) == "my-course-de_public_de.zip"


def _make_spec(output_targets=None):
    """Build a MagicMock that quacks like a CourseSpec."""
    spec = MagicMock()
    spec.output_targets = output_targets or []
    spec.output_dir_name = {"de": "course-de", "en": "course-en"}
    return spec


class TestFindOutputDirectoriesWithTargets:
    def test_returns_entry_per_target_and_language(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target_a = MagicMock(name="public", path="out/public", languages=["de", "en"])
        target_a.name = "public"
        target_b = MagicMock(name="speaker", path="out/speaker", languages=["de"])
        target_b.name = "speaker"
        spec = _make_spec(output_targets=[target_a, target_b])

        monkeypatch.setattr(zip_ops_module.CourseSpec, "from_file", lambda _: spec)
        monkeypatch.setattr(
            zip_ops_module,
            "resolve_course_paths",
            lambda _: (tmp_path, tmp_path / "output"),
        )

        result = find_output_directories(tmp_path / "spec.xml")

        names = {(d.target_name, d.language) for d in result}
        assert names == {
            ("public", "de"),
            ("public", "en"),
            ("speaker", "de"),
        }

    def test_target_filter_narrows_results(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target_a = MagicMock(path="out/public", languages=["de"])
        target_a.name = "public"
        target_b = MagicMock(path="out/speaker", languages=["de"])
        target_b.name = "speaker"
        spec = _make_spec(output_targets=[target_a, target_b])

        monkeypatch.setattr(zip_ops_module.CourseSpec, "from_file", lambda _: spec)
        monkeypatch.setattr(
            zip_ops_module,
            "resolve_course_paths",
            lambda _: (tmp_path, tmp_path / "output"),
        )

        result = find_output_directories(tmp_path / "spec.xml", target_filter="public")

        assert len(result) == 1
        assert result[0].target_name == "public"

    def test_absolute_target_path_is_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        absolute_path = (tmp_path / "elsewhere").resolve()
        target = MagicMock(path=str(absolute_path), languages=["en"])
        target.name = "public"
        spec = _make_spec(output_targets=[target])

        monkeypatch.setattr(zip_ops_module.CourseSpec, "from_file", lambda _: spec)
        monkeypatch.setattr(
            zip_ops_module,
            "resolve_course_paths",
            lambda _: (tmp_path, tmp_path / "output"),
        )

        result = find_output_directories(tmp_path / "spec.xml")

        assert result[0].path == absolute_path / "course-en"

    def test_default_languages_when_none_specified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target = MagicMock(path="out", languages=None)
        target.name = "public"
        spec = _make_spec(output_targets=[target])

        monkeypatch.setattr(zip_ops_module.CourseSpec, "from_file", lambda _: spec)
        monkeypatch.setattr(
            zip_ops_module,
            "resolve_course_paths",
            lambda _: (tmp_path, tmp_path / "output"),
        )

        result = find_output_directories(tmp_path / "spec.xml")

        assert {d.language for d in result} == {"de", "en"}


class TestFindOutputDirectoriesNoTargets:
    def test_falls_back_to_public_and_speaker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        spec = _make_spec(output_targets=[])

        monkeypatch.setattr(zip_ops_module.CourseSpec, "from_file", lambda _: spec)
        monkeypatch.setattr(
            zip_ops_module,
            "resolve_course_paths",
            lambda _: (tmp_path, tmp_path / "output"),
        )

        result = find_output_directories(tmp_path / "spec.xml")

        names = {(d.target_name, d.language) for d in result}
        assert names == {
            ("public", "de"),
            ("public", "en"),
            ("speaker", "de"),
            ("speaker", "en"),
        }

    def test_target_filter_with_fallback_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        spec = _make_spec(output_targets=[])

        monkeypatch.setattr(zip_ops_module.CourseSpec, "from_file", lambda _: spec)
        monkeypatch.setattr(
            zip_ops_module,
            "resolve_course_paths",
            lambda _: (tmp_path, tmp_path / "output"),
        )

        result = find_output_directories(tmp_path / "spec.xml", target_filter="speaker")

        assert len(result) == 2
        assert {d.language for d in result} == {"de", "en"}
        assert {d.target_name for d in result} == {"speaker"}


def _patch_find_output_directories(monkeypatch, directories):
    monkeypatch.setattr(
        zip_ops_module, "find_output_directories", lambda spec, target_filter=None: directories
    )


class TestZipGroupListCommand:
    def test_reports_existing_and_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "spec.xml").touch()
        exists_dir = tmp_path / "built"
        exists_dir.mkdir()

        dirs = [
            OutputDirectory(path=exists_dir, target_name="public", language="de"),
            OutputDirectory(path=tmp_path / "missing", target_name="public", language="en"),
        ]
        _patch_find_output_directories(monkeypatch, dirs)

        runner = CliRunner()
        result = runner.invoke(zip_group, ["list", str(tmp_path / "spec.xml")])

        assert result.exit_code == 0, result.output
        assert "public/de" in result.output
        assert "exists" in result.output
        assert "public/en" in result.output
        assert "not built" in result.output

    def test_reports_no_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "spec.xml").touch()
        _patch_find_output_directories(monkeypatch, [])

        runner = CliRunner()
        result = runner.invoke(zip_group, ["list", str(tmp_path / "spec.xml")])

        assert result.exit_code == 0
        assert "No output directories found" in result.output


class TestZipGroupCreateCommand:
    def test_no_directories_early_return(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "spec.xml").touch()
        _patch_find_output_directories(monkeypatch, [])

        runner = CliRunner()
        result = runner.invoke(zip_group, ["create", str(tmp_path / "spec.xml")])

        assert result.exit_code == 0
        assert "No output directories found" in result.output

    def test_no_existing_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "spec.xml").touch()
        missing_dir = tmp_path / "missing"
        dirs = [
            OutputDirectory(path=missing_dir, target_name="public", language="de"),
        ]
        _patch_find_output_directories(monkeypatch, dirs)

        runner = CliRunner()
        result = runner.invoke(zip_group, ["create", str(tmp_path / "spec.xml")])

        assert result.exit_code == 0
        assert "No built output directories found" in result.output

    def test_dry_run_does_not_create_archives(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / "spec.xml").touch()
        built = tmp_path / "built_de"
        built.mkdir()
        dirs = [OutputDirectory(path=built, target_name="public", language="de")]
        _patch_find_output_directories(monkeypatch, dirs)

        runner = CliRunner()
        result = runner.invoke(zip_group, ["create", str(tmp_path / "spec.xml"), "--dry-run"])

        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        # No zip files should exist anywhere under tmp_path.
        assert list(tmp_path.rglob("*.zip")) == []

    def test_creates_archive_for_existing_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / "spec.xml").touch()
        built = tmp_path / "course-de"
        built.mkdir()
        (built / "content.html").write_text("<html></html>")

        dirs = [OutputDirectory(path=built, target_name="public", language="de")]
        _patch_find_output_directories(monkeypatch, dirs)

        runner = CliRunner()
        result = runner.invoke(zip_group, ["create", str(tmp_path / "spec.xml")])

        assert result.exit_code == 0, result.output
        archive_path = tmp_path / _archive_name(dirs[0])
        assert archive_path.is_file()

    def test_custom_output_dir_for_archives(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "spec.xml").touch()
        built = tmp_path / "course-de"
        built.mkdir()
        (built / "content.html").write_text("<html></html>")
        archives_root = tmp_path / "archives"

        dirs = [OutputDirectory(path=built, target_name="public", language="de")]
        _patch_find_output_directories(monkeypatch, dirs)

        runner = CliRunner()
        result = runner.invoke(
            zip_group,
            [
                "create",
                str(tmp_path / "spec.xml"),
                "--output-dir",
                str(archives_root),
            ],
        )

        assert result.exit_code == 0, result.output
        # Archive lands in the custom directory, not next to the source.
        expected = archives_root / _archive_name(dirs[0])
        assert expected.is_file()
        assert not (built.parent / _archive_name(dirs[0])).exists()
