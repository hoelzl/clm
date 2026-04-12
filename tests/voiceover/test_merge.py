"""Tests for voiceover merge module.

Tests the core merge logic: prompt building, JSON parsing, batching,
polish_and_merge with mocked LLM, and noise filter fixtures.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.voiceover.merge import (
    DEFAULT_BATCH_CHAR_LIMIT,
    MergeResult,
    SlideInput,
    _build_batch_user_prompt,
    _build_user_prompt,
    _parse_batch_result,
    _parse_single_result,
    build_batches,
    merge_batch,
    polish_and_merge,
)

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    def test_basic_prompt_with_all_fields(self):
        slide = SlideInput(
            slide_id="test/1",
            baseline="- existing point",
            transcript="the trainer said something new",
            slide_content="# My Slide Title",
        )
        prompt = _build_user_prompt(slide)
        assert "SLIDE CONTEXT" in prompt
        assert "My Slide Title" in prompt
        assert "BASELINE VOICEOVER" in prompt
        assert "existing point" in prompt
        assert "TRANSCRIPT" in prompt
        assert "trainer said something new" in prompt

    def test_empty_baseline_says_empty(self):
        slide = SlideInput(
            slide_id="test/1",
            baseline="",
            transcript="some transcript",
            slide_content="# Title",
        )
        prompt = _build_user_prompt(slide)
        assert "(empty -- no existing voiceover)" in prompt

    def test_boundary_hint_included(self):
        slide = SlideInput(
            slide_id="test/1",
            baseline="- bullet",
            transcript="transcript",
            slide_content="",
            boundary_hint=True,
        )
        prompt = _build_user_prompt(slide)
        assert "recording part boundary" in prompt
        assert "greeting/sign-off noise" in prompt

    def test_no_boundary_hint_by_default(self):
        slide = SlideInput(
            slide_id="test/1",
            baseline="- bullet",
            transcript="transcript",
            slide_content="",
        )
        prompt = _build_user_prompt(slide)
        assert "recording part boundary" not in prompt

    def test_empty_slide_content_omitted(self):
        slide = SlideInput(
            slide_id="test/1",
            baseline="- bullet",
            transcript="transcript",
            slide_content="   ",
        )
        prompt = _build_user_prompt(slide)
        assert "SLIDE CONTEXT" not in prompt


class TestBuildBatchUserPrompt:
    def test_batch_prompt_contains_all_slides(self):
        slides = [
            SlideInput("s/1", "- a", "transcript a", "slide a"),
            SlideInput("s/2", "- b", "transcript b", "slide b"),
        ]
        prompt = _build_batch_user_prompt(slides)
        assert "2 slides" in prompt
        assert "--- SLIDE: s/1 ---" in prompt
        assert "--- SLIDE: s/2 ---" in prompt
        assert "transcript a" in prompt
        assert "transcript b" in prompt


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseSingleResult:
    def test_valid_json(self):
        raw = json.dumps(
            {
                "merged_bullets": "- bullet one\n- bullet two",
                "rewrites": [],
                "dropped_from_transcript": ["hello there"],
            }
        )
        result = _parse_single_result(raw, "test/1")
        assert result.slide_id == "test/1"
        assert result.merged_bullets == "- bullet one\n- bullet two"
        assert result.rewrites == []
        assert result.dropped_from_transcript == ["hello there"]

    def test_strips_code_fences(self):
        raw = (
            '```json\n{"merged_bullets": "- x", "rewrites": [], "dropped_from_transcript": []}\n```'
        )
        result = _parse_single_result(raw, "test/1")
        assert result.merged_bullets == "- x"

    def test_with_rewrites(self):
        raw = json.dumps(
            {
                "merged_bullets": "- corrected bullet",
                "rewrites": [
                    {
                        "original": "- wrong fact",
                        "revised": "- corrected bullet",
                        "transcript_evidence": "the trainer said otherwise",
                    }
                ],
                "dropped_from_transcript": [],
            }
        )
        result = _parse_single_result(raw, "test/2")
        assert len(result.rewrites) == 1
        assert result.rewrites[0]["original"] == "- wrong fact"


class TestParseBatchResult:
    def test_array_format(self):
        raw = json.dumps(
            [
                {
                    "slide_id": "s/1",
                    "merged_bullets": "- a",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
                {
                    "slide_id": "s/2",
                    "merged_bullets": "- b",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
            ]
        )
        results = _parse_batch_result(raw, ["s/1", "s/2"])
        assert "s/1" in results
        assert "s/2" in results
        assert results["s/1"].merged_bullets == "- a"
        assert results["s/2"].merged_bullets == "- b"

    def test_dict_keyed_format(self):
        raw = json.dumps(
            {
                "s/1": {
                    "merged_bullets": "- a",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
                "s/2": {
                    "merged_bullets": "- b",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
            }
        )
        results = _parse_batch_result(raw, ["s/1", "s/2"])
        assert "s/1" in results
        assert results["s/1"].merged_bullets == "- a"

    def test_single_dict_format(self):
        raw = json.dumps(
            {
                "slide_id": "s/1",
                "merged_bullets": "- a",
                "rewrites": [],
                "dropped_from_transcript": [],
            }
        )
        results = _parse_batch_result(raw, ["s/1"])
        assert "s/1" in results

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_batch_result("not json at all", ["s/1"])


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


class TestBuildBatches:
    def test_single_slide_single_batch(self):
        slides = [SlideInput("s/1", "a" * 100, "b" * 100, "c" * 100)]
        batches = build_batches(slides, char_limit=1000)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_splits_when_over_budget(self):
        slides = [
            SlideInput("s/1", "a" * 5000, "b" * 5000, "c" * 5000),
            SlideInput("s/2", "d" * 5000, "e" * 5000, "f" * 5000),
        ]
        batches = build_batches(slides, char_limit=16000)
        assert len(batches) == 2

    def test_packs_small_slides_together(self):
        slides = [SlideInput(f"s/{i}", "a" * 10, "b" * 10, "c" * 10) for i in range(10)]
        batches = build_batches(slides, char_limit=1000)
        # 30 chars per slide, 10 slides = 300 chars total, fits in one batch
        assert len(batches) == 1

    def test_oversized_slide_gets_own_batch(self):
        slides = [
            SlideInput("s/1", "a" * 10, "b" * 10, "c" * 10),
            SlideInput("s/2", "x" * 30000, "y" * 30000, "z" * 30000),
            SlideInput("s/3", "a" * 10, "b" * 10, "c" * 10),
        ]
        batches = build_batches(slides, char_limit=20000)
        # s/1 alone, s/2 alone (oversized), s/3 alone
        assert len(batches) == 3

    def test_empty_input(self):
        batches = build_batches([], char_limit=1000)
        assert batches == []


# ---------------------------------------------------------------------------
# polish_and_merge with mocked LLM
# ---------------------------------------------------------------------------


def _mock_llm_response(content: str):
    """Create a mock OpenAI response."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


class TestPolishAndMerge:
    @pytest.mark.asyncio
    async def test_merge_with_baseline(self):
        mock_response = _mock_llm_response(
            json.dumps(
                {
                    "merged_bullets": "- existing point\n- new addition from transcript",
                    "rewrites": [],
                    "dropped_from_transcript": ["willkommen zurück"],
                }
            )
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_merge(
                baseline_bullets="- existing point",
                transcript_text="new addition from transcript. willkommen zurück",
                slide_content="# Test Slide",
                language="de",
                slide_id="test/1",
            )

        assert result.slide_id == "test/1"
        assert "existing point" in result.merged_bullets
        assert "new addition" in result.merged_bullets
        assert result.rewrites == []
        assert "willkommen zurück" in result.dropped_from_transcript

    @pytest.mark.asyncio
    async def test_empty_baseline_degrades_to_polish(self):
        """When baseline is empty, should produce fresh bullets."""
        mock_response = _mock_llm_response(
            json.dumps(
                {
                    "merged_bullets": "- point from transcript",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                }
            )
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_merge(
                baseline_bullets="",
                transcript_text="point from transcript",
                slide_content="# Test",
                language="en",
                slide_id="test/2",
            )

        assert result.merged_bullets == "- point from transcript"

    @pytest.mark.asyncio
    async def test_rewrite_detection(self):
        """Baseline rewrite should be captured in rewrites field."""
        mock_response = _mock_llm_response(
            json.dumps(
                {
                    "merged_bullets": "- extend mutates the list in place and returns None",
                    "rewrites": [
                        {
                            "original": "- extend returns a new list",
                            "revised": "- extend mutates the list in place and returns None",
                            "transcript_evidence": "actually, extend mutates in place",
                        }
                    ],
                    "dropped_from_transcript": [],
                }
            )
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_merge(
                baseline_bullets="- extend returns a new list",
                transcript_text="actually, extend mutates in place, it doesn't return anything",
                slide_content="",
                language="en",
                slide_id="test/3",
            )

        assert len(result.rewrites) == 1
        assert result.rewrites[0]["original"] == "- extend returns a new list"

    @pytest.mark.asyncio
    async def test_json_parse_failure_uses_raw_text(self):
        """When LLM returns non-JSON, fall back to raw text."""
        mock_response = _mock_llm_response("- just plain bullets\n- no json here")

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_merge(
                baseline_bullets="- old",
                transcript_text="something",
                language="en",
                slide_id="test/4",
            )

        assert "just plain bullets" in result.merged_bullets
        assert result.rewrites == []

    @pytest.mark.asyncio
    async def test_llm_error_raises(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            with pytest.raises(Exception, match="Merge LLM call failed"):
                await polish_and_merge(
                    baseline_bullets="- x",
                    transcript_text="y",
                    language="en",
                    slide_id="test/5",
                )


class TestMergeBatch:
    @pytest.mark.asyncio
    async def test_single_slide_batch_delegates_to_polish_and_merge(self):
        mock_response = _mock_llm_response(
            json.dumps(
                {
                    "merged_bullets": "- result",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                }
            )
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        slides = [SlideInput("s/1", "- baseline", "transcript", "content")]

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            results = await merge_batch(slides, language="en")

        assert len(results) == 1
        assert results[0].merged_bullets == "- result"

    @pytest.mark.asyncio
    async def test_multi_slide_batch_parses_array(self):
        batch_response = json.dumps(
            [
                {
                    "slide_id": "s/1",
                    "merged_bullets": "- merged a",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
                {
                    "slide_id": "s/2",
                    "merged_bullets": "- merged b",
                    "rewrites": [],
                    "dropped_from_transcript": [],
                },
            ]
        )

        mock_response = _mock_llm_response(batch_response)
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        slides = [
            SlideInput("s/1", "- a", "transcript a", "content a"),
            SlideInput("s/2", "- b", "transcript b", "content b"),
        ]

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            results = await merge_batch(slides, language="en")

        assert len(results) == 2
        assert results[0].merged_bullets == "- merged a"
        assert results[1].merged_bullets == "- merged b"

    @pytest.mark.asyncio
    async def test_batch_json_failure_falls_back_to_per_slide(self):
        """When batch JSON parse fails, fall back to per-slide calls."""
        # First call (batch) returns garbage
        # Subsequent calls (per-slide) return valid JSON
        call_count = 0

        async def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Batch call returns non-JSON
                return _mock_llm_response("this is not json")
            # Per-slide fallback calls
            slide_id = f"s/{call_count - 1}"
            return _mock_llm_response(
                json.dumps(
                    {
                        "merged_bullets": f"- fallback {call_count - 1}",
                        "rewrites": [],
                        "dropped_from_transcript": [],
                    }
                )
            )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=fake_create)

        slides = [
            SlideInput("s/1", "- a", "tx a", "c a"),
            SlideInput("s/2", "- b", "tx b", "c b"),
        ]

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            results = await merge_batch(slides, language="en")

        assert len(results) == 2
        # Fallback produced results (call_count was incremented)
        assert call_count == 3  # 1 batch + 2 per-slide


# ---------------------------------------------------------------------------
# Noise filter fixtures
# ---------------------------------------------------------------------------


class TestNoiseFixturesGerman:
    """Fixture data for noise categories that should be filtered.

    These are the concrete examples from the proposal. The actual filtering
    is done by the LLM — these tests verify that the prompt builder includes
    the right context and that the fixture data is well-structured for
    future prompt tuning.
    """

    GREETINGS_DROP = [
        "Hallo, willkommen zurück zu Teil 2, ich hatte die Aufnahme kurz unterbrochen.",
        "So, das war's für heute, bis zum nächsten Mal!",
        "Jetzt in Teil 2 angelangt.",
    ]

    SELF_CORRECTIONS_DROP = [
        "Moment, ich hab da was übersehen, lass mich kurz zurückscrollen.",
        "Oh sorry, das war der falsche Slide, ich muss da nochmal hin.",
        "Uh, entschuldigung, das Mikrofon hat gerade kurz ausgesetzt.",
    ]

    ENVIRONMENT_REMARKS_DROP = [
        "Mein Docker-Container ist rot, weil ich das falsche Environment habe.",
        "Mein Editor zeigt da rot, das ist aber egal.",
    ]

    OPERATOR_ASIDES_DROP = [
        "Kannst du das nachher rausschneiden.",
        "Das kommt in den Schnitt.",
    ]

    CODE_TYPING_DICTATION_DROP = [
        "And then we define the function — def — fact — open paren — n — colon…",
        "For m comma n in range…",
        "Close paren, colon, return.",
    ]

    SUBSTANTIVE_KEEP = [
        "Oh, and by the way — extend mutates the list in place, it doesn't return a new one.",
        "The free OpenRouter tier has a rate limit of ~20 requests per minute, so don't spam it when testing.",
        "One thing I forgot to put on the slide: you can also pass system_prompt as a regular string instead of a SystemMessage in newer LangChain versions.",
    ]

    def test_drop_categories_are_non_empty(self):
        """Ensure each noise category has enough examples for testing."""
        assert len(self.GREETINGS_DROP) >= 3
        assert len(self.SELF_CORRECTIONS_DROP) >= 3
        assert len(self.ENVIRONMENT_REMARKS_DROP) >= 2
        assert len(self.OPERATOR_ASIDES_DROP) >= 2
        assert len(self.CODE_TYPING_DICTATION_DROP) >= 3

    def test_keep_examples_are_substantive(self):
        """Substantive additions should be non-trivial sentences."""
        for text in self.SUBSTANTIVE_KEEP:
            assert len(text) > 30

    def test_prompt_contains_filter_categories(self):
        """Verify the merge prompt mentions all filter categories."""
        from clm.voiceover.merge import _load_system_prompt

        prompt = _load_system_prompt("de")
        # Check that key filter categories are mentioned
        assert "Begruessung" in prompt or "Verabschiedung" in prompt
        assert "Selbstkorrektur" in prompt or "Aufnahme-Selbstkorrektur" in prompt
        assert "Umgebungsbemerkung" in prompt or "Docker" in prompt
        assert "Operator" in prompt or "rausschneiden" in prompt
        assert "Code-Tipp" in prompt or "Live-Coding" in prompt

    def test_en_prompt_contains_filter_categories(self):
        """Verify the English merge prompt mentions all filter categories."""
        from clm.voiceover.merge import _load_system_prompt

        prompt = _load_system_prompt("en")
        assert "Greetings" in prompt or "sign-off" in prompt
        assert "self-correction" in prompt or "wrong slide" in prompt
        assert "environment" in prompt or "Docker" in prompt
        assert "operator" in prompt or "cut that out" in prompt
        assert "Code-typing" in prompt or "live-coding" in prompt


class TestNoiseFixturesEnglish:
    """English noise fixture seed data."""

    GREETINGS_DROP = [
        "Welcome back to part 2, I had to pause the recording briefly.",
        "Alright, that's it for today, see you next time!",
        "Now arriving in part 3.",
    ]

    SELF_CORRECTIONS_DROP = [
        "Wait, I overlooked something, let me scroll back.",
        "Oh sorry, that was the wrong slide, I need to go back.",
        "Uh, excuse me, the microphone just cut out briefly.",
    ]

    ENVIRONMENT_REMARKS_DROP = [
        "My Docker container is red because I have the wrong environment.",
        "My editor shows red there, but that's fine.",
    ]

    SUBSTANTIVE_KEEP = [
        "Oh, and by the way -- extend mutates the list in place, it doesn't return a new one.",
        "The free OpenRouter tier has a rate limit of about 20 requests per minute.",
        "One thing I forgot: you can also pass system_prompt as a regular string.",
    ]

    def test_drop_categories_are_non_empty(self):
        assert len(self.GREETINGS_DROP) >= 3
        assert len(self.SELF_CORRECTIONS_DROP) >= 3
        assert len(self.ENVIRONMENT_REMARKS_DROP) >= 2

    def test_keep_examples_are_substantive(self):
        for text in self.SUBSTANTIVE_KEEP:
            assert len(text) > 30


# ---------------------------------------------------------------------------
# Rewrite detection
# ---------------------------------------------------------------------------


class TestRewriteDetection:
    """Tests that rewrite metadata flows through correctly."""

    @pytest.mark.asyncio
    async def test_rewrite_preserved_in_result(self):
        rewrite = {
            "original": "- extend returns a new list",
            "revised": "- extend mutates the list in place and returns None",
            "transcript_evidence": "actually, extend mutates in place",
        }
        mock_response = _mock_llm_response(
            json.dumps(
                {
                    "merged_bullets": "- extend mutates the list in place and returns None",
                    "rewrites": [rewrite],
                    "dropped_from_transcript": [],
                }
            )
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("clm.infrastructure.llm.client._build_client", return_value=mock_client):
            result = await polish_and_merge(
                baseline_bullets="- extend returns a new list",
                transcript_text="actually, extend mutates in place and returns None",
                language="en",
                slide_id="test/rewrite",
            )

        assert len(result.rewrites) == 1
        assert result.rewrites[0]["original"] == "- extend returns a new list"
        assert "mutates" in result.rewrites[0]["revised"]
        assert "transcript_evidence" in result.rewrites[0]


# ---------------------------------------------------------------------------
# System prompt loading
# ---------------------------------------------------------------------------


class TestSystemPromptLoading:
    def test_load_de_prompt(self):
        from clm.voiceover.merge import _load_system_prompt

        prompt = _load_system_prompt("de")
        assert "Voiceover" in prompt
        assert "JSON" in prompt

    def test_load_en_prompt(self):
        from clm.voiceover.merge import _load_system_prompt

        prompt = _load_system_prompt("en")
        assert "voiceover" in prompt
        assert "JSON" in prompt

    def test_unknown_language_falls_back_to_en(self):
        from clm.voiceover.merge import _load_system_prompt

        prompt = _load_system_prompt("fr")
        assert "voiceover" in prompt  # English fallback
