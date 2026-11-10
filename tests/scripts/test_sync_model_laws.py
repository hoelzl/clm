"""The sync reference model satisfies every sync law (``scripts/sync_model.py``).

``docs/claude/sync-structural-core-investigation.md`` proposes replacing the
structural core of ``clm slides sync`` and rests on a claim: its reference
model satisfies the oracle-free laws of ``tests/slides/sync_laws.py`` on every
small deck, where the engine does not. This is the test that executes that
claim, over the same cases the engine's ratchet runs
(``tests/slides/test_sync_laws.py``). It also keeps the model and
``scripts/sync_laws.py --model`` from rotting while the engine is unchanged.

The model's own, wider check is ``scripts/sync_model_laws.py`` (about four
minutes, run by hand).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from tests.slides.sync_laws import HOLDS, Case, cases, check

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_laws_script = _load("sync_laws")


def test_reference_model_satisfies_every_law() -> None:
    violations = {
        case.id: outcome
        for case in cases("ci")
        if (outcome := check(case, sync_laws_script.model_sync)) != HOLDS
    }
    assert not violations, dict(list(violations.items())[:10])


def test_reference_model_asks_about_identical_cells_each_half_edited() -> None:
    """Decision D3: two byte-identical recorded cells, each half edited one.
    Whether they edited the same cell cannot be read from the files, so the
    model asks instead of merging both edits."""
    asked = sync_laws_script.model_sync(("x", "x"), ("y", "x"), ("x", "z"))
    assert (asked.status, asked.detail) == ("asks", "conflict_copies")
    # one half alone editing a copy needs no question (the one-sided law)
    case = Case("one-sided", ("x", "x"), ("y", "x"), ("x", "x"))
    assert check(case, sync_laws_script.model_sync) == HOLDS
