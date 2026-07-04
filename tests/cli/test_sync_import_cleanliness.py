"""Import-graph guarantees for the sync verb layer (post-#520-cutover).

The sync verbs are the agent path: ``clm slides sync`` must never load a
model client (no live LLM on the agent path / in CI is a property of the
import graph, not a discipline), and the engine's document modules must not
grow hidden couplings — ``doc_identity`` / ``doc_write`` stay importable
without the differ or the ledger (``clm harvest`` builds on them, #546).
Checks run in a *fresh subprocess* because ``sys.modules`` is shared across
the test session (other tests import ``openai`` long before this runs).

The v2 engine (``sync_plan`` / ``sync_apply`` / ``sync_code`` and friends)
was deleted at the Phase 4 cutover; a test here pins that it stays deleted so
a stray re-introduction is caught immediately.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import clm.slides


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_importing_sync_loads_no_model_client():
    probe = _run_probe(
        """
        import sys
        import clm.cli.commands.slides.sync as s

        # The OpenAI SDK must never load on the agent path. (The thin
        # openrouter_client shim may be imported by sibling commands like
        # `translate`, but it builds its client lazily — the SDK itself is
        # the thing that must stay out.)
        assert "openai" not in sys.modules, "openai must not load on the agent path"

        for verb in ("report", "apply", "verify", "record"):
            assert verb in s.slides_sync_group.commands

        print("OK")
        """
    )
    assert probe.returncode == 0, probe.stderr or probe.stdout
    assert probe.stdout.strip().endswith("OK")


def test_engine_modules_load_no_model_client():
    # The agents (and the MCP tool) drive the engine modules directly, not only
    # the CLI — so the model-free guarantee must hold there too.
    probe = _run_probe(
        """
        import sys
        import clm.slides.doc_report, clm.slides.doc_apply, clm.slides.doc_ledger

        for mod in (
            "openai",
            "clm.infrastructure.llm.openrouter_client",
            "clm.infrastructure.llm.ollama_client",
        ):
            assert mod not in sys.modules, f"{mod} must not load with the engine"
        print("OK")
        """
    )
    assert probe.returncode == 0, probe.stderr or probe.stdout
    assert probe.stdout.strip().endswith("OK")


def test_doc_identity_and_doc_write_import_no_sync_engine():
    # Harvest Phase 1 (#546): the identity/snapshot layer and the write
    # surface are the pieces `clm harvest` builds on, so they must be
    # importable without pulling in the differ or the ledger — otherwise
    # "sync-free consumer of the deck model" is a fiction.
    probe = _run_probe(
        """
        import sys
        import clm.slides.doc_identity, clm.slides.doc_write

        for mod in (
            "clm.slides.sync_diff",
            "clm.slides.doc_ledger",
        ):
            assert mod not in sys.modules, f"{mod} must not load from doc_identity/doc_write"
        print("OK")
        """
    )
    assert probe.returncode == 0, probe.stderr or probe.stdout
    assert probe.stdout.strip().endswith("OK")


def test_v2_core_modules_stay_deleted():
    # Phase 4 (#520) deleted the v2 plan/apply core. Pin the deletion at the
    # filesystem level: a re-introduced module would silently bifurcate the
    # engine again.
    slides_dir = Path(clm.slides.__file__).parent
    for name in (
        "sync_plan.py",
        "sync_apply.py",
        "sync_code.py",
        "sync_plan_walker.py",
        "sync_report.py",
        "sync_shadow.py",
        "sync_ledger.py",
        "sync_diagnose.py",
        "sync_task.py",
        "sync_accept.py",
        "sync_semantic.py",
        "sync_recover.py",
    ):
        assert not (slides_dir / name).exists(), f"{name} belongs to the deleted v2 core"
