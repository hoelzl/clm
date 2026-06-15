"""Tests for the ``clm export context`` command.

Covers scope resolution (section number/id, windows, topic anchors, error
cases), the three depth levels (titles/full deterministic, summary with a
mocked LLM), the ``agent`` audience prompt, and JSON output.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.export.context import (
    ScopeError,
    _SectionUnit,
    _TopicUnit,
    apply_scope,
)
from clm.cli.main import cli

SPEC = "tests/test-data/course-specs/test-spec-1.xml"


# ---------------------------------------------------------------------------
# Scope resolution (pure, on synthetic units)
# ---------------------------------------------------------------------------
def _units() -> list[_SectionUnit]:
    """Three sections; section 2 carries an id; topics named t<sec><n>."""
    return [
        _SectionUnit(
            number=1,
            name="One",
            disabled=False,
            section_id=None,
            topics=[_TopicUnit("t11"), _TopicUnit("t12")],
        ),
        _SectionUnit(
            number=2,
            name="Two",
            disabled=False,
            section_id="sec_two",
            topics=[_TopicUnit("t21"), _TopicUnit("t22")],
        ),
        _SectionUnit(
            number=3,
            name="Three",
            disabled=False,
            section_id=None,
            topics=[_TopicUnit("t31")],
        ),
    ]


def _numbers(units: list[_SectionUnit]) -> list[int]:
    return [u.number for u in units]


def _topic_ids(units: list[_SectionUnit]) -> list[str]:
    return [t.topic_id for u in units for t in u.topics]


class TestSectionScope:
    def test_no_selector_returns_all(self):
        assert _numbers(apply_scope(_units())) == [1, 2, 3]

    def test_through_number(self):
        assert _numbers(apply_scope(_units(), through="2")) == [1, 2]

    def test_through_section_id(self):
        assert _numbers(apply_scope(_units(), through="sec_two")) == [1, 2]

    def test_from_window(self):
        assert _numbers(apply_scope(_units(), from_section="2", through="3")) == [2, 3]

    def test_from_to_end(self):
        assert _numbers(apply_scope(_units(), from_section="2")) == [2, 3]

    def test_number_preserved_under_window(self):
        # --from keeps the original numbering (2, 3), not a re-based 1, 2.
        scoped = apply_scope(_units(), from_section="2")
        assert scoped[0].number == 2

    def test_unknown_number_raises(self):
        with pytest.raises(ScopeError, match="out of range"):
            apply_scope(_units(), through="9")

    def test_unknown_id_raises(self):
        with pytest.raises(ScopeError, match="no section with id"):
            apply_scope(_units(), through="nope")

    def test_from_after_through_raises(self):
        with pytest.raises(ScopeError, match="comes after"):
            apply_scope(_units(), from_section="3", through="1")


class TestTopicScope:
    def test_upto_inclusive(self):
        scoped = apply_scope(_units(), upto="t21")
        assert _topic_ids(scoped) == ["t11", "t12", "t21"]
        assert _numbers(scoped) == [1, 2]

    def test_before_exclusive(self):
        scoped = apply_scope(_units(), before="t21")
        # Section 2 emptied of kept topics → dropped entirely.
        assert _topic_ids(scoped) == ["t11", "t12"]
        assert _numbers(scoped) == [1]

    def test_before_mid_section_keeps_section(self):
        scoped = apply_scope(_units(), before="t22")
        assert _topic_ids(scoped) == ["t11", "t12", "t21"]
        assert _numbers(scoped) == [1, 2]

    def test_upto_first_topic(self):
        scoped = apply_scope(_units(), upto="t11")
        assert _topic_ids(scoped) == ["t11"]

    def test_unknown_topic_raises(self):
        with pytest.raises(ScopeError, match="not in this course view"):
            apply_scope(_units(), upto="ghost")

    def test_before_and_upto_raises(self):
        with pytest.raises(ScopeError, match="mutually exclusive"):
            apply_scope(_units(), before="t11", upto="t12")


# ---------------------------------------------------------------------------
# CLI — help / validation
# ---------------------------------------------------------------------------
class TestContextCLIValidation:
    def test_help(self):
        result = CliRunner().invoke(cli, ["export", "context", "--help"])
        assert result.exit_code == 0
        assert "scoped to a cut point" in result.output
        assert "--through" in result.output
        assert "--level" in result.output

    def test_appears_under_export(self):
        result = CliRunner().invoke(cli, ["export", "--help"])
        assert result.exit_code == 0
        assert "context" in result.output

    def test_section_and_topic_selectors_mutually_exclusive(self):
        result = CliRunner().invoke(
            cli, ["export", "context", SPEC, "--through", "1", "--before", "x"]
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_output_and_output_dir_mutually_exclusive(self):
        result = CliRunner().invoke(cli, ["export", "context", SPEC, "-o", "a.md", "-d", "out"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_merge_disabled_rejected(self):
        result = CliRunner().invoke(
            cli, ["export", "context", SPEC, "--level", "titles", "--include-disabled=merge"]
        )
        assert result.exit_code != 0
        assert "merge" in result.output.lower()

    def test_bad_section_number(self):
        result = CliRunner().invoke(cli, ["export", "context", SPEC, "--through", "99"])
        assert result.exit_code != 0
        assert "out of range" in result.output.lower()

    def test_bad_topic_id(self):
        result = CliRunner().invoke(cli, ["export", "context", SPEC, "--upto", "ghost"])
        assert result.exit_code != 0
        assert "not in this course view" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI — deterministic levels (titles, full)
# ---------------------------------------------------------------------------
class TestContextTitlesLevel:
    def test_titles_markdown(self):
        result = CliRunner().invoke(cli, ["export", "context", SPEC, "--level", "titles"])
        assert result.exit_code == 0
        assert "# My Course" in result.output
        assert "## 1. Week 1" in result.output
        assert "- Some Topic from Test 1" in result.output

    def test_titles_scoped_through(self):
        result = CliRunner().invoke(
            cli, ["export", "context", SPEC, "--level", "titles", "--through", "1"]
        )
        assert result.exit_code == 0
        assert "## 1. Week 1" in result.output
        assert "## 2." not in result.output

    def test_titles_json(self):
        result = CliRunner().invoke(
            cli, ["export", "context", SPEC, "--level", "titles", "-f", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["level"] == "titles"
        assert data["course_name"] == "My Course"
        assert data["sections"][0]["number"] == 1
        # titles level carries no per-slide summary/content
        slide = data["sections"][0]["topics"][0]["slides"][0]
        assert "summary" not in slide and "content" not in slide

    def test_before_excludes_anchor(self):
        result = CliRunner().invoke(
            cli,
            ["export", "context", SPEC, "--level", "titles", "--before", "punctuation_test"],
        )
        assert result.exit_code == 0
        assert "Was this really ML?" not in result.output
        assert "Some Topic from Test 1" in result.output


class TestContextFullLevel:
    def test_full_embeds_content(self):
        result = CliRunner().invoke(
            cli,
            ["export", "context", SPEC, "--level", "full", "--upto", "some_topic_from_test_1"],
        )
        assert result.exit_code == 0
        assert "### Some Topic from Test 1" in result.output
        # Raw extracted body text from the deck appears verbatim.
        assert "This is some text." in result.output

    def test_full_json_has_content(self):
        result = CliRunner().invoke(
            cli, ["export", "context", SPEC, "--level", "full", "--through", "1", "-f", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["level"] == "full"
        slide = data["sections"][0]["topics"][0]["slides"][0]
        assert "content" in slide and slide["content"]


# ---------------------------------------------------------------------------
# CLI — summary level (mocked LLM)
# ---------------------------------------------------------------------------
class TestContextSummaryLevel:
    def test_summary_uses_agent_audience(self, tmp_path):
        mock = AsyncMock(return_value="AGENT SUMMARY TEXT")
        with patch("clm.infrastructure.llm.client.summarize_notebook", mock):
            result = CliRunner().invoke(
                cli,
                [
                    "export",
                    "context",
                    SPEC,
                    "--level",
                    "summary",
                    "--through",
                    "1",
                    "--no-progress",
                    "--no-cache",
                ],
            )
        assert result.exit_code == 0, result.output
        assert "AGENT SUMMARY TEXT" in result.output
        assert mock.await_count >= 1
        # Every call runs under the agent audience.
        assert all(c.kwargs["audience"] == "agent" for c in mock.await_args_list)

    def test_summary_json_carries_summary(self, tmp_path):
        mock = AsyncMock(return_value="AGENT SUMMARY TEXT")
        with patch("clm.infrastructure.llm.client.summarize_notebook", mock):
            result = CliRunner().invoke(
                cli,
                [
                    "export",
                    "context",
                    SPEC,
                    "--level",
                    "summary",
                    "--through",
                    "1",
                    "-f",
                    "json",
                    "--no-progress",
                    "--no-cache",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        slide = data["sections"][0]["topics"][0]["slides"][0]
        assert slide["summary"] == "AGENT SUMMARY TEXT"


# ---------------------------------------------------------------------------
# Agent audience: prompt + content extraction
# ---------------------------------------------------------------------------
class TestAgentAudience:
    def test_agent_prompt_selected(self):
        from clm.infrastructure.llm.prompts import get_prompts

        system, user = get_prompts(
            audience="agent",
            course_name="C",
            section_name="S",
            notebook_title="T",
            content="BODY",
            has_workshop=True,
            language="en",
            style="bullets",
        )
        assert "another AI assistant" in system
        assert "BODY" in user
        # workshop note flows through the agent (non-client) template
        assert "workshop" in user.lower()

    def test_agent_prompt_german(self):
        from clm.infrastructure.llm.prompts import get_prompts

        system, _ = get_prompts(
            audience="agent",
            course_name="C",
            section_name="S",
            notebook_title="T",
            content="BODY",
            language="de",
            style="prose",
        )
        assert "KI-Assistenten" in system

    def test_agent_extraction_includes_code(self, tmp_path):
        from clm.cli.commands.export.summary import extract_notebook_content

        nb = {
            "cells": [
                {"cell_type": "markdown", "metadata": {}, "source": ["# Intro"]},
                {"cell_type": "code", "metadata": {}, "source": ["secret_api_call()"]},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        path = tmp_path / "n.ipynb"
        path.write_text(json.dumps(nb), encoding="utf-8")

        agent = extract_notebook_content(path, "agent", "en")
        client = extract_notebook_content(path, "client", "en")
        assert "secret_api_call()" in agent
        assert "secret_api_call()" not in client
