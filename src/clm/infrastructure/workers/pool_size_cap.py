"""Environment-aware worker pool-size cap.

This module computes the effective number of workers a pool should start,
clamping the requested count against the machine's CPU count, available
RAM, and any operator-supplied cap (``CLM_MAX_WORKERS`` environment
variable or ``--max-workers`` CLI flag).

Background
----------

Course repositories can override per-type worker counts in their spec
files. PythonCourses, for example, asks for 18 notebook workers. Those
numbers were tuned on a beefy build machine but, on a developer laptop
with 8 CPU cores and 16 GB of RAM, 18 parallel notebook workers churn
through memory fast enough to make the machine unresponsive and (per
the worker cleanup incident) trigger cascading kernel leaks.

The cap is *conservative on purpose*. A notebook worker runs a Jupyter
kernel plus the notebook processor, which pulls in nbclient, nbconvert,
jinja, and any ML libraries the course uses. Roughly budget ~2 GB per
worker and half a CPU core per worker. If the user knows their workload
is lighter they can always raise the cap via ``CLM_MAX_WORKERS`` or
``--max-workers``.

Design
------

The helper is pure and synchronous: it takes the requested count and an
optional explicit cap, reads ``CLM_MAX_WORKERS`` from the environment
when no explicit cap is given, and returns a ``(effective, details)``
tuple. The ``details`` dict carries the individual cap values so the
caller can log a precise, machine-readable message. Logging itself is
left to the caller to keep this module easy to unit-test without
``caplog`` contortions.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

import psutil  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Per-worker resource budget used to derive the memory-based cap.
# A notebook worker runs a Jupyter kernel + nbclient + nbconvert and any
# libraries imported by the cells. 2 GB is a conservative upper bound
# that accommodates courses that pull in pandas/scikit-learn/torch.
_MEM_GB_PER_WORKER = 2


@dataclass(frozen=True)
class PoolSizeCapResult:
    """Outcome of :func:`compute_pool_size_cap`.

    Attributes:
        effective: The clamped worker count the caller should actually
            use. Always ``>= 1`` and ``<= requested``.
        requested: The original count before clamping, for logging.
        cpu_cap: The CPU-derived cap. Always ``>= 1``.
        mem_cap: The memory-derived cap. Always ``>= 1``.
        explicit_cap: The operator-supplied cap (``CLM_MAX_WORKERS`` or
            ``--max-workers``), or ``None`` if none was set.
        was_clamped: ``True`` iff ``effective < requested``.
    """

    effective: int
    requested: int
    cpu_cap: int
    mem_cap: int
    explicit_cap: int | None
    was_clamped: bool

    def format_reason(self) -> str:
        """Render a human-readable "why" string for logging.

        Example::

            "Spec requested 18 workers; capping to 6 (cpu_cap=8,
             mem_cap=6, explicit_cap=None)"
        """
        return (
            f"Spec requested {self.requested} workers; capping to "
            f"{self.effective} (cpu_cap={self.cpu_cap}, "
            f"mem_cap={self.mem_cap}, explicit_cap={self.explicit_cap})"
        )


def _read_env_cap() -> int | None:
    """Read ``CLM_MAX_WORKERS`` from the environment, tolerating junk.

    The plan lets users set ``CLM_MAX_WORKERS`` as a short, memorable
    short-cut. It must gracefully ignore empty strings, zero, negative
    values, and non-integer values — clamping should never *raise* at
    pool-start time because of a malformed env var.

    Returns:
        A positive integer if ``CLM_MAX_WORKERS`` is set to one, else
        ``None``.
    """
    raw = os.environ.get("CLM_MAX_WORKERS")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Ignoring CLM_MAX_WORKERS={raw!r}: not a valid integer")
        return None
    if value <= 0:
        # Treat 0 and negatives as "unset" per the plan's
        # ``int(... or 0) or None`` idiom.
        return None
    return value


def _compute_cpu_cap() -> int:
    """Half of the visible CPU count, floored at 1.

    Notebook workers are single-process but launch Jupyter kernels
    that themselves spawn threads and subprocesses. Leaving half the
    cores free lets the rest of the system (OS scheduler, tests, the
    editor, the monitor UI) stay responsive during a big build.
    """
    count = os.cpu_count() or 2
    return max(1, count // 2)


def _compute_mem_cap() -> int:
    """Total RAM in GB divided by :data:`_MEM_GB_PER_WORKER`, floored at 1.

    Uses ``psutil.virtual_memory().total`` (promoted to a hard
    dependency in Fix 2). Total RAM — not *available* RAM — is the
    right input: pool caps should be deterministic across runs, not
    dependent on what else happens to be running at that instant.

    Falls back to ``1`` if psutil reports zero or negative RAM, which
    should never happen but would otherwise divide by a nonsense
    value.
    """
    try:
        # psutil is untyped for mypy, so the value is Any here; cast to
        # int explicitly so the arithmetic and the return type stay honest.
        total_bytes = int(psutil.virtual_memory().total)
    except Exception as exc:
        # psutil really should not fail on a modern OS, but if it
        # does we fall back to a safe single-worker cap instead of
        # letting a pool-start command crash.
        logger.warning(f"psutil.virtual_memory() failed: {exc}; mem_cap=1")
        return 1

    total_gb = total_bytes / (1024**3)
    if total_gb <= 0:
        return 1
    # math.floor is explicit; ``int(x)`` truncates toward zero which
    # behaves the same for positive numbers but is less obvious.
    return max(1, math.floor(total_gb / _MEM_GB_PER_WORKER))


def compute_pool_size_cap(requested: int, *, explicit_cap: int | None = None) -> PoolSizeCapResult:
    """Clamp ``requested`` against CPU, memory, and operator caps.

    Args:
        requested: The count the caller (spec file / CLI / default)
            asked for. ``requested <= 0`` means "do not run any
            workers of this type" and passes through **unchanged** so
            a spec/CLI can disable a worker type entirely (e.g. a
            plantuml-free course setting ``plantuml_count=0``).
            ``requested >= 1`` is clamped to ``max(1, min(caps))`` —
            the floor at 1 only applies to positive requests.
        explicit_cap: An operator-supplied cap from
            ``--max-workers``. If ``None``, the helper reads
            ``CLM_MAX_WORKERS`` from the environment instead. A
            non-None, non-positive value is treated as "no explicit
            cap".

    Returns:
        A :class:`PoolSizeCapResult` carrying the effective count,
        each individual cap, and a ``was_clamped`` flag so the caller
        can decide whether to log a warning.
    """
    # Normalise the caller-supplied explicit cap. Callers may pass 0
    # or a negative to mean "no cap", matching the CLM_MAX_WORKERS
    # handling below.
    effective_explicit: int | None
    if explicit_cap is not None and explicit_cap > 0:
        effective_explicit = explicit_cap
    elif explicit_cap is None:
        effective_explicit = _read_env_cap()
    else:
        effective_explicit = None

    cpu_cap = _compute_cpu_cap()
    mem_cap = _compute_mem_cap()

    # Zero / negative requests mean "disable this worker type". Pass
    # them through without touching the CPU/RAM/explicit caps — those
    # only matter for oversized positive requests, and flooring at 1
    # would silently re-enable a disabled type (the Fix 5 CI regression
    # where `plantuml_count=0` still spawned one plantuml worker).
    if requested <= 0:
        return PoolSizeCapResult(
            effective=requested,
            requested=requested,
            cpu_cap=cpu_cap,
            mem_cap=mem_cap,
            explicit_cap=effective_explicit,
            was_clamped=False,
        )

    # Build the list of caps to enforce. ``requested`` is always in
    # the list so the result can never exceed what the caller asked
    # for. All caps are guaranteed >= 1 by the individual helpers, so
    # the final ``max(1, ...)`` floor is only needed as a defensive
    # guard against future cap values of 0.
    caps: list[int] = [requested, cpu_cap, mem_cap]
    if effective_explicit is not None:
        caps.append(effective_explicit)

    effective = max(1, min(caps))

    return PoolSizeCapResult(
        effective=effective,
        requested=requested,
        cpu_cap=cpu_cap,
        mem_cap=mem_cap,
        explicit_cap=effective_explicit,
        was_clamped=effective < requested,
    )
