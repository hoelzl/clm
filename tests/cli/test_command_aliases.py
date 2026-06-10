"""Tests for the CLI command-tree structure.

The flat top-level commands (``normalize-slides``, ``extract-voiceover``,
``resolve-topic``, ...) were moved under verb groups in CLM 1.6, kept as
deprecated aliases through 1.7, and **removed in 1.8**. Issue #310 then
merged the single-command groups (``topic``, ``spec``, ``authoring``) and
remaining strays (``targets``, ``sync-includes``, ``delete-database``,
``polish``) into the domain groups ``course``, ``slides``, ``db``, and
``calendar`` — as a clean break, without aliases. These tests verify:

1. Canonical invocations work (``clm slides normalize``, ``clm course
   resolve-topic``, ``clm slides rules``, ``clm db delete``, ...).
2. Old names — both the 1.8 alias removals and the #310 regrouping —
   are no longer registered and error out.
3. Intentional synonyms (``slides bootstrap``, ``export summarize``)
   stay invocable but are hidden from ``--help``.
4. The unified ``clm validate`` dispatches to spec/slide validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.main import cli


def _invoke(args: list[str], tmp_path: Path | None = None):
    """Invoke the top-level ``cli`` group with a CliRunner."""
    return CliRunner().invoke(cli, args)


# ---------------------------------------------------------------------------
# CLI structure: new groups expose canonical subcommands
# ---------------------------------------------------------------------------


class TestNewGroupStructure:
    @pytest.mark.parametrize(
        "group_path",
        [
            ["slides", "--help"],
            ["course", "--help"],
            ["export", "--help"],
            ["calendar", "--help"],
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
            ["slides", "rules", "--help"],
            ["course", "resolve-topic", "--help"],
            ["course", "decks", "--help"],
            ["course", "orphans", "--help"],
            ["course", "targets", "--help"],
            ["course", "gate", "--help"],
            ["course", "sync-includes", "--help"],
            ["calendar", "generate", "--help"],
            ["db", "delete", "--help"],
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
# Deprecated flat aliases were removed in 1.8
# ---------------------------------------------------------------------------


# Top-level names that no longer exist, with the canonical invocation that
# replaced each one (kept here as documentation). The first block is the
# 1.8 removal of the 1.6-era deprecated aliases; the second block is the
# #310 regrouping (clean break, no aliases).
REMOVED_NAMES: list[tuple[str, str]] = [
    ("normalize-slides", "slides normalize"),
    ("language-view", "slides language-view"),
    ("suggest-sync", "slides suggest-sync"),
    ("search-slides", "slides search"),
    ("resolve-topic", "course resolve-topic"),
    ("authoring-rules", "slides rules"),
    ("validate-slides", "validate"),
    ("validate-spec", "validate"),
    ("extract-voiceover", "voiceover extract"),
    ("inline-voiceover", "voiceover inline"),
    # Issue #310 regrouping:
    ("topic", "course resolve-topic"),
    ("spec", "course decks / course orphans"),
    ("authoring", "slides rules"),
    ("targets", "course targets"),
    ("sync-includes", "course sync-includes"),
    ("delete-database", "db delete"),
    ("polish", "slides polish"),
]


class TestRemovedNames:
    @pytest.mark.parametrize("old, _new", REMOVED_NAMES)
    def test_name_is_not_registered(self, old: str, _new: str) -> None:
        assert old not in cli.commands

    @pytest.mark.parametrize("old, _new", REMOVED_NAMES)
    def test_invoking_name_errors(self, old: str, _new: str) -> None:
        result = _invoke([old, "--help"])
        assert result.exit_code != 0
        assert "No such command" in result.output

    def test_export_calendar_moved_to_calendar_generate(self) -> None:
        result = _invoke(["export", "calendar", "--help"])
        assert result.exit_code != 0
        assert "No such command" in result.output

    def test_voiceover_port_voiceover_renamed_to_port(self) -> None:
        from clm.cli.main import voiceover_group

        if voiceover_group is None:
            pytest.skip("voiceover extra not installed")
        result = _invoke(["voiceover", "port-voiceover", "--help"])
        assert result.exit_code != 0
        assert "No such command" in result.output


class TestHiddenSynonyms:
    """`bootstrap`/`summarize` stay invocable but appear in --help once."""

    @pytest.mark.parametrize(
        "path, canonical",
        [
            (["slides", "bootstrap", "--help"], "translate"),
            (["export", "summarize", "--help"], "summary"),
        ],
    )
    def test_synonym_invocable(self, path: list[str], canonical: str) -> None:
        result = _invoke(path)
        assert result.exit_code == 0

    @pytest.mark.parametrize(
        "group_path, synonym",
        [
            (["slides", "--help"], "bootstrap"),
            (["export", "--help"], "summarize"),
        ],
    )
    def test_synonym_hidden_from_group_help(self, group_path: list[str], synonym: str) -> None:
        result = _invoke(group_path)
        assert result.exit_code == 0
        assert synonym not in result.output


class TestCanonicalVoiceoverSubcommands:
    # Conditional on the [voiceover] extra being installed. The CLI
    # registration in main.py only adds these subcommands when the
    # voiceover_group import succeeded.
    def _voiceover_available(self) -> bool:
        from clm.cli.main import voiceover_group

        return voiceover_group is not None

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
