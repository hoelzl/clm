"""Tests for shell completion support (``clm completion`` + PowerShell).

Covers:
- the ``clm completion <shell>`` command for every supported shell,
- that native shells delegate to Click's generator,
- that PowerShell emits a non-empty ``Register-ArgumentCompleter`` script,
- the PowerShell completion protocol (env-var parsing + formatting),
- registration of the PowerShell completion class with Click.
"""

import pytest
from click.shell_completion import get_completion_class
from click.testing import CliRunner

from clm.cli.commands.completion import _INSTALL_HINTS
from clm.cli.completion import (
    NATIVE_SHELLS,
    POWERSHELL_SHELL_NAME,
    SUPPORTED_SHELLS,
    PowerShellComplete,
    get_completion_source,
    register_powershell_completion,
)
from clm.cli.main import cli


class TestCompletionCommand:
    """The ``clm completion`` CLI command."""

    def test_completion_in_main_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "completion" in result.output

    def test_completion_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "--help"])
        assert result.exit_code == 0
        assert "SHELL" in result.output

    @pytest.mark.parametrize("shell", SUPPORTED_SHELLS)
    def test_emits_non_empty_script(self, shell):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", shell])
        assert result.exit_code == 0
        assert result.output.strip(), f"empty script for {shell}"

    def test_rejects_unknown_shell(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "nushell"])
        assert result.exit_code != 0

    @pytest.mark.parametrize("shell", SUPPORTED_SHELLS)
    def test_install_hint(self, shell):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", shell, "--install-hint"])
        assert result.exit_code == 0
        assert result.output.strip()
        # The hint should not be the activation script itself.
        assert "Register-ArgumentCompleter" not in result.output or shell != "bash"

    def test_install_hints_cover_all_shells(self):
        assert set(_INSTALL_HINTS) == set(SUPPORTED_SHELLS)


class TestNativeShellSource:
    """Native shells delegate to Click's generator."""

    @pytest.mark.parametrize("shell", NATIVE_SHELLS)
    def test_uses_click_native_generator(self, shell):
        source = get_completion_source(shell, prog_name="clm")
        # Click's native scripts reference the completion env var.
        assert "_CLM_COMPLETE" in source
        assert "clm" in source

    def test_bash_defines_completion_function(self):
        source = get_completion_source("bash")
        assert "_clm_completion" in source

    def test_fish_uses_complete_command(self):
        source = get_completion_source("fish")
        assert "complete" in source


class TestPowerShellSource:
    """PowerShell activation script."""

    def test_emits_register_argument_completer(self):
        source = get_completion_source("powershell")
        assert "Register-ArgumentCompleter" in source
        assert "-CommandName clm" in source
        assert "powershell_complete" in source

    def test_custom_prog_name(self):
        source = get_completion_source("powershell", prog_name="my-tool")
        assert "-CommandName my-tool" in source
        assert "_MY_TOOL_COMPLETE" in source

    def test_unsupported_shell_raises(self):
        with pytest.raises(ValueError, match="Unsupported shell"):
            get_completion_source("nushell")


class TestPowerShellRegistration:
    """The PowerShell completion class registers with Click."""

    def test_registered_under_powershell(self):
        register_powershell_completion()
        cls = get_completion_class(POWERSHELL_SHELL_NAME)
        assert cls is PowerShellComplete

    def test_registration_is_idempotent(self):
        register_powershell_completion()
        register_powershell_completion()
        assert get_completion_class(POWERSHELL_SHELL_NAME) is PowerShellComplete


class TestPowerShellCompletionProtocol:
    """The env-var-based completion protocol used at runtime."""

    def _make(self):
        return PowerShellComplete(
            cli=cli,
            ctx_args={},
            prog_name="clm",
            complete_var="_CLM_COMPLETE",
        )

    def test_parses_args_and_incomplete(self, monkeypatch):
        monkeypatch.setenv("COMP_LINE", "clm comp")
        monkeypatch.setenv("COMP_POINT", "8")
        monkeypatch.setenv("COMP_WORD_TO_COMPLETE", "comp")
        comp = self._make()
        args, incomplete = comp.get_completion_args()
        assert args == []
        assert incomplete == "comp"

    def test_empty_incomplete_after_space(self, monkeypatch):
        monkeypatch.setenv("COMP_LINE", "clm completion ")
        monkeypatch.setenv("COMP_POINT", "15")
        monkeypatch.setenv("COMP_WORD_TO_COMPLETE", "")
        comp = self._make()
        args, incomplete = comp.get_completion_args()
        assert args == ["completion"]
        assert incomplete == ""

    def test_subcommand_args_preserved(self, monkeypatch):
        monkeypatch.setenv("COMP_LINE", "clm completion ba")
        monkeypatch.setenv("COMP_POINT", "17")
        monkeypatch.setenv("COMP_WORD_TO_COMPLETE", "ba")
        comp = self._make()
        args, incomplete = comp.get_completion_args()
        assert args == ["completion"]
        assert incomplete == "ba"

    def test_invalid_comp_point_falls_back_to_line_length(self, monkeypatch):
        monkeypatch.setenv("COMP_LINE", "clm completion")
        monkeypatch.setenv("COMP_POINT", "not-an-int")
        monkeypatch.setenv("COMP_WORD_TO_COMPLETE", "completion")
        comp = self._make()
        args, incomplete = comp.get_completion_args()
        assert args == []
        assert incomplete == "completion"

    def test_format_completion_with_help(self):
        from click.shell_completion import CompletionItem

        comp = self._make()
        formatted = comp.format_completion(CompletionItem("build", help="Build it"))
        assert formatted == "build\tBuild it"

    def test_format_completion_without_help(self):
        from click.shell_completion import CompletionItem

        comp = self._make()
        formatted = comp.format_completion(CompletionItem("build"))
        assert formatted == "build\t"

    def test_end_to_end_completion_resolves_command(self, monkeypatch):
        """A partial top-level command resolves to a real completion."""
        monkeypatch.setenv("COMP_LINE", "clm comp")
        monkeypatch.setenv("COMP_POINT", "8")
        monkeypatch.setenv("COMP_WORD_TO_COMPLETE", "comp")
        comp = self._make()
        args, incomplete = comp.get_completion_args()
        items = comp.get_completions(args, incomplete)
        values = [item.value for item in items]
        assert "completion" in values
