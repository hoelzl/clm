"""CLI guard tests for ``clm slides sync autopilot --conflict`` (Issue #447).

The data-loss gates fire before any model construction: a writing resolving policy
needs ``--yes``, and ``--conflict`` is mutually exclusive with ``--interactive``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync_autopilot import slides_sync_cmd


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # pragma: no cover - older click
        return CliRunner()


def _deck(lang: str, body: str) -> str:
    return (
        f"# j2 from 'macros.j2' import header_{lang}\n"
        f'# {{{{ header_{lang}("T") }}}}\n'
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="intro"\n'
        f"# {body}\n"
    )


def _pair(tmp_path: Path) -> Path:
    de_path = tmp_path / "slides_x.de.py"
    en_path = tmp_path / "slides_x.en.py"
    de_path.write_text(_deck("de", "Hallo"), encoding="utf-8")
    en_path.write_text(_deck("en", "Hello"), encoding="utf-8")
    return de_path


def test_de_wins_writing_run_requires_yes(cli_runner, tmp_path):
    de_path = _pair(tmp_path)
    res = cli_runner.invoke(
        slides_sync_cmd, ["--conflict", "de-wins", "--cache-dir", str(tmp_path / "c"), str(de_path)]
    )
    assert res.exit_code != 0
    out = res.stderr + res.output
    assert "pass --yes" in out
    assert "OVERWRITING" in out  # the data-loss banner fired


def test_de_wins_and_interactive_are_mutually_exclusive(cli_runner, tmp_path):
    de_path = _pair(tmp_path)
    res = cli_runner.invoke(
        slides_sync_cmd,
        [
            "--conflict",
            "de-wins",
            "--interactive",
            "--cache-dir",
            str(tmp_path / "c"),
            str(de_path),
        ],
    )
    assert res.exit_code != 0
    assert "mutually exclusive" in (res.stderr + res.output)


def test_dry_run_needs_no_yes_and_previews(cli_runner, tmp_path):
    # --dry-run is read-only, so the --yes gate does not apply; a clean pair has no
    # conflict, so the run simply succeeds (no crash, no usage error).
    de_path = _pair(tmp_path)
    res = cli_runner.invoke(
        slides_sync_cmd,
        ["--conflict", "de-wins", "--dry-run", "--cache-dir", str(tmp_path / "c"), str(de_path)],
    )
    assert res.exit_code == 0, res.output + res.stderr
    assert "pass --yes" not in (res.stderr + res.output)


def test_leave_is_the_unchanged_default(cli_runner, tmp_path):
    # Omitting --conflict (default leave) needs no --yes and writes nothing on a clean pair.
    de_path = _pair(tmp_path)
    res = cli_runner.invoke(
        slides_sync_cmd, ["--dry-run", "--cache-dir", str(tmp_path / "c"), str(de_path)]
    )
    assert res.exit_code == 0, res.output + res.stderr
