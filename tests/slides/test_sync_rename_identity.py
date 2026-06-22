"""Rename-identity behavior for ``clm slides sync`` (epic #440, phase 2).

A deck author may rename a topic folder / deck stem **and** revise content, committed
or not, using the editor — without having to think about the sync tooling. But deck
identity is currently **path-derived** at every level (topic id = folder slug,
watermark key = path, git baseline = ``HEAD:<path>``), so a single rename breaks every
handle at once and the engine loses the baseline it needs to tell "edited" from
"always was".

These tests pin the behavior the redesigned toolkit must deliver. There are three
real situations, observed live on PythonCourses
(``topic_036_ai_coding_tools_comparison`` → ``topic_036_using_ai_for_coding``, German
revised, English stale):

* **Committed *pure* rename, then revise** — already works today: ``HEAD:<newpath>``
  carries the old content, so the git baseline sees the one-sided revision. (positive
  control — must not regress)
* **Committed rename + edits in ONE commit** — today the default ``HEAD`` baseline sees
  current == HEAD and reports *clean* (silently hiding the stale half). The redesign
  must make the git baseline **follow the rename** (to the pre-rename ancestor) and
  surface the drift. (``xfail(strict)`` until phase 2)
* **Uncommitted rename** (old = deleted, new = untracked) — today ``report`` gives up
  with ``baseline_source == "none"``. The redesign must **detect the rename candidate**
  by matching ``slide_id`` sets against recently-deleted paths (and/or accept an explicit
  ``--baseline-from <oldpath>``) and recover the drift. (``xfail(strict)`` until phase 2)

The ``xfail(strict=True)`` markers make each acceptance test flip to a hard failure the
moment phase 2 makes it pass, forcing the marker (and this comment) to be removed — the
established "living checklist" pattern in this suite (cf. the #364/#365 strict-xfail).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

# ---------------------------------------------------------------------------
# Deck builders — a minimal split pair: an id'd slide + its id'd voiceover
# companion (the localized cell a one-sided revision lands on) + a trailing slide.
# ---------------------------------------------------------------------------


def _deck(lang: str, vo_line: str) -> str:
    title = {"de": "Einführung", "en": "Introduction"}[lang]
    end = {"de": "Ende", "en": "The End"}[lang]
    p1 = {"de": "Punkt eins", "en": "Point one"}[lang]
    return (
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="intro"\n'
        f"#\n# ## {title}\n#\n# - {p1}\n"
        "\n"
        f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="intro"\n'
        f"#\n# - {vo_line}\n"
        "\n"
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="ende"\n'
        f"#\n# ## {end}\n"
    )


DE_V1 = _deck("de", "Willkommen zu diesem Kurs.")
DE_V2 = _deck("de", "Willkommen zu diesem tollen Kurs.")  # the German revision
EN_V1 = _deck("en", "Welcome to this course.")  # stays stale across the revision

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> None:
    import os

    subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _write_pair(folder: Path, stem: str, de: str, en: str) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    de_path = folder / f"{stem}.de.py"
    en_path = folder / f"{stem}.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _report(de_path: Path, *extra: str) -> dict:
    """Run ``clm slides sync report <de_path> --json`` and return the ``report`` block."""
    res = CliRunner().invoke(slides_sync_group, ["report", str(de_path), "--json", *extra])
    out = res.output
    start = out.find("{")
    if start < 0:
        raise AssertionError(f"no JSON in report output (exit {res.exit_code}):\n{out}")
    payload = json.loads(out[start:])
    return payload.get("report", payload)


def _has_de_to_en_edit(report: dict) -> bool:
    return any(
        item.get("direction") == "de->en"
        for item in (*report.get("assisted", []), *report.get("mechanical", []))
    )


# ---------------------------------------------------------------------------
# Positive controls — paths that already work; must not regress.
# ---------------------------------------------------------------------------


def test_no_rename_revision_detects_de_to_en(tmp_path: Path):
    """Baseline sanity: with NO rename, a one-sided DE revision is a de->en edit."""
    repo = _init_repo(tmp_path)
    de, _en = _write_pair(repo / "topic_010_intro", "slides_x", DE_V1, EN_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    de.write_text(DE_V2, encoding="utf-8")  # revise DE only (uncommitted)
    report = _report(de)
    assert report["baseline_source"] == "git-head"
    assert report["is_clean"] is False
    assert _has_de_to_en_edit(report)


def test_committed_pure_rename_then_revise_detects_de_to_en(tmp_path: Path):
    """A rename committed SEPARATELY (before the edit) already works: ``HEAD:<newpath>``
    carries the old content, so the git baseline sees the later revision."""
    repo = _init_repo(tmp_path)
    _write_pair(repo / "topic_010_old", "slides_x", DE_V1, EN_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    # pure folder rename, committed on its own (no content change)
    _git(repo, "mv", "topic_010_old", "topic_010_new")
    _git(repo, "commit", "-qm", "rename")
    new_de = repo / "topic_010_new" / "slides_x.de.py"
    new_de.write_text(DE_V2, encoding="utf-8")  # revise DE only (uncommitted)
    report = _report(new_de)
    assert report["baseline_source"] == "git-head"
    assert report["is_clean"] is False
    assert _has_de_to_en_edit(report)


# ---------------------------------------------------------------------------
# Acceptance tests — the phase-2 mechanisms (strict-xfail until built).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="phase 2 (#440): git baseline must FOLLOW the rename to the pre-rename ancestor "
    "instead of reading HEAD:<newpath> (== current) and reporting clean.",
)
def test_committed_rename_with_edits_follows_rename(tmp_path: Path):
    """Rename + revision in ONE commit. The default HEAD baseline sees current == HEAD
    and (wrongly) reports clean; the redesign must follow the rename to HEAD~1 and
    surface the de->en drift."""
    repo = _init_repo(tmp_path)
    _write_pair(repo / "topic_010_old", "slides_x", DE_V1, EN_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    # rename AND revise in the same commit (the natural author move)
    _git(repo, "mv", "topic_010_old", "topic_010_new")
    new_de = repo / "topic_010_new" / "slides_x.de.py"
    new_de.write_text(DE_V2, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rename+revise")
    report = _report(new_de)
    assert report["is_clean"] is False
    assert _has_de_to_en_edit(report)


@pytest.mark.xfail(
    strict=True,
    reason="phase 2 (#440): untracked-rename detection — match the new deck to a "
    "recently-deleted path by slide_id set and recover the drift (instead of baseline=none).",
)
def test_uncommitted_rename_recovers_via_candidate(tmp_path: Path):
    """The live situation: folder renamed in the editor (old=deleted, new=untracked),
    DE revised. Today ``report`` gives up with baseline=none; the redesign must detect
    the rename candidate (matching slide_ids) and recover the de->en drift."""
    repo = _init_repo(tmp_path)
    _write_pair(repo / "topic_010_old", "slides_x", DE_V1, EN_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    # filesystem rename (NOT git mv) + revise DE — leaves old deleted, new untracked
    shutil.move(str(repo / "topic_010_old"), str(repo / "topic_010_new"))
    new_de = repo / "topic_010_new" / "slides_x.de.py"
    new_de.write_text(DE_V2, encoding="utf-8")
    report = _report(new_de)
    assert report["is_clean"] is False
    assert _has_de_to_en_edit(report)


@pytest.mark.xfail(
    strict=True,
    reason="phase 2 (#440): `--baseline-from PATH[@REF]` — point the baseline at the deck's "
    "pre-rename location (a different PATH, not just a different ref).",
)
def test_baseline_from_explicit_old_path(tmp_path: Path):
    """The explicit recovery primitive: ``--baseline-from <oldpath>@HEAD`` diffs the
    renamed/untracked deck against its committed pre-rename content."""
    repo = _init_repo(tmp_path)
    old_de, _ = _write_pair(repo / "topic_010_old", "slides_x", DE_V1, EN_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    shutil.move(str(repo / "topic_010_old"), str(repo / "topic_010_new"))
    new_de = repo / "topic_010_new" / "slides_x.de.py"
    new_de.write_text(DE_V2, encoding="utf-8")
    report = _report(new_de, "--baseline-from", f"{old_de}@HEAD")
    assert report["is_clean"] is False
    assert _has_de_to_en_edit(report)
