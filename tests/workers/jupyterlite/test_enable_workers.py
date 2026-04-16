"""Tests for ``enable_jupyterlite_workers_if_needed`` in the build command.

Per the Phase 2 opt-in contract:
- A course with no ``jupyterlite`` targets must not spin up a worker.
- A course with at least one ``jupyterlite`` target bumps ``count`` to 1
  (preserving any operator-supplied override).
"""

from __future__ import annotations

from types import SimpleNamespace

from clm.cli.commands.build import enable_jupyterlite_workers_if_needed
from clm.infrastructure.config import WorkersManagementConfig


def _fake_course(jl_targets: int, non_jl_targets: int):
    targets = []
    for i in range(jl_targets):
        targets.append(
            SimpleNamespace(
                name=f"jl-{i}",
                includes_format=lambda fmt: fmt == "jupyterlite",
            )
        )
    for i in range(non_jl_targets):
        targets.append(
            SimpleNamespace(
                name=f"plain-{i}",
                includes_format=lambda fmt: False,
            )
        )
    return SimpleNamespace(output_targets=targets)


def test_no_jupyterlite_targets_leaves_count_unchanged() -> None:
    config = WorkersManagementConfig()
    course = _fake_course(jl_targets=0, non_jl_targets=2)
    enable_jupyterlite_workers_if_needed(course, config)
    assert config.jupyterlite.count is None


def test_jupyterlite_target_bumps_count_to_one() -> None:
    config = WorkersManagementConfig()
    course = _fake_course(jl_targets=1, non_jl_targets=1)
    enable_jupyterlite_workers_if_needed(course, config)
    assert config.jupyterlite.count == 1


def test_preserves_operator_override() -> None:
    config = WorkersManagementConfig()
    config.jupyterlite.count = 3
    course = _fake_course(jl_targets=1, non_jl_targets=0)
    enable_jupyterlite_workers_if_needed(course, config)
    assert config.jupyterlite.count == 3  # not reset to 1
