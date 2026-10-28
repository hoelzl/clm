"""End-to-end: several specs build into one output root (issue #1095).

Every output-target root used to hold a single ``.clm-manifest.json`` that
each build overwrote, so of all the specs sharing a root only the one built
last kept its provenance — and could ``clm release sync``. Each spec now
writes its own ``.clm-manifest.<spec>.json``. These builds check that two
specs with distinct slugs coexist in one root (default ``shared`` /
``trainer`` / ``speaker`` structure under one ``--output-dir``), and that a
third spec resolving to an already-claimed course directory is refused
before it writes anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands import build as build_module
from clm.core.provenance_manifest import MANIFEST_FILENAME, manifest_filename

_SPEC = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs {slug}</de><en>Course {slug}</en></name>
    <prog-lang>python</prog-lang>
    <project-slug>{slug}</project-slug>
    <sections/>
    <dir-groups>
        <dir-group>
            <name>Extra</name>
            <path>extra</path>
        </dir-group>
    </dir-groups>
</course>
"""


def _make_repo(tmp_path: Path) -> Path:
    data = tmp_path / "repo"
    (data / "slides").mkdir(parents=True)
    (data / "extra").mkdir(parents=True)
    (data / "extra" / "file.txt").write_text("hello", encoding="utf-8")
    (data / "course-specs").mkdir()
    return data


def _make_spec(data: Path, stem: str, slug: str) -> Path:
    spec = data / "course-specs" / f"{stem}.xml"
    spec.write_text(_SPEC.format(slug=slug), encoding="utf-8")
    return spec


def _build(spec: Path, data: Path, out: Path, tmp_path: Path):
    obj = {"CACHE_DB_PATH": tmp_path / "cache.db", "JOBS_DB_PATH": tmp_path / "jobs.db"}
    # ``--workers direct``: see test_output_ownership_e2e (process-global
    # worker-config singleton).
    return CliRunner().invoke(
        build_module.build,
        [str(spec), "--data-dir", str(data), "--output-dir", str(out), "--workers", "direct"],
        obj=obj,
    )


def _course_dirs(manifest_path: Path) -> set[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {entry["path"].split("/", 1)[0] for entry in manifest["files"]}


def test_two_specs_keep_their_own_manifest_in_a_shared_root(tmp_path: Path) -> None:
    data = _make_repo(tmp_path)
    spec_a = _make_spec(data, "alpha", "alpha")
    spec_b = _make_spec(data, "beta", "beta")
    out = tmp_path / "out"

    first = _build(spec_a, data, out, tmp_path)
    assert first.exit_code == 0, first.output
    second = _build(spec_b, data, out, tmp_path)
    assert second.exit_code == 0, second.output

    shared = out / "shared"
    manifest_a = shared / manifest_filename(spec_a.name)
    manifest_b = shared / manifest_filename(spec_b.name)
    assert manifest_a.is_file(), "building beta must not clobber alpha's manifest"
    assert manifest_b.is_file()
    assert not (shared / MANIFEST_FILENAME).exists()
    assert _course_dirs(manifest_a) == {"alpha-de", "alpha-en"}
    assert _course_dirs(manifest_b) == {"beta-de", "beta-en"}
    # Beta's build (and its stray-file sweep) left alpha's output alone.
    assert list((shared / "alpha-en").rglob("file.txt"))


def test_rebuilding_one_spec_leaves_the_other_intact(tmp_path: Path) -> None:
    data = _make_repo(tmp_path)
    spec_a = _make_spec(data, "alpha", "alpha")
    spec_b = _make_spec(data, "beta", "beta")
    out = tmp_path / "out"
    for spec in (spec_a, spec_b, spec_a):
        result = _build(spec, data, out, tmp_path)
        assert result.exit_code == 0, result.output

    shared = out / "shared"
    assert (shared / manifest_filename(spec_b.name)).is_file()
    assert list((shared / "beta-en").rglob("file.txt"))


def test_a_second_spec_with_the_same_slug_is_refused(tmp_path: Path) -> None:
    data = _make_repo(tmp_path)
    spec_a = _make_spec(data, "alpha", "same")
    spec_c = _make_spec(data, "gamma", "same")
    out = tmp_path / "out"

    first = _build(spec_a, data, out, tmp_path)
    assert first.exit_code == 0, first.output
    before = (out / "shared" / manifest_filename(spec_a.name)).read_text(encoding="utf-8")

    second = _build(spec_c, data, out, tmp_path)

    assert second.exit_code != 0
    assert "alpha" in second.output
    assert "same-" in second.output
    assert not (out / "shared" / manifest_filename(spec_c.name)).exists()
    assert (out / "shared" / manifest_filename(spec_a.name)).read_text(encoding="utf-8") == before


def test_a_legacy_shared_manifest_migrates_without_stranding_other_specs(tmp_path: Path) -> None:
    """A root from before #1095: one ``.clm-manifest.json``, written by beta.

    Alpha's tree is already there too. Alpha's rebuild must still count the
    root as clm's (or its sweep would be refused and it would never get a
    manifest), and must leave beta's legacy manifest in place.
    """
    data = _make_repo(tmp_path)
    spec_a = _make_spec(data, "alpha", "alpha")
    spec_b = _make_spec(data, "beta", "beta")
    out = tmp_path / "out"
    for spec in (spec_a, spec_b):
        result = _build(spec, data, out, tmp_path)
        assert result.exit_code == 0, result.output
    shared = out / "shared"
    # Recreate the pre-#1095 state: only beta's manifest, under the legacy name.
    (shared / manifest_filename(spec_a.name)).unlink()
    (shared / manifest_filename(spec_b.name)).rename(shared / MANIFEST_FILENAME)
    stale = shared / "alpha-en" / "stale.html"
    stale.write_text("from a renamed section", encoding="utf-8")

    result = _build(spec_a, data, out, tmp_path)

    assert result.exit_code == 0, result.output
    assert not stale.exists(), "alpha's sweep must not be refused after the migration"
    assert (shared / manifest_filename(spec_a.name)).is_file()
    assert (shared / MANIFEST_FILENAME).is_file(), "beta's legacy manifest is not alpha's to drop"
