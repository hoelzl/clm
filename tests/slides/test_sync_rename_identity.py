"""Rename-identity behavior for ``clm slides sync`` (epic #440, phase 2).

A deck author may rename a topic folder / deck stem **and** revise content, committed
or not, using the editor — without having to think about the sync tooling. But deck
identity is currently **path-derived** at every level (topic id = folder slug,
watermark key = path, git baseline = ``HEAD:<path>``), so a single rename breaks every
handle at once and the engine loses the baseline it needs to tell "edited" from
"always was".

These tests pin the rename-recovery behavior the toolkit delivers (epic #440 phase 2).
There are three real situations, observed live on PythonCourses
(``topic_036_ai_coding_tools_comparison`` → ``topic_036_using_ai_for_coding``, German
revised, English stale):

* **Committed *pure* rename, then revise** — ``HEAD:<newpath>`` carries the old content,
  so the default git baseline sees the one-sided revision unaided.
* **Committed rename + edits in ONE commit** — the default ``HEAD`` baseline would see
  current == HEAD and report *clean* (hiding the stale half), so the engine **follows
  the rename to HEAD^** (the pre-rename ancestor) when the deck was introduced into its
  current name by HEAD and the work tree is fully committed.
* **Uncommitted rename** (old = deleted, new = untracked) — ``HEAD:<newpath>`` does not
  exist, so the engine **detects the rename candidate** by matching the new deck's
  ``slide_id`` set against each recently-deleted committed half and recovers the
  baseline from the match. ``--baseline-from PATH[@REF]`` pins it explicitly when the
  rename can't be auto-detected.

All three converge on the same bundle machinery (``build_sync_plan(detect_rename=True)``
/ ``baseline_from=...``) — they only change *where* the baseline text is read from, never
how the diff is computed. The author may rename folders/stems freely; the tools recognize
the situation and behave correctly.
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
# Acceptance tests — the phase-2 rename-recovery mechanisms.
# ---------------------------------------------------------------------------


def test_committed_rename_with_edits_follows_rename(tmp_path: Path):
    """Rename + revision in ONE commit. The default HEAD baseline sees current == HEAD;
    the engine follows the rename to HEAD^ (work tree fully committed) and surfaces the
    de->en drift."""
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


def test_uncommitted_rename_recovers_via_candidate(tmp_path: Path):
    """The live situation: folder renamed in the editor (old=deleted, new=untracked),
    DE revised. ``report`` detects the rename candidate (matching slide_ids against the
    deleted predecessor) and recovers the de->en drift instead of giving up at
    baseline=none."""
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


# ---------------------------------------------------------------------------
# Precision — the recovery must NOT fire when there is no rename-with-edits to
# recover (else every clean deck would false-drift against an ancient ancestor).
# ---------------------------------------------------------------------------


def test_committed_pure_rename_no_edits_stays_clean(tmp_path: Path):
    """A committed rename with NO edits: following it to HEAD^ is harmless — both halves
    equal their pre-rename content, so the report is clean (no phantom drift)."""
    repo = _init_repo(tmp_path)
    _write_pair(repo / "topic_010_old", "slides_x", DE_V1, EN_V1)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "mv", "topic_010_old", "topic_010_new")
    _git(repo, "commit", "-qm", "rename")  # rename only, no content change
    report = _report(repo / "topic_010_new" / "slides_x.de.py")
    assert report["is_clean"] is True


def test_fresh_create_not_treated_as_rename(tmp_path: Path):
    """A brand-new deck added on top of an unrelated commit is a create, not a rename:
    it has no pre-rename ancestor at HEAD^, so the baseline stays git-HEAD (clean)."""
    repo = _init_repo(tmp_path)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    _write_pair(repo / "topic_010_intro", "slides_x", DE_V1, EN_V1)  # new, consistent
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add deck")
    report = _report(repo / "topic_010_intro" / "slides_x.de.py")
    assert report["baseline_source"] == "git-head"
    assert report["is_clean"] is True


def test_uncommitted_rename_no_match_when_ids_differ(tmp_path: Path):
    """The untracked-rename match is strict (exact slide_id set): a deleted predecessor
    with a DIFFERENT id set is not bound, so the engine does not silently diff against
    the wrong deck — it falls back to no baseline rather than a wrong one."""
    repo = _init_repo(tmp_path)
    # Old deck carries an EXTRA slide, so its committed id set differs from the new one.
    de_extra = (
        DE_V1 + '\n# %% [markdown] lang="de" tags=["slide"] slide_id="extra"\n#\n# ## Extra\n'
    )
    en_extra = (
        EN_V1 + '\n# %% [markdown] lang="en" tags=["slide"] slide_id="extra"\n#\n# ## Extra\n'
    )
    _write_pair(repo / "topic_010_old", "slides_x", de_extra, en_extra)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    shutil.move(str(repo / "topic_010_old"), str(repo / "topic_010_new"))
    new_de = repo / "topic_010_new" / "slides_x.de.py"
    new_de.write_text(
        DE_V2, encoding="utf-8"
    )  # V2 has the {intro, ende} set, != {intro, ende, extra}
    report = _report(new_de)
    assert report["baseline_source"] == "none"
