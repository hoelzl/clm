"""Tests for the ``clm export agent-guide`` command.

Covers the live-source section builders, the deterministic core, the issues
block (gh mocked) and its exclusion from the staleness gate, and the
``--check`` gate's four outcomes (fresh / missing / version drift / content
drift).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from click.testing import CliRunner

# The MCP tool list is a core part of the guide, so the whole command (and these
# tests) need the [mcp] extra.
pytest.importorskip("mcp.server.fastmcp", reason="mcp SDK not installed (needs [mcp] extra)")

# Fetch the *module* via importlib: the ``export`` package's __init__ binds the
# ``agent_guide`` command onto the package, shadowing the submodule attribute, so
# ``import ...agent_guide as ag`` would resolve to the Command, not the module.
ag = importlib.import_module("clm.cli.commands.export.agent_guide")
from clm.__version__ import __version__  # noqa: E402
from clm.cli.commands.export.agent_guide import (  # noqa: E402
    CURATED_COMMANDS,
    DEFAULT_OUTPUT_NAME,
    _command_short_help,
    _mcp_tools,
    build_core,
    build_guide,
    extract_core,
    extract_stamp,
)
from clm.cli.main import cli  # noqa: E402

REPO = Path("tests/test-data")  # has course-specs/ + slides/


def _runner() -> CliRunner:
    """A runner that keeps stderr separate where the Click version allows it."""
    try:
        return CliRunner(mix_stderr=False)  # Click < 8.2
    except TypeError:
        return CliRunner()  # Click 8.2+ removed the kwarg (stderr already separate)


def _combined(result) -> str:
    """stdout + stderr, robust across Click versions."""
    out = result.output or ""
    try:
        out += result.stderr or ""
    except (ValueError, AttributeError):
        pass
    return out


# ---------------------------------------------------------------------------
# Live-source builders
# ---------------------------------------------------------------------------
class TestBuilders:
    def test_curated_commands_all_resolve(self):
        # Drift guard: a rename in the CLI must not leave a curated entry dangling.
        missing = [" ".join(p) for p in CURATED_COMMANDS if not _command_short_help(p)]
        assert not missing, f"curated commands no longer resolve: {missing}"

    def test_command_short_help_unknown_is_none(self):
        assert _command_short_help(("definitely", "not", "a", "command")) is None

    def test_mcp_tools_nonempty_and_described(self):
        tools = dict(_mcp_tools())
        assert tools, "no MCP tools enumerated"
        assert "validate" in tools
        assert "course_context" in tools
        assert all(desc for desc in tools.values()), "an MCP tool has no description"

    def test_build_core_is_deterministic(self):
        assert build_core(REPO, __version__) == build_core(REPO, __version__)

    def test_core_has_expected_sections(self):
        core = build_core(REPO, __version__)
        assert f"Generated from clm {__version__}." in core
        for heading in (
            "## Documentation index (`clm info`)",
            "## Key commands (summary)",
            "## MCP tools (live surface)",
            "## Repo layout",
            "## Key paths",
        ):
            assert heading in core, f"missing section: {heading}"
        # Live content from each source.
        assert "`spec-files`" in core  # clm info index
        assert "`clm build`" in core  # curated command
        assert "`course_context`" in core  # MCP tool
        assert "test-spec-1.xml" in core  # repo layout
        assert "$CLM_CACHE_DIR" in core  # portable key-path rule
        # Portability: no embedded absolute paths from this machine.
        assert "C:\\Users" not in core and "/home/" not in core

    def test_repo_layout_without_course_specs(self, tmp_path):
        core = build_core(tmp_path, __version__)
        assert "No `course-specs/` directory found" in core


# ---------------------------------------------------------------------------
# Core / issues separation
# ---------------------------------------------------------------------------
class TestExtract:
    def test_extract_stamp(self):
        assert extract_stamp(build_core(REPO, __version__)) == __version__

    def test_extract_stamp_absent(self):
        assert extract_stamp("no stamp here") is None

    def test_extract_core_drops_issues_block(self):
        core = build_core(REPO, __version__)
        issues = (
            f"{ag._ISSUES_MARKER} volatile -->\n## Open issues\n- #1 foo\n"
            "<!-- END agent-guide:issues -->"
        )
        full = build_guide(REPO, __version__, issues_block=issues)
        assert "#1 foo" in full
        assert extract_core(full) == core


# ---------------------------------------------------------------------------
# CLI: generation
# ---------------------------------------------------------------------------
class TestCli:
    def test_help(self):
        r = _runner().invoke(cli, ["export", "agent-guide", "--help"])
        assert r.exit_code == 0
        assert "cheat-sheet" in r.output.lower()

    def test_listed_under_export_group(self):
        r = _runner().invoke(cli, ["export", "--help"])
        assert r.exit_code == 0
        assert "agent-guide" in r.output

    def test_stdout(self):
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "--stdout"])
        assert r.exit_code == 0, _combined(r)
        assert "# CLM Agent Guide" in r.output
        assert "## MCP tools (live surface)" in r.output

    def test_writes_default_output_name(self, tmp_path):
        r = _runner().invoke(cli, ["export", "agent-guide", str(tmp_path)])
        assert r.exit_code == 0, _combined(r)
        assert (tmp_path / DEFAULT_OUTPUT_NAME).exists()

    def test_written_file_uses_lf(self, tmp_path):
        out = tmp_path / "AG.md"
        _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out)])
        assert b"\r\n" not in out.read_bytes()


# ---------------------------------------------------------------------------
# CLI: --check staleness gate
# ---------------------------------------------------------------------------
class TestCheckGate:
    def _generate(self, tmp_path: Path) -> Path:
        out = tmp_path / DEFAULT_OUTPUT_NAME
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out)])
        assert r.exit_code == 0, _combined(r)
        return out

    def test_fresh_passes(self, tmp_path):
        out = self._generate(tmp_path)
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--check"])
        assert r.exit_code == 0, _combined(r)
        assert "up to date" in _combined(r)

    def test_missing_fails(self, tmp_path):
        out = tmp_path / "nope.md"
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--check"])
        assert r.exit_code == 1
        assert "missing" in _combined(r)

    def test_version_drift_fails(self, tmp_path):
        out = self._generate(tmp_path)
        text = out.read_text(encoding="utf-8")
        out.write_text(
            text.replace(f"Generated from clm {__version__}.", "Generated from clm 0.0.1."),
            encoding="utf-8",
            newline="\n",
        )
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--check"])
        assert r.exit_code == 1
        assert "0.0.1" in _combined(r)

    def test_content_drift_fails(self, tmp_path):
        out = self._generate(tmp_path)
        text = out.read_text(encoding="utf-8")
        out.write_text(text.rstrip() + "\n\nINJECTED CORE LINE\n", encoding="utf-8", newline="\n")
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--check"])
        assert r.exit_code == 1
        assert "content drift" in _combined(r)

    def test_check_ignores_issues_block(self, tmp_path):
        # A fresh core carrying a (volatile) issues block still passes the gate.
        out = tmp_path / DEFAULT_OUTPUT_NAME
        core = build_core(REPO, __version__)
        issues = (
            f"{ag._ISSUES_MARKER} vol -->\n## Open issues\n\nAs of 2099-01-01\n"
            "- #999 — something\n<!-- END agent-guide:issues -->"
        )
        out.write_text(core + "\n" + issues + "\n", encoding="utf-8", newline="\n")
        r = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--check"])
        assert r.exit_code == 0, _combined(r)


# ---------------------------------------------------------------------------
# --with-issues (gh mocked)
# ---------------------------------------------------------------------------
class _FakeProc:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class TestIssues:
    def test_issues_block_sorted_and_fenced(self, monkeypatch):
        monkeypatch.setattr(
            ag.subprocess,
            "run",
            lambda *a, **k: _FakeProc(
                '[{"number": 5, "title": "Bug A"}, {"number": 2, "title": "Bug B"}]'
            ),
        )
        block = ag._issues_block("hoelzl/clm", "agent-impact", "2026-01-01")
        assert ag._ISSUES_MARKER in block
        assert "<!-- END agent-guide:issues -->" in block
        assert "2026-01-01" in block
        assert block.index("#2 — Bug B") < block.index("#5 — Bug A")  # sorted by number

    def test_issues_block_empty(self, monkeypatch):
        monkeypatch.setattr(ag.subprocess, "run", lambda *a, **k: _FakeProc("[]"))
        block = ag._issues_block("hoelzl/clm", "agent-impact", "2026-01-01")
        assert "No open `agent-impact` issues" in block

    def test_issues_block_gh_missing(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError

        monkeypatch.setattr(ag.subprocess, "run", boom)
        import click

        with pytest.raises(click.ClickException, match="GitHub CLI"):
            ag._issues_block("hoelzl/clm", "agent-impact", "2026-01-01")

    def test_with_issues_cli_then_check_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            ag.subprocess,
            "run",
            lambda *a, **k: _FakeProc('[{"number": 7, "title": "Open thing"}]'),
        )
        out = tmp_path / "AG.md"
        r = _runner().invoke(
            cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--with-issues"]
        )
        assert r.exit_code == 0, _combined(r)
        assert "#7 — Open thing" in out.read_text(encoding="utf-8")
        # The volatile issues block must not trip the gate.
        r2 = _runner().invoke(cli, ["export", "agent-guide", str(REPO), "-o", str(out), "--check"])
        assert r2.exit_code == 0, _combined(r2)
