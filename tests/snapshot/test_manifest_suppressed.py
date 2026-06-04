"""Integration guard: the build provenance manifest is suppressed under
``--snapshot`` / ``--verify-against`` (issue #208 follow-up 4).

The existing coverage is unit-level: ``_resolve_write_provenance_manifest`` /
``_should_emit_provenance_manifest`` matrices and a wiring test that checks the
flag passed *into* ``main_build`` (``tests/cli/test_build_command.py``). But the
actual gate **and** the ``write_provenance_manifests`` call both live *inside*
``main_build`` — so no test ran a real build to the filesystem and asserted the
manifest's presence/absence. These do.

The build here is a **dir-group-only** course: it copies files to output via
plain ``CopyFileOperation``s, so a real ``main_build`` runs end-to-end and
writes a real ``.clm-manifest.json`` **without** spawning notebook/plantuml
workers — deterministic and fast (~0.1s), no worker-lifecycle flakiness. Nothing
is stubbed, so a future refactor that moved the manifest write before/outside
the suppression gate (or into a path that ignores it) would fail the
``--snapshot`` assertion here.

* ``--snapshot DIR``      → real output under ``DIR`` but NO ``.clm-manifest.json``
  anywhere under it (the manifest embeds a timestamp + source commit and must
  never enter a byte-reproducibility baseline).
* ``--output-dir OUT``    → a ``.clm-manifest.json`` IS written (the positive
  control: proves the gate + writer fire, so the ``--snapshot`` absence is real
  suppression, not a vacuous pass).
* ``--verify-against``    → suppressed by the same gate.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands import build as build_module
from clm.core.provenance_manifest import MANIFEST_FILENAME

_SPEC = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections/>
    <dir-groups>
        <dir-group>
            <name>Extra</name>
            <path>extra</path>
        </dir-group>
    </dir-groups>
</course>
"""


def _make_course(tmp_path: Path) -> tuple[Path, Path]:
    """Lay out a minimal dir-group-only course; return ``(spec, data_dir)``.

    The course has no topics, so the real build needs no notebook workers — it
    only copies the ``extra/`` dir-group into each output variant.
    """
    data = tmp_path / "repo"
    (data / "slides").mkdir(parents=True)  # the build scans <data>/slides
    (data / "extra").mkdir(parents=True)
    (data / "extra" / "file.txt").write_text("hello", encoding="utf-8")
    spec = data / "course-specs" / "course.xml"
    spec.parent.mkdir(parents=True)
    spec.write_text(_SPEC, encoding="utf-8")
    return spec, data


def _invoke_build(args, tmp_path: Path):
    obj = {
        "CACHE_DB_PATH": tmp_path / "cache.db",
        "JOBS_DB_PATH": tmp_path / "jobs.db",
    }
    # Force Direct execution mode explicitly. These are dir-group-only builds that
    # never spawn workers, but the worker-config loader mutates the process-global
    # ``get_config()`` singleton's ``default_execution_mode`` in place — so an
    # earlier Docker-mode build/test on the same xdist worker can leave the default
    # at ``"docker"``, and a build here that did not override it would then fail with
    # "Docker execution mode requires 'image'" (a real, order-dependent flake seen on
    # CI). Overriding ``--workers direct`` makes this test immune to that leak. See
    # the load_worker_config singleton-mutation issue tracked separately.
    return CliRunner().invoke(build_module.build, [*args, "--workers", "direct"], obj=obj)


def _manifests_under(root: Path) -> list[Path]:
    return list(root.rglob(MANIFEST_FILENAME))


def test_snapshot_writes_no_provenance_manifest(tmp_path: Path) -> None:
    spec, data = _make_course(tmp_path)
    snap = tmp_path / "snap"

    result = _invoke_build([str(spec), "--data-dir", str(data), "--snapshot", str(snap)], tmp_path)

    assert result.exit_code == 0, result.output
    # The build really produced output (the dir-group was copied)...
    assert list(snap.rglob("file.txt")), "dir-group output should exist under the snapshot"
    # ...but the private provenance manifest must not appear anywhere under it.
    assert _manifests_under(snap) == []


def test_normal_build_writes_provenance_manifest(tmp_path: Path) -> None:
    """Positive control: the SAME course built to ``--output-dir`` DOES write a
    manifest, so the suppression above is real (the gate + writer fire)."""
    spec, data = _make_course(tmp_path)
    out = tmp_path / "out"

    result = _invoke_build([str(spec), "--data-dir", str(data), "--output-dir", str(out)], tmp_path)

    assert result.exit_code == 0, result.output
    assert (out / MANIFEST_FILENAME).is_file()


def test_verify_against_writes_no_provenance_manifest(tmp_path: Path) -> None:
    """``--verify-against`` is suppressed by the same gate: a verify build must
    not drop a timestamped manifest into its fresh output tree."""
    spec, data = _make_course(tmp_path)
    baseline = tmp_path / "baseline"
    assert (
        _invoke_build(
            [str(spec), "--data-dir", str(data), "--snapshot", str(baseline)], tmp_path
        ).exit_code
        == 0
    )
    assert _manifests_under(baseline) == []  # the baseline snapshot has none either

    out = tmp_path / "out2"
    result = _invoke_build(
        [
            str(spec),
            "--data-dir",
            str(data),
            "--output-dir",
            str(out),
            "--verify-against",
            str(baseline),
        ],
        tmp_path,
    )

    assert result.exit_code == 0, result.output
    assert _manifests_under(out) == []
