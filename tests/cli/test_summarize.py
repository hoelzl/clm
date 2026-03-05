"""
Unit tests for the summarize command.

Tests cover:
- CLI help and argument validation
- Content extraction from notebooks
- Workshop detection
- Cache hit/miss behavior
- Prompt construction
- Output formatting (client vs trainer)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.summarize import (
    SKIP_TAGS,
    content_hash,
    detect_workshop,
    extract_notebook_content,
)
from clm.cli.main import cli
from clm.infrastructure.llm.cache import SummaryCache
from clm.infrastructure.llm.prompts import get_prompts

# --- Fixtures ---


@pytest.fixture
def sample_ipynb(tmp_path):
    """Create a minimal .ipynb file."""
    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Introduction\n", "\n", "This notebook covers Python basics."],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "source": ["x = 42\n", "print(x)"],
            },
            {
                "cell_type": "markdown",
                "metadata": {"tags": ["del"]},
                "source": ["# This should be skipped"],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / "test.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


@pytest.fixture
def workshop_ipynb(tmp_path):
    """Create a notebook with workshop indicators."""
    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Workshop: Build a Calculator\n"],
            },
            {
                "cell_type": "code",
                "metadata": {"tags": ["workshop"]},
                "source": ["# Exercise code here"],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / "workshop.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


@pytest.fixture
def summary_cache(tmp_path):
    """Create a temporary SummaryCache."""
    cache = SummaryCache(tmp_path / "test_summaries.db")
    yield cache
    cache.close()


# --- CLI Tests ---


class TestSummarizeCommandHelp:
    def test_summarize_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["summarize", "--help"])
        assert result.exit_code == 0
        assert "Generate LLM-powered summaries" in result.output
        assert "--audience" in result.output
        assert "--dry-run" in result.output

    def test_summarize_appears_in_main_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "summarize" in result.output


class TestSummarizeCommandValidation:
    def test_requires_spec_file(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["summarize", "--audience", "client"])
        assert result.exit_code != 0

    def test_requires_audience(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["summarize", "some-file.xml"])
        assert result.exit_code != 0

    def test_rejects_output_and_output_dir_together(self):
        runner = CliRunner()
        spec_file = "tests/test-data/course-specs/test-spec-1.xml"
        result = runner.invoke(
            cli,
            ["summarize", spec_file, "--audience", "client", "-o", "out.md", "-d", "dir"],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()


class TestSummarizeCommandDryRun:
    """Test dry-run mode which doesn't require litellm."""

    @pytest.fixture
    def test_spec_path(self):
        return Path("tests/test-data/course-specs/test-spec-1.xml")

    def test_dry_run_stdout(self, test_spec_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["summarize", str(test_spec_path), "--audience", "client", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "# My Course" in result.output

    def test_dry_run_trainer(self, test_spec_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["summarize", str(test_spec_path), "--audience", "trainer", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output

    def test_dry_run_german(self, test_spec_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["summarize", str(test_spec_path), "--audience", "client", "--dry-run", "-L", "de"],
        )
        assert result.exit_code == 0
        assert "# Mein Kurs" in result.output


# --- Content Extraction Tests ---


class TestContentExtraction:
    def test_extract_ipynb_client(self, sample_ipynb):
        content = extract_notebook_content(sample_ipynb, "client")
        assert content is not None
        assert "Introduction" in content
        assert "Python basics" in content
        # Client should not see code
        assert "x = 42" not in content
        # Deleted cell should be skipped
        assert "should be skipped" not in content

    def test_extract_ipynb_trainer(self, sample_ipynb):
        content = extract_notebook_content(sample_ipynb, "trainer")
        assert content is not None
        assert "Introduction" in content
        # Trainer should see code
        assert "x = 42" in content
        # Deleted cell should still be skipped
        assert "should be skipped" not in content

    def test_extract_nonexistent_file(self, tmp_path):
        result = extract_notebook_content(tmp_path / "missing.ipynb", "client")
        assert result is None

    def test_extract_invalid_json(self, tmp_path):
        path = tmp_path / "bad.ipynb"
        path.write_text("not json", encoding="utf-8")
        result = extract_notebook_content(path, "client")
        assert result == ""

    def test_extract_py_file(self, tmp_path):
        path = tmp_path / "test.py"
        path.write_text(
            "# %% [markdown]\n# # Hello\n# This is markdown\n# %%\nx = 1\n",
            encoding="utf-8",
        )
        content = extract_notebook_content(path, "client")
        assert content is not None
        assert "Hello" in content
        # Client shouldn't see code from py files
        assert "x = 1" not in content

    def test_extract_py_file_trainer(self, tmp_path):
        path = tmp_path / "test.py"
        path.write_text(
            "# %% [markdown]\n# # Hello\n# %%\nx = 1\n",
            encoding="utf-8",
        )
        content = extract_notebook_content(path, "trainer")
        assert content is not None
        assert "x = 1" in content

    def test_extract_ipynb_filters_by_language(self, tmp_path):
        """Cells tagged with a different language are excluded."""
        nb = {
            "cells": [
                {
                    "cell_type": "markdown",
                    "metadata": {"lang": "en"},
                    "source": ["# English only"],
                },
                {
                    "cell_type": "markdown",
                    "metadata": {"lang": "de"},
                    "source": ["# Nur Deutsch"],
                },
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": ["# Both languages"],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        path = tmp_path / "lang.ipynb"
        path.write_text(json.dumps(nb), encoding="utf-8")

        en_content = extract_notebook_content(path, "client", "en")
        assert "English only" in en_content
        assert "Nur Deutsch" not in en_content
        assert "Both languages" in en_content

        de_content = extract_notebook_content(path, "client", "de")
        assert "English only" not in de_content
        assert "Nur Deutsch" in de_content
        assert "Both languages" in de_content


# --- Workshop Detection Tests ---


class TestWorkshopDetection:
    def test_detect_workshop_heading(self):
        cells = [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Workshop: Build X"]},
        ]
        assert detect_workshop(cells) is True

    def test_detect_exercise_heading(self):
        cells = [
            {"cell_type": "markdown", "metadata": {}, "source": ["## Exercise 1"]},
        ]
        assert detect_workshop(cells) is True

    def test_detect_hands_on_heading(self):
        cells = [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Hands-on: Lists"]},
        ]
        assert detect_workshop(cells) is True

    def test_detect_german_uebung(self):
        cells = [
            {"cell_type": "markdown", "metadata": {}, "source": ["\u00dc# \u00dcbung: Listen"]},
        ]
        # The heading check requires line starts with #
        cells2 = [
            {"cell_type": "markdown", "metadata": {}, "source": ["# \u00dcbung: Listen"]},
        ]
        assert detect_workshop(cells2) is True

    def test_detect_workshop_tag(self):
        cells = [
            {"cell_type": "code", "metadata": {"tags": ["workshop"]}, "source": ["pass"]},
        ]
        assert detect_workshop(cells) is True

    def test_no_workshop(self):
        cells = [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Introduction"]},
        ]
        assert detect_workshop(cells) is False


# --- Cache Tests ---


class TestSummaryCache:
    def test_cache_miss(self, summary_cache):
        result = summary_cache.get("hash1", "client", "model1", "en")
        assert result is None

    def test_cache_put_and_get(self, summary_cache):
        summary_cache.put("hash1", "client", "model1", "A summary", "en")
        result = summary_cache.get("hash1", "client", "model1", "en")
        assert result == "A summary"

    def test_cache_different_audience(self, summary_cache):
        summary_cache.put("hash1", "client", "model1", "Client summary", "en")
        summary_cache.put("hash1", "trainer", "model1", "Trainer summary", "en")
        assert summary_cache.get("hash1", "client", "model1", "en") == "Client summary"
        assert summary_cache.get("hash1", "trainer", "model1", "en") == "Trainer summary"

    def test_cache_different_model(self, summary_cache):
        summary_cache.put("hash1", "client", "model1", "Summary A", "en")
        summary_cache.put("hash1", "client", "model2", "Summary B", "en")
        assert summary_cache.get("hash1", "client", "model1", "en") == "Summary A"
        assert summary_cache.get("hash1", "client", "model2", "en") == "Summary B"

    def test_cache_overwrite(self, summary_cache):
        summary_cache.put("hash1", "client", "model1", "Old", "en")
        summary_cache.put("hash1", "client", "model1", "New", "en")
        assert summary_cache.get("hash1", "client", "model1", "en") == "New"

    def test_cache_different_language(self, summary_cache):
        summary_cache.put("hash1", "client", "model1", "English summary", "en")
        summary_cache.put("hash1", "client", "model1", "German summary", "de")
        assert summary_cache.get("hash1", "client", "model1", "en") == "English summary"
        assert summary_cache.get("hash1", "client", "model1", "de") == "German summary"

    def test_cache_language_miss(self, summary_cache):
        summary_cache.put("hash1", "client", "model1", "English", "en")
        assert summary_cache.get("hash1", "client", "model1", "de") is None

    def test_cache_migration_from_old_schema(self, tmp_path):
        """Test that an old database without language column gets migrated."""
        import sqlite3

        db_path = tmp_path / "old_cache.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE summaries (
                content_hash TEXT NOT NULL,
                audience TEXT NOT NULL,
                model TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (content_hash, audience, model)
            )"""
        )
        conn.execute(
            "INSERT INTO summaries (content_hash, audience, model, summary) VALUES (?, ?, ?, ?)",
            ("h1", "client", "m1", "Old summary"),
        )
        conn.commit()
        conn.close()

        # Open with new cache — should migrate
        cache = SummaryCache(db_path)
        # Old data should be accessible with default language 'en'
        assert cache.get("h1", "client", "m1", "en") == "Old summary"
        # Should be able to store new data with language
        cache.put("h2", "client", "m1", "New German", "de")
        assert cache.get("h2", "client", "m1", "de") == "New German"
        cache.close()


# --- Prompt Tests ---


class TestPrompts:
    def test_client_prompt_english(self):
        system, user = get_prompts(
            audience="client",
            course_name="Python Course",
            section_name="Basics",
            notebook_title="Variables",
            content="# Variables\nLearn about variables.",
            language="en",
        )
        assert "prospective clients" in system
        assert "Do NOT" in system
        assert "Python Course" in user
        assert "Basics" in user
        assert "Variables" in user
        assert "Learn about variables" in user

    def test_trainer_prompt_english(self):
        system, user = get_prompts(
            audience="trainer",
            course_name="Python Course",
            section_name="Basics",
            notebook_title="Variables",
            content="# Variables\nTeach about variables.",
            has_workshop=True,
            language="en",
        )
        assert "internal trainers" in system
        assert "workshop" in system.lower()
        assert "workshop" in user.lower()

    def test_trainer_prompt_no_workshop(self):
        system, user = get_prompts(
            audience="trainer",
            course_name="Python Course",
            section_name="Basics",
            notebook_title="Variables",
            content="content here",
            has_workshop=False,
            language="en",
        )
        assert "workshop" not in user.lower() or "hands-on" not in user.lower()

    def test_client_prompt_german(self):
        system, user = get_prompts(
            audience="client",
            course_name="Python Kurs",
            section_name="Grundlagen",
            notebook_title="Variablen",
            content="# Variablen\nLerne Variablen.",
            language="de",
        )
        assert "auf Deutsch" in system
        assert "Kunden" in system
        assert "Python Kurs" in user
        assert "Grundlagen" in user

    def test_trainer_prompt_german(self):
        system, user = get_prompts(
            audience="trainer",
            course_name="Python Kurs",
            section_name="Grundlagen",
            notebook_title="Variablen",
            content="content",
            has_workshop=True,
            language="de",
        )
        assert "auf Deutsch" in system
        assert "Trainer" in system
        assert "Workshop" in user or "Übung" in user

    def test_trainer_prompt_german_no_workshop(self):
        system, user = get_prompts(
            audience="trainer",
            course_name="Python Kurs",
            section_name="Grundlagen",
            notebook_title="Variablen",
            content="content",
            has_workshop=False,
            language="de",
        )
        assert "Workshop" not in user and "Übung" not in user

    def test_defaults_to_english_for_unknown_language(self):
        system, user = get_prompts(
            audience="client",
            course_name="Course",
            section_name="Sec",
            notebook_title="NB",
            content="content",
            language="fr",
        )
        assert "prospective clients" in system


# --- Content Hash Tests ---


class TestContentHash:
    def test_hash_deterministic(self):
        h1 = content_hash("hello")
        h2 = content_hash("hello")
        assert h1 == h2

    def test_hash_different_content(self):
        h1 = content_hash("hello")
        h2 = content_hash("world")
        assert h1 != h2

    def test_hash_is_hex_string(self):
        h = content_hash("test")
        assert len(h) == 64  # SHA-256 produces 64 hex chars
        assert all(c in "0123456789abcdef" for c in h)


# --- Error Formatting Tests ---


class TestCellLanguageFiltering:
    def test_no_lang_metadata_included(self):
        from clm.cli.commands.summarize import _is_cell_included_for_language

        cell = {"metadata": {}}
        assert _is_cell_included_for_language(cell, "en") is True
        assert _is_cell_included_for_language(cell, "de") is True

    def test_matching_lang_included(self):
        from clm.cli.commands.summarize import _is_cell_included_for_language

        cell = {"metadata": {"lang": "de"}}
        assert _is_cell_included_for_language(cell, "de") is True

    def test_non_matching_lang_excluded(self):
        from clm.cli.commands.summarize import _is_cell_included_for_language

        cell = {"metadata": {"lang": "de"}}
        assert _is_cell_included_for_language(cell, "en") is False

    def test_empty_lang_included(self):
        from clm.cli.commands.summarize import _is_cell_included_for_language

        cell = {"metadata": {"lang": ""}}
        assert _is_cell_included_for_language(cell, "en") is True


# --- Error Formatting Tests ---


class TestLLMErrorFormatting:
    def test_format_rate_limit_error(self):
        import litellm

        from clm.infrastructure.llm.client import _format_llm_error

        exc = litellm.RateLimitError(
            message="Rate limit exceeded",
            model="test",
            llm_provider="anthropic",
        )
        msg = _format_llm_error(exc, "My Notebook")
        assert "Rate limited" in msg
        assert "My Notebook" in msg
        assert "reduce" in msg.lower()

    def test_format_auth_error(self):
        import litellm

        from clm.infrastructure.llm.client import _format_llm_error

        exc = litellm.AuthenticationError(
            message="Invalid API key",
            model="test",
            llm_provider="anthropic",
        )
        msg = _format_llm_error(exc, "My Notebook")
        assert "Authentication failed" in msg
        assert "API key" in msg

    def test_format_not_found_error(self):
        import litellm

        from clm.infrastructure.llm.client import _format_llm_error

        exc = litellm.NotFoundError(
            message="Model not found",
            model="test",
            llm_provider="openrouter",
        )
        msg = _format_llm_error(exc, "My Notebook")
        assert "Model not found" in msg
        assert "My Notebook" in msg

    def test_format_context_window_error(self):
        import litellm

        from clm.infrastructure.llm.client import _format_llm_error

        exc = litellm.ContextWindowExceededError(
            message="Too many tokens",
            model="test",
            llm_provider="anthropic",
        )
        msg = _format_llm_error(exc, "My Notebook")
        assert "too long" in msg.lower() or "context window" in msg.lower()

    def test_format_connection_error(self):
        import litellm

        from clm.infrastructure.llm.client import _format_llm_error

        exc = litellm.APIConnectionError(
            message="Connection refused",
            model="test",
            llm_provider="openai",
        )
        msg = _format_llm_error(exc, "My Notebook")
        assert "connect" in msg.lower()

    def test_format_generic_error(self):
        from clm.infrastructure.llm.client import _format_llm_error

        exc = RuntimeError("something went wrong")
        msg = _format_llm_error(exc, "My Notebook")
        assert "My Notebook" in msg
        assert "something went wrong" in msg


class TestLLMErrorClass:
    def test_llm_error_is_exception(self):
        from clm.infrastructure.llm.client import LLMError

        exc = LLMError("test message")
        assert isinstance(exc, Exception)
        assert str(exc) == "test message"


class TestLitellmConfiguration:
    def test_configure_suppresses_debug(self):
        import litellm

        from clm.infrastructure.llm.client import _configure_litellm

        _configure_litellm()
        assert litellm.suppress_debug_info is True


# --- Progress Reporter Tests ---


class TestSummarizeProgress:
    def test_progress_tracks_cached(self):
        from io import StringIO

        from rich.console import Console

        from clm.cli.commands.summarize import SummarizeProgress

        console = Console(file=StringIO(), force_terminal=False)
        prog = SummarizeProgress(console, show_progress=False)
        prog.start("Test Course", 5, "test-model", "client")
        prog.on_cached("nb1")
        prog.on_cached("nb2")
        assert prog._cached == 2
        assert prog._generated == 0
        assert prog._errors == 0

    def test_progress_tracks_generated(self):
        from io import StringIO

        from rich.console import Console

        from clm.cli.commands.summarize import SummarizeProgress

        console = Console(file=StringIO(), force_terminal=False)
        prog = SummarizeProgress(console, show_progress=False)
        prog.start("Test Course", 3, "test-model", "trainer")
        prog.on_generated("nb1")
        prog.on_generated("nb2")
        assert prog._generated == 2

    def test_progress_tracks_errors(self):
        from io import StringIO

        from rich.console import Console

        from clm.cli.commands.summarize import SummarizeProgress

        console = Console(file=StringIO(), force_terminal=False)
        prog = SummarizeProgress(console, show_progress=False)
        prog.start("Test Course", 3, "test-model", "client")
        prog.on_error("nb1", "rate limited")
        assert prog._errors == 1

    def test_progress_finish_summary(self):
        from io import StringIO

        from rich.console import Console

        from clm.cli.commands.summarize import SummarizeProgress

        buf = StringIO()
        console = Console(file=buf, force_terminal=False)
        prog = SummarizeProgress(console, show_progress=False)
        prog.start("Test Course", 5, "test-model", "client")
        prog.on_cached("nb1")
        prog.on_cached("nb2")
        prog.on_generated("nb3")
        prog.on_generated("nb4")
        prog.on_error("nb5", "oops")
        prog.finish()
        output = buf.getvalue()
        assert "5 notebooks processed" in output
        assert "2 generated via LLM" in output
        assert "2 from cache" in output
        assert "1 errors" in output

    def test_progress_finish_success(self):
        from io import StringIO

        from rich.console import Console

        from clm.cli.commands.summarize import SummarizeProgress

        buf = StringIO()
        console = Console(file=buf, force_terminal=False)
        prog = SummarizeProgress(console, show_progress=False)
        prog.start("Test Course", 2, "test-model", "client")
        prog.on_generated("nb1")
        prog.on_generated("nb2")
        prog.finish()
        output = buf.getvalue()
        assert "successfully" in output
        assert "2 notebooks processed" in output

    def test_progress_finish_with_errors(self):
        from io import StringIO

        from rich.console import Console

        from clm.cli.commands.summarize import SummarizeProgress

        buf = StringIO()
        console = Console(file=buf, force_terminal=False)
        prog = SummarizeProgress(console, show_progress=False)
        prog.start("Test Course", 2, "test-model", "client")
        prog.on_error("nb1", "fail")
        prog.on_generated("nb2")
        prog.finish()
        output = buf.getvalue()
        assert "with errors" in output


class TestSummarizeNoProgressFlag:
    @pytest.fixture
    def test_spec_path(self):
        return Path("tests/test-data/course-specs/test-spec-1.xml")

    def test_no_progress_flag_accepted(self, test_spec_path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "summarize",
                str(test_spec_path),
                "--audience",
                "client",
                "--dry-run",
                "--no-progress",
            ],
        )
        assert result.exit_code == 0
        assert "# My Course" in result.output
