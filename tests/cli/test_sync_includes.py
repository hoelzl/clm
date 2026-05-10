"""Tests for the ``clm sync-includes`` CLI command.

These tests exercise the on-disk materialization of ``<include>``
declarations: copy/symlink/hardlink modes, the ``.clm-include`` ledger,
``--remove``, ``--gitignore``, and graceful symlink fallback when the
host filesystem refuses.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.sync_includes import LEDGER_NAME
from clm.cli.main import cli


def _write_spec(course_root: Path, sections_xml: str) -> Path:
    """Write a minimal course spec under ``course-specs/test.xml``."""
    spec_file = course_root / "course-specs" / "test.xml"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        dedent(f"""\
        <course>
          <name><de>Test</de><en>Test</en></name>
          <prog-lang>python</prog-lang>
          <description><de></de><en></en></description>
          <certificate><de></de><en></en></certificate>
          {sections_xml}
        </course>
        """),
        encoding="utf-8",
    )
    return spec_file


def _make_topic(course_root: Path, module: str, topic: str) -> Path:
    """Create a topic directory with a single slide file. Returns the topic path."""
    topic_dir = course_root / "slides" / module / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    (topic_dir / "slides_intro.py").write_text("# %% [markdown]\n# Hello\n", encoding="utf-8")
    return topic_dir


def _make_include_source(course_root: Path) -> Path:
    """Create a representative source package under ``examples/pkg/``."""
    src = course_root / "examples" / "pkg"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("VERSION = '1.0'\n", encoding="utf-8")
    (src / "core.py").write_text("def hello(): return 'hi'\n", encoding="utf-8")
    nested = src / "sub"
    nested.mkdir()
    (nested / "tool.py").write_text("# tool\n", encoding="utf-8")
    return src


def _invoke(*args: str) -> CliRunner.invoke:  # type: ignore[name-defined]
    """Shorthand for invoking the CLI with mix_stderr=False."""
    runner = CliRunner(mix_stderr=False)
    return runner.invoke(cli, list(args))


class TestSyncIncludesCopyMode:
    def test_copy_directory_include(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0
        topic_dir = tmp_path / "slides" / "module_100" / "topic_010_intro"
        # Materialized content
        assert (topic_dir / "pkg" / "__init__.py").is_file()
        assert (topic_dir / "pkg" / "core.py").is_file()
        assert (topic_dir / "pkg" / "sub" / "tool.py").is_file()
        # Ledger
        ledger_path = topic_dir / LEDGER_NAME
        assert ledger_path.is_file()
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        as_paths = [e["as_path"] for e in data["entries"]]
        assert as_paths == ["pkg"]
        assert data["entries"][0]["mode"] == "copy"
        assert data["entries"][0]["source"] == "examples/pkg"

    def test_copy_file_include(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        env_file = tmp_path / "examples" / ".env.example"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("KEY=value\n", encoding="utf-8")
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/.env.example" as=".env.example"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0
        topic_dir = tmp_path / "slides" / "module_100" / "topic_010_intro"
        assert (topic_dir / ".env.example").read_text(encoding="utf-8") == "KEY=value\n"
        data = json.loads((topic_dir / LEDGER_NAME).read_text(encoding="utf-8"))
        assert data["entries"][0]["as_path"] == ".env.example"

    def test_idempotent_rerun_refreshes(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        src = _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        # First run.
        first = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))
        assert first.exit_code == 0

        # Modify source.
        (src / "core.py").write_text("def hello(): return 'updated'\n", encoding="utf-8")

        # Second run: should refresh, not error.
        second = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))
        assert second.exit_code == 0
        topic_dir = tmp_path / "slides" / "module_100" / "topic_010_intro"
        assert "updated" in (topic_dir / "pkg" / "core.py").read_text(encoding="utf-8")
        assert "refreshed" in second.stdout

    def test_untracked_target_is_left_alone(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        # User pre-creates a real pkg/ dir, before any sync. No ledger present.
        (topic_dir / "pkg").mkdir()
        (topic_dir / "pkg" / "hand_written.py").write_text("# local\n", encoding="utf-8")

        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0
        # Local file survived; include did not overwrite the user's pkg dir.
        assert (topic_dir / "pkg" / "hand_written.py").is_file()
        # No ledger entry: nothing was materialized.
        assert not (topic_dir / LEDGER_NAME).exists()
        assert "shadowed" in result.stderr.lower() or "shadow" in result.stdout.lower()


class TestSyncIncludesOptionalAndMissing:
    def test_optional_missing_source_is_silent(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/missing" as="pkg" optional="true"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0

    def test_required_missing_source_errors(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/missing" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 1
        assert "missing" in result.stderr.lower() or "missing" in result.stdout.lower()


class TestSyncIncludesRemove:
    def test_remove_deletes_only_ledger_entries(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))
        # Drop an unrelated file alongside the include — must survive --remove.
        bystander = topic_dir / "user_file.txt"
        bystander.write_text("keep me\n", encoding="utf-8")

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path), "--remove")

        assert result.exit_code == 0
        assert not (topic_dir / "pkg").exists()
        assert not (topic_dir / LEDGER_NAME).exists()
        assert bystander.read_text(encoding="utf-8") == "keep me\n"

    def test_remove_without_prior_sync_is_noop(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path), "--remove")

        assert result.exit_code == 0


class TestSyncIncludesSymlinkAndHardlink:
    def test_hardlink_directory(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        src = _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke(
            "sync-includes",
            str(spec),
            "--data-dir",
            str(tmp_path),
            "--mode",
            "hardlink",
        )

        assert result.exit_code == 0
        target_file = topic_dir / "pkg" / "core.py"
        source_file = src / "core.py"
        assert target_file.is_file()
        # On filesystems that support hardlinks (NTFS, ext4, APFS), the two
        # paths share an inode. Be tolerant on Windows: we accept either
        # hardlink success or copy fallback.
        if sys.platform != "win32":
            assert target_file.stat().st_ino == source_file.stat().st_ino

    def test_symlink_fallback_to_copy_on_oserror(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        # Force the symlink call to fail so we exercise the fallback path
        # deterministically on any host.
        with patch(
            "clm.cli.commands.sync_includes.os.symlink",
            side_effect=OSError("simulated no-perm"),
        ):
            result = _invoke(
                "sync-includes",
                str(spec),
                "--data-dir",
                str(tmp_path),
                "--mode",
                "symlink",
            )

        assert result.exit_code == 0
        assert (topic_dir / "pkg" / "core.py").is_file()
        # Ledger records the effective mode after fallback.
        data = json.loads((topic_dir / LEDGER_NAME).read_text(encoding="utf-8"))
        assert data["entries"][0]["mode"] == "copy"
        assert "fallback" in result.stdout.lower() or "fallback" in result.stderr.lower()

    @pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows")
    def test_symlink_directory_on_posix(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke(
            "sync-includes",
            str(spec),
            "--data-dir",
            str(tmp_path),
            "--mode",
            "symlink",
        )

        assert result.exit_code == 0
        target = topic_dir / "pkg"
        assert target.is_symlink()
        data = json.loads((topic_dir / LEDGER_NAME).read_text(encoding="utf-8"))
        assert data["entries"][0]["mode"] == "symlink"


class TestSyncIncludesSectionInheritance:
    def test_section_default_applies_to_all_topics(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_topic(tmp_path, "module_100", "topic_020_deep")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <include source="examples/pkg" as="pkg"/>
              <topics>
                <topic>intro</topic>
                <topic>deep</topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0
        intro_dir = tmp_path / "slides" / "module_100" / "topic_010_intro"
        deep_dir = tmp_path / "slides" / "module_100" / "topic_020_deep"
        assert (intro_dir / "pkg" / "core.py").is_file()
        assert (deep_dir / "pkg" / "core.py").is_file()
        assert (intro_dir / LEDGER_NAME).is_file()
        assert (deep_dir / LEDGER_NAME).is_file()

    def test_topic_override_replaces_section_default(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_topic(tmp_path, "module_100", "topic_020_deep")
        _make_include_source(tmp_path)
        # An override target pointing at a different source.
        alt = tmp_path / "examples" / "alt"
        alt.mkdir(parents=True)
        (alt / "alt_module.py").write_text("# alt\n", encoding="utf-8")

        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <include source="examples/pkg" as="pkg"/>
              <topics>
                <topic>intro</topic>
                <topic>
                  deep
                  <include source="examples/alt" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0
        intro_dir = tmp_path / "slides" / "module_100" / "topic_010_intro"
        deep_dir = tmp_path / "slides" / "module_100" / "topic_020_deep"
        # intro got the section default
        assert (intro_dir / "pkg" / "core.py").is_file()
        # deep got the override (alt/alt_module.py landed under pkg/)
        assert (deep_dir / "pkg" / "alt_module.py").is_file()
        assert not (deep_dir / "pkg" / "core.py").exists()


class TestSyncIncludesGitignore:
    def test_gitignore_writes_per_topic_entries(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke(
            "sync-includes",
            str(spec),
            "--data-dir",
            str(tmp_path),
            "--gitignore",
        )

        assert result.exit_code == 0
        gi = (topic_dir / ".gitignore").read_text(encoding="utf-8")
        assert "pkg" in gi
        assert LEDGER_NAME in gi

    def test_gitignore_is_idempotent(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path), "--gitignore")
        _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path), "--gitignore")

        gi = (topic_dir / ".gitignore").read_text(encoding="utf-8")
        # Each meaningful line should appear exactly once.
        assert gi.count("\npkg\n") <= 1
        assert gi.count(f"\n{LEDGER_NAME}\n") <= 1


class TestSyncIncludesDryRun:
    def test_dry_run_does_not_touch_disk(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path), "--dry-run")

        assert result.exit_code == 0
        assert "dry-run" in result.stdout.lower() or "would" in result.stdout.lower()
        assert not (topic_dir / "pkg").exists()
        assert not (topic_dir / LEDGER_NAME).exists()


class TestSyncIncludesInferredDataDir:
    def test_data_dir_inferred_from_spec_location(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        # No --data-dir passed; should infer from spec_file.parent.parent.
        result = _invoke("sync-includes", str(spec))

        assert result.exit_code == 0
        topic_dir = tmp_path / "slides" / "module_100" / "topic_010_intro"
        assert (topic_dir / "pkg" / "core.py").is_file()


class TestSyncIncludesNoIncludes:
    def test_spec_without_includes_is_noop(self, tmp_path):
        _make_topic(tmp_path, "module_100", "topic_010_intro")
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics><topic>intro</topic></topics>
            </section></sections>""",
        )

        result = _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path))

        assert result.exit_code == 0
        assert "no includes" in result.stdout.lower()


class TestSyncIncludesModeChange:
    def test_switching_modes_replaces_materialization(self, tmp_path):
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            """\
            <sections><section>
              <name><de>S</de><en>S</en></name>
              <topics>
                <topic>
                  intro
                  <include source="examples/pkg" as="pkg"/>
                </topic>
              </topics>
            </section></sections>""",
        )

        # First, materialize as copy.
        _invoke("sync-includes", str(spec), "--data-dir", str(tmp_path), "--mode", "copy")
        assert (topic_dir / "pkg" / "core.py").is_file()
        # Re-run as hardlink — the existing copy must be cleared first.
        result = _invoke(
            "sync-includes",
            str(spec),
            "--data-dir",
            str(tmp_path),
            "--mode",
            "hardlink",
        )
        assert result.exit_code == 0
        # File still present after the switch (we don't care about inode
        # equality here — fallback to copy is acceptable on Windows).
        assert (topic_dir / "pkg" / "core.py").is_file()
        data = json.loads((topic_dir / LEDGER_NAME).read_text(encoding="utf-8"))
        # Should record the new mode (or 'copy' if hardlink fell back).
        assert data["entries"][0]["mode"] in {"hardlink", "copy"}
