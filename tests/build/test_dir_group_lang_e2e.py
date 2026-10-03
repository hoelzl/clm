"""End-to-end: scoping a ``<dir-group>`` to one language (#1031) across builds.

Changing a dir-group from unscoped to ``lang="de"`` must remove the EN copy
the previous build left behind: the stray-file sweep deletes what the build
did not write, so the skipped EN copy must not be written (or registered)
anywhere. The manifest must agree with the tree. The harness is the
dir-group-only course of ``test_output_ownership_e2e.py``: no topics, so no
workers spawn and the build is fast.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands import build as build_module
from clm.core.provenance_manifest import MANIFEST_FILENAME

_SPEC = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <project-slug>course</project-slug>
    <sections/>
    <dir-groups>
        <dir-group{lang_attr}>
            <name>Extra</name>
            <path>extra</path>
        </dir-group>
    </dir-groups>
</course>
"""


def _write_spec(data: Path, lang_attr: str) -> Path:
    spec = data / "course-specs" / "course.xml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(_SPEC.format(lang_attr=lang_attr), encoding="utf-8")
    return spec


def _build(spec: Path, data: Path, out: Path, tmp_path: Path):
    obj = {"CACHE_DB_PATH": tmp_path / "cache.db", "JOBS_DB_PATH": tmp_path / "jobs.db"}
    return CliRunner().invoke(
        build_module.build,
        [str(spec), "--data-dir", str(data), "--output-dir", str(out), "--workers", "direct"],
        obj=obj,
    )


def _manifest_paths(target_root: Path) -> set[str]:
    manifest = json.loads((target_root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return {entry["path"] for entry in manifest["files"]}


def test_scoping_a_dir_group_removes_the_other_languages_copy(tmp_path: Path) -> None:
    data = tmp_path / "repo"
    (data / "slides").mkdir(parents=True)
    (data / "extra").mkdir(parents=True)
    (data / "extra" / "file.txt").write_text("hello", encoding="utf-8")
    out = tmp_path / "out"
    shared = out / "shared"

    first = _build(_write_spec(data, ""), data, out, tmp_path)
    assert first.exit_code == 0, first.output
    assert (shared / "course-de" / "Extra" / "file.txt").is_file()
    assert (shared / "course-en" / "Extra" / "file.txt").is_file()

    second = _build(_write_spec(data, ' lang="de"'), data, out, tmp_path)
    assert second.exit_code == 0, second.output
    assert (shared / "course-de" / "Extra" / "file.txt").is_file()
    assert not (shared / "course-en" / "Extra" / "file.txt").exists(), (
        "the stale EN copy of a now DE-only dir-group must be swept"
    )

    paths = _manifest_paths(shared)
    assert any(p.endswith("course-de/Extra/file.txt") for p in paths), paths
    assert not any("course-en/Extra" in p for p in paths), paths
