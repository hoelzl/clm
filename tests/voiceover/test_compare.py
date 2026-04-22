"""Tests for clm.voiceover.compare.judge_slide_pair and CompareReport."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.voiceover.bullet_schema import BulletOutcome, BulletStatus
from clm.voiceover.compare import (
    CompareReport,
    SlideComparison,
    judge_slide_pair,
)
from clm.voiceover.slide_matcher import MatchKind


def _mock_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestJudgeSlidePair:
    @pytest.mark.asyncio
    async def test_both_empty_skips_llm(self):
        outcomes, notes, err = await judge_slide_pair(
            prior_bullets="",
            baseline_bullets="",
            slide_content_head="# Slide",
            slide_content_prior=None,
            language="en",
            content_changed=False,
            slide_id="s/1",
        )
        assert outcomes == []
        assert notes is None
        assert err is None

    @pytest.mark.asyncio
    async def test_parses_structured_response(self):
        mock_llm = _mock_response(
            json.dumps(
                {
                    "bullets": "- a\n- b",
                    "outcomes": [
                        {"status": "covered", "target": "- a", "source": "- a"},
                        {
                            "status": "rewritten",
                            "target": "- b",
                            "source": "- b old",
                            "note": "clarified wording",
                        },
                    ],
                    "notes": "Minor rewording only.",
                }
            )
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_llm)
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            outcomes, notes, err = await judge_slide_pair(
                prior_bullets="- a\n- b old",
                baseline_bullets="- a\n- b",
                slide_content_head="# Head",
                slide_content_prior=None,
                language="en",
                content_changed=False,
                slide_id="s/1",
            )
        assert err is None
        assert notes == "Minor rewording only."
        assert len(outcomes) == 2
        assert outcomes[0].status is BulletStatus.COVERED
        assert outcomes[1].status is BulletStatus.REWRITTEN
        assert outcomes[1].note == "clarified wording"

    @pytest.mark.asyncio
    async def test_non_llmerror_exception_reported_as_error(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("network"))
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            outcomes, notes, err = await judge_slide_pair(
                prior_bullets="- a",
                baseline_bullets="- b",
                slide_content_head="",
                slide_content_prior=None,
                language="en",
                content_changed=False,
                slide_id="s/1",
            )
        assert outcomes == []
        assert err is not None
        assert "network" in err

    @pytest.mark.asyncio
    async def test_content_changed_includes_prior_slide(self):
        captured: list[dict] = []

        async def capture_create(**kwargs):
            captured.extend(kwargs["messages"])
            return _mock_response(json.dumps({"bullets": "", "outcomes": []}))

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=capture_create)
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            await judge_slide_pair(
                prior_bullets="- a",
                baseline_bullets="- b",
                slide_content_head="# New",
                slide_content_prior="# Old",
                language="en",
                content_changed=True,
                slide_id="s/1",
            )
        user_msg = next(m["content"] for m in captured if m["role"] == "user")
        assert "# Old" in user_msg
        assert "prior/source version" in user_msg

    @pytest.mark.asyncio
    async def test_uses_compare_prompt_not_port_prompt(self):
        captured_system: list[str] = []

        async def capture_create(**kwargs):
            for m in kwargs["messages"]:
                if m["role"] == "system":
                    captured_system.append(m["content"])
            return _mock_response(json.dumps({"bullets": "", "outcomes": []}))

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=capture_create)
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            await judge_slide_pair(
                prior_bullets="- a",
                baseline_bullets="- b",
                slide_content_head="",
                slide_content_prior=None,
                language="en",
                content_changed=False,
                slide_id="s/1",
            )
        assert captured_system, "system prompt not captured"
        # The compare prompt has evaluative language; the port prompt is constructive.
        assert "read-only" in captured_system[0] or "evaluation" in captured_system[0]


class TestCompareReport:
    def _sample(self) -> CompareReport:
        return CompareReport(
            source=Path("old.py"),
            target=Path("new.py"),
            language="en",
            slides=[
                SlideComparison(
                    key="id:s1",
                    kind=MatchKind.UNCHANGED,
                    target_index=0,
                    source_index=0,
                    content_similarity=100.0,
                    outcomes=[
                        BulletOutcome(status=BulletStatus.COVERED, target="- x", source="- x"),
                        BulletOutcome(status=BulletStatus.REWRITTEN, target="- y", source="- y'"),
                    ],
                ),
                SlideComparison(
                    key="id:s2",
                    kind=MatchKind.NEW_AT_HEAD,
                    target_index=1,
                    source_index=None,
                ),
                SlideComparison(
                    key="id:s3",
                    kind=MatchKind.MODIFIED,
                    target_index=2,
                    source_index=1,
                    content_similarity=70.0,
                    outcomes=[
                        BulletOutcome(status=BulletStatus.DROPPED, source="- dropped"),
                    ],
                ),
            ],
        )

    def test_status_totals(self):
        report = self._sample()
        totals = report.status_totals()
        assert totals[BulletStatus.COVERED] == 1
        assert totals[BulletStatus.REWRITTEN] == 1
        assert totals[BulletStatus.DROPPED] == 1
        assert totals[BulletStatus.ADDED] == 0

    def test_kind_totals(self):
        report = self._sample()
        kinds = report.kind_totals()
        assert kinds[MatchKind.UNCHANGED] == 1
        assert kinds[MatchKind.MODIFIED] == 1
        assert kinds[MatchKind.NEW_AT_HEAD] == 1

    def test_to_json_roundtrips_fields(self):
        report = self._sample()
        payload = report.to_json()
        assert payload["language"] == "en"
        assert payload["slide_count"] == 3
        assert payload["status_totals"]["covered"] == 1
        assert payload["status_totals"]["rewritten"] == 1
        assert payload["status_totals"]["dropped"] == 1
        assert payload["kind_totals"]["unchanged"] == 1
        assert len(payload["slides"]) == 3
        assert payload["slides"][0]["outcomes"][0]["status"] == "covered"

    def test_slide_comparison_status_counts_empty_when_no_outcomes(self):
        comp = SlideComparison(
            key="id:x",
            kind=MatchKind.NEW_AT_HEAD,
            target_index=0,
            source_index=None,
        )
        counts = comp.status_counts()
        assert all(v == 0 for v in counts.values())


_SOURCE_SLIDES = """# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# Intro

Explain what we are going to learn.

# %% [markdown] lang="en" tags=["voiceover"]
- talk about goals
- emphasise practice over theory
"""

_TARGET_SLIDES = """# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# Intro

Explain what we are going to learn.

# %% [markdown] lang="en" tags=["voiceover"]
- talk about goals
- mention the syllabus
"""


class TestCompareCLI:
    def test_runs_and_emits_json(self, tmp_path: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        source = tmp_path / "old.py"
        target = tmp_path / "new.py"
        source.write_text(_SOURCE_SLIDES, encoding="utf-8")
        target.write_text(_TARGET_SLIDES, encoding="utf-8")

        llm_payload = json.dumps(
            {
                "bullets": "- talk about goals\n- mention the syllabus",
                "outcomes": [
                    {
                        "status": "covered",
                        "target": "- talk about goals",
                        "source": "- talk about goals",
                    },
                    {
                        "status": "rewritten",
                        "target": "- mention the syllabus",
                        "source": "- emphasise practice over theory",
                        "note": "substantive rewording",
                    },
                ],
                "notes": "One bullet rewritten.",
            }
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(llm_payload))

        runner = CliRunner()
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = runner.invoke(
                voiceover_group,
                [
                    "compare",
                    str(source),
                    str(target),
                    "--lang",
                    "en",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output
        # Output begins with rich-console lines, but --json prints the payload via click.echo.
        # Find the first `{` and parse from there.
        start = result.output.find("{")
        assert start >= 0, f"No JSON found in output: {result.output!r}"
        data = json.loads(result.output[start:])
        assert data["language"] == "en"
        assert data["status_totals"]["covered"] == 1
        assert data["status_totals"]["rewritten"] == 1

    def test_writes_json_to_output(self, tmp_path: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        source = tmp_path / "old.py"
        target = tmp_path / "new.py"
        source.write_text(_SOURCE_SLIDES, encoding="utf-8")
        target.write_text(_TARGET_SLIDES, encoding="utf-8")
        out = tmp_path / "report.json"

        llm_payload = json.dumps({"bullets": "", "outcomes": []})
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(llm_payload))

        runner = CliRunner()
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = runner.invoke(
                voiceover_group,
                [
                    "compare",
                    str(source),
                    str(target),
                    "--lang",
                    "en",
                    "--json",
                    "-o",
                    str(out),
                ],
            )
        assert result.exit_code == 0, result.output
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["target"].endswith("new.py")
        assert data["source"].endswith("old.py")
