"""CLI tests for ``clm slides sync diagnose`` (read-only by default; --apply; --json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import sync_diagnose_cmd


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # pragma: no cover - older click
        return CliRunner()


def _deck(lang: str, cells: list[tuple[list[str], str | None, str]]) -> str:
    head = f"# j2 from 'macros.j2' import header_{lang}\n# {{{{ header_{lang}(\"T\") }}}}\n"
    out = [head]
    for tags, sid, body in cells:
        tagstr = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
        idstr = f' slide_id="{sid}"' if sid else ""
        out.append(f'# %% [markdown] lang="{lang}" tags={tagstr}{idstr}\n# {body}\n')
    return "".join(out)


def _dup_pair(tmp_path: Path) -> tuple[Path, Path]:
    de = _deck(
        "de",
        [
            (["slide"], "intro", "Hallo"),
            (["voiceover"], "intro", "Eins"),
            (["voiceover"], "intro", "Zwei"),
        ],
    )
    en = _deck(
        "en",
        [
            (["slide"], "intro", "Hello"),
            (["voiceover"], "intro", "One"),
            (["voiceover"], "intro", "Two"),
        ],
    )
    dp, ep = tmp_path / "x.de.py", tmp_path / "x.en.py"
    dp.write_text(de, encoding="utf-8")
    ep.write_text(en, encoding="utf-8")
    return dp, ep


def test_clean_pair_passes(cli_runner, tmp_path):
    dp = tmp_path / "x.de.py"
    ep = tmp_path / "x.en.py"
    dp.write_text(_deck("de", [(["slide"], "intro", "Hallo")]), encoding="utf-8")
    ep.write_text(_deck("en", [(["slide"], "intro", "Hello")]), encoding="utf-8")
    res = cli_runner.invoke(sync_diagnose_cmd, [str(dp)])
    assert res.exit_code == 0, res.output + res.stderr
    assert "PASS" in res.output


def test_dup_narration_fails_and_json_carries_root_cause(cli_runner, tmp_path):
    dp, _ep = _dup_pair(tmp_path)
    res = cli_runner.invoke(sync_diagnose_cmd, ["--json", str(dp)])
    assert res.exit_code == 2, res.output + res.stderr
    payload = json.loads(res.output[res.output.find("{") :])
    causes = [d["root_cause"] for d in payload["pairs"][0]["diagnoses"]]
    assert "DUPLICATE-NARRATION-OVERSTAMP" in causes
    assert payload["mode"] == "diagnose"


def test_apply_fixes_the_mechanical_case(cli_runner, tmp_path):
    dp, _ep = _dup_pair(tmp_path)
    res = cli_runner.invoke(sync_diagnose_cmd, ["--apply", str(dp)])
    # After stripping the over-stamped narration ids the pair is clean → exit 0.
    assert res.exit_code == 0, res.output + res.stderr
    assert "--apply" in res.output
    # A second dry-run diagnose now passes.
    res2 = cli_runner.invoke(sync_diagnose_cmd, [str(dp)])
    assert res2.exit_code == 0, res2.output + res2.stderr


def test_apply_is_dry_run_by_default(cli_runner, tmp_path):
    dp, ep = _dup_pair(tmp_path)
    before = dp.read_text(encoding="utf-8")
    res = cli_runner.invoke(sync_diagnose_cmd, [str(dp)])
    assert res.exit_code == 2
    assert dp.read_text(encoding="utf-8") == before  # no write without --apply


def test_directory_mode(cli_runner, tmp_path):
    _dup_pair(tmp_path)
    res = cli_runner.invoke(sync_diagnose_cmd, ["--json", str(tmp_path)])
    assert res.exit_code == 2, res.output + res.stderr
    payload = json.loads(res.output[res.output.find("{") :])
    assert payload["root"] == str(tmp_path)
    assert len(payload["pairs"]) == 1
