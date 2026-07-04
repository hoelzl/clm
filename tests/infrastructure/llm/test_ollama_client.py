"""Tests for the protocol shape of :mod:`clm.infrastructure.llm.ollama_client`.

The real :class:`OllamaTitleSuggester` is exercised by manual fixtures; in
the test suite we only verify the protocol's mockable behavior so the
assign-ids tests can rely on :class:`StaticTitleSuggester`.
"""

from __future__ import annotations

import pytest

from clm.infrastructure.llm.ollama_client import (
    COVERAGE_PROMPT_VERSION,
    TITLE_PROMPT_VERSION,
    BulletVerdict,
    CoverageVerdict,
    OllamaCoverageJudge,
    OllamaError,
    OllamaTitleSuggester,
    StaticCoverageJudge,
    StaticTitleSuggester,
    _clean_title,
    coverage_key,
    is_available,
    parse_coverage_response,
)


class TestStaticTitleSuggester:
    def test_mapping_lookup(self):
        s = StaticTitleSuggester({"content-a": "Title A"})
        assert s.suggest("content-a") == "Title A"

    def test_default_used_for_unknown(self):
        s = StaticTitleSuggester(default="Default")
        assert s.suggest("anything") == "Default"

    def test_raises_when_no_match(self):
        s = StaticTitleSuggester()
        with pytest.raises(OllamaError):
            s.suggest("missing")

    def test_records_calls(self):
        s = StaticTitleSuggester(default="x")
        s.suggest("first")
        s.suggest("second")
        assert s.calls == ["first", "second"]

    def test_prompt_version_default(self):
        s = StaticTitleSuggester()
        assert s.prompt_version == TITLE_PROMPT_VERSION

    def test_prompt_version_override(self):
        s = StaticTitleSuggester(prompt_version="v99")
        assert s.prompt_version == "v99"


class TestCleanTitle:
    def test_strips_double_quotes(self):
        assert _clean_title('"My Title"') == "My Title"

    def test_strips_single_quotes(self):
        assert _clean_title("'My Title'") == "My Title"

    def test_takes_first_line(self):
        assert _clean_title("My Title\nExplanation follows") == "My Title"

    def test_strips_trailing_period(self):
        assert _clean_title("My Title.") == "My Title"

    def test_passthrough(self):
        assert _clean_title("Clean Output") == "Clean Output"


class TestIsAvailable:
    def test_static_suggester_always_available(self):
        assert is_available(StaticTitleSuggester()) is True

    def test_static_judge_always_available(self):
        assert is_available(StaticCoverageJudge()) is True

    def test_none_unavailable(self):
        assert is_available(None) is False

    def test_real_suggester_unavailable_with_bad_url(self):
        # Point at a port nothing listens on. The check should fail soft
        # rather than raise.
        s = OllamaTitleSuggester(base_url="http://127.0.0.1:1")
        assert is_available(s) is False

    def test_real_judge_unavailable_with_bad_url(self):
        j = OllamaCoverageJudge(base_url="http://127.0.0.1:1")
        assert is_available(j) is False


class TestCoverageVerdict:
    def test_has_gaps_for_gap_verdict(self):
        v = CoverageVerdict(verdict="gaps", bullets=(BulletVerdict("a", False),))
        assert v.has_gaps is True

    def test_no_gaps_for_covered_verdict(self):
        v = CoverageVerdict(verdict="covered", bullets=(BulletVerdict("a", True),))
        assert v.has_gaps is False

    def test_uncovered_bullets_filtered(self):
        v = CoverageVerdict(
            verdict="gaps",
            bullets=(
                BulletVerdict("a", True, "ok"),
                BulletVerdict("b", False, "missing"),
                BulletVerdict("c", False, "missing"),
            ),
        )
        assert [b.text for b in v.uncovered_bullets] == ["b", "c"]

    def test_to_from_json_round_trip(self):
        original = CoverageVerdict(
            verdict="gaps",
            bullets=(
                BulletVerdict("first bullet", True, "explicitly stated"),
                BulletVerdict("second bullet", False, "not mentioned"),
            ),
        )
        restored = CoverageVerdict.from_json(original.to_json())
        assert restored.verdict == "gaps"
        assert restored.bullets == original.bullets


class TestStaticCoverageJudge:
    def test_default_verdict_used_for_unknown(self):
        default = CoverageVerdict(verdict="covered")
        j = StaticCoverageJudge(default_verdict=default)
        assert j.judge(["a"], "voiceover", lang="en") is default

    def test_mapping_lookup_by_coverage_key(self):
        verdict = CoverageVerdict(verdict="gaps", bullets=(BulletVerdict("a", False),))
        key = coverage_key(["a"], "voiceover", lang="en")
        j = StaticCoverageJudge({key: verdict})
        assert j.judge(["a"], "voiceover", lang="en") is verdict

    def test_raises_when_no_match(self):
        j = StaticCoverageJudge()
        with pytest.raises(OllamaError):
            j.judge(["a"], "voiceover", lang="en")

    def test_records_calls(self):
        j = StaticCoverageJudge(default_verdict=CoverageVerdict(verdict="covered"))
        j.judge(["a"], "vo", lang="en")
        j.judge(["b"], "vo2", lang="de")
        assert j.calls == [(("a",), "vo", "en"), (("b",), "vo2", "de")]

    def test_prompt_version_default(self):
        assert StaticCoverageJudge().prompt_version == COVERAGE_PROMPT_VERSION

    def test_prompt_version_override(self):
        assert StaticCoverageJudge(prompt_version="v99").prompt_version == "v99"


class TestParseCoverageResponse:
    def test_plain_json(self):
        text = '{"verdict":"covered","bullets":[{"text":"a","covered":true,"reason":"ok"}]}'
        v = parse_coverage_response(text, ["a"])
        assert v.verdict == "covered"
        assert v.bullets[0] == BulletVerdict("a", True, "ok")

    def test_prose_around_json(self):
        text = 'Sure! Here is the verdict:\n{"verdict":"gaps","bullets":[]}'
        v = parse_coverage_response(text, [])
        assert v.verdict == "gaps"

    def test_fenced_code_block(self):
        text = '```json\n{"verdict":"covered","bullets":[]}\n```'
        v = parse_coverage_response(text, [])
        assert v.verdict == "covered"

    def test_verdict_inferred_when_missing(self):
        text = '{"bullets":[{"text":"a","covered":false}]}'
        v = parse_coverage_response(text, ["a"])
        assert v.verdict == "gaps"

    def test_verdict_inferred_covered_when_all_covered(self):
        text = '{"bullets":[{"text":"a","covered":true}]}'
        v = parse_coverage_response(text, ["a"])
        assert v.verdict == "covered"

    def test_invalid_json_raises(self):
        with pytest.raises(OllamaError):
            parse_coverage_response("no braces here at all", ["a"])

    def test_missing_bullets_list_raises(self):
        with pytest.raises(OllamaError):
            parse_coverage_response('{"verdict":"covered"}', ["a"])
