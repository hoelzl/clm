"""Tests for ``scripts/collect_changelog.py``.

The script folds ``changelog.d/`` fragment files into a new release
section of CHANGELOG.md. The contract under test: fragments are grouped
into Keep-a-Changelog sections in canonical order, leftover hand-written
``[Unreleased]`` entries are folded in ahead of fragments, collected
fragments are deleted, and *no* changelog text is ever silently dropped
(malformed input must abort without modifying anything).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_changelog.py"
_spec = importlib.util.spec_from_file_location("collect_changelog", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
collect_changelog = importlib.util.module_from_spec(_spec)
sys.modules["collect_changelog"] = collect_changelog
_spec.loader.exec_module(collect_changelog)

NOTE = collect_changelog.UNRELEASED_NOTE

CHANGELOG_TEMPLATE = f"""# Changelog

All notable changes to CLM are documented in this file.

## [Unreleased]

{NOTE}
## [1.11.0] - 2026-06-10

### Added

- **Old entry.** Already released.
"""


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Point the script at a tmp CHANGELOG.md and changelog.d/."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG_TEMPLATE, encoding="utf-8")
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()
    (fragments / "README.md").write_text("conventions...\n", encoding="utf-8")
    monkeypatch.setattr(collect_changelog, "CHANGELOG", changelog)
    monkeypatch.setattr(collect_changelog, "FRAGMENTS_DIR", fragments)
    return tmp_path


def run(*argv: str) -> int:
    old_argv = sys.argv
    sys.argv = ["collect_changelog.py", *argv]
    try:
        return collect_changelog.main()
    finally:
        sys.argv = old_argv


class TestCollect:
    def test_fragments_become_release_section(self, fake_repo):
        frags = fake_repo / "changelog.d"
        (frags / "101-feature.added.md").write_text("- **Feature.** Does X.\n", encoding="utf-8")
        (frags / "102-bug.fixed.md").write_text("- **Bug.** Fixed Y.\n", encoding="utf-8")

        assert run("1.12.0", "--date", "2026-06-15") == 0

        text = (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8")
        added = text.index("## [1.12.0] - 2026-06-15")
        assert text.index("### Added") > added
        assert text.index("- **Feature.** Does X.") < text.index("### Fixed")
        assert "- **Bug.** Fixed Y." in text
        # Old releases preserved below the new section.
        assert text.index("## [1.11.0] - 2026-06-10") > text.index("- **Bug.** Fixed Y.")
        # Unreleased heading + note stay on top for the next cycle.
        assert text.index("## [Unreleased]") < added
        assert NOTE in text

    def test_collected_fragments_are_deleted_readme_kept(self, fake_repo):
        frags = fake_repo / "changelog.d"
        (frags / "101-feature.added.md").write_text("- entry\n", encoding="utf-8")

        assert run("1.12.0", "--date", "2026-06-15") == 0
        assert [p.name for p in frags.iterdir()] == ["README.md"]

    def test_sections_in_canonical_order_fragments_by_filename(self, fake_repo):
        frags = fake_repo / "changelog.d"
        (frags / "203-z.security.md").write_text("- sec entry\n", encoding="utf-8")
        (frags / "202-b.added.md").write_text("- added B\n", encoding="utf-8")
        (frags / "201-a.added.md").write_text("- added A\n", encoding="utf-8")
        (frags / "200-c.removed.md").write_text("- removed C\n", encoding="utf-8")

        assert run("1.12.0", "--date", "2026-06-15") == 0
        text = (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8")
        assert text.index("### Added") < text.index("### Removed") < text.index("### Security")
        assert text.index("- added A") < text.index("- added B")

    def test_leftover_unreleased_entries_fold_in_before_fragments(self, fake_repo):
        changelog = fake_repo / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8")
        text = text.replace(
            NOTE + "\n",
            NOTE + "\n### Added\n\n- hand-written entry\n\n",
        )
        changelog.write_text(text, encoding="utf-8")
        frags = fake_repo / "changelog.d"
        (frags / "101-feature.added.md").write_text("- fragment entry\n", encoding="utf-8")

        assert run("1.12.0", "--date", "2026-06-15") == 0
        result = changelog.read_text(encoding="utf-8")
        assert result.index("- hand-written entry") < result.index("- fragment entry")
        # The hand-written entry moved out of [Unreleased] into the release.
        unreleased = result[result.index("## [Unreleased]") : result.index("## [1.12.0]")]
        assert "hand-written" not in unreleased

    def test_default_date_is_today(self, fake_repo):
        from datetime import date

        (fake_repo / "changelog.d" / "1-x.added.md").write_text("- e\n", encoding="utf-8")
        assert run("1.12.0") == 0
        text = (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## [1.12.0] - {date.today().isoformat()}" in text

    def test_dry_run_modifies_nothing(self, fake_repo, capsys):
        frags = fake_repo / "changelog.d"
        (frags / "101-feature.added.md").write_text("- entry\n", encoding="utf-8")
        before = (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8")

        assert run("1.12.0", "--date", "2026-06-15", "--dry-run") == 0
        assert (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8") == before
        assert (frags / "101-feature.added.md").exists()
        assert "## [1.12.0] - 2026-06-15" in capsys.readouterr().out


class TestErrors:
    def expect_unchanged_failure(self, fake_repo, *argv: str) -> str:
        before_changelog = (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8")
        before_files = sorted(p.name for p in (fake_repo / "changelog.d").iterdir())
        assert run(*argv) == 1
        assert (fake_repo / "CHANGELOG.md").read_text(encoding="utf-8") == before_changelog
        assert sorted(p.name for p in (fake_repo / "changelog.d").iterdir()) == before_files
        return before_changelog

    def test_unknown_fragment_type(self, fake_repo, capsys):
        (fake_repo / "changelog.d" / "1-x.adged.md").write_text("- e\n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.12.0")
        assert "unknown fragment type" in capsys.readouterr().err

    def test_unrecognized_file_name(self, fake_repo, capsys):
        (fake_repo / "changelog.d" / "notes.txt").write_text("hi\n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.12.0")
        assert "unrecognized file" in capsys.readouterr().err

    def test_empty_fragment(self, fake_repo, capsys):
        (fake_repo / "changelog.d" / "1-x.added.md").write_text("  \n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.12.0")
        assert "is empty" in capsys.readouterr().err

    def test_nothing_to_collect(self, fake_repo, capsys):
        self.expect_unchanged_failure(fake_repo, "1.12.0")
        assert "nothing to collect" in capsys.readouterr().err

    def test_version_already_released(self, fake_repo, capsys):
        (fake_repo / "changelog.d" / "1-x.added.md").write_text("- e\n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.11.0")
        assert "already has a section" in capsys.readouterr().err

    def test_invalid_version(self, fake_repo, capsys):
        (fake_repo / "changelog.d" / "1-x.added.md").write_text("- e\n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.12")
        assert "not an X.Y.Z version" in capsys.readouterr().err

    def test_unknown_unreleased_section_heading(self, fake_repo, capsys):
        changelog = fake_repo / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8")
        changelog.write_text(
            text.replace(NOTE + "\n", NOTE + "\n### Improved\n\n- e\n\n"), encoding="utf-8"
        )
        (fake_repo / "changelog.d" / "1-x.added.md").write_text("- e\n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.12.0")
        assert "unknown section" in capsys.readouterr().err

    def test_unreleased_content_outside_section(self, fake_repo, capsys):
        changelog = fake_repo / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8")
        changelog.write_text(
            text.replace(NOTE + "\n", NOTE + "\n- stray bullet without a heading\n\n"),
            encoding="utf-8",
        )
        (fake_repo / "changelog.d" / "1-x.added.md").write_text("- e\n", encoding="utf-8")
        self.expect_unchanged_failure(fake_repo, "1.12.0")
        assert "outside any" in capsys.readouterr().err
