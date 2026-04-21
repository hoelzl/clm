"""Tests for the ``clm voiceover backfill`` scratch/git helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clm.voiceover.backfill import (
    compute_port_patch,
    extract_slide_file_at_rev,
    plan_scratch_dir,
    resolve_rev,
)


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimal git repo with two revisions of a slide-shaped .py file."""
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "t@t"], tmp_path)
    _run(["git", "config", "user.name", "T"], tmp_path)

    slides = tmp_path / "slides.py"
    slides.write_text("# rev 1\n", encoding="utf-8")
    _run(["git", "add", "slides.py"], tmp_path)
    _run(["git", "commit", "-q", "-m", "rev 1"], tmp_path)

    slides.write_text("# rev 2\n", encoding="utf-8")
    _run(["git", "commit", "-q", "-am", "rev 2"], tmp_path)

    return tmp_path


class TestPlanScratchDir:
    def test_creates_dir_next_to_slides(self, tmp_path: Path):
        slides = tmp_path / "topic.py"
        slides.write_text("", encoding="utf-8")
        scratch = plan_scratch_dir(slides)
        assert scratch.exists()
        assert scratch.is_dir()
        assert scratch.parent.name == "voiceover-backfill"
        assert scratch.parent.parent.name == ".clm"
        assert scratch.name.startswith("topic-")

    def test_repeated_calls_do_not_collide(self, tmp_path: Path):
        slides = tmp_path / "topic.py"
        slides.write_text("", encoding="utf-8")
        import time

        a = plan_scratch_dir(slides)
        time.sleep(1.01)  # timestamps are per-second
        b = plan_scratch_dir(slides)
        assert a != b


class TestExtractSlideFileAtRev:
    def test_exports_rev1_into_scratch(self, repo: Path):
        slides = repo / "slides.py"
        rev1 = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD~1"],
            text=True,
        ).strip()
        scratch = repo / "scratch"
        scratch.mkdir()
        out = extract_slide_file_at_rev(slides, rev1, scratch)
        assert out.exists()
        assert out.read_text(encoding="utf-8") == "# rev 1\n"
        # HEAD stays untouched.
        assert slides.read_text(encoding="utf-8") == "# rev 2\n"

    def test_file_missing_at_rev_raises(self, repo: Path):
        new_file = repo / "new.py"
        new_file.write_text("# added later\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "new.py"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "add new"],
            check=True,
        )

        rev1 = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD~2"],
            text=True,
        ).strip()
        scratch = repo / "scratch"
        scratch.mkdir()
        with pytest.raises(FileNotFoundError):
            extract_slide_file_at_rev(new_file, rev1, scratch)


class TestResolveRev:
    def test_resolves_short_sha(self, repo: Path):
        slides = repo / "slides.py"
        full = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        short = full[:7]
        assert resolve_rev(slides, short) == full

    def test_unknown_rev_raises(self, repo: Path):
        slides = repo / "slides.py"
        with pytest.raises(ValueError):
            resolve_rev(slides, "deadbeef" * 5)


class TestSyncAtRevGuards:
    """CLI-level guards on ``sync-at-rev`` that short-circuit before any sync runs."""

    def test_rejects_output_equal_to_slide_file(self, repo: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slides = repo / "slides.py"
        video = repo / "video.mp4"
        video.write_bytes(b"\x00")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync-at-rev",
                str(slides),
                str(video),
                "--rev",
                "HEAD",
                "--output",
                str(slides),
                "--lang",
                "de",
            ],
        )
        assert result.exit_code != 0
        assert "working copy" in result.output.lower()

    def test_rejects_unknown_rev(self, repo: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slides = repo / "slides.py"
        video = repo / "video.mp4"
        video.write_bytes(b"\x00")
        output = repo / "scratch-out.py"

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync-at-rev",
                str(slides),
                str(video),
                "--rev",
                "deadbeef" * 5,
                "--output",
                str(output),
                "--lang",
                "de",
            ],
        )
        assert result.exit_code != 0
        assert "unknown revision" in result.output.lower()


class TestBackfillGuards:
    def test_dry_run_and_apply_are_mutually_exclusive(self, repo: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slides = repo / "slides.py"
        video = repo / "video.mp4"
        video.write_bytes(b"\x00")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "backfill",
                str(slides),
                str(video),
                "--lang",
                "de",
                "--rev",
                "HEAD",
                "--dry-run",
                "--apply",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_rejects_unknown_rev(self, repo: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slides = repo / "slides.py"
        video = repo / "video.mp4"
        video.write_bytes(b"\x00")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "backfill",
                str(slides),
                str(video),
                "--lang",
                "de",
                "--rev",
                "deadbeef" * 5,
            ],
        )
        assert result.exit_code != 0
        assert "unknown revision" in result.output.lower()


class TestComputePortPatch:
    def test_identical_returns_empty(self, tmp_path: Path):
        target = tmp_path / "t.py"
        target.write_text("hello\n", encoding="utf-8")
        assert compute_port_patch(target, "hello\n") == ""

    def test_emits_unified_diff(self, tmp_path: Path):
        target = tmp_path / "t.py"
        target.write_text("line1\nline2\n", encoding="utf-8")
        patch = compute_port_patch(target, "line1\nline2-edited\n")
        assert patch.startswith("--- a/t.py")
        assert "+++ b/t.py" in patch
        assert "-line2" in patch
        assert "+line2-edited" in patch

    def test_accepts_explicit_original_text(self, tmp_path: Path):
        target = tmp_path / "t.py"
        # target file intentionally not present — original_text supplied directly.
        patch = compute_port_patch(
            target,
            "two\n",
            original_text="one\n",
        )
        assert "-one" in patch
        assert "+two" in patch
