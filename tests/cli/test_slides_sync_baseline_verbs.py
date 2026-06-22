"""``clm slides sync baseline bless`` — the commit-free baseline recorder (#430, #440).

``bless`` records the current working tree as the sync baseline without the throwaway
commit ``--rebaseline``'s git-HEAD-no-op gate used to force (#430). It is gated on
structural ``verify``: a consistent pair is recorded (and a later ``report
--use-watermark`` sees it in sync); a structurally corrupt pair is refused so a genuine
divergence is surfaced rather than blessed.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group


def _pair(de_title: str, en_title: str, *, de_id: str = "a", en_id: str = "a") -> tuple[str, str]:
    """A minimal split pair: one neutral code cell (byte-identical) + one localized slide."""
    neutral = '# %% tags=["code"]\nprint("hello")\n\n'
    de = (
        neutral
        + f'# %% [markdown] lang="de" tags=["slide"] slide_id="{de_id}"\n#\n# ## {de_title}\n'
    )
    en = (
        neutral
        + f'# %% [markdown] lang="en" tags=["slide"] slide_id="{en_id}"\n#\n# ## {en_title}\n'
    )
    return de, en


def _write(folder: Path, de: str, en: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "slides_x.de.py").write_text(de, encoding="utf-8")
    (folder / "slides_x.en.py").write_text(en, encoding="utf-8")
    return folder / "slides_x.de.py"


def _run(*args: str) -> tuple[int, str]:
    res = CliRunner().invoke(slides_sync_group, list(args))
    return res.exit_code, res.output


def _report_json(de_path: Path, cache: Path) -> dict:
    code, out = _run("report", str(de_path), "--use-watermark", "--cache-dir", str(cache), "--json")
    start = out.find("{")
    assert start >= 0, f"no JSON in report output (exit {code}):\n{out}"
    payload = json.loads(out[start:])
    return payload.get("report", payload)


def test_bless_records_consistent_pair_without_commit(tmp_path: Path):
    """A verify-clean pair (even uncommitted, no git) is blessed; a later
    watermark-baselined report then sees it in sync — no throwaway commit needed."""
    cache = tmp_path / "cache"
    de, en = _pair("Titel", "Title")
    de_path = _write(tmp_path / "topic_010_intro", de, en)

    code, out = _run("baseline", "bless", str(de_path), "--cache-dir", str(cache), "--json")
    assert code == 0, out
    payload = json.loads(out)
    assert payload["blessed"] is True

    # The watermark now reflects the working tree → a watermark-baselined report is clean.
    report = _report_json(de_path, cache)
    assert report["baseline_source"] == "watermark"
    assert report["is_clean"] is True


def test_bless_refuses_structurally_corrupt_pair(tmp_path: Path):
    """A pair whose halves carry mismatched slide_ids is not a valid split — bless
    refuses (exit 2) so the divergence is surfaced, not masked into the baseline."""
    cache = tmp_path / "cache"
    de, en = _pair("Titel", "Title", de_id="a", en_id="b")  # de_id != en_id → asymmetric
    de_path = _write(tmp_path / "topic_010_intro", de, en)

    code, out = _run("baseline", "bless", str(de_path), "--cache-dir", str(cache), "--json")
    assert code == 2, out
    payload = json.loads(out)
    assert payload["blessed"] is False
    assert payload["errors"], "expected the structural errors to be reported"


def test_baseline_show_lists_a_blessed_pair(tmp_path: Path):
    """``baseline show`` (the renamed ``watermark list``) surfaces a blessed pair."""
    cache = tmp_path / "cache"
    de, en = _pair("Titel", "Title")
    de_path = _write(tmp_path / "topic_010_intro", de, en)
    code, _out = _run("baseline", "bless", str(de_path), "--cache-dir", str(cache))
    assert code == 0

    code, out = _run("baseline", "show", "--cache-dir", str(cache), "--json")
    assert code == 0, out
    entries = json.loads(out)
    assert any(Path(e["de_path"]).name == "slides_x.de.py" for e in entries)
