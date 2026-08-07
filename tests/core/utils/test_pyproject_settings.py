"""Tests for the shared ``[tool.clm]`` pyproject reader (A7, #802)."""

from __future__ import annotations

from pathlib import Path

from clm.core.utils.pyproject_settings import (
    find_nearest_pyproject,
    read_tool_clm,
    read_tool_clm_key,
)


def _write(pyproject: Path, content: str) -> Path:
    pyproject.write_text(content, encoding="utf-8")
    return pyproject


class TestReadToolClm:
    def test_returns_table(self, tmp_path: Path) -> None:
        pyproject = _write(
            tmp_path / "pyproject.toml",
            '[tool.clm]\ncache_dir = "../shared-cache"\nsidecar-layout = "subdir"\n',
        )
        assert read_tool_clm(pyproject) == {
            "cache_dir": "../shared-cache",
            "sidecar-layout": "subdir",
        }

    def test_missing_file_reads_as_empty(self, tmp_path: Path) -> None:
        assert read_tool_clm(tmp_path / "pyproject.toml") == {}

    def test_unparseable_file_reads_as_empty(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", "[tool.clm\nbroken")
        assert read_tool_clm(pyproject) == {}

    def test_missing_table_reads_as_empty(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
        assert read_tool_clm(pyproject) == {}

    def test_non_table_tool_clm_reads_as_empty(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", '[tool]\nclm = "not-a-table"\n')
        assert read_tool_clm(pyproject) == {}


class TestReadToolClmKey:
    def test_string_value(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", '[tool.clm]\ncache_dir = "x"\n')
        assert read_tool_clm_key(pyproject, "cache_dir") == "x"

    def test_missing_key_is_none(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", "[tool.clm]\n")
        assert read_tool_clm_key(pyproject, "cache_dir") is None

    def test_empty_string_is_unset(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", '[tool.clm]\ncache_dir = ""\n')
        assert read_tool_clm_key(pyproject, "cache_dir") is None

    def test_non_string_is_unset(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", "[tool.clm]\ncache_dir = 3\n")
        assert read_tool_clm_key(pyproject, "cache_dir") is None


class TestFindNearestPyproject:
    def test_finds_in_start_dir(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", "")
        assert find_nearest_pyproject(tmp_path) == pyproject

    def test_walks_up_from_subdir(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", "")
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        assert find_nearest_pyproject(subdir) == pyproject

    def test_file_start_uses_its_parent(self, tmp_path: Path) -> None:
        pyproject = _write(tmp_path / "pyproject.toml", "")
        slide = tmp_path / "deck.py"
        slide.write_text("", encoding="utf-8")
        assert find_nearest_pyproject(slide) == pyproject

    def test_stops_at_first_match(self, tmp_path: Path) -> None:
        _write(tmp_path / "pyproject.toml", "")
        inner = tmp_path / "course"
        inner.mkdir()
        inner_pyproject = _write(inner / "pyproject.toml", "")
        assert find_nearest_pyproject(inner) == inner_pyproject

    def test_none_when_absent(self, tmp_path: Path) -> None:
        assert find_nearest_pyproject(tmp_path) is None
