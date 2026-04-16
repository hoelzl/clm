"""Playwright smoke test for a built JupyterLite site.

Marked ``@pytest.mark.e2e`` — excluded from the fast suite and from
``pytest -m "not docker"``. Runs via ``pytest -m e2e``.

Chromium only (v1 scope). Skipped on Windows dev boxes (JupyterLite
kernel startup is unreliable under Windows CI) and when ``playwright``
is not installed.

Requires:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import importlib.util
import json
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
HAS_JUPYTERLITE = importlib.util.find_spec("jupyterlite_core") is not None
IS_LINUX = platform.system() == "Linux"

skip_reason_parts: list[str] = []
if not HAS_PLAYWRIGHT:
    skip_reason_parts.append("playwright not installed")
if not HAS_JUPYTERLITE:
    skip_reason_parts.append("jupyterlite-core not installed")
if not IS_LINUX:
    skip_reason_parts.append(f"skipped on {platform.system()} (Linux CI only)")

SKIP = bool(skip_reason_parts)
SKIP_REASON = "; ".join(skip_reason_parts) if skip_reason_parts else ""


@pytest.mark.skipif(SKIP, reason=SKIP_REASON)
def test_jupyterlite_site_loads_and_evaluates_cell(tmp_path: Path) -> None:
    """Build a minimal site, launch it, and evaluate ``1 + 1`` in a cell."""
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
                        "source": ["print('hello from jupyterlite')"],
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
        target_label="e2e/en/completed",
        jupyterlite_core_version="e2e-test",
    )
    result = build_site(args)
    assert (result.site_dir / "index.html").is_file()

    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = SimpleHTTPRequestHandler
    handler.extensions_map[".wasm"] = "application/wasm"

    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        import os

        os.chdir(result.site_dir)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/lab/index.html", timeout=60000)
                page.wait_for_selector(".jp-Notebook", timeout=60000)
                page.locator("text=hello from jupyterlite").wait_for(timeout=60000)
                browser.close()
        finally:
            httpd.shutdown()
