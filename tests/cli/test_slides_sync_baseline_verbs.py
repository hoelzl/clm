"""``clm slides sync baseline bless`` — the commit-free baseline recorder (#430, #440).

``bless`` records the current working tree as the sync baseline without the throwaway
commit ``--rebaseline``'s git-HEAD-no-op gate used to force (#430). It is gated on
structural ``verify``: a consistent pair is recorded (and a later ``report
--use-watermark`` sees it in sync); a structurally corrupt pair is refused so a genuine
divergence is surfaced rather than blessed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
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


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_batch_report_baseline_surfaces_committed_edits(tmp_path: Path):
    """The 'reconcile a week of committed single-language edits' sweep (#440 follow-up).

    A DE edit committed without syncing EN matches git HEAD, so a default batch `report`
    falsely reads it 'clean'. `report DIR --baseline REF` diffs every pair against REF and
    surfaces the drift — the missing batch capability that blocked the timeframe workflow.
    """
    topic = tmp_path / "topic_010_x"
    deA, enA = _pair("Titel", "Title", de_id="a", en_id="a")
    _write(topic, deA, enA)  # writes slides_x.de.py / slides_x.en.py
    deB, enB = _pair("Zwei", "Two", de_id="b", en_id="b")
    (topic / "slides_y.de.py").write_text(deB, encoding="utf-8")
    (topic / "slides_y.en.py").write_text(enB, encoding="utf-8")

    def _git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=str(tmp_path), check=True, capture_output=True, text=True)

    _git("init", "-q")
    _git("config", "user.email", "t@e.com")
    _git("config", "user.name", "t")
    _git("add", "-A")
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    # Edit deck A's DE half and COMMIT it (EN left behind, unsynced).
    deA2, _ = _pair("Titel bearbeitet", "Title", de_id="a", en_id="a")
    (topic / "slides_x.de.py").write_text(deA2, encoding="utf-8")
    _git("add", "-A")
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "edit A de only")

    def _pair_by(env: dict, stem: str) -> dict:
        return next(p["report"] for p in env["pairs"] if stem in p["de_path"])

    # Default git-HEAD batch: the committed edit matches HEAD → FALSE-CLEAN.
    code, out = _run("report", str(topic), "--json")
    env = json.loads(out)
    assert _pair_by(env, "slides_x")["is_clean"] is True  # the motivating blind spot

    # --baseline HEAD~1 over the directory: deck A's edit surfaces, deck B stays clean.
    code, out = _run("report", str(topic), "--baseline", "HEAD~1", "--json")
    assert code == 1, out  # work pending in the sweep
    env = json.loads(out)
    a = _pair_by(env, "slides_x")
    assert a["is_clean"] is False and a["needs_model"] is True  # the de->en edit
    assert _pair_by(env, "slides_y")["is_clean"] is True  # untouched deck unaffected


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_batch_report_baseline_from_still_rejected(tmp_path: Path):
    """`--baseline-from` pins ONE deck's pre-rename half, so it stays single-pair only."""
    topic = tmp_path / "topic_010_x"
    de, en = _pair("Titel", "Title")
    _write(topic, de, en)
    code, out = _run("report", str(topic), "--baseline-from", "old.de.py")
    assert code == 2
    assert "single-pair" in out.lower() or "single pair" in out.lower()
