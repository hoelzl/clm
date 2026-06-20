"""Smoke tests for the deck editor FastAPI app factory.

Verifies ``create_app`` wires up state, templates, static mount, and the
expected routes. Route *behavior* is covered by ``test_routes.py``; this
file only asserts the app glue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# fastapi is a core dependency, but jinja2 (needed by the app) is an
# optional extra — skip cleanly if the [edit] extra isn't installed.
pytest.importorskip("jinja2", reason="jinja2 not installed (needs [edit] extra)")
pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI  # noqa: E402

from clm.edit.app import create_app  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "slides" / "module_010_demo").mkdir(parents=True)
    (tmp_path / "slides" / "module_010_demo" / "topic_100_demo.py").write_text(
        "# %%\nprint('hi')\n", encoding="utf-8"
    )
    return tmp_path


class TestCreateApp:
    def test_returns_fastapi_app(self, data_dir: Path):
        app = create_app(data_dir)
        assert isinstance(app, FastAPI)

    def test_app_state_holds_config(self, data_dir: Path):
        app = create_app(data_dir, host="0.0.0.0", port=9999)
        assert app.state.data_dir == data_dir
        assert app.state.slides_dir == data_dir / "slides"
        assert app.state.host == "0.0.0.0"
        assert app.state.port == 9999
        assert app.state.templates is not None

    def test_routes_registered(self, data_dir: Path):
        app = create_app(data_dir)
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/" in paths
        assert "/deck" in paths

    def test_title_and_version(self, data_dir: Path):
        app = create_app(data_dir)
        assert app.title == "CLM Deck Editor"
        assert app.version  # non-empty
