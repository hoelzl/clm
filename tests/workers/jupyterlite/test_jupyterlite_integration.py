"""End-to-end integration test for the JupyterLite builder.

Runs the real ``jupyter lite build`` CLI on a synthetic 2-notebook tree
and asserts the site's ``index.html`` is produced. Skipped when
``jupyterlite-core`` is not importable (i.e., the ``[jupyterlite]``
extra is not installed) so the fast-suite dev box doesn't need to pull
the extra.

Marked ``integration`` — excluded from the default fast suite and
Docker suite; runs via ``pytest -m integration``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

HAS_JUPYTERLITE = importlib.util.find_spec("jupyterlite_core") is not None


@pytest.mark.skipif(
    not HAS_JUPYTERLITE,
    reason="jupyterlite-core not installed (pip install -e '.[jupyterlite]')",
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
        notebook_tree=notebook_tree,
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
