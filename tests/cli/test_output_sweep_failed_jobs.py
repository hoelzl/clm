"""The stray-file sweep scoped around failed jobs (#923).

When a build records per-job errors, the write registry is incomplete only
for those jobs' outputs. Instead of skipping the whole sweep (which left
stale duplicate decks everywhere after a spec restructure), the sweep runs
with the failed jobs' outputs protected: the directory holding a failed
output is left untouched together with everything below it (a job may
write companion files next to its output, so the exact set of missing
writes is unknowable). Nothing else is special-cased.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.build.output_sweep import sweep_stray_files
from clm.core.output_write_registry import OutputWriteRegistry


def _record(registry: OutputWriteRegistry, path: Path, content: bytes = b"x") -> None:
    registry.record_write(path, content=content, source=path)


def _make_file(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def tree(tmp_path: Path):
    """Two section dirs; the deck in ``week2`` failed this build.

    ``week1/old_deck.ipynb`` is a genuine stray (a deck moved out of week1).
    ``week1/failed.ipynb`` is the failed deck's copy at its OLD location —
    an ordinary stray too.
    ``week2/failed.ipynb`` is its (stale) copy at the current location.
    ``week2/failed_files/plot.png`` is a companion of the failed output.
    ``week2/nested/deeper/x.txt`` is a stray two levels below the protected dir.
    ``week2/other_stray.ipynb`` is a stray inside the protected directory.
    ``week2/ok.ipynb`` was written this build.
    """
    root = tmp_path / "out"
    registry = OutputWriteRegistry()
    ok = _make_file(root / "week2" / "ok.ipynb")
    _record(registry, ok)
    files = {
        "old_deck": _make_file(root / "week1" / "old_deck.ipynb"),
        "failed_old_copy": _make_file(root / "week1" / "failed.ipynb"),
        "failed_current": _make_file(root / "week2" / "failed.ipynb"),
        "companion": _make_file(root / "week2" / "failed_files" / "plot.png"),
        "nested": _make_file(root / "week2" / "nested" / "deeper" / "x.txt"),
        "other_stray": _make_file(root / "week2" / "other_stray.ipynb"),
        "ok": ok,
    }
    (root / "week3").mkdir()
    files["empty_dir"] = root / "week3"
    return root, registry, files


def test_protects_the_failed_output_directory_tree_and_sweeps_the_rest(tree):
    root, registry, f = tree
    failed_output = root / "week2" / "failed.ipynb"

    report = sweep_stray_files([root], registry, failed_outputs=[failed_output])

    # The sweep did run: strays outside the protected tree are gone, and so
    # is the empty directory. That includes the failed deck's old-location
    # copy — an ordinary stray; the deck regenerates once it builds.
    assert not f["old_deck"].exists()
    assert not f["failed_old_copy"].exists()
    assert not f["empty_dir"].exists()
    # Everything under the failed output's directory survives, however deep.
    for key in ("failed_current", "companion", "nested", "other_stray", "ok"):
        assert f[key].exists(), key

    assert report.protected_dirs == [root / "week2"]
    assert set(report.deleted_files) == {f["old_deck"], f["failed_old_copy"]}
    assert report.unverified_roots == []
    assert report.skipped is False


def test_without_failed_outputs_behaviour_is_unchanged(tree):
    root, registry, f = tree

    report = sweep_stray_files([root], registry)

    assert not f["failed_current"].exists()
    assert not f["companion"].exists()
    assert not f["nested"].exists()
    assert f["ok"].exists()
    assert report.protected_dirs == []


def test_failed_output_outside_every_root_protects_nothing_there(tmp_path: Path):
    root = tmp_path / "out"
    stray = _make_file(root / "a" / "stray.ipynb")
    elsewhere = tmp_path / "elsewhere" / "deck.ipynb"

    report = sweep_stray_files([root], OutputWriteRegistry(), failed_outputs=[elsewhere])

    assert not stray.exists()
    assert report.protected_dirs == []


def test_failed_output_directly_under_the_root_protects_the_whole_root(tmp_path: Path):
    """The root is the failed output's directory: one rule, applied at the
    top of the walk, leaves the entire root alone."""
    root = tmp_path / "out"
    failed = _make_file(root / "failed.ipynb")
    stray = _make_file(root / "sub" / "stray.ipynb")

    report = sweep_stray_files([root], OutputWriteRegistry(), failed_outputs=[failed])

    assert failed.exists() and stray.exists()
    assert report.protected_dirs == [root]
    assert report.deleted_files == []


def test_unowned_root_with_a_protected_directory_is_unverified_not_refused(tmp_path: Path):
    """An empty deletion plan is ownership evidence only when the walk saw
    the whole tree. A protected directory was skipped, so the root gives no
    evidence: it stays unowned (``unverified_roots``) — but nothing would
    have been deleted, so it is NOT an ownership refusal."""
    root = tmp_path / "out"
    failed = _make_file(root / "week2" / "failed.ipynb")
    _make_file(root / "week2" / "stray_inside.ipynb")

    report = sweep_stray_files(
        [root], OutputWriteRegistry(), failed_outputs=[failed], unowned_roots=[root]
    )

    assert report.unverified_roots == [root]
    assert report.refused_roots == []
    assert (root / "week2" / "stray_inside.ipynb").exists()
    assert report.deleted_files == []


def test_unowned_root_with_deletable_strays_is_still_refused(tmp_path: Path):
    """Protection does not weaken the S11 gate: a stray outside the protected
    tree under an unowned root still refuses the whole root."""
    root = tmp_path / "out"
    failed = _make_file(root / "week2" / "failed.ipynb")
    stray = _make_file(root / "week1" / "stray.ipynb")

    report = sweep_stray_files(
        [root], OutputWriteRegistry(), failed_outputs=[failed], unowned_roots=[root]
    )

    assert report.refused_roots == [root]
    assert report.unverified_roots == []
    assert stray.exists()


def test_dry_run_reports_protection_without_touching_anything(tree):
    root, registry, f = tree
    failed_output = root / "week2" / "failed.ipynb"

    report = sweep_stray_files([root], registry, failed_outputs=[failed_output], dry_run=True)

    assert report.dry_run is True
    assert all(p.exists() for p in f.values())
    assert report.protected_dirs == [root / "week2"]
    assert set(report.deleted_files) == {f["old_deck"], f["failed_old_copy"]}
