"""Phase 7 item 1 (#801): the golden end-to-end build characterization suite.

The D11 hard gate on the Phase 8 re-layering (#802): reference courses built
end-to-end, full output trees snapshotted, and asserted **byte-identical** —
so a refactor that changes any output byte fails before it merges. Each test
here builds the same course twice from scratch (fresh source copies, cold
caches, ``--ignore-cache``) and verifies build B against build A's snapshot:
green means the pipeline is deterministic on unchanged code, which is the
handover's acceptance criterion and what makes the suite trustworthy as a
refactor gate. During Phase 8 the workflow is the same mechanism across the
refactor boundary: snapshot before, verify after.

Two reference courses:

* ``test-spec-1`` — the rich course: notebooks (bilingual + split routing),
  PlantUML + DrawIO conversions, static and generated images, data files,
  dir-groups. This is the tree most of the A-findings' code paths feed.
* ``test-spec-3`` — the minimal single-notebook course, so a determinism
  break in the core notebook path is attributable without the noise of the
  full course.

The HTTP-replay course has its own golden-style round trip in
``test_e2e_http_replay.py``.

``--verify-against`` skips ``.html`` by default (live-kernel output is
inherently nondeterministic — object reprs, ASLR); everything else, including
executed ``.ipynb``, images and copied data, is byte-compared. Builds run as
CLI subprocesses with a scrubbed environment (no inherited ``CLM_*``), the
standing hermeticity landmine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.serial("subproc")]

_TEST_DATA = Path(__file__).resolve().parent.parent / "test-data"
_BUILD_TIMEOUT = 900


def _build(course: Path, spec_name: str, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLM_")}
    env.pop("CI", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "clm",
            "build",
            str(course / "course-specs" / spec_name),
            "-d",
            str(course),
            "--ignore-cache",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_BUILD_TIMEOUT,
        env=env,
    )


def _double_build_is_byte_identical(tmp_path: Path, spec_name: str) -> None:
    snapshot = tmp_path / "snapshot"

    course_a = tmp_path / "a"
    shutil.copytree(_TEST_DATA, course_a)
    first = _build(course_a, spec_name, "--snapshot", str(snapshot))
    assert first.returncode == 0, first.stdout + first.stderr

    # A completely fresh copy: cold job/cache DBs, untouched source tree —
    # nothing from build A can leak into build B except through the outputs
    # being genuinely deterministic.
    course_b = tmp_path / "b"
    shutil.copytree(_TEST_DATA, course_b)
    second = _build(course_b, spec_name, "--verify-against", str(snapshot))
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Verification passed" in second.stdout


def test_rich_course_double_build_is_byte_identical(tmp_path: Path) -> None:
    """test-spec-1: notebooks, both diagram converters, images, data,
    dir-groups — the golden gate over the paths Phase 8 will move."""
    _double_build_is_byte_identical(tmp_path, "test-spec-1.xml")


def test_minimal_notebook_course_double_build_is_byte_identical(tmp_path: Path) -> None:
    """test-spec-3: the single-notebook course — a determinism break in the
    core notebook path fails here without the full course's noise."""
    _double_build_is_byte_identical(tmp_path, "test-spec-3.xml")
