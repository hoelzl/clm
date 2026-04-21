"""Tests for clm.voiceover.port.polish_and_port."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.voiceover.bullet_schema import BulletStatus
from clm.voiceover.port import PortResult, polish_and_port


def _mock_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestPolishAndPortShortcuts:
    @pytest.mark.asyncio
    async def test_both_empty_returns_empty_without_llm(self):
        result = await polish_and_port(
            baseline_bullets="",
            prior_voiceover="",
            slide_content_head="# Slide",
            slide_content_prior=None,
            language="en",
            content_changed=False,
            slide_id="s/1",
        )
        assert isinstance(result, PortResult)
        assert result.bullets == ""
        assert result.outcomes == []

    @pytest.mark.asyncio
    async def test_empty_prior_returns_baseline_without_llm(self):
        result = await polish_and_port(
            baseline_bullets="- existing\n- two",
            prior_voiceover="",
            slide_content_head="",
            slide_content_prior=None,
            language="en",
            content_changed=False,
            slide_id="s/1",
        )
        assert result.bullets == "- existing\n- two"


class TestPolishAndPortLLM:
    @pytest.mark.asyncio
    async def test_successful_port(self):
        mock_llm = _mock_response(
            json.dumps(
                {
                    "bullets": "- existing\n- new from prior",
                    "outcomes": [
                        {"status": "covered", "target": "- existing", "source": "- existing"},
                        {
                            "status": "added",
                            "target": "- new from prior",
                            "source": "- new from prior",
                        },
                    ],
                    "notes": "Clean port.",
                }
            )
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_llm)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_port(
                baseline_bullets="- existing",
                prior_voiceover="- new from prior",
                slide_content_head="# Head",
                slide_content_prior=None,
                language="en",
                content_changed=False,
                slide_id="s/1",
            )

        assert "new from prior" in result.bullets
        assert result.notes == "Clean port."
        assert len(result.outcomes) == 2
        assert result.outcomes[0].status is BulletStatus.COVERED
        assert result.error is None

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_baseline(self):
        mock_llm = _mock_response("this is not JSON")
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_llm)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_port(
                baseline_bullets="- baseline",
                prior_voiceover="- prior",
                slide_content_head="",
                slide_content_prior=None,
                language="en",
                content_changed=False,
                slide_id="s/1",
            )

        # Invalid JSON -> fallback to baseline (preferred over unmerged prior).
        assert result.bullets == "- baseline"
        assert result.outcomes == []

    @pytest.mark.asyncio
    async def test_llm_client_failure_non_llmerror_returns_fallback(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("network down"))
        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_port(
                baseline_bullets="- baseline",
                prior_voiceover="- prior",
                slide_content_head="",
                slide_content_prior=None,
                language="en",
                content_changed=False,
                slide_id="s/1",
            )
        assert result.error is not None
        assert "network down" in result.error
        assert result.bullets == "- baseline"

    @pytest.mark.asyncio
    async def test_content_changed_passes_prior_slide_text(self):
        """When content_changed=True the LLM must receive the prior slide text."""
        captured_messages: list[dict] = []

        async def capture_create(**kwargs):
            captured_messages.extend(kwargs["messages"])
            return _mock_response(json.dumps({"bullets": "- x", "outcomes": []}))

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=capture_create)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            await polish_and_port(
                baseline_bullets="",
                prior_voiceover="- prior",
                slide_content_head="# Head",
                slide_content_prior="# Prior",
                language="en",
                content_changed=True,
                slide_id="s/1",
            )

        user_content = next(m["content"] for m in captured_messages if m["role"] == "user")
        assert "# Prior" in user_content
        assert "prior/source version" in user_content

    @pytest.mark.asyncio
    async def test_content_unchanged_hides_prior_slide_text(self):
        captured_messages: list[dict] = []

        async def capture_create(**kwargs):
            captured_messages.extend(kwargs["messages"])
            return _mock_response(json.dumps({"bullets": "- x", "outcomes": []}))

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=capture_create)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            await polish_and_port(
                baseline_bullets="",
                prior_voiceover="- prior",
                slide_content_head="# Same",
                slide_content_prior="# Same",
                language="en",
                content_changed=False,
                slide_id="s/1",
            )

        user_content = next(m["content"] for m in captured_messages if m["role"] == "user")
        assert "prior/source version" not in user_content
