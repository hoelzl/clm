"""Cross-group "Did you mean" hints on unknown commands (issue #1241).

``clm course outline`` and ``clm outline`` used to fail with a bare
``No such command 'outline'`` although ``clm export outline`` exists:
Click only suggests siblings of the failing group.
"""

from __future__ import annotations

import json
import subprocess
import sys

import click
import pytest
from click.testing import CliRunner

from clm.cli._command_hints import (
    CommandHintGroup,
    add_cross_group_hint,
    suggest_command_paths,
    walk_command_tree,
)
from clm.cli._command_index import COMMAND_PATHS
from clm.cli.main import cli


def _invoke(args: list[str]):
    return CliRunner().invoke(cli, args, prog_name="clm")


class TestCliHints:
    def test_top_level_unknown_suggests_group_command(self) -> None:
        """Regression test for #1241: ``clm outline``."""
        result = _invoke(["outline"])
        assert result.exit_code == 2
        assert "No such command 'outline'." in result.output
        assert "Did you mean: clm export outline" in result.output

    def test_wrong_group_suggests_right_group(self) -> None:
        """Regression test for #1241: ``clm course outline``."""
        result = _invoke(["course", "outline", "spec.xml"])
        assert result.exit_code == 2
        assert "Did you mean: clm export outline" in result.output

    def test_several_matches_are_all_listed(self) -> None:
        result = _invoke(["course", "verify"])
        assert result.exit_code == 2
        assert "Did you mean one of:" in result.output
        assert "  clm slides sync verify" in result.output

    def test_close_match_across_groups(self) -> None:
        result = _invoke(["outlne"])
        assert result.exit_code == 2
        assert "Did you mean: clm export outline" in result.output

    def test_sibling_near_miss_keeps_clicks_own_hint_only(self) -> None:
        result = _invoke(["export", "outlne"])
        assert result.exit_code == 2
        assert "Did you mean 'outline'?" in result.output
        assert "clm export outline" not in result.output

    def test_no_match_leaves_message_alone(self) -> None:
        result = _invoke(["zzz-no-such-thing"])
        assert result.exit_code == 2
        assert "No such command 'zzz-no-such-thing'." in result.output
        assert "Did you mean" not in result.output

    def test_other_usage_errors_untouched(self) -> None:
        result = _invoke(["export", "outline"])  # missing SPEC argument
        assert result.exit_code == 2
        assert "Did you mean" not in result.output


class TestSuggestCommandPaths:
    PATHS = (
        ("export",),
        ("export", "outline"),
        ("course",),
        ("course", "decks"),
        ("slides",),
        ("slides", "sync"),
        ("slides", "sync", "verify"),
        ("harvest",),
        ("harvest", "verify"),
    )

    def test_exact_matches_shortest_first(self) -> None:
        assert suggest_command_paths("verify", ("course",), self.PATHS) == [
            ("harvest", "verify"),
            ("slides", "sync", "verify"),
        ]

    def test_siblings_of_failing_group_skipped(self) -> None:
        assert suggest_command_paths("verify", ("harvest",), self.PATHS) == [
            ("slides", "sync", "verify"),
        ]

    def test_close_matches_only_without_exact(self) -> None:
        assert suggest_command_paths("outlin", (), self.PATHS) == [("export", "outline")]
        assert suggest_command_paths("outlin", (), self.PATHS, close_matches=False) == []

    def test_no_match(self) -> None:
        assert suggest_command_paths("frobnicate", (), self.PATHS) == []


class TestPlainClickFallback:
    def test_plain_usage_error_message_is_parsed(self) -> None:
        """Click < 8.4 raises a plain UsageError without ``command_name``."""
        ctx = click.Context(click.Group("clm"), info_name="clm")
        error = click.UsageError("No such command 'outline'.", ctx)
        add_cross_group_hint(error)
        assert error.message.endswith("Did you mean: clm export outline")

    def test_hint_added_once(self) -> None:
        ctx = click.Context(click.Group("clm"), info_name="clm")
        error = click.UsageError("No such command 'outline'.", ctx)
        add_cross_group_hint(error)
        add_cross_group_hint(error)
        assert error.message.count("Did you mean") == 1


class TestCommandIndex:
    def test_root_uses_hint_group(self) -> None:
        assert isinstance(cli, CommandHintGroup)

    def test_index_matches_live_tree(self) -> None:
        """The static index must list exactly the visible command tree.

        On failure run ``python scripts/update_cli_command_index.py``.
        """
        live = walk_command_tree(cli)
        optional = {"voiceover", "harvest", "recordings", "mcp"}
        missing_optional = optional - {p[0] for p in live}
        if missing_optional:
            pytest.skip(f"optional extras not installed: {sorted(missing_optional)}")
        assert list(COMMAND_PATHS) == live, (
            "src/clm/cli/_command_index.py is stale; run python scripts/update_cli_command_index.py"
        )

    def test_error_path_imports_no_command_modules(self) -> None:
        """The hint is built from the static index, not by importing commands."""
        code = (
            "import json, sys\n"
            "from clm.cli.main import cli\n"
            "try:\n"
            "    cli.main(['outline'], prog_name='clm', standalone_mode=False)\n"
            "except Exception as e:\n"
            "    msg = e.format_message()\n"
            "print(json.dumps({'msg': msg, 'mods': sorted(m for m in sys.modules\n"
            "    if m.startswith('clm.cli.commands'))}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout.strip().splitlines()[-1])
        assert "Did you mean: clm export outline" in data["msg"]
        assert data["mods"] == []

    def test_real_cli_prints_hint_to_stderr(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "clm.cli.main", "course", "outline"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "Did you mean:" in result.stderr
        assert "export outline" in result.stderr
