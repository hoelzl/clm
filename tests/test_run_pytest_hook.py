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


class TestPrePushTier:
    """The wrapper slims the gate to the deterministic tier (issue #926)."""

    def _captured_cmd(self, monkeypatch, argv):
        captured: list[str] = []

        class _FakeResult:
            returncode = 0

        def fake_run(cmd, env, **kwargs):
            captured.extend(cmd)
            return _FakeResult()

        monkeypatch.setattr(run_pytest_hook.subprocess, "run", fake_run)
        monkeypatch.setattr(run_pytest_hook.sys, "argv", argv)
        assert run_pytest_hook.main() == 0
        return captured

    def test_adds_load_sensitive_exclusion_by_default(self, monkeypatch):
        cmd = self._captured_cmd(monkeypatch, ["run_pytest_hook.py", "-q"])
        m_index = cmd.index("-m")
        assert cmd[m_index + 1] == run_pytest_hook.PRE_PUSH_MARKER_EXPR
        assert "not load_sensitive" in cmd[m_index + 1]

    def test_full_flag_runs_unfiltered_suite(self, monkeypatch):
        cmd = self._captured_cmd(monkeypatch, ["run_pytest_hook.py", "--full", "-q"])
        # The flag is consumed by the wrapper, not forwarded to pytest,
        # and no narrowing -m is appended (the ini addopts filter stands).
        assert "--full" not in cmd
        assert "-m" not in cmd

    def test_marker_expr_matches_pyproject_addopts_plus_exclusion(self):
        """The wrapper's filter must stay the ini addopts filter plus the
        ``load_sensitive`` clause — if addopts gains a new exclusion and the
        wrapper doesn't, the pre-push gate silently runs a broader tier
        than a plain ``uv run pytest``."""
        import re

        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        match = re.search(r"addopts\s*=\s*\"([^\"]+)\"", pyproject)
        assert match, "addopts not found in pyproject.toml"
        addopts = match.group(1)
        ini_match = re.search(r"-m\s*'([^']+)'", addopts)
        assert ini_match, "no -m expression in addopts"
        assert run_pytest_hook.PRE_PUSH_MARKER_EXPR == (
            f"{ini_match.group(1)} and not load_sensitive"
        )
