"""Tests for the Phase 0 CLI restructure: verb groups + deprecated aliases.

The flat top-level commands (``normalize-slides``, ``extract-voiceover``,
``resolve-topic``, ...) were moved under verb groups in CLM 1.6, with the
old names kept as deprecated aliases for two minor releases. These tests
verify three properties:

1. New canonical invocations work (``clm slides normalize``, ``clm
   voiceover extract``, ``clm topic resolve``, ``clm authoring rules``).
2. Old top-level aliases still work and produce the same behavior.
3. Each alias emits a deprecation notice naming the new invocation, so
   users get an immediately actionable migration hint.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from clm.cli.commands._aliases import deprecated_alias
from clm.cli.main import cli


def _invoke(args: list[str], tmp_path: Path | None = None) -> click.testing.Result:
    """Invoke the top-level ``cli`` group with a CliRunner."""
    return CliRunner().invoke(cli, args)


# ---------------------------------------------------------------------------
# deprecated_alias helper unit tests
# ---------------------------------------------------------------------------


class TestDeprecatedAliasHelper:
    def test_alias_runs_target_callback(self):
        # The target command's *name* is the name users will see and the
        # name the alias is registered under. The migration message
        # uses target.name verbatim, so test fixtures must align names.
        invocations: list[tuple] = []

        @click.command("old-name")
        @click.argument("name")
        @click.option("--flag", is_flag=True)
        def target(name: str, flag: bool) -> None:
            invocations.append((name, flag))

        alias = deprecated_alias(target, new_invocation="new-group new-name")

        @click.group()
        def root() -> None: ...

        root.add_command(alias)
        result = CliRunner().invoke(root, ["old-name", "hello", "--flag"])

        assert result.exit_code == 0
        assert invocations == [("hello", True)]

    def test_alias_emits_migration_notice(self):
        @click.command("old-name")
        def target() -> None: ...

        alias = deprecated_alias(target, new_invocation="new path")

        @click.group()
        def root() -> None: ...

        root.add_command(alias)
        result = CliRunner().invoke(root, ["old-name"])

        assert result.exit_code == 0
        assert "DeprecationWarning" in result.output
        assert "`clm old-name` is deprecated" in result.output
        assert "Use `clm new path` instead" in result.output

    def test_alias_help_marks_deprecated(self):
        @click.command("old-name", help="Do a thing.")
        def target() -> None: ...

        alias = deprecated_alias(target, new_invocation="new path")

        @click.group()
        def root() -> None: ...

        root.add_command(alias)
        result = CliRunner().invoke(root, ["old-name", "--help"])

        assert result.exit_code == 0
        # Click renders the deprecation tag as "(Deprecated)" in 8.1.x
        # and "(DEPRECATED)" in 8.3.x — compare case-insensitively.
        assert "(deprecated)" in result.output.lower()

    def test_alias_inherits_params(self):
        @click.command("old-name")
        @click.argument("name")
        @click.option("--count", type=int, default=1)
        def target(name: str, count: int) -> None: ...

        alias = deprecated_alias(target, new_invocation="new path")
        # Alias should expose the same parameter list (by reference).
        assert alias.params is not target.params  # different list object
        assert len(alias.params) == len(target.params)
        # Parameters themselves are the same objects.
        for a, c in zip(alias.params, target.params):
            assert a is c

    def test_alias_raises_for_callback_less_target(self):
        # Groups have no callback unless explicitly set; the helper is
        # designed for plain commands.
        @click.group("g")
        def g() -> None: ...

        # Click groups DO have a callback set (a pass-through). Force the
        # callback-less case by constructing a Command directly.
        no_cb = click.Command(name="no-callback")
        with pytest.raises(TypeError, match="requires target.callback"):
            deprecated_alias(no_cb, new_invocation="x")


# ---------------------------------------------------------------------------
# CLI structure: new groups expose canonical subcommands
# ---------------------------------------------------------------------------


class TestNewGroupStructure:
    @pytest.mark.parametrize(
        "group_path",
        [
            ["slides", "--help"],
            ["topic", "--help"],
            ["authoring", "--help"],
        ],
    )
    def test_groups_are_reachable(self, group_path: list[str]) -> None:
        result = _invoke(group_path)
        assert result.exit_code == 0

    @pytest.mark.parametrize(
        "subcommand_path",
        [
            ["slides", "normalize", "--help"],
            ["slides", "language-view", "--help"],
            ["slides", "suggest-sync", "--help"],
            ["slides", "search", "--help"],
            ["topic", "resolve", "--help"],
            ["authoring", "rules", "--help"],
        ],
    )
    def test_canonical_subcommands_resolve(self, subcommand_path: list[str]) -> None:
        result = _invoke(subcommand_path)
        assert result.exit_code == 0
        # Help output should not announce deprecation for canonical paths.
        # Case-insensitive guard against Click version variance (8.1.x
        # renders "(Deprecated)"; 8.3.x renders "(DEPRECATED)").
        assert "(deprecated)" not in result.output.lower()


# ---------------------------------------------------------------------------
# Deprecation aliases: old top-level names still work
# ---------------------------------------------------------------------------


# Mapping: (old-flat-name, new-canonical-path)
ALIAS_RENAMES: list[tuple[str, str]] = [
    ("normalize-slides", "slides normalize"),
    ("language-view", "slides language-view"),
    ("suggest-sync", "slides suggest-sync"),
    ("search-slides", "slides search"),
    ("resolve-topic", "topic resolve"),
    ("authoring-rules", "authoring rules"),
    ("validate-slides", "validate"),
    ("validate-spec", "validate"),
]


class TestDeprecatedTopLevelAliases:
    @pytest.mark.parametrize("old, new", ALIAS_RENAMES)
    def test_alias_is_registered(self, old: str, new: str) -> None:
        result = _invoke([old, "--help"])
        assert result.exit_code == 0
        # Each alias help should self-describe as deprecated.
        # Case-insensitive: Click 8.1.x renders "(Deprecated)";
        # Click 8.3.x renders "(DEPRECATED)".
        assert "(deprecated)" in result.output.lower()

    @pytest.mark.parametrize("old, new", ALIAS_RENAMES)
    def test_alias_help_does_not_emit_warning(self, old: str, new: str) -> None:
        # --help should *not* invoke the callback, so the migration
        # notice should not fire. Click's own "(deprecated)" tag still
        # shows up in the help text — but the "use X instead" line is
        # callback-only.
        result = _invoke([old, "--help"])
        assert "Use `clm" not in result.output


class TestExtractInlineVoiceoverAliases:
    # Conditional on the [voiceover] extra being installed. The CLI
    # registration in main.py only adds these aliases when the
    # voiceover_group import succeeded.
    def _voiceover_available(self) -> bool:
        from clm.cli.main import voiceover_group

        return voiceover_group is not None

    def test_extract_alias_registered(self) -> None:
        if not self._voiceover_available():
            pytest.skip("voiceover extra not installed")
        result = _invoke(["extract-voiceover", "--help"])
        assert result.exit_code == 0
        # Case-insensitive — see TestDeprecatedAliasHelper above.
        assert "(deprecated)" in result.output.lower()

    def test_inline_alias_registered(self) -> None:
        if not self._voiceover_available():
            pytest.skip("voiceover extra not installed")
        result = _invoke(["inline-voiceover", "--help"])
        assert result.exit_code == 0
        assert "(deprecated)" in result.output.lower()

    def test_canonical_voiceover_extract_resolves(self) -> None:
        if not self._voiceover_available():
            pytest.skip("voiceover extra not installed")
        result = _invoke(["voiceover", "extract", "--help"])
        assert result.exit_code == 0
        assert "(deprecated)" not in result.output.lower()

    def test_canonical_voiceover_inline_resolves(self) -> None:
        if not self._voiceover_available():
            pytest.skip("voiceover extra not installed")
        result = _invoke(["voiceover", "inline", "--help"])
        assert result.exit_code == 0
        assert "(deprecated)" not in result.output.lower()


# ---------------------------------------------------------------------------
# Unified `clm validate` dispatch
# ---------------------------------------------------------------------------


class TestValidateDispatch:
    def _write_spec(self, path: Path) -> Path:
        path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections/>
</course>
""",
            encoding="utf-8",
        )
        return path

    def _write_slide(self, path: Path) -> Path:
        path.write_text(
            '# %% [markdown] lang="en" tags=["slide"]\n# # Hello\n',
            encoding="utf-8",
        )
        return path

    def test_xml_dispatches_to_spec_validator(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path / "course.xml")
        # Provide a slides dir for the spec validator's data-dir
        # inference. We just need _something_ that exists.
        slides_dir = tmp_path / "slides"
        slides_dir.mkdir()
        result = _invoke(["validate", str(spec), "--data-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_py_file_dispatches_to_slide_validator(self, tmp_path: Path) -> None:
        slide = self._write_slide(tmp_path / "slides_test.py")
        result = _invoke(["validate", str(slide)])
        # The slide-validator's exit code depends on findings; we only
        # care that we got past dispatch. Any non-usage exit is fine.
        assert "Cannot infer validator kind" not in result.output

    def test_directory_dispatches_to_slide_validator(self, tmp_path: Path) -> None:
        slide_dir = tmp_path / "slides"
        slide_dir.mkdir()
        self._write_slide(slide_dir / "slides_test.py")
        result = _invoke(["validate", str(slide_dir)])
        assert "Cannot infer validator kind" not in result.output

    def test_kind_spec_rejects_slides_only_flags(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path / "course.xml")
        result = _invoke(["validate", str(spec), "--kind=spec", "--quick"])
        assert result.exit_code != 0
        assert "slides-only" in result.output

    def test_kind_slides_rejects_spec_only_flags(self, tmp_path: Path) -> None:
        slide = self._write_slide(tmp_path / "slides_test.py")
        result = _invoke(["validate", str(slide), "--kind=slides", "--include-disabled"])
        assert result.exit_code != 0
        assert "spec-only" in result.output

    def test_kind_spec_rejects_non_xml(self, tmp_path: Path) -> None:
        slide = self._write_slide(tmp_path / "slides_test.py")
        result = _invoke(["validate", str(slide), "--kind=spec"])
        assert result.exit_code != 0
        assert "--kind=spec requires an .xml file" in result.output
