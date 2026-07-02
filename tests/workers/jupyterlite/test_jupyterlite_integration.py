"""End-to-end integration test for the JupyterLite builder.

Runs the real ``jupyter lite build`` CLI on a synthetic 1-notebook tree
and asserts the site's ``index.html`` is produced. The build shells out
to an isolated ``uvx`` tool env (Wave 2a) — clm no longer installs
jupyterlite-core — so this test is skipped when ``uvx`` is not on PATH.
The first run provisions the tool env (a few seconds); subsequent runs
reuse the cached uv tool env.

Marked ``integration`` — excluded from the default fast suite and
Docker suite; runs via ``pytest -m integration``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

HAS_UVX = shutil.which("uvx") is not None


@pytest.mark.skipif(
    not HAS_UVX,
    reason="uvx not on PATH (JupyterLite builds run in an isolated uvx tool env)",
)
def test_jupyter_lite_build_produces_index_html(tmp_path: Path) -> None:
    from clm.workers.jupyterlite.builder import BuildArgs, build_site

    notebook_tree = tmp_path / "notebooks"
    notebook_tree.mkdir()
    (notebook_tree / "hello.ipynb").write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": ["print('hello')"],
                        "outputs": [],
                        "execution_count": None,
                    }
                ],
                "metadata": {
                    "kernelspec": {"name": "python", "display_name": "Python"},
                    "language_info": {"name": "python"},
                },
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )

    args = BuildArgs(
        notebook_trees={"completed": notebook_tree},
        output_dir=tmp_path / "site",
        kernel="pyodide",
        wheels=[],
        environment_yml=None,
        app_archive="offline",
        launcher="python",
        target_label="integration/en/completed",
        jupyterlite_core_version="integration-test",
    )
    result = build_site(args)

    # The site has an index.html (SPA entrypoint).
    assert (result.site_dir / "index.html").is_file(), (
        "jupyter lite build did not produce index.html"
    )
    # The manifest is written next to the site.
    assert result.manifest_path.is_file()
    # Launcher and README emitted alongside the built site.
    assert (tmp_path / "site" / "launch.py").is_file()
    assert (tmp_path / "site" / "README-offline.md").is_file()
