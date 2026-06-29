"""CLI tests for ``clm slides sync ... --since DATE|REF`` (issue #446).

``--since`` resolves a *timeframe* to a baseline commit: a git ref is used verbatim
(an alias for ``--baseline``); a date / relative time resolves to the last commit
at/before that instant (``git rev-list -1 --before=…``). It is pure CLI sugar over
``--baseline`` — the resolved SHA flows through the same engine path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import sync_apply_cmd, sync_report_cmd
from clm.cli.commands.slides.sync_autopilot import slides_sync_cmd

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


def _git(tmp_path: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", *args], cwd=str(tmp_path), check=True, capture_output=True, text=True, env=env
    )


def _init_commit(tmp_path: Path, de_text: str, en_text: str, *, date: str, stem: str = "slides_x"):
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de_text, encoding="utf-8")
    en_path.write_text(en_text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline", date=date)
    return de_path, en_path


def _commit_all(tmp_path: Path, msg: str, *, date: str) -> None:
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg, date=date)


def _committed_en_edit(tmp_path: Path):
    """Commit a pair (2020-01-01), edit EN, commit again (2020-01-03)."""
    de_path, en_path = _init_commit(
        tmp_path, _deck("de", "alt"), _deck("en", "old"), date="2020-01-01T00:00:00"
    )
    en_path.write_text(_deck("en", "new"), encoding="utf-8")
    _commit_all(tmp_path, "edit en", date="2020-01-03T00:00:00")
    return de_path, en_path, tmp_path / "cache"


class TestSinceRefPath:
    def test_since_ref_detects_committed_edit_like_baseline(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--since", "HEAD~1", "--cache-dir", str(cache), str(de_path)],
        )
        # --since HEAD~1 resolves to the pre-edit commit (a ref, used verbatim), so the
        # committed EN edit — invisible against HEAD — is seen.
        assert res.exit_code != 0, res.output + res.stderr
        assert "git:" in res.output  # baseline sourced from a concrete ref/sha
        assert "1 edit" in res.output
        assert "resolved --since" in res.stderr

    def test_since_head_is_a_noop_baseline(self, cli_runner, tmp_path):
        # --since HEAD resolves the literal ref HEAD (try-ref-first), giving the HEAD
        # baseline — a no-op for an unedited pair.
        de_path, en_path = _init_commit(
            tmp_path, _deck("de", "x"), _deck("en", "y"), date="2020-01-01T00:00:00"
        )
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--since", "HEAD", "--cache-dir", str(tmp_path / "c"), str(de_path)],
        )
        assert res.exit_code == 0, res.output + res.stderr
        assert "resolved --since 'HEAD'" in res.stderr


class TestSinceDatePath:
    def test_since_date_resolves_pre_edit_commit(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        # A date between the two commits resolves to the baseline (2020-01-01) commit,
        # so the EN edit committed on 2020-01-03 is detected.
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--since", "2020-01-02", "--cache-dir", str(cache), str(de_path)],
        )
        assert res.exit_code != 0, res.output + res.stderr
        assert "1 edit" in res.output
        assert "resolved --since '2020-01-02'" in res.stderr

    def test_since_date_after_all_edits_is_clean(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        # A date after the edit commit resolves to the edit commit itself (HEAD), so the
        # working tree matches the baseline → clean.
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--since", "2020-06-01", "--cache-dir", str(cache), str(de_path)],
        )
        assert res.exit_code == 0, res.output + res.stderr

    def test_since_before_history_errors(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--dry-run", "--since", "1990-01-01", "--cache-dir", str(cache), str(de_path)],
        )
        assert res.exit_code != 0
        assert "no commit at or before" in (res.stderr + res.output)


class TestSinceValidation:
    def test_since_and_baseline_mutually_exclusive(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd,
            [
                "--dry-run",
                "--since",
                "HEAD~1",
                "--baseline",
                "HEAD~1",
                "--cache-dir",
                str(cache),
                str(de_path),
            ],
        )
        assert res.exit_code != 0
        assert "mutually exclusive" in (res.stderr + res.output)

    def test_since_and_rebaseline_mutually_exclusive(self, cli_runner, tmp_path):
        de_path, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd,
            ["--since", "HEAD~1", "--rebaseline", "--cache-dir", str(cache), str(de_path)],
        )
        assert res.exit_code != 0
        assert "mutually exclusive" in (res.stderr + res.output)

    def test_since_outside_git_repo_errors(self, cli_runner, tmp_path):
        # A deck in a directory that is not a git work tree.
        de_path = tmp_path / "slides_x.de.py"
        en_path = tmp_path / "slides_x.en.py"
        de_path.write_text(_deck("de", "x"), encoding="utf-8")
        en_path.write_text(_deck("en", "y"), encoding="utf-8")
        res = cli_runner.invoke(
            slides_sync_cmd,
            [
                "--dry-run",
                "--since",
                "2 days ago",
                "--cache-dir",
                str(tmp_path / "c"),
                str(de_path),
            ],
        )
        assert res.exit_code != 0
        assert "not inside a git work tree" in (res.stderr + res.output)


class TestSinceOnVerbs:
    def test_report_verb_since_ref(self, cli_runner, tmp_path):
        de_path, _en, _cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(sync_report_cmd, ["--since", "HEAD~1", str(de_path)])
        assert res.exit_code != 0, res.output + res.stderr
        assert "1 edit" in res.output
        assert "resolved --since" in res.stderr

    def test_report_verb_since_works_over_directory(self, cli_runner, tmp_path):
        _de, _en, _cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(sync_report_cmd, ["--since", "2020-01-02", str(tmp_path)])
        # A directory sweep with --since resolves one repo-wide commit and diffs each
        # pair against it — the committed EN edit is detected, so the sweep is not clean.
        assert res.exit_code != 0, res.output + res.stderr
        assert "resolved --since" in res.stderr

    def test_apply_verb_since_and_baseline_exclusive_over_directory(self, cli_runner, tmp_path):
        # The guard fires for a DIRECTORY apply (resolution is before the dir dispatch).
        _de, _en, cache = _committed_en_edit(tmp_path)
        res = cli_runner.invoke(
            sync_apply_cmd,
            [
                "--since",
                "HEAD~1",
                "--baseline",
                "HEAD~1",
                "--cache-dir",
                str(cache),
                str(tmp_path),
            ],
        )
        assert res.exit_code != 0
        assert "mutually exclusive" in (res.stderr + res.output)
