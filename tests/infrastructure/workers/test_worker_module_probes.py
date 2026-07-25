"""Guard against skip guards that can never be satisfied.

``test_direct_integration.py`` skipped its *entire* module — 8 Direct-mode
integration tests covering worker startup/registration, concurrent claiming,
health monitoring and graceful shutdown — for as long as it took nobody to
notice, because its ``find_spec`` probes named ``drawio_converter`` and
``plantuml_converter``: top-level modules that were folded into
``clm.workers.*`` long ago. A stale probe is invisible in a test report,
because "skipped" reads as "not applicable in this environment" rather than
"permanently dead".

The worker packages are *always* part of the installed ``clm`` distribution —
the optional extras add third-party dependencies, not these modules — so
``find_spec`` on them cannot legitimately fail. Anything the probes protect
against is therefore a rename, which is exactly what these assertions catch.

Keep this test in sync with the ``check_worker_module_available`` call sites
if a new worker package is added.
"""

from importlib.util import find_spec

import pytest

# Every module name passed to a ``check_worker_module_available`` /
# ``find_spec`` skip guard anywhere in the test suite.
PROBED_WORKER_MODULES = [
    "clm.workers.notebook",
    "clm.workers.drawio",
    "clm.workers.plantuml",
]


@pytest.mark.parametrize("module_name", PROBED_WORKER_MODULES)
def test_probed_worker_module_resolves(module_name: str) -> None:
    """A skip guard naming a nonexistent module silently kills its tests."""
    assert find_spec(module_name) is not None, (
        f"{module_name!r} does not resolve. Some test module gates its "
        f"tests on this name via find_spec and is now permanently skipped. "
        f"Update the probe rather than leaving it stale."
    )


def test_direct_integration_module_is_not_skipped() -> None:
    """The availability flags in ``test_direct_integration`` must all be true.

    Asserted on the real module attributes, not recomputed here, so a future
    edit to the probe names is caught at the place it happens.
    """
    from tests.infrastructure.workers import test_direct_integration as mod

    assert mod.NOTEBOOK_WORKER_AVAILABLE
    assert mod.DRAWIO_WORKER_AVAILABLE
    assert mod.PLANTUML_WORKER_AVAILABLE


def test_lifecycle_integration_module_is_not_skipped() -> None:
    """Same for ``test_lifecycle_integration``'s single availability flag."""
    from tests.infrastructure.workers import test_lifecycle_integration as mod

    assert mod.NOTEBOOK_WORKER_AVAILABLE
