"""CLI smoke tests for ``clm slides sync``."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides_sync import slides_sync_cmd


@pytest.fixture
def cli_runner():
    # Click 8.1 needs ``mix_stderr=False`` to separate stderr; Click 8.2+
    # removed the parameter and always separates stderr. Support both.
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


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
        # On Click 8.2+ stderr is mixed into result.output when CliRunner
        # is constructed without the (removed) ``mix_stderr=False`` flag,
        # so the "Ollama is not reachable" warning may precede the JSON
        # body. Locate the JSON object by its opening brace.
        output = result.output
        brace = output.find("{")
        assert brace >= 0, f"no JSON object in CLI output:\n{output}"
        payload = json.loads(output[brace:])
        assert payload["pairs_visited"] == 1
        assert payload["pairs_error"] == 1
        assert len(payload["outcomes"]) == 1
        assert payload["outcomes"][0]["verdict"] == "error"
        # New v2 keys ride along with zero values when no walker ran.
        assert payload["pairs_accepted"] == 0
        assert payload["pairs_skipped"] == 0
        assert payload["pairs_edited"] == 0
        assert payload["pairs_quit"] == 0


class TestInteractiveCli:
    """End-to-end smoke for ``--interactive`` driven by a stub judge.

    These tests can't reach a real Ollama daemon and instead patch the
    judge selection inside ``slides_sync_cmd`` to return a
    :class:`StaticSyncJudge`. That keeps the CLI surface (flag parsing,
    walker wiring, file writes, snapshot recording) under test without
    requiring the local LLM.
    """

    def test_interactive_apply_writes_target_and_reports_counters(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        from clm.cli.commands import slides_sync as cmd_module
        from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

        # Force the CLI to skip the Ollama liveness check and supply a
        # judge that always proposes an update.
        proposal = SyncProposal(
            verdict="update",
            proposed_text="# ## Introduction\n# - Point one\n# - Point two",
            reason="DE added a bullet",
        )
        monkeypatch.setattr(cmd_module, "is_available", lambda _judge: True)
        monkeypatch.setattr(
            cmd_module,
            "OllamaSyncJudge",
            lambda **_kw: StaticSyncJudge(default_proposal=proposal),
        )

        de = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einleitung\n# - Punkt eins\n# - Punkt zwei\n"
        )
        en = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n'
            "# ## Introduction\n# - Point one\n"
        )
        de_path = tmp_path / "slides_intro.de.py"
        en_path = tmp_path / "slides_intro.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--interactive",
                "--cache-dir",
                str(tmp_path / "cache"),
            ],
            input="a\n",
        )

        # Exit 1 = at least one proposed update (now accepted).
        assert result.exit_code == 1
        assert "Point two" in en_path.read_text(encoding="utf-8")
        assert "walker:" in result.output
        assert "1 accepted" in result.output

    def test_interactive_json_is_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de = '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## A\n'
        en = '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# ## A\n'
        de_path = tmp_path / "x.de.py"
        en_path = tmp_path / "x.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--interactive",
                "--json",
            ],
        )
        combined = (result.stderr or "") + (result.output or "")
        assert result.exit_code != 0
        assert "mutually exclusive" in combined


class TestApplyTrivial:
    """``--apply --trivial`` CLI smoke tests.

    These tests follow the same monkeypatch pattern as
    :class:`TestInteractiveCli` — the local LLM is never reached and
    the judge is replaced with a :class:`StaticSyncJudge` whose proposed
    text drives the trivial-vs-non-trivial decision.
    """

    @staticmethod
    def _stub_judge(
        monkeypatch,
        proposed_text: str,
        reason: str = "",
    ) -> None:
        from clm.cli.commands import slides_sync as cmd_module
        from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

        proposal = SyncProposal(verdict="update", proposed_text=proposed_text, reason=reason)
        monkeypatch.setattr(cmd_module, "is_available", lambda _judge: True)
        monkeypatch.setattr(
            cmd_module,
            "OllamaSyncJudge",
            lambda **_kw: StaticSyncJudge(default_proposal=proposal),
        )

    @staticmethod
    def _pair(tmp_path: Path, *, de_body: str, en_body: str) -> tuple[Path, Path]:
        de = f'# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n{de_body}\n'
        en = f'# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n{en_body}\n'
        de_path = tmp_path / "slides_intro.de.py"
        en_path = tmp_path / "slides_intro.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")
        return de_path, en_path

    def test_apply_without_trivial_is_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = self._pair(tmp_path, de_body="# ## A", en_body="# ## A")
        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--apply",
            ],
        )
        combined = (result.stderr or "") + (result.output or "")
        assert result.exit_code != 0
        assert "--trivial" in combined

    def test_trivial_without_apply_is_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = self._pair(tmp_path, de_body="# ## A", en_body="# ## A")
        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--trivial",
            ],
        )
        combined = (result.stderr or "") + (result.output or "")
        assert result.exit_code != 0
        assert "--apply" in combined

    def test_trivial_diff_is_auto_applied(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch):
        # EN has a double-space inside one bullet; proposal collapses it.
        self._stub_judge(monkeypatch, "# ## Introduction\n# - one")
        de_path, en_path = self._pair(
            tmp_path,
            de_body="# ## Einleitung\n# - eins",
            en_body="# ## Introduction\n# -  one",
        )

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--apply",
                "--trivial",
                "--cache-dir",
                str(tmp_path / "cache"),
            ],
        )

        # Exit 0 = all proposals resolved (the only one was trivial).
        assert result.exit_code == 0
        # File rewritten.
        en_text = en_path.read_text(encoding="utf-8")
        assert "# - one" in en_text
        assert "# -  one" not in en_text
        # Report mentions the auto-apply.
        assert "auto-apply:" in result.output
        assert "1 trivial update(s) applied" in result.output
        assert "applied (trivial)" in result.output

    def test_non_trivial_diff_falls_through_to_report(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # Proposal adds a bullet — not a whitespace-only-one-line change.
        self._stub_judge(monkeypatch, "# ## Introduction\n# - one\n# - two")
        de_path, en_path = self._pair(
            tmp_path,
            de_body="# ## Einleitung\n# - eins\n# - zwei",
            en_body="# ## Introduction\n# - one",
        )

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--apply",
                "--trivial",
                "--cache-dir",
                str(tmp_path / "cache"),
            ],
        )

        # Exit 1 = proposal remains for human review.
        assert result.exit_code == 1
        # File unchanged (non-trivial was NOT auto-applied).
        assert "# - two" not in en_path.read_text(encoding="utf-8")
        # Report carries both the auto-apply line (0 applied) and the
        # propose entry.
        assert "auto-apply: 0 trivial update(s) applied" in result.output
        assert "propose intro/slide" in result.output

    def test_json_shape_includes_pairs_auto_applied(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        import json as _json

        self._stub_judge(monkeypatch, "# ## Introduction\n# - one")
        de_path, en_path = self._pair(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction\n# -  one",
        )

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--source-lang",
                "de",
                "--apply",
                "--trivial",
                "--json",
                "--cache-dir",
                str(tmp_path / "cache"),
            ],
        )

        assert result.exit_code == 0
        brace = result.output.find("{")
        assert brace >= 0
        payload = _json.loads(result.output[brace:])
        assert payload["pairs_auto_applied"] == 1
        # Each outcome carries the applied_trivially flag.
        applied_flags = [o.get("applied_trivially") for o in payload["outcomes"]]
        assert any(applied_flags)
