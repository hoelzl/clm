"""Meta-tests for the ``load_sensitive`` pre-push gate tier (issue #926).

The pre-push hook (``scripts/run_pytest_hook.py``) narrows the fast suite
with ``-m "... and not load_sensitive"`` so the local gate stays fast and
deterministic; CI and a plain ``uv run pytest`` run everything. The tagging
itself is automatic in ``tests/conftest.py`` (directory-based, see
``LOAD_SENSITIVE_TEST_DIRS``) — these tests pin that the wiring works:

1. every collected test under a listed family directory carries the marker;
2. the conftest hook's tally covers every listed family when unfiltered.

``load_fragile`` (issue #1040) is the per-test subset that the local release
gate excludes. Its section at the end pins that CI still runs those tests and
that the release docs give the gate command.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _pytest.mark.expression import Expression

from tests.conftest import LOAD_SENSITIVE_TEST_DIRS


def test_tagged_families_cover_listed_directories(request) -> None:
    """Every family in LOAD_SENSITIVE_TEST_DIRS has tagged items this run.

    Skipped per family when a narrowed ``-m`` filter deselects it (e.g. the
    pre-push tier itself, which deselects *all* of them).
    """
    counts = getattr(request.config, "_clm_load_sensitive_counts", None)
    assert counts is not None, "the load_sensitive tagging hook did not run"
    if not counts:
        pytest.skip("running under a filter that deselects the load_sensitive tier")
    for family in LOAD_SENSITIVE_TEST_DIRS:
        name = "/".join(family)
        assert counts.get(name, 0) > 0, f"no load_sensitive tests tagged under tests/{name}"


def test_collected_items_under_family_dirs_are_tagged(request) -> None:
    """Spot-check the tagging invariant from the item side: any collected
    item whose path sits under a family directory must carry the marker
    (this is what makes adding a test there need no manual marker)."""
    tests_root = Path(__file__).resolve().parent
    checked = 0
    for item in request.session.items:
        try:
            rel_parts = Path(str(item.fspath)).resolve().relative_to(tests_root).parts
        except ValueError:
            continue
        if any(rel_parts[: len(f)] == f for f in LOAD_SENSITIVE_TEST_DIRS):
            checked += 1
            assert item.get_closest_marker("load_sensitive") is not None, (
                f"{item.nodeid} sits under a load_sensitive family but is untagged"
            )
    if checked == 0:
        pytest.skip("no load_sensitive family items collected under this filter")


# ---------------------------------------------------------------------------
# The local release gate and ``load_fragile`` (issue #1040)
# ---------------------------------------------------------------------------

#: The documented gate. ``releasing.md`` is its canonical home; the other
#: files repeat the command and must not drift from it.
RELEASE_GATE_COMMAND = 'pytest -m "not docker and not load_fragile" -n 12'
RELEASE_GATE_DOCS = (
    "docs/developer-guide/releasing.md",
    "docs/developer-guide/architecture.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".claude/skills/clm-release/SKILL.md",
)
REPO_ROOT = Path(__file__).resolve().parent.parent
PYTEST_SELECTOR = re.compile(r"""pytest\b[^\n]*?\s-m\s+(?:"([^"]*)"|'([^']*)')""")


def _workflow_selectors(workflow: Path) -> list[str]:
    """Every ``pytest -m`` expression in one workflow file."""
    text = workflow.read_text(encoding="utf-8")
    return [double or single for double, single in PYTEST_SELECTOR.findall(text)]


def test_ci_selectors_are_read_from_the_workflows() -> None:
    """Guard for the two tests below: an unparsed workflow would pass them."""
    assert len(_workflow_selectors(REPO_ROOT / ".github/workflows/ci.yml")) == 5


def test_no_workflow_selector_excludes_load_fragile() -> None:
    """The marker keeps a test out of the local gate, never out of CI."""
    for workflow in (REPO_ROOT / ".github/workflows").glob("*.yml"):
        for selector in _workflow_selectors(workflow):
            assert "load_fragile" not in selector, f"{workflow.name}: {selector}"


def test_every_load_fragile_test_runs_in_a_ci_suite(request) -> None:
    """Evaluate ci.yml's real selectors against each marked test's markers."""
    marked = getattr(request.config, "_clm_load_fragile_markers", None)
    assert marked is not None, "the load_fragile tally in conftest did not run"
    if not marked:
        pytest.skip("no load_fragile test collected under this path selection")
    selectors = [
        Expression.compile(selector)
        for selector in _workflow_selectors(REPO_ROOT / ".github/workflows/ci.yml")
    ]
    for nodeid, markers in marked.items():
        assert any(
            selector.evaluate(lambda name, **_: name in markers) for selector in selectors
        ), f"{nodeid} (markers {sorted(markers)}) is selected by no CI suite"


def test_load_fragile_tests_are_left_out_of_the_deterministic_tier(request) -> None:
    """A marked test outside the directory families must not reach ``--tier``."""
    marked = getattr(request.config, "_clm_load_fragile_markers", None)
    if not marked:
        pytest.skip("no load_fragile test collected under this path selection")
    for nodeid, markers in marked.items():
        assert "load_sensitive" in markers, nodeid


@pytest.mark.parametrize("doc", RELEASE_GATE_DOCS)
def test_release_docs_give_the_gate_command(doc: str) -> None:
    """Each doc states the new gate, and none still gives the old one."""
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert RELEASE_GATE_COMMAND in text
    assert 'pytest -m "not docker"' not in text


def test_releasing_md_says_how_to_run_the_excluded_tests() -> None:
    text = (REPO_ROOT / "docs/developer-guide/releasing.md").read_text(encoding="utf-8")
    assert "pytest -m load_fragile" in text
