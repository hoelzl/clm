"""Unit tests for ``BuildJupyterLiteSiteOperation``.

Verifies that the operation:
- Builds a content-addressed payload from the on-disk notebook tree.
- Resolves wheel + environment paths relative to the course root.
- Dispatches via ``backend.execute_operation`` under the
  ``jupyterlite-builder`` service name.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clm.core.course_spec import JupyterLiteConfig
from clm.core.operations.build_jupyterlite_site import BuildJupyterLiteSiteOperation
from clm.infrastructure.messaging.jupyterlite_classes import JupyterLitePayload


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    root.mkdir()
    (root / "wheels").mkdir()
    (root / "wheels" / "pkg-1.0-py3-none-any.whl").write_bytes(b"fake wheel")
    return root


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
def config() -> JupyterLiteConfig:
    return JupyterLiteConfig(
        kernel="pyodide",
        wheels=["wheels/pkg-1.0-py3-none-any.whl"],
        environment="",
        launcher="python",
        app_archive="offline",
    )


@pytest.mark.asyncio
async def test_payload_resolves_wheel_paths_relative_to_course_root(
    course_root: Path, notebook_tree: Path, config: JupyterLiteConfig, tmp_path: Path
) -> None:
    op = BuildJupyterLiteSiteOperation(
        course_root=course_root,
        notebook_tree=notebook_tree,
        output_dir=tmp_path / "site",
        target_name="online-playground",
        language="en",
        kind="completed",
        config=config,
    )
    payload = await op.payload()
    assert payload.wheels == [str(course_root / "wheels" / "pkg-1.0-py3-none-any.whl")]
    assert payload.target_name == "online-playground"
    assert payload.kernel == "pyodide"
    assert payload.app_archive == "offline"
    assert payload.launcher == "python"


@pytest.mark.asyncio
async def test_payload_output_file_points_at_site_index(
    course_root: Path, notebook_tree: Path, config: JupyterLiteConfig, tmp_path: Path
) -> None:
    output_dir = tmp_path / "site"
    op = BuildJupyterLiteSiteOperation(
        course_root=course_root,
        notebook_tree=notebook_tree,
        output_dir=output_dir,
        target_name="playground",
        language="en",
        kind="completed",
        config=config,
    )
    payload = await op.payload()
    assert Path(payload.output_file) == output_dir / "_output" / "index.html"


@pytest.mark.asyncio
async def test_payload_content_hash_sensitive_to_kernel(
    course_root: Path, notebook_tree: Path, config: JupyterLiteConfig, tmp_path: Path
) -> None:
    op_pyodide = BuildJupyterLiteSiteOperation(
        course_root=course_root,
        notebook_tree=notebook_tree,
        output_dir=tmp_path / "a",
        target_name="t",
        language="en",
        kind="completed",
        config=config,
    )
    xeus_config = JupyterLiteConfig(
        kernel="xeus-python",
        wheels=config.wheels,
        environment=config.environment,
        launcher=config.launcher,
        app_archive=config.app_archive,
    )
    op_xeus = BuildJupyterLiteSiteOperation(
        course_root=course_root,
        notebook_tree=notebook_tree,
        output_dir=tmp_path / "b",
        target_name="t",
        language="en",
        kind="completed",
        config=xeus_config,
    )
    hash_p = (await op_pyodide.payload()).content_hash()
    hash_x = (await op_xeus.payload()).content_hash()
    assert hash_p != hash_x


@pytest.mark.asyncio
async def test_payload_resolves_absolute_wheel_paths_unchanged(
    course_root: Path, notebook_tree: Path, tmp_path: Path
) -> None:
    absolute_wheel = tmp_path / "elsewhere" / "abs.whl"
    absolute_wheel.parent.mkdir()
    absolute_wheel.write_bytes(b"hello")
    config = JupyterLiteConfig(
        kernel="pyodide",
        wheels=[str(absolute_wheel)],
        environment="",
        launcher="none",
        app_archive="cdn",
    )
    op = BuildJupyterLiteSiteOperation(
        course_root=course_root,
        notebook_tree=notebook_tree,
        output_dir=tmp_path / "site",
        target_name="t",
        language="en",
        kind="completed",
        config=config,
    )
    payload = await op.payload()
    assert payload.wheels == [str(absolute_wheel)]
    assert payload.app_archive == "cdn"
    assert payload.launcher == "none"


@pytest.mark.asyncio
async def test_execute_dispatches_to_backend(
    course_root: Path, notebook_tree: Path, config: JupyterLiteConfig, tmp_path: Path
) -> None:
    op = BuildJupyterLiteSiteOperation(
        course_root=course_root,
        notebook_tree=notebook_tree,
        output_dir=tmp_path / "site",
        target_name="t",
        language="en",
        kind="completed",
        config=config,
    )
    backend = AsyncMock()
    await op.execute(backend)
    backend.execute_operation.assert_awaited_once()
    args, _ = backend.execute_operation.call_args
    passed_op, passed_payload = args
    assert passed_op is op
    assert isinstance(passed_payload, JupyterLitePayload)
    assert op.service_name == "jupyterlite-builder"
