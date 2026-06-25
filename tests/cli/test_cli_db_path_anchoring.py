"""The root CLI anchors the *default* build/jobs DB paths to the discovered
project root (issue #477, full sweep), so a build/status from a subdirectory
opens the same database as one from the repo root. An explicitly supplied path
is respected verbatim.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from clm.cli.main import cli


@pytest.fixture
def probe():
    """Attach a throwaway command that echoes the resolved DB paths, then remove
    it so the real command tree is unaffected."""

    @click.command(name="_probe_db_paths")
    @click.pass_context
    def _probe(ctx):
        click.echo(f"CACHE={ctx.obj['CACHE_DB_PATH']}")
        click.echo(f"JOBS={ctx.obj['JOBS_DB_PATH']}")

    cli.add_command(_probe, name="_probe_db_paths")
    try:
        yield "_probe_db_paths"
    finally:
        cli.commands.pop("_probe_db_paths", None)


def _make_project(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    sub = repo / "slides" / "topic_031"
    sub.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
    return sub


def test_default_db_paths_anchor_to_root_from_subdir(tmp_path, monkeypatch, probe):
    sub = _make_project(tmp_path)
    root = (tmp_path / "repo").resolve()
    monkeypatch.chdir(sub)
    result = CliRunner().invoke(cli, [probe])
    assert result.exit_code == 0, result.output
    assert f"CACHE={root / 'clm_cache.db'}" in result.output
    assert f"JOBS={root / 'clm_jobs.db'}" in result.output


def test_explicit_relative_path_respected(tmp_path, monkeypatch, probe):
    sub = _make_project(tmp_path)
    monkeypatch.chdir(sub)
    result = CliRunner().invoke(cli, ["--cache-db-path", "mine.db", probe])
    assert result.exit_code == 0, result.output
    # Explicit relative path is NOT re-anchored — stays as given (cwd-relative).
    assert "CACHE=mine.db" in result.output


def test_explicit_absolute_path_respected(tmp_path, monkeypatch, probe):
    sub = _make_project(tmp_path)
    monkeypatch.chdir(sub)
    abs_db = (tmp_path / "elsewhere" / "c.db").resolve()
    result = CliRunner().invoke(cli, ["--cache-db-path", str(abs_db), probe])
    assert result.exit_code == 0, result.output
    assert f"CACHE={abs_db}" in result.output
