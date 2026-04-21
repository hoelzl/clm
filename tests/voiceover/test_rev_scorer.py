"""Tests for the identify-rev fingerprint scorer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from clm.voiceover.rev_scorer import (
    NARRATIVE_PRIOR,
    fuzzy_lcs_score,
    score_revisions,
    slide_labels,
)

SLIDES_V1 = '''\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
"""
## Einführung
Willkommen.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="rest-vs-soap"
"""
## REST vs SOAP
REST ist der moderne Ansatz.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="graphql"
"""
## GraphQL
Flexible Abfragen.
"""
'''

SLIDES_V2_NOTES_ADDED = '''\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
"""
## Einführung
Willkommen.
"""

# %% [markdown] lang="de" tags=["notes"]
"""
- Erster Aufhänger für die Einführung.
- Zweiter Aufhänger mit mehr Kontext.
- Dritter Punkt für den Übergang.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="rest-vs-soap"
"""
## REST vs SOAP
REST ist der moderne Ansatz.
"""

# %% [markdown] lang="de" tags=["notes"]
"""
- Historischer Kontext zu SOAP.
- Warum REST gewonnen hat.
- Wann SOAP noch sinnvoll ist.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="graphql"
"""
## GraphQL
Flexible Abfragen.
"""
'''

SLIDES_V3_RENAMED = '''\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
"""
## Einführung in Web APIs
Willkommen zum Kurs.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="rest-vs-soap"
"""
## REST vs SOAP
REST ist der moderne Ansatz.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="graphql"
"""
## GraphQL und moderne Query-Languages
Flexible Abfragen.
"""

# %% [markdown] lang="de" tags=["slide"] slide_id="grpc"
"""
## gRPC
Binäres RPC-Protokoll.
"""
'''


class TestSlideLabels:
    def test_uses_slide_id_when_present(self):
        labels = slide_labels(SLIDES_V1, "de")
        assert labels == ["id:intro", "id:rest-vs-soap", "id:graphql"]

    def test_falls_back_to_title_without_slide_id(self):
        text = '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Titel'
        labels = slide_labels(text, "de")
        assert labels == ["title:Mein Titel"]

    def test_empty_file(self):
        assert slide_labels("", "de") == []

    def test_notes_cells_excluded(self):
        labels = slide_labels(SLIDES_V2_NOTES_ADDED, "de")
        # Same three slide labels; notes cells don't contribute.
        assert labels == ["id:intro", "id:rest-vs-soap", "id:graphql"]


class TestFuzzyLcsScore:
    def test_empty_inputs(self):
        assert fuzzy_lcs_score([], []) == 0.0
        assert fuzzy_lcs_score(["a"], []) == 0.0
        assert fuzzy_lcs_score([], ["a"]) == 0.0

    def test_exact_match_is_one(self):
        labels = ["title:Intro", "title:REST", "title:GraphQL"]
        score = fuzzy_lcs_score(labels, labels)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_complete_mismatch(self):
        # Very different tokens — below the default 70 threshold.
        score = fuzzy_lcs_score(["title:xxxx"], ["title:yyyy"])
        assert score == 0.0

    def test_partial_order_preserved(self):
        # Video saw 2 of 3 slides in order → partial credit.
        rev = ["title:Intro", "title:REST", "title:GraphQL"]
        vid = ["title:Intro", "title:GraphQL"]
        score = fuzzy_lcs_score(rev, vid)
        # 2 exact matches (worth ~2.0), normalised by max(3,2)=3.
        assert 0.6 < score < 0.7

    def test_out_of_order_video_still_scores(self):
        # LCS picks the best in-order subsequence.
        rev = ["title:A", "title:B", "title:C"]
        vid = ["title:C", "title:A", "title:B"]
        # Best in-order subsequence is A,B or C alone or A alone.
        # A,B matches: 2 contributions normalised by max(3,3)=3 → ~0.67.
        score = fuzzy_lcs_score(rev, vid)
        assert 0.6 < score < 0.75

    def test_fuzzy_matching_handles_ocr_noise(self):
        # OCR mangles 'REST' → 'RE5T' but token_set_ratio still matches.
        rev = ["title:REST vs SOAP"]
        vid = ["title:RE5T vs SOAP"]
        score = fuzzy_lcs_score(rev, vid)
        assert score > 0.7


def _git(repo: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2026-04-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2026-04-01T00:00:00+0000",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=True,
    )
    return result.stdout


@pytest.mark.integration
class TestScoreRevisionsIntegration:
    """End-to-end scoring against a real temp git repo."""

    def _make_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")

        slide = repo / "slides.py"

        slide.write_text(SLIDES_V1, encoding="utf-8")
        _git(repo, "add", "slides.py")
        _git(repo, "commit", "-q", "-m", "initial slides")

        slide.write_text(SLIDES_V2_NOTES_ADDED, encoding="utf-8")
        _git(repo, "add", "slides.py")
        _git(repo, "commit", "-q", "-m", "record notes for session 1")

        slide.write_text(SLIDES_V3_RENAMED, encoding="utf-8")
        _git(repo, "add", "slides.py")
        _git(repo, "commit", "-q", "-m", "add gRPC + rename intro")

        return slide

    def test_matching_fingerprint_ranks_correct_rev_first(self, tmp_path):
        slide = self._make_repo(tmp_path)
        # Video fingerprint matching the v1/v2 slide labels (which share
        # the same three slide_ids).
        video_fp = ["Einführung", "REST vs SOAP", "GraphQL"]

        scored = score_revisions(slide, video_fp, lang="de")
        assert len(scored) >= 3
        # Top score should be meaningful (not zero).
        assert scored[0].score > 0.5
        # v1 and v2 both have the matching 3 slide_ids; v3 renamed intro
        # and adds gRPC which has no video-side match — v3 should score
        # strictly lower than the others.
        v3_subject = "add gRPC + rename intro"
        v3_score = next(r.score for r in scored if r.subject == v3_subject)
        other_scores = [r.score for r in scored if r.subject != v3_subject]
        assert all(s >= v3_score for s in other_scores)

    def test_narrative_prior_applied_to_run_endpoints(self, tmp_path):
        slide = self._make_repo(tmp_path)
        video_fp = ["Einführung", "REST vs SOAP", "GraphQL"]

        scored = score_revisions(slide, video_fp, lang="de")
        # The notes-adding commit is narrative-heavy; its post-run tip
        # and the pre-run parent (initial commit) should be endpoints.
        endpoints = [r for r in scored if r.is_narrative_candidate]
        assert endpoints, "expected at least one narrative-run endpoint"
        for r in endpoints:
            assert r.narrative_prior == NARRATIVE_PRIOR
            # base_score * prior == final score
            assert r.score == pytest.approx(r.base_score * r.narrative_prior)

    def test_empty_video_fingerprint_returns_zero_scores(self, tmp_path):
        slide = self._make_repo(tmp_path)
        scored = score_revisions(slide, [], lang="de")
        assert all(r.base_score == 0.0 for r in scored)
