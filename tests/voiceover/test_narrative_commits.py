"""Tests for the narrative-commit heuristic spike.

Covers the pure-logic functions with unit tests and the end-to-end walk
with a smoke test against a temporary git repo.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clm.notebooks.slide_parser import parse_cells
from clm.voiceover.narrative_commits import (
    CommitInfo,
    CommitMetrics,
    classify_cells,
    collapse_runs,
    compute_commit_metrics,
    compute_ratio,
    scan_slide_file,
)

SLIDE_FILE_PRE_NOTES = '''\
# %% [markdown] lang="de" tags=["slide"]
"""
## Einführung
Hier eine Einführung in Web Services.
"""

# %% [markdown] lang="en" tags=["slide"]
"""
## Introduction
An introduction to web services.
"""

# %% [markdown] lang="de" tags=["slide"]
"""
## REST vs SOAP
REST ist der moderne Ansatz.
"""
'''


SLIDE_FILE_WITH_NOTES = '''\
# %% [markdown] lang="de" tags=["slide"]
"""
## Einführung
Hier eine Einführung in Web Services.
"""

# %% [markdown] lang="de" tags=["notes"]
"""
- Web Services erlauben lose Kopplung zwischen Systemen.
- Sie sind die Grundlage moderner verteilter Architekturen.
- Wir werden REST-APIs detailliert untersuchen.
"""

# %% [markdown] lang="en" tags=["slide"]
"""
## Introduction
An introduction to web services.
"""

# %% [markdown] lang="en" tags=["notes"]
"""
- Web services enable loose coupling across systems.
- They are the foundation of modern distributed architectures.
- We will examine REST APIs in detail.
"""

# %% [markdown] lang="de" tags=["slide"]
"""
## REST vs SOAP
REST ist der moderne Ansatz.
"""
'''


SLIDE_FILE_WITH_VOICEOVER = '''\
# %% [markdown] lang="de" tags=["slide"]
"""
## Einführung
Hier eine Einführung in Web Services.
"""

# %% [markdown] lang="de" tags=["voiceover"]
"""
- Web Services erlauben lose Kopplung zwischen Systemen.
- Sie sind die Grundlage moderner verteilter Architekturen.
- Wir werden REST-APIs detailliert untersuchen.
"""
'''


SLIDE_FILE_CONTENT_CHANGED = '''\
# %% [markdown] lang="de" tags=["slide"]
"""
## Einführung in verteilte Systeme und Web Services
Eine ausführliche Einführung in die Welt der Web Services
mit vielen Beispielen und Erklärungen zur Architektur,
Geschichte und aktuellen Best Practices in der Industrie.
"""

# %% [markdown] lang="en" tags=["slide"]
"""
## Introduction to Distributed Systems and Web Services
A detailed introduction to the world of web services with
many examples and explanations about architecture, history,
and current best practices in the industry.
"""

# %% [markdown] lang="de" tags=["slide"]
"""
## REST vs SOAP — Eine Gegenüberstellung
REST ist der moderne Ansatz, aber SOAP existiert noch.
Hier eine ausführliche Gegenüberstellung beider Ansätze
mit Blick auf Performance, Tooling und Wartbarkeit.
"""
'''


class TestClassifyCells:
    def test_pure_content(self):
        cells = parse_cells(SLIDE_FILE_PRE_NOTES)
        narrative, content = classify_cells(cells)
        assert narrative == 0
        assert content > 0

    def test_notes_count_as_narrative(self):
        cells = parse_cells(SLIDE_FILE_WITH_NOTES)
        narrative, content = classify_cells(cells)
        assert narrative > 0
        assert content > 0

    def test_voiceover_count_as_narrative(self):
        cells = parse_cells(SLIDE_FILE_WITH_VOICEOVER)
        narrative, content = classify_cells(cells)
        assert narrative > 0

    def test_empty(self):
        assert classify_cells([]) == (0, 0)


class TestComputeRatio:
    def test_pure_narrative(self):
        # narrative_delta=100, content_delta=0 → ~1.0
        r = compute_ratio(100, 0)
        assert r > 0.9

    def test_pure_content(self):
        r = compute_ratio(0, 100)
        assert r < 0.1

    def test_balanced(self):
        r = compute_ratio(100, 100)
        assert 0.4 < r < 0.6

    def test_zero_zero_safe(self):
        # +1 guard prevents division by zero
        assert compute_ratio(0, 0) == 0.0


class TestComputeCommitMetrics:
    def _commit(self) -> CommitInfo:
        return CommitInfo(
            sha="a" * 40,
            parent_sha="b" * 40,
            date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            subject="test commit",
        )

    def test_adding_notes_is_narrative_heavy(self):
        m = compute_commit_metrics(
            self._commit(),
            SLIDE_FILE_PRE_NOTES,
            SLIDE_FILE_WITH_NOTES,
        )
        assert m.is_narrative_heavy
        assert m.narrative_delta > 0
        assert m.ratio >= 0.7

    def test_content_edit_is_not_narrative_heavy(self):
        m = compute_commit_metrics(
            self._commit(),
            SLIDE_FILE_PRE_NOTES,
            SLIDE_FILE_CONTENT_CHANGED,
        )
        assert not m.is_narrative_heavy
        assert m.content_delta > 0
        assert m.ratio < 0.7

    def test_new_file_no_parent(self):
        m = compute_commit_metrics(
            self._commit(),
            parent_text=None,
            commit_text=SLIDE_FILE_PRE_NOTES,
        )
        assert m.narrative_delta == 0
        assert m.content_delta > 0
        assert not m.is_narrative_heavy

    def test_identical_is_zero(self):
        m = compute_commit_metrics(
            self._commit(),
            SLIDE_FILE_WITH_NOTES,
            SLIDE_FILE_WITH_NOTES,
        )
        assert m.narrative_delta == 0
        assert m.content_delta == 0
        assert not m.is_narrative_heavy

    def test_floor_rejects_tiny_narrative_commit(self):
        # narrative_delta below floor → not heavy even if ratio is high
        tiny_before = '# %% [markdown] lang="de" tags=["notes"]\n"""\n- a\n"""'
        tiny_after = '# %% [markdown] lang="de" tags=["notes"]\n"""\n- ab\n"""'
        m = compute_commit_metrics(
            self._commit(),
            tiny_before,
            tiny_after,
            floor=50,
        )
        assert not m.is_narrative_heavy

    def test_threshold_is_adjustable(self):
        # A borderline change ranks heavy at threshold=0.5, not at 0.9
        m_loose = compute_commit_metrics(
            self._commit(),
            SLIDE_FILE_PRE_NOTES,
            SLIDE_FILE_WITH_NOTES,
            threshold=0.5,
        )
        m_strict = compute_commit_metrics(
            self._commit(),
            SLIDE_FILE_PRE_NOTES,
            SLIDE_FILE_WITH_NOTES,
            threshold=0.99,
        )
        assert m_loose.is_narrative_heavy
        # Same commit: ratio is identical; only the heavy flag differs
        assert m_strict.ratio == m_loose.ratio


def _make_metric(
    sha: str,
    parent: str | None,
    heavy: bool,
) -> CommitMetrics:
    return CommitMetrics(
        commit=CommitInfo(
            sha=sha,
            parent_sha=parent,
            date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            subject=f"commit {sha[:4]}",
        ),
        narrative_delta=100 if heavy else 10,
        content_delta=10 if heavy else 100,
        ratio=0.9 if heavy else 0.1,
        is_narrative_heavy=heavy,
    )


class TestCollapseRuns:
    def test_empty(self):
        assert collapse_runs([]) == []

    def test_no_heavy_commits(self):
        metrics = [
            _make_metric("a", "p", False),
            _make_metric("b", "a", False),
        ]
        assert collapse_runs(metrics) == []

    def test_single_heavy_commit_becomes_a_run(self):
        metrics = [
            _make_metric("a", "p", False),
            _make_metric("b", "a", True),
            _make_metric("c", "b", False),
        ]
        runs = collapse_runs(metrics)
        assert len(runs) == 1
        assert runs[0].run_id == 1
        assert runs[0].pre_run_sha == "a"
        assert runs[0].post_run_sha == "b"
        assert len(runs[0].commit_metrics) == 1

    def test_consecutive_heavy_commits_collapse(self):
        metrics = [
            _make_metric("a", "p", False),
            _make_metric("b", "a", True),
            _make_metric("c", "b", True),
            _make_metric("d", "c", True),
            _make_metric("e", "d", False),
        ]
        runs = collapse_runs(metrics)
        assert len(runs) == 1
        assert runs[0].pre_run_sha == "a"
        assert runs[0].post_run_sha == "d"
        assert len(runs[0].commit_metrics) == 3

    def test_multiple_runs(self):
        metrics = [
            _make_metric("a", "p", True),
            _make_metric("b", "a", False),
            _make_metric("c", "b", True),
            _make_metric("d", "c", True),
        ]
        runs = collapse_runs(metrics)
        assert len(runs) == 2
        assert runs[0].post_run_sha == "a"
        assert runs[1].pre_run_sha == "b"
        assert runs[1].post_run_sha == "d"

    def test_root_commit_has_none_pre_run(self):
        metrics = [_make_metric("a", None, True)]
        runs = collapse_runs(metrics)
        assert runs[0].pre_run_sha is None


@pytest.mark.integration
class TestScanSlideFile:
    """Smoke test against a real temp git repo."""

    def _git(self, repo: Path, *args: str, check: bool = True) -> str:
        env = {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_AUTHOR_DATE": "2026-04-01T00:00:00+0000",
            "GIT_COMMITTER_DATE": "2026-04-01T00:00:00+0000",
        }
        import os

        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
            check=check,
        )
        return result.stdout

    def test_end_to_end(self, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")

        slide = repo / "slides.py"

        # Commit 1: initial slides, no notes.
        slide.write_text(SLIDE_FILE_PRE_NOTES, encoding="utf-8")
        self._git(repo, "add", "slides.py")
        self._git(repo, "commit", "-q", "-m", "initial slides")

        # Commit 2: add notes (narrative-heavy).
        slide.write_text(SLIDE_FILE_WITH_NOTES, encoding="utf-8")
        self._git(repo, "add", "slides.py")
        self._git(repo, "commit", "-q", "-m", "add recording notes")

        # Commit 3: rewrite slide content substantively (not narrative-heavy).
        slide.write_text(SLIDE_FILE_CONTENT_CHANGED, encoding="utf-8")
        self._git(repo, "add", "slides.py")
        self._git(repo, "commit", "-q", "-m", "expand slide explanations")

        metrics, runs = scan_slide_file(slide)

        assert len(metrics) == 3
        # Oldest → newest order.
        subjects = [m.commit.subject for m in metrics]
        assert subjects == [
            "initial slides",
            "add recording notes",
            "expand slide explanations",
        ]

        # Initial commit classified as content (no parent, all content chars
        # appear as additions).
        assert not metrics[0].is_narrative_heavy

        # Note-addition commit should be narrative-heavy.
        assert metrics[1].is_narrative_heavy

        # Slide-content-expansion should not.
        assert not metrics[2].is_narrative_heavy

        # One run, containing only the note-addition commit.
        assert len(runs) == 1
        assert len(runs[0].commit_metrics) == 1
        assert runs[0].pre_run_sha == metrics[0].commit.sha
        assert runs[0].post_run_sha == metrics[1].commit.sha
