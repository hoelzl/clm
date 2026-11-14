"""The nightly probe's pass/fail rule (``scripts/sync_probe.py``).

The nightly ``sync-probe`` job runs the sequence core with
``--require-converged``; these tests pin which outcomes count as converged,
so a later-round prefix (``r2:converged``) does not fail the night.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("sync_probe")


def _results(*statuses: tuple[str, str]) -> dict[str, dict]:
    return {f"c{i}": {"family": fam, "status": st} for i, (fam, st) in enumerate(statuses)}


def test_converged_cases_and_later_rounds_pass():
    results = _results(("a", "converged"), ("a", "r2:converged"), ("b", "converged"))
    assert not probe.unconverged(results)


def test_visible_and_silent_failures_are_counted_by_family_and_outcome():
    results = _results(
        ("a", "converged"),
        ("a", "manual_stall"),
        ("a", "manual_stall"),
        ("b", "silent_pick"),
        ("b", "r2:misplaced"),
    )
    assert probe.unconverged(results) == {
        ("a", "manual_stall"): 2,
        ("b", "silent_pick"): 1,
        ("b", "r2:misplaced"): 1,
    }
