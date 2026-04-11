"""Tests for the environment-aware pool-size cap helper.

See ``src/clm/infrastructure/workers/pool_size_cap.py`` and Fix 4 in
``docs/proposals/WORKER_CLEANUP_IMPLEMENTATION_PLAN.md`` for background.

The helper is deliberately pure and synchronous, so these tests monkey-
patch the cpu/mem probes and the ``CLM_MAX_WORKERS`` environment
variable to exercise every clamping branch without spinning up any
workers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clm.infrastructure.workers import pool_size_cap
from clm.infrastructure.workers.pool_size_cap import (
    PoolSizeCapResult,
    compute_pool_size_cap,
)


@pytest.fixture
def clear_env(monkeypatch):
    """Ensure CLM_MAX_WORKERS is not set for the duration of a test.

    The test worker (pytest-xdist) may inherit the env var from the
    parent shell; each test should control it explicitly.
    """
    monkeypatch.delenv("CLM_MAX_WORKERS", raising=False)
    return monkeypatch


def _mock_caps(monkeypatch, *, cpu_cap: int, mem_cap: int) -> None:
    """Pin both machine-derived caps so the only remaining variables are
    ``requested`` and ``explicit_cap``. Patches the private helpers
    directly rather than monkey-patching os.cpu_count / psutil so the
    intent of each test is explicit."""
    monkeypatch.setattr(pool_size_cap, "_compute_cpu_cap", lambda: cpu_cap)
    monkeypatch.setattr(pool_size_cap, "_compute_mem_cap", lambda: mem_cap)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_no_clamping_when_all_caps_are_higher(clear_env):
    """requested <= every cap → passes through unchanged."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)

    result = compute_pool_size_cap(4)

    assert isinstance(result, PoolSizeCapResult)
    assert result.effective == 4
    assert result.requested == 4
    assert result.cpu_cap == 16
    assert result.mem_cap == 16
    assert result.explicit_cap is None
    assert result.was_clamped is False


def test_cpu_cap_wins_when_smallest(clear_env):
    """cpu_cap = 2 on a small machine limits 8 → 2."""
    _mock_caps(clear_env, cpu_cap=2, mem_cap=16)

    result = compute_pool_size_cap(8)

    assert result.effective == 2
    assert result.was_clamped is True


def test_mem_cap_wins_when_smallest(clear_env):
    """mem_cap = 3 limits 8 → 3 even with plenty of CPU."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=3)

    result = compute_pool_size_cap(8)

    assert result.effective == 3
    assert result.was_clamped is True


def test_explicit_cap_wins_when_smallest(clear_env):
    """Operator-supplied explicit_cap overrides higher machine caps."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)

    result = compute_pool_size_cap(8, explicit_cap=2)

    assert result.effective == 2
    assert result.explicit_cap == 2
    assert result.was_clamped is True


def test_requested_wins_when_smallest(clear_env):
    """Nothing clamps if the caller asked for less than any cap.

    Guards the ``result.effective <= requested`` invariant.
    """
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)

    result = compute_pool_size_cap(1, explicit_cap=8)

    assert result.effective == 1
    assert result.was_clamped is False


def test_requested_zero_passes_through_unchanged(clear_env):
    """``requested=0`` means "disable this worker type" — do NOT floor at 1.

    Regression test for the Fix 5 CI failure where
    ``lifecycle_integration`` tests set
    ``plantuml_count=0, drawio_count=0`` to disable those worker
    types, but Fix 4's ``max(1, min(caps))`` floor silently promoted
    them to 1, starting unwanted plantuml/drawio workers. Production
    code (``get_worker_config``) passes ``type_config.count`` in
    directly, and that count can legitimately be zero.
    """
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)

    result = compute_pool_size_cap(0)

    assert result.effective == 0
    assert result.requested == 0
    assert result.was_clamped is False


def test_requested_negative_passes_through_unchanged(clear_env):
    """Negative requests also pass through (same semantics as zero)."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)

    result = compute_pool_size_cap(-2)

    assert result.effective == -2
    assert result.was_clamped is False


# ---------------------------------------------------------------------------
# CLM_MAX_WORKERS environment variable
# ---------------------------------------------------------------------------


def test_env_cap_read_when_no_explicit_cap(clear_env):
    """CLM_MAX_WORKERS is honoured when explicit_cap is None."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "3")

    result = compute_pool_size_cap(8)

    assert result.effective == 3
    assert result.explicit_cap == 3
    assert result.was_clamped is True


def test_explicit_cap_beats_env_cap(clear_env):
    """Explicit_cap is preferred over the env var when both are set.

    This keeps ``--max-workers`` authoritative: if the user passes it on
    the CLI, it overrides the shell env.
    """
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "3")

    result = compute_pool_size_cap(8, explicit_cap=5)

    assert result.effective == 5
    assert result.explicit_cap == 5


def test_env_cap_zero_is_ignored(clear_env):
    """CLM_MAX_WORKERS=0 is treated as 'no cap'."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "0")

    result = compute_pool_size_cap(8)

    assert result.effective == 8
    assert result.explicit_cap is None


def test_env_cap_negative_is_ignored(clear_env):
    """CLM_MAX_WORKERS=-1 is treated as 'no cap'."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "-1")

    result = compute_pool_size_cap(8)

    assert result.effective == 8
    assert result.explicit_cap is None


def test_env_cap_garbage_is_ignored(clear_env, caplog):
    """CLM_MAX_WORKERS='nope' must not crash pool start.

    The helper should log a WARNING and fall through to machine caps.
    """
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "nope")

    with caplog.at_level("WARNING", logger="clm.infrastructure.workers.pool_size_cap"):
        result = compute_pool_size_cap(8)

    assert result.effective == 8
    assert result.explicit_cap is None
    assert any("CLM_MAX_WORKERS" in rec.message for rec in caplog.records)


def test_empty_env_cap_is_ignored(clear_env):
    """CLM_MAX_WORKERS='' (empty string) is treated as unset."""
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "")

    result = compute_pool_size_cap(8)

    assert result.effective == 8
    assert result.explicit_cap is None


def test_explicit_zero_falls_through_to_env(clear_env):
    """explicit_cap <= 0 is treated as 'no explicit cap' and env is also skipped.

    Callers pass 0 (or negative) to mean 'clear the cap, let machine
    caps handle it'. In that case we do NOT fall through to the env
    var — zero is an explicit "I said no cap" from the caller.
    """
    _mock_caps(clear_env, cpu_cap=16, mem_cap=16)
    clear_env.setenv("CLM_MAX_WORKERS", "3")

    result = compute_pool_size_cap(8, explicit_cap=0)

    assert result.effective == 8
    assert result.explicit_cap is None


# ---------------------------------------------------------------------------
# Machine cap computation fallbacks
# ---------------------------------------------------------------------------


def test_cpu_cap_falls_back_when_cpu_count_is_none(clear_env):
    """os.cpu_count() returning None (possible on exotic systems) must
    not crash the helper."""
    with patch("clm.infrastructure.workers.pool_size_cap.os.cpu_count", return_value=None):
        cap = pool_size_cap._compute_cpu_cap()

    # Plan idiom: ``(os.cpu_count() or 2) // 2`` → 1
    assert cap == 1


def test_cpu_cap_floor_is_one(clear_env):
    """A 1-core VM still gets at least 1 worker (not 0)."""
    with patch("clm.infrastructure.workers.pool_size_cap.os.cpu_count", return_value=1):
        cap = pool_size_cap._compute_cpu_cap()

    assert cap == 1


def test_mem_cap_uses_total_ram(clear_env):
    """mem_cap = floor(total_gb / 2) — 16 GB → 8 workers."""

    class FakeMem:
        total = 16 * (1024**3)

    with patch(
        "clm.infrastructure.workers.pool_size_cap.psutil.virtual_memory",
        return_value=FakeMem(),
    ):
        cap = pool_size_cap._compute_mem_cap()

    assert cap == 8


def test_mem_cap_floor_is_one(clear_env):
    """A 512 MB VM floors at 1 rather than returning 0."""

    class FakeMem:
        total = 512 * (1024**2)

    with patch(
        "clm.infrastructure.workers.pool_size_cap.psutil.virtual_memory",
        return_value=FakeMem(),
    ):
        cap = pool_size_cap._compute_mem_cap()

    assert cap == 1


def test_mem_cap_falls_back_when_psutil_raises(clear_env, caplog):
    """psutil failure must log and return the safe default of 1."""
    with patch(
        "clm.infrastructure.workers.pool_size_cap.psutil.virtual_memory",
        side_effect=RuntimeError("no /proc"),
    ):
        with caplog.at_level("WARNING", logger="clm.infrastructure.workers.pool_size_cap"):
            cap = pool_size_cap._compute_mem_cap()

    assert cap == 1
    assert any("virtual_memory" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Reason string
# ---------------------------------------------------------------------------


def test_format_reason_contains_all_caps(clear_env):
    """format_reason embeds every cap so operators can see *why* the
    clamping happened."""
    _mock_caps(clear_env, cpu_cap=8, mem_cap=6)

    result = compute_pool_size_cap(18, explicit_cap=4)
    reason = result.format_reason()

    assert "18" in reason
    assert "4" in reason
    assert "cpu_cap=8" in reason
    assert "mem_cap=6" in reason
    assert "explicit_cap=4" in reason
