"""End-to-end guard: an unverified output tree survives repeated builds.

Finding **S11** (umbrella #798) has a failure mode that only shows up
across *two* builds, and only in a real one. Build 1 declines to delete
from an output root it cannot prove is clm's — but if it still writes the
``.clm-manifest.json`` provenance index into that root, build 2 reads the
manifest as ownership evidence and deletes everything the build did not
write. The gate would authorize itself after one run.

Unit tests miss this twice over: the manifest write lives in ``run_build``
while the refusal happens inside ``process_course_with_backend``, and the
first version of the fix keyed the suppression on *the sweep having
refused* — so ``--no-sweep`` and ``--incremental``, which skip the sweep
entirely, sailed through and deleted the user's file on the second build
with no warning on either run (found in adversarial review of PR #864).

These tests therefore run the real ``clm build`` twice against a
pre-seeded output tree and assert on the file. The course is
dir-group-only — no topics, so no notebook/plantuml workers spawn and the
build is fast and deterministic (the harness is lifted from
``tests/snapshot/test_manifest_suppressed.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
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
        <dir-group>
            <name>Extra</name>
            <path>extra</path>
        </dir-group>
    </dir-groups>
</course>
"""


def _make_course(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "repo"
    (data / "slides").mkdir(parents=True)
    (data / "extra").mkdir(parents=True)
    (data / "extra" / "file.txt").write_text("hello", encoding="utf-8")
    spec = data / "course-specs" / "course.xml"
    spec.parent.mkdir(parents=True)
    spec.write_text(_SPEC, encoding="utf-8")
    return spec, data


def _build(spec: Path, data: Path, out: Path, tmp_path: Path, *extra: str):
    obj = {"CACHE_DB_PATH": tmp_path / "cache.db", "JOBS_DB_PATH": tmp_path / "jobs.db"}
    # ``--workers direct`` for the same reason as the sibling manifest
    # test: the worker-config loader mutates a process-global singleton,
    # so an earlier docker-mode test on this xdist worker could otherwise
    # leave the default at "docker".
    return CliRunner().invoke(
        build_module.build,
        [
            str(spec),
            "--data-dir",
            str(data),
            "--output-dir",
            str(out),
            *extra,
            "--workers",
            "direct",
        ],
        obj=obj,
    )


def _seed_foreign_file(out: Path) -> Path:
    """Put a file the build will never write into a target's output tree.

    ``--output-dir`` re-roots each default tier under ``<out>/<tier>/``, so
    this is inside the ``shared`` target root and inside the swept course
    directory below it.
    """
    precious = out / "shared" / "course-en" / "PRECIOUS.txt"
    precious.parent.mkdir(parents=True)
    precious.write_text("the user's notes", encoding="utf-8")
    return precious


@pytest.mark.parametrize(
    "first_build_flags",
    [
        pytest.param((), id="plain"),
        pytest.param(("--no-sweep",), id="no-sweep"),
        pytest.param(("--incremental",), id="incremental"),
    ],
)
def test_an_unverified_tree_is_never_adopted_by_a_first_build(
    tmp_path: Path, first_build_flags: tuple[str, ...]
) -> None:
    """Two builds, and the foreign file must survive both.

    The flags decide whether a sweep runs at all, which is exactly the
    hole the first fix left: no sweep means no refusal to key the
    manifest suppression on, so the credential got written anyway.
    """
    spec, data = _make_course(tmp_path)
    out = tmp_path / "out"
    precious = _seed_foreign_file(out)

    first = _build(spec, data, out, tmp_path, *first_build_flags)
    assert first.exit_code == 0, first.output
    assert precious.exists(), "the first build must not delete an unverified tree"
    assert not (out / "shared" / MANIFEST_FILENAME).exists(), (
        "the unverified target root must not receive the ownership credential"
    )

    second = _build(spec, data, out, tmp_path)
    assert second.exit_code == 0, second.output
    assert precious.exists(), "the second build inherited permission it was never granted"


def test_healthy_targets_keep_their_manifest(tmp_path: Path) -> None:
    """Only the unverified tier loses its manifest.

    Suppressing all of them would leave the healthy tiers unmarked — and
    therefore refused on the next build in turn, spreading one bad
    directory across the whole course.
    """
    spec, data = _make_course(tmp_path)
    out = tmp_path / "out"
    _seed_foreign_file(out)

    result = _build(spec, data, out, tmp_path)

    assert result.exit_code == 0, result.output
    assert not (out / "shared" / MANIFEST_FILENAME).exists()
    assert (out / "trainer" / MANIFEST_FILENAME).is_file()
    assert (out / "speaker" / MANIFEST_FILENAME).is_file()


def test_the_override_adopts_the_tree_once(tmp_path: Path) -> None:
    """``--allow-unowned-output`` is the documented way out.

    It deletes the unaccounted-for files *and* writes the manifest, so
    the tree is clm's from then on — otherwise the remedy in the refusal
    message would loop.
    """
    spec, data = _make_course(tmp_path)
    out = tmp_path / "out"
    precious = _seed_foreign_file(out)

    result = _build(spec, data, out, tmp_path, "--allow-unowned-output")

    assert result.exit_code == 0, result.output
    assert not precious.exists(), "the override must actually sweep"
    assert (out / "shared" / MANIFEST_FILENAME).is_file()


def test_a_fresh_tree_is_marked_and_swept(tmp_path: Path) -> None:
    """Positive control: nothing about a normal build changed.

    Without it, every assertion above could pass because the build never
    writes a manifest or never sweeps at all.
    """
    spec, data = _make_course(tmp_path)
    out = tmp_path / "out"

    first = _build(spec, data, out, tmp_path)
    assert first.exit_code == 0, first.output
    assert (out / "shared" / MANIFEST_FILENAME).is_file()

    stray = out / "shared" / "course-en" / "stale.html"
    stray.write_text("from a renamed section", encoding="utf-8")

    second = _build(spec, data, out, tmp_path)
    assert second.exit_code == 0, second.output
    assert not stray.exists(), "the sweep must still remove orphans in an owned tree"


def test_clean_refuses_an_unverified_tree_through_the_cli(tmp_path: Path) -> None:
    """``--clean`` fails with the remedy message, not a traceback."""
    spec, data = _make_course(tmp_path)
    out = tmp_path / "out"
    precious = _seed_foreign_file(out)

    result = _build(spec, data, out, tmp_path, "--clean")

    assert result.exit_code != 0
    assert precious.exists()
    assert "--allow-unowned-output" in result.output
    assert not isinstance(result.exception, BaseException) or isinstance(
        result.exception, SystemExit
    ), f"expected a rendered error, got {result.exception!r}"
