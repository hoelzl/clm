"""Tests for ``clm slides assign-ids --accept-code-derived`` (#251).

The first-code-line fallback for bare-expression code cells, exercised through
the CLI surface: exit codes (0 when it clears the would-be hard refusals, 2
when a genuinely content-less cell remains), in-file minting, the
``--accept-content-derived``-alone back-compat guard, and a non-Python deck.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from clm.cli.main import cli

BARE = '# %% lang="en" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n'


def _deck(tmp_path: Path, content: str = BARE, name: str = "slides_code.py") -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def test_bare_expr_hard_refuses_by_default(tmp_path):
    f = _deck(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f)])
    assert r.exit_code == 2
    assert f.read_text(encoding="utf-8") == BARE


def test_accept_code_derived_mints_and_exits_clean(tmp_path):
    f = _deck(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--accept-code-derived"])
    assert r.exit_code == 0
    assert 'slide_id="1-1j-1-1j"' in f.read_text(encoding="utf-8")


def test_accept_content_derived_alone_does_not_mint(tmp_path):
    # Back-compat guard: the content-derived flag must not start minting
    # opaque code-line slugs — the bare expression still hard-refuses.
    f = _deck(tmp_path)
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--accept-content-derived"])
    assert r.exit_code == 2
    assert f.read_text(encoding="utf-8") == BARE


def test_report_only_with_flag_writes_nothing(tmp_path):
    f = _deck(tmp_path)
    r = CliRunner().invoke(
        cli, ["slides", "assign-ids", str(f), "--accept-code-derived", "--report-only"]
    )
    assert r.exit_code == 0
    assert f.read_text(encoding="utf-8") == BARE  # unchanged
    assert "1-1j-1-1j" in r.output  # but the proposed id is reported


def test_genuinely_empty_cell_still_exits_2_with_flag(tmp_path):
    # A bare expression is cleared, but a pure-punctuation cell remains a hard
    # refusal even with the flag — so the run still exits 2 and reports it.
    content = (
        '# %% lang="en" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n'
        '# %% lang="en" tags=["subslide"]\n...\n'
    )
    f = _deck(tmp_path, content)
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--accept-code-derived"])
    assert r.exit_code == 2
    # The bare expression was still minted despite the residual hard refusal.
    assert 'slide_id="1-1j-1-1j"' in f.read_text(encoding="utf-8")


def test_report_refusals_shrinks_with_flag(tmp_path):
    f = _deck(tmp_path)
    r = CliRunner().invoke(
        cli, ["slides", "assign-ids", str(f), "--accept-code-derived", "--report-refusals"]
    )
    assert r.exit_code == 0
    assert "No refusals" in r.output


def test_non_python_deck_minted_with_flag(tmp_path):
    cs = '// %% lang="en" tags=["subslide"]\nvar z = (1 + 2) * (3 + 4);\n'
    f = _deck(tmp_path, cs, name="slides_code.cs")
    r = CliRunner().invoke(cli, ["slides", "assign-ids", str(f), "--accept-code-derived"])
    assert r.exit_code == 0
    assert 'slide_id="var-z-1-2-3-4"' in f.read_text(encoding="utf-8")


def test_flag_appears_in_help():
    r = CliRunner().invoke(cli, ["slides", "assign-ids", "--help"])
    assert "--accept-code-derived" in r.output
