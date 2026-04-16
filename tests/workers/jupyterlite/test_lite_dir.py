"""Tests for the JupyterLite ``lite_dir`` assembler.

These are pure-IO tests that don't require ``jupyterlite-core`` to be
installed. They verify that given a synthetic notebook tree, wheel list,
and environment file, the assembler produces the exact on-disk layout
``jupyter lite build`` expects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clm.workers.jupyterlite.lite_dir import (
    assemble_lite_dir,
    collect_notebook_tree,
    hash_manifest,
    populate_environment,
    populate_files,
    populate_wheels,
    sha256_of_file,
    write_jupyter_lite_config,
    write_overrides,
)


@pytest.fixture
def notebook_tree(tmp_path: Path) -> Path:
    """Create a minimal notebook tree with nested sections."""
    tree = tmp_path / "notebook_tree"
    tree.mkdir()
    (tree / "01-intro.ipynb").write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    subsection = tree / "section-a"
    subsection.mkdir()
    (subsection / "02-lesson.ipynb").write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    # A non-ipynb support file travels alongside the notebooks.
    (subsection / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return tree


@pytest.fixture
def wheel_files(tmp_path: Path) -> list[Path]:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    files = []
    for name in ["pkg_a-1.0-py3-none-any.whl", "pkg_b-2.3-py3-none-any.whl"]:
        path = wheels_dir / name
        path.write_bytes(b"fake wheel bytes for " + name.encode())
        files.append(path)
    return files


def test_sha256_of_file_is_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    assert sha256_of_file(f) == sha256_of_file(f)


def test_collect_notebook_tree_returns_sorted_relative_posix_paths(notebook_tree: Path) -> None:
    entries = collect_notebook_tree(notebook_tree)
    paths = [rel for rel, _ in entries]
    assert paths == sorted(paths)
    assert all("/" in p or "." in p for p in paths)
    assert "01-intro.ipynb" in paths
    assert "section-a/02-lesson.ipynb" in paths


def test_collect_notebook_tree_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        collect_notebook_tree(tmp_path / "does-not-exist")


def test_populate_files_mirrors_full_tree(tmp_path: Path, notebook_tree: Path) -> None:
    lite = tmp_path / "lite"
    copied = populate_files(lite, notebook_tree)
    files_dir = lite / "files"
    assert (files_dir / "01-intro.ipynb").is_file()
    assert (files_dir / "section-a" / "02-lesson.ipynb").is_file()
    # Non-notebook support files ride along so relative links survive.
    assert (files_dir / "section-a" / "data.csv").is_file()
    assert sorted(copied) == sorted(
        ["01-intro.ipynb", "section-a/02-lesson.ipynb", "section-a/data.csv"]
    )


def test_populate_wheels_stages_and_hashes(tmp_path: Path, wheel_files: list[Path]) -> None:
    lite = tmp_path / "lite"
    staged = populate_wheels(lite, wheel_files)
    names = [name for name, _ in staged]
    assert names == sorted(w.name for w in wheel_files)
    for name, digest in staged:
        assert (lite / "pypi" / name).is_file()
        assert len(digest) == 64  # sha256 hex


def test_populate_wheels_is_empty_noop(tmp_path: Path) -> None:
    lite = tmp_path / "lite"
    assert populate_wheels(lite, []) == []
    # No pypi/ directory created when the wheel list is empty.
    assert not (lite / "pypi").exists()


def test_populate_wheels_raises_on_missing_wheel(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        populate_wheels(tmp_path, [tmp_path / "ghost.whl"])


def test_populate_environment_copies_and_hashes(tmp_path: Path) -> None:
    env = tmp_path / "env.yml"
    env.write_text("name: demo\nchannels:\n  - conda-forge\n", encoding="utf-8")
    lite = tmp_path / "lite"
    lite.mkdir()
    digest = populate_environment(lite, env)
    assert (lite / "environment.yml").is_file()
    assert digest == sha256_of_file(env)


def test_populate_environment_noop_when_absent(tmp_path: Path) -> None:
    lite = tmp_path / "lite"
    lite.mkdir()
    assert populate_environment(lite, None) is None
    assert not (lite / "environment.yml").exists()


def test_write_jupyter_lite_config_without_wheels(tmp_path: Path) -> None:
    config = write_jupyter_lite_config(
        tmp_path,
        kernel="pyodide",
        wheel_names=[],
        app_archive="offline",
    )
    assert config["LiteBuildConfig"]["apps"] == ["lab"]
    # PipliteAddon.piplite_urls is only present when wheels are staged.
    assert "PipliteAddon" not in config
    on_disk = json.loads((tmp_path / "jupyter_lite_config.json").read_text(encoding="utf-8"))
    assert on_disk == config


def test_write_jupyter_lite_config_with_wheels(tmp_path: Path) -> None:
    config = write_jupyter_lite_config(
        tmp_path,
        kernel="pyodide",
        wheel_names=["rich-13.0-py3-none-any.whl"],
        app_archive="offline",
    )
    assert config["PipliteAddon"]["piplite_urls"] == ["./pypi/rich-13.0-py3-none-any.whl"]


def test_write_jupyter_lite_config_rejects_bad_kernel(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_jupyter_lite_config(
            tmp_path,
            kernel="totally-not-a-kernel",
            wheel_names=[],
            app_archive="offline",
        )


def test_write_jupyter_lite_config_rejects_bad_app_archive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_jupyter_lite_config(
            tmp_path,
            kernel="pyodide",
            wheel_names=[],
            app_archive="beta",
        )


def test_assemble_lite_dir_end_to_end(
    tmp_path: Path, notebook_tree: Path, wheel_files: list[Path]
) -> None:
    lite = tmp_path / "lite"
    env = tmp_path / "env.yml"
    env.write_text("name: demo\n", encoding="utf-8")
    manifest = assemble_lite_dir(
        lite,
        notebook_tree=notebook_tree,
        kernel="xeus-python",
        wheels=wheel_files,
        environment_yml=env,
        app_archive="offline",
    )

    # All expected artifacts landed in the lite-dir:
    assert (lite / "jupyter_lite_config.json").is_file()
    assert (lite / "files" / "01-intro.ipynb").is_file()
    assert (lite / "pypi" / wheel_files[0].name).is_file()
    assert (lite / "environment.yml").is_file()

    # Manifest shape is stable and drives cache keying.
    assert manifest["kernel"] == "xeus-python"
    assert manifest["app_archive"] == "offline"
    assert len(manifest["notebooks"]) == 2
    assert len(manifest["wheels"]) == 2
    assert manifest["environment_sha256"] == sha256_of_file(env)
    assert manifest["files_count"] == 3  # 2 notebooks + data.csv


class TestWriteOverrides:
    def test_empty_branding_writes_nothing(self, tmp_path: Path) -> None:
        result = write_overrides(tmp_path)
        assert result is None
        assert not (tmp_path / "overrides.json").exists()

    def test_theme_sets_jupyterlab_theme(self, tmp_path: Path) -> None:
        result = write_overrides(tmp_path, branding_theme="dark")
        assert result is not None
        assert (tmp_path / "overrides.json").is_file()
        on_disk = json.loads((tmp_path / "overrides.json").read_text(encoding="utf-8"))
        assert on_disk["@jupyterlab/apputils-extension:themes"]["theme"] == "JupyterLab Dark"

    def test_light_theme(self, tmp_path: Path) -> None:
        result = write_overrides(tmp_path, branding_theme="light")
        assert result is not None
        assert result["@jupyterlab/apputils-extension:themes"]["theme"] == "JupyterLab Light"

    def test_site_name_sets_logo_title(self, tmp_path: Path) -> None:
        result = write_overrides(tmp_path, branding_site_name="My Course")
        assert result is not None
        assert result["@jupyterlab/application-extension:logo"]["title"] == "My Course"

    def test_logo_sets_icon(self, tmp_path: Path) -> None:
        result = write_overrides(tmp_path, branding_logo="assets/logo.svg")
        assert result is not None
        assert result["@jupyterlab/application-extension:logo"]["icon"] == "assets/logo.svg"

    def test_combined_branding(self, tmp_path: Path) -> None:
        result = write_overrides(
            tmp_path,
            branding_theme="dark",
            branding_logo="logo.png",
            branding_site_name="Test Course",
        )
        assert result is not None
        logo_ext = result["@jupyterlab/application-extension:logo"]
        assert logo_ext["title"] == "Test Course"
        assert logo_ext["icon"] == "logo.png"


def test_assemble_lite_dir_with_branding(tmp_path: Path, notebook_tree: Path) -> None:
    lite = tmp_path / "lite"
    manifest = assemble_lite_dir(
        lite,
        notebook_tree=notebook_tree,
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        branding_theme="dark",
        branding_site_name="My Course",
    )
    assert (lite / "overrides.json").is_file()
    assert manifest["overrides"] is not None
    assert "dark" in manifest["overrides"]["@jupyterlab/apputils-extension:themes"]["theme"].lower()


def test_assemble_lite_dir_without_branding(tmp_path: Path, notebook_tree: Path) -> None:
    lite = tmp_path / "lite"
    manifest = assemble_lite_dir(
        lite,
        notebook_tree=notebook_tree,
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
    )
    assert not (lite / "overrides.json").exists()
    assert manifest["overrides"] is None


def test_hash_manifest_stable_and_version_sensitive(tmp_path: Path, notebook_tree: Path) -> None:
    lite = tmp_path / "lite"
    manifest = assemble_lite_dir(
        lite,
        notebook_tree=notebook_tree,
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
    )
    key_a = hash_manifest(manifest, jupyterlite_core_version="0.7.4")
    key_b = hash_manifest(manifest, jupyterlite_core_version="0.7.4")
    key_c = hash_manifest(manifest, jupyterlite_core_version="0.8.0")
    assert key_a == key_b
    # Bumping the builder version changes the cache key so new builds run.
    assert key_a != key_c
