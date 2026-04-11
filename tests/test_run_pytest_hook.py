"""Regression tests for the pre-commit pytest wrapper.

See ``scripts/run_pytest_hook.py`` and
``docs/proposals/PRE_COMMIT_HOOK_HARDENING.md`` for context.

The wrapper's job is to strip git-injected environment variables before
invoking pytest, so that test subprocesses that shell out to ``git init``
in a ``tmp_path`` create their own gitdirs instead of writing into the
main repo's ``.git/`` directory. These tests pin down the list of
variables that must be cleared and verify the helper performs the clear
correctly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Import the wrapper module directly from ``scripts/`` — it's not part
# of the ``clm`` package, so we load it by file path.
_WRAPPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_pytest_hook.py"
_spec = importlib.util.spec_from_file_location("run_pytest_hook", _WRAPPER_PATH)
assert _spec is not None and _spec.loader is not None
run_pytest_hook = importlib.util.module_from_spec(_spec)
sys.modules["run_pytest_hook"] = run_pytest_hook
_spec.loader.exec_module(run_pytest_hook)


EXPECTED_LEAKING_GIT_VARS = {
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}


class TestLeakingGitVars:
    def test_covers_all_documented_variables(self):
        """The wrapper must clear every git-exported env var that git's
        hook protocol injects into subprocesses. Losing one of these
        re-introduces Problem 2 from PRE_COMMIT_HOOK_HARDENING.md.
        """
        assert set(run_pytest_hook.LEAKING_GIT_VARS) == EXPECTED_LEAKING_GIT_VARS

    def test_is_a_tuple(self):
        """The constant should be an immutable tuple so tests and
        callers can't accidentally mutate it."""
        assert isinstance(run_pytest_hook.LEAKING_GIT_VARS, tuple)


class TestEnvironmentClearing:
    def test_main_removes_vars_from_env_passed_to_subprocess(self, monkeypatch):
        """Calling ``main()`` must strip every leaking var from the env
        dict it passes to subprocess.run, regardless of which ones were
        actually set on entry."""
        # Set every leaking var in the process environment.
        for var in EXPECTED_LEAKING_GIT_VARS:
            monkeypatch.setenv(var, f"/some/path/for/{var}")
        # Also set an unrelated var to verify we don't strip too much.
        monkeypatch.setenv("CLM_TEST_KEEP_ME", "keep")

        captured_env: dict[str, str] = {}

        class _FakeResult:
            returncode = 0

        def fake_run(cmd, env, **kwargs):
            captured_env.update(env)
            return _FakeResult()

        monkeypatch.setattr(run_pytest_hook.subprocess, "run", fake_run)
        monkeypatch.setattr(run_pytest_hook.sys, "argv", ["run_pytest_hook.py"])

        rc = run_pytest_hook.main()
        assert rc == 0

        # All leaking vars should be absent from the env passed to subprocess.
        for var in EXPECTED_LEAKING_GIT_VARS:
            assert var not in captured_env, f"{var} leaked into subprocess env"
        # Unrelated vars should still be present.
        assert captured_env.get("CLM_TEST_KEEP_ME") == "keep"

    def test_main_tolerates_vars_not_being_set(self, monkeypatch):
        """Unsetting a var that wasn't set must not raise — the helper
        should handle the common case where only some of the leaking
        vars are present in a given commit transaction."""
        # Deliberately do NOT set any leaking vars.
        for var in EXPECTED_LEAKING_GIT_VARS:
            monkeypatch.delenv(var, raising=False)

        class _FakeResult:
            returncode = 7

        monkeypatch.setattr(
            run_pytest_hook.subprocess,
            "run",
            lambda *args, **kwargs: _FakeResult(),
        )
        monkeypatch.setattr(run_pytest_hook.sys, "argv", ["run_pytest_hook.py"])

        # Must not raise, and must propagate the subprocess exit code.
        assert run_pytest_hook.main() == 7
