"""The shared bare-command-is-report Click groups (#959).

Both strategies are pinned directly here; the end-to-end behavior is also
covered by the sync/harvest CLI suites (bare ``sync DECK`` / bare
``harvest DECK VIDEO`` run ``report``).
"""

from __future__ import annotations

import click
from click.testing import CliRunner

from clm.cli._default_verb_group import DefaultVerbGroup, DefaultVerbLazyGroup


def _verbs(group: click.Group) -> click.Group:
    @group.command("report")
    @click.argument("args", nargs=-1)
    def report(args: tuple[str, ...]) -> None:
        click.echo(f"report:{','.join(args)}")

    @group.command("write")
    def write() -> None:
        click.echo("write")

    return group


class TestDefaultVerbGroup:
    def test_bare_args_run_the_default_verb(self):
        group = _verbs(DefaultVerbGroup("tool"))
        result = CliRunner().invoke(group, ["deck.md"])
        assert result.output == "report:deck.md\n"

    def test_explicit_verb_is_not_rewritten(self):
        group = _verbs(DefaultVerbGroup("tool"))
        result = CliRunner().invoke(group, ["write"])
        assert result.output == "write\n"

    def test_help_is_not_rewritten(self):
        group = _verbs(DefaultVerbGroup("tool"))
        result = CliRunner().invoke(group, ["--help"])
        assert result.exit_code == 0
        assert "report" in result.output


class TestDefaultVerbLazyGroup:
    def test_bare_args_run_the_default_verb(self):
        group = _verbs(DefaultVerbLazyGroup("tool"))
        result = CliRunner().invoke(group, ["deck.md", "video.mp4"])
        assert result.output == "report:deck.md,video.mp4\n"

    def test_group_option_is_not_treated_as_the_default_verbs_args(self):
        @click.group("tool", cls=DefaultVerbLazyGroup)
        @click.option("--flag", is_flag=True)
        def tool(flag: bool) -> None:
            click.echo(f"flag:{flag}")

        @tool.command("report")
        @click.argument("args", nargs=-1)
        def report(args: tuple[str, ...]) -> None:
            click.echo(f"report:{','.join(args)}")

        result = CliRunner().invoke(tool, ["--flag", "deck.md"])
        assert result.output == "flag:True\nreport:deck.md\n"

    def test_unknown_flag_still_errors(self):
        group = _verbs(DefaultVerbLazyGroup("tool"))
        result = CliRunner().invoke(group, ["--bogus"])
        assert result.exit_code != 0
