"""CLI smoke tests for ``clm slides sync``."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides_sync import slides_sync_cmd


@pytest.fixture
def cli_runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a minimal split DE/EN pair to disk and return both paths."""
    de = '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Einleitung\n'
    en = '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# ## Introduction\n'
    de_path = tmp_path / "slides_intro.de.py"
    en_path = tmp_path / "slides_intro.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


class TestArgumentParsing:
    def test_missing_source_lang_errors(self, cli_runner: CliRunner, pair):
        de_path, en_path = pair
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path)],
        )
        assert result.exit_code != 0
        assert "source-lang" in (result.stderr or result.output).lower()

    def test_invalid_source_lang_errors(self, cli_runner: CliRunner, pair):
        de_path, en_path = pair
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--source-lang", "fr"],
        )
        assert result.exit_code != 0
        combined = (result.stderr or "") + (result.output or "")
        assert "fr" in combined.lower() or "invalid" in combined.lower()

    def test_missing_paths_errors(self, cli_runner: CliRunner):
        result = cli_runner.invoke(slides_sync_cmd, ["--source-lang", "de"])
        assert result.exit_code != 0

    def test_nonexistent_path_errors(self, cli_runner: CliRunner, tmp_path: Path):
        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(tmp_path / "missing.de.py"),
                str(tmp_path / "missing.en.py"),
                "--source-lang",
                "de",
            ],
        )
        assert result.exit_code != 0


class TestOllamaUnavailable:
    """When Ollama is not reachable, every pair becomes an error
    outcome. Exit code is 2 (structural error)."""

    def test_unreachable_ollama_records_errors(self, cli_runner: CliRunner, pair, tmp_path: Path):
        de_path, en_path = pair
        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--ollama-url",
                "http://127.0.0.1:1",  # nothing listens here
                "--llm-timeout",
                "1.0",
                "--no-cache",
            ],
        )
        # Exit 2 = at least one error.
        assert result.exit_code == 2
        # Warning was emitted about Ollama being unreachable.
        assert "Ollama is not reachable" in (result.stderr or "")
        # The lone pair was counted as an error.
        assert "1 pair(s) visited" in result.output
        assert "1 error(s)" in result.output

    def test_json_output_shape(self, cli_runner: CliRunner, pair, tmp_path: Path):
        import json

        de_path, en_path = pair
        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--ollama-url",
                "http://127.0.0.1:1",
                "--llm-timeout",
                "1.0",
                "--no-cache",
                "--json",
            ],
        )
        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["pairs_visited"] == 1
        assert payload["pairs_error"] == 1
        assert len(payload["outcomes"]) == 1
        assert payload["outcomes"][0]["verdict"] == "error"
