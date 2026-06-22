"""Decision (B) made structural: the agent-path ``sync`` module imports no model client.

Epic #440 lifts the four embedded OpenRouter/Ollama clients (and the legacy all-in-one
command that drives them) into ``sync_autopilot``, registered on the verb group *lazily*.
The guarantee these tests pin: importing ``clm.cli.commands.slides.sync`` — the module the
agent verbs (report / verify / task / accept / apply) live in, loaded on every ``clm
slides`` invocation — pulls in **neither** the OpenAI SDK **nor** the autopilot module, so
"no live LLM on the agent path / in CI" is a property of the import graph rather than a
discipline. The check runs in a *fresh subprocess* because ``sys.modules`` is shared across
the test session (other tests import ``openai`` / ``sync_autopilot`` long before this runs).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_importing_sync_loads_neither_openai_nor_autopilot():
    probe = _run_probe(
        """
        import sys
        import clm.cli.commands.slides.sync as s

        AUTOPILOT = "clm.cli.commands.slides.sync_autopilot"
        assert "openai" not in sys.modules, "openai must not load on the agent path"
        assert AUTOPILOT not in sys.modules, "the autopilot command module must be lazy"

        # The agent module binds NO model-client class.
        for name in (
            "OpenRouterSyncJudge",
            "OpenRouterSlideTranslator",
            "OpenRouterAlignmentRecoverer",
            "OpenRouterCorrespondenceVerifier",
            "OllamaSyncJudge",
        ):
            assert not hasattr(s, name), f"{name} must not be reachable from the agent module"

        # autopilot is registered, but only as a lazy spec — listing it does not import it.
        assert "autopilot" in s.slides_sync_group.list_commands(None)
        assert "report" in s.slides_sync_group.commands  # a real (eager) agent verb
        assert AUTOPILOT not in sys.modules

        print("OK")
        """
    )
    assert probe.returncode == 0, probe.stderr or probe.stdout
    assert probe.stdout.strip().endswith("OK")


def test_resolving_autopilot_loads_it_but_still_not_openai():
    # Accessing slides_sync_cmd (PEP 562 back-compat / the lazy verb spec) imports the
    # autopilot module — but even THAT does not construct a client, so the OpenAI SDK
    # stays unloaded until a model is actually called.
    probe = _run_probe(
        """
        import sys
        from clm.cli.commands.slides.sync import slides_sync_cmd  # PEP 562 -> lazy import

        assert slides_sync_cmd is not None
        assert "clm.cli.commands.slides.sync_autopilot" in sys.modules
        assert "openai" not in sys.modules, "merely importing autopilot must not load the SDK"
        print("OK")
        """
    )
    assert probe.returncode == 0, probe.stderr or probe.stdout
    assert probe.stdout.strip().endswith("OK")
