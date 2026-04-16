"""Tests for the JupyterLite ``builder`` module.

Uses a fake ``_run_jupyter_lite_build`` so these tests never shell out
to the real ``jupyterlite-core`` — they run in the fast suite. An
integration test that does exercise the CLI lives in
``test_jupyterlite_integration.py`` and is marked
``@pytest.mark.integration``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.workers.jupyterlite import builder as builder_module
from clm.workers.jupyterlite.builder import (
    BuildArgs,
    build_result_to_summary,
    build_site,
)


@pytest.fixture
def notebook_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "notebooks"
    tree.mkdir()
    (tree / "01.ipynb").write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    return tree


@pytest.fixture
def fake_jupyter_lite_build():
    """Replace the subprocess call with a stub that writes a minimal site."""

    def _fake(lite_dir: Path, site_dir: Path, *, kernel: str) -> None:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text("<html>lite</html>", encoding="utf-8")
        (site_dir / "lab").mkdir(exist_ok=True)
        (site_dir / "lab" / "index.html").write_text("<html>lab</html>", encoding="utf-8")

    with patch.object(builder_module, "_run_jupyter_lite_build", side_effect=_fake) as m:
        yield m


def test_build_site_writes_output_and_manifest(
    tmp_path: Path, notebook_tree: Path, fake_jupyter_lite_build
) -> None:
    output_dir = tmp_path / "output"
    args = BuildArgs(
        notebook_trees={"code-along": notebook_tree},
        output_dir=output_dir,
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        launcher="python",
        target_label="test-target/en/completed",
        jupyterlite_core_version="0.7.4",
    )
    result = build_site(args)

    # Fake build produced the site tree.
    assert result.site_dir == output_dir / "_output"
    assert (output_dir / "_output" / "index.html").is_file()

    # Manifest is written and round-trips.
    assert result.manifest_path.is_file()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["target"] == "test-target/en/completed"
    assert manifest["kernel"] == "pyodide"
    assert manifest["cache_key"] == result.cache_key

    # Python launcher and README emitted.
    assert (output_dir / "launch.py").is_file()
    assert (output_dir / "README-offline.md").is_file()


def test_build_site_skips_launcher_when_disabled(
    tmp_path: Path, notebook_tree: Path, fake_jupyter_lite_build
) -> None:
    args = BuildArgs(
        notebook_trees={"code-along": notebook_tree},
        output_dir=tmp_path / "out",
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        launcher="none",
        target_label="t/en/completed",
        jupyterlite_core_version="0.7.4",
    )
    build_site(args)
    assert not (tmp_path / "out" / "launch.py").exists()
    assert not (tmp_path / "out" / "README-offline.md").exists()


def test_build_site_clears_existing_output(
    tmp_path: Path, notebook_tree: Path, fake_jupyter_lite_build
) -> None:
    output_dir = tmp_path / "out"
    stale = output_dir / "_output"
    stale.mkdir(parents=True)
    (stale / "stale-file.txt").write_text("stale", encoding="utf-8")

    args = BuildArgs(
        notebook_trees={"code-along": notebook_tree},
        output_dir=output_dir,
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        launcher="none",
        target_label="t/en/completed",
        jupyterlite_core_version="0.7.4",
    )
    build_site(args)
    # Stale leftovers are purged before the fresh build runs.
    assert not (stale / "stale-file.txt").exists()
    assert (stale / "index.html").exists()


def test_build_site_normalizes_backslash_paths_in_contents(
    tmp_path: Path, notebook_tree: Path
) -> None:
    """``api/contents/**/all.json`` must never ship Windows-style paths.

    Regression test for the kernel-hang bug on Windows: jupyterlite-core's
    contents addon writes listings with ``os.sep`` in the ``path`` field,
    which reaches the pyodide kernel's ``os.chdir("${localPath}")`` code
    verbatim and produces ``SyntaxWarning: invalid escape sequence '\\W'``
    for a path starting with ``completed\\Woche…``. We post-process
    ``all.json`` to force POSIX separators.
    """

    def _fake_with_bad_all_json(lite_dir: Path, site_dir: Path, *, kernel: str) -> None:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text("<html>lite</html>", encoding="utf-8")
        contents = site_dir / "api" / "contents"
        root_listing = contents / "completed" / "Woche 01"
        root_listing.mkdir(parents=True)
        (root_listing / "all.json").write_text(
            json.dumps(
                {
                    "path": "completed\\Woche 01",
                    "content": [
                        {
                            "name": "nb.ipynb",
                            "path": "completed\\Woche 01/nb.ipynb",
                            "type": "notebook",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        clean_listing = contents / "code-along"
        clean_listing.mkdir(parents=True)
        (clean_listing / "all.json").write_text(
            json.dumps({"path": "code-along", "content": []}),
            encoding="utf-8",
        )

    args = BuildArgs(
        notebook_trees={"code-along": notebook_tree},
        output_dir=tmp_path / "out",
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        launcher="none",
        target_label="t/en/completed",
        jupyterlite_core_version="0.7.4",
    )
    with patch.object(
        builder_module, "_run_jupyter_lite_build", side_effect=_fake_with_bad_all_json
    ):
        build_site(args)

    rewritten = json.loads(
        (
            tmp_path
            / "out"
            / "_output"
            / "api"
            / "contents"
            / "completed"
            / "Woche 01"
            / "all.json"
        ).read_text(encoding="utf-8")
    )
    assert rewritten["path"] == "completed/Woche 01"
    assert rewritten["content"][0]["path"] == "completed/Woche 01/nb.ipynb"

    for all_json in (tmp_path / "out" / "_output" / "api" / "contents").rglob("all.json"):
        data = json.loads(all_json.read_text(encoding="utf-8"))

        def _no_backslash_in_path(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "path" and isinstance(value, str):
                        assert "\\" not in value, f"{all_json}: {value!r}"
                    else:
                        _no_backslash_in_path(value)
            elif isinstance(node, list):
                for item in node:
                    _no_backslash_in_path(item)

        _no_backslash_in_path(data)


def test_build_result_summary_is_valid_json(
    tmp_path: Path, notebook_tree: Path, fake_jupyter_lite_build
) -> None:
    args = BuildArgs(
        notebook_trees={"code-along": notebook_tree},
        output_dir=tmp_path / "out",
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        launcher="none",
        target_label="t/en/completed",
        jupyterlite_core_version="0.7.4",
    )
    result = build_site(args)
    summary = json.loads(build_result_to_summary(result))
    assert summary["cache_key"] == result.cache_key
    assert Path(summary["site_dir"]) == result.site_dir
