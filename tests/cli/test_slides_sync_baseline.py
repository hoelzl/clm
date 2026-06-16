"""CLI tests for ``clm slides sync --baseline <ref>`` and the cold-baseline hint (Fix D).

When single-language edits are *committed* before syncing, the watermark/HEAD
baseline sees the committed edits as already consistent and reports "0 changes".
``--baseline HEAD~1`` diffs against the pre-edit commit so the edits are detected;
the cold-baseline hint points the user there when no watermark exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_cmd

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # pragma: no cover - older click
        return CliRunner()


def _deck(lang: str, body: str, *, sid: str = "intro") -> str:
    return (
        f"# j2 from 'macros.j2' import header_{lang}\n"
        f'# {{{{ header_{lang}("T") }}}}\n'
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n'
        f"# {body}\n"
    )


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True, text=True)


def _init_commit(tmp_path: Path, de_text: str, en_text: str, *, stem: str = "slides_intro"):
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de_text, encoding="utf-8")
    en_path.write_text(en_text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    return de_path, en_path


def _commit_all(tmp_path: Path, msg: str) -> None:
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)


def _committed_en_edit(tmp_path: Path):
    """Commit a pair, then edit EN and commit again (the gotcha scenario)."""
    de_path, en_path = _init_commit(tmp_path, _deck("de", "alt"), _deck("en", "old"))
    en_path.write_text(_deck("en", "new"), encoding="utf-8")
    _commit_all(tmp_path, "edit en")
    return de_path, en_path, tmp_path / "cache"


class TestBaselineFlag:
    def test_baseline_detects_committed_single_language_edit(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--baseline", "HEAD~1", "--cache-dir", str(cache), str(de_path)],
        )
        # The EN edit, invisible against HEAD, is seen against HEAD~1.
        assert res.exit_code != 0, res.output + res.stderr
        assert "git:HEAD~1" in res.output
        assert "1 edit" in res.output

    def test_without_baseline_committed_edit_is_invisible_with_cold_hint(
        self, cli_runner, tmp_path
    ):
        # No watermark + committed edit → git HEAD baseline sees nothing, but the
        # cold-baseline hint points at --baseline.
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--cache-dir", str(cache), str(de_path)]
        )
        assert res.exit_code == 0, res.output + res.stderr
        assert "no watermark recorded" in res.stderr
        assert "--baseline HEAD~1" in res.stderr

    def test_cold_baseline_hint_in_json(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--json", "--cache-dir", str(cache), str(de_path)]
        )
        payload = json.loads(res.output[res.output.find("{") :])
        assert payload["cold_baseline_hint"] is True

    def test_baseline_no_cold_hint_emitted(self, cli_runner, tmp_path):
        # An explicit --baseline run is sourced "git:<ref>", not "git-head", so the
        # cold-baseline hint must not fire even when the plan is a no-op.
        de_path, en_path = _init_commit(tmp_path, _deck("de", "x"), _deck("en", "y"))
        cache = tmp_path / "cache"
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--baseline", "HEAD", "--cache-dir", str(cache), str(de_path)],
        )
        assert "no watermark recorded" not in res.stderr


class TestBaselineValidation:
    def test_rejected_with_rebaseline(self, cli_runner, tmp_path):
        de_path, _en = _init_commit(tmp_path, _deck("de", "x"), _deck("en", "y"))
        res = cli_runner.invoke(
            slides_sync_cmd,
            [
                "--baseline",
                "HEAD~1",
                "--rebaseline",
                "--cache-dir",
                str(tmp_path / "c"),
                str(de_path),
            ],
        )
        assert res.exit_code != 0
        assert "mutually exclusive" in (res.stderr + res.output)

    def test_rejected_on_directory(self, cli_runner, tmp_path):
        _init_commit(tmp_path, _deck("de", "x"), _deck("en", "y"))
        res = cli_runner.invoke(
            slides_sync_cmd,
            [
                "--baseline",
                "HEAD~1",
                "--dry-run",
                "--cache-dir",
                str(tmp_path / "c"),
                str(tmp_path),
            ],
        )
        assert res.exit_code != 0
        assert "single deck pair" in (res.stderr + res.output)
