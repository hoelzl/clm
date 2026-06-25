"""Helpers for mapping the ``serial`` marker to per-resource xdist load groups.

Kept out of ``conftest.py`` so both the collection hook (which imports it) and
the meta-test (``tests/test_serial_xdist_groups.py``) import the same pure
function unambiguously.
"""

from __future__ import annotations


def serial_group_name(resource_class: str | None) -> str:
    """Map a ``serial`` marker's resource class to its xdist load-group name.

    ``@pytest.mark.serial`` (no arg) maps to the default ``"serial"`` group;
    ``@pytest.mark.serial("subproc")`` maps to ``"serial-subproc"``. Distinct
    classes get distinct, stable group names, so under ``--dist loadgroup`` each
    heavy resource family is serialized *within itself* while the families run
    on DIFFERENT workers concurrently — instead of every serial test funnelling
    onto one worker (the single-bucket bottleneck that M-1 removes).

    The group name is deterministic and prefixed with ``serial`` so it never
    collides with an unrelated ``xdist_group`` a test might set for its own
    reasons.
    """
    return "serial" if resource_class is None else f"serial-{resource_class}"
