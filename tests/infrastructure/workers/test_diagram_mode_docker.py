"""The default diagram mode against a real Docker daemon (issue #1143)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from clm.infrastructure.workers.diagram_mode import probe_docker_images
from tests.docker_image_helpers import find_drawio_image

pytestmark = [pytest.mark.docker, pytest.mark.integration]

DRAWING = (
    Path(__file__).parents[2]
    / "test-data"
    / "slides"
    / "module_050_single_drawio"
    / "topic_100_simple_drawio"
    / "drawio"
    / "simple_drawing.drawio"
)
SPEC = """<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <description><de>d</de><en>d</en></description>
  <certificate><de>c</de><en>c</en></certificate>
  <sections><section><name><de>A</de><en>A</en></name><topics>
    <topic>demo</topic>
  </topics></section></sections>
</course>
"""


def _build(root: Path, image: str, *flags: str) -> tuple[bytes, str]:
    topic = root / "slides" / "module_010_x" / "topic_010_demo" / "drawio"
    topic.mkdir(parents=True)
    shutil.copy(DRAWING, topic)
    spec = root / "course-specs" / "t.xml"
    spec.parent.mkdir()
    spec.write_text(SPEC, encoding="utf-8")

    env = dict(os.environ)
    # The suite pins ``direct``; this test exercises the shipped default.
    env.pop("CLM_WORKER_MANAGEMENT__DIAGRAM_MODE", None)
    env["CLM_JOBS_DB_PATH"] = str(root / "jobs.db")
    env["CLM_CACHE_DB_PATH"] = str(root / "cache.db")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "clm.cli.main",
            "build",
            str(spec),
            "-o",
            str(root / "out"),
            "--drawio-image",
            image,
            "--notebook-workers",
            "0",
            "--plantuml-workers",
            "0",
            *flags,
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return next(root.rglob("img-generated/simple_drawing.png")).read_bytes(), output


def test_default_mode_renders_the_same_bytes_as_explicit_docker(tmp_path):
    image = find_drawio_image()
    if image is None:
        pytest.skip("No Draw.io converter image available")

    auto_png, auto_output = _build(tmp_path / "auto", image)
    docker_png, _ = _build(tmp_path / "docker", image, "--diagram-mode", "docker")

    assert auto_png == docker_png
    assert "rendered with the local tool" not in auto_output


def test_probe_against_a_real_daemon(tmp_path):
    image = find_drawio_image()
    if image is None:
        pytest.skip("No Draw.io converter image available")

    absent = "clm-drawio-converter:no-such-tag-1143"
    result = probe_docker_images({"drawio": image, "plantuml": absent})

    assert result["drawio"] is None
    assert f"docker pull {absent}" in result["plantuml"]
