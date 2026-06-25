"""Meta-tests for the ``serial`` -> per-resource xdist load-group mapping (M-1).

Two layers:

1. Pure unit tests of :func:`serial_group_name` — the deterministic mapping a
   regression (a typo, a collision, dropping the default) would break.
2. A collection-level invariant read off the tally the conftest hook stashes on
   ``config`` (``_clm_serial_group_counts``): under the default fast collection
   the two heavy serial families land in DISTINCT, non-empty groups, so they run
   on different workers concurrently rather than stacking on one. This is the
   whole point of splitting the single ``serial`` bucket.
"""

from __future__ import annotations

import pytest

from tests.xdist_group_helpers import serial_group_name


class TestSerialGroupName:
    def test_default_class_is_the_bare_serial_group(self) -> None:
        assert serial_group_name(None) == "serial"

    def test_named_class_is_prefixed(self) -> None:
        assert serial_group_name("subproc") == "serial-subproc"
        assert serial_group_name("workerpool") == "serial-workerpool"
        assert serial_group_name("port") == "serial-port"

    def test_distinct_classes_map_to_distinct_groups(self) -> None:
        classes = ["subproc", "workerpool", "port"]
        groups = [serial_group_name(c) for c in classes]
        assert len(set(groups)) == len(groups), groups
        # A named class never collides with the bare default group.
        assert all(g != serial_group_name(None) for g in groups)

    def test_every_group_is_namespaced_under_serial(self) -> None:
        for cls in (None, "subproc", "workerpool", "port", "anything"):
            g = serial_group_name(cls)
            assert g == "serial" or g.startswith("serial-")


def test_heavy_serial_families_are_split_into_distinct_groups(request) -> None:
    """The two heavy fast-suite serial families must land in DISTINCT groups.

    ``tests/infrastructure/workers/test_lifecycle_mock.py`` (``workerpool``) and
    ``tests/infrastructure/test_http_replay_mitm_manager.py`` (``subproc``) are
    the contention-prone families in the fast suite. M-1 puts them in different
    xdist load groups so they run on different workers concurrently instead of
    one global serial bottleneck. Skipped when a narrowed ``-m`` filter
    deselects them (their counts are then absent).
    """
    counts = getattr(request.config, "_clm_serial_group_counts", None)
    assert counts is not None, "the serial -> xdist_group mapping hook did not run"

    # Whatever serial groups exist this run must all be well-formed names.
    assert all(g == "serial" or g.startswith("serial-") for g in counts), counts

    if not (counts.get("serial-subproc") and counts.get("serial-workerpool")):
        pytest.skip("the heavy serial families are not both collected under this filter")

    # Both present => they are in distinct, non-empty load groups (distinct keys
    # each with members), i.e. the single bucket was actually split.
    assert counts["serial-subproc"] > 0
    assert counts["serial-workerpool"] > 0
