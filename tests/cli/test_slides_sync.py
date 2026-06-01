"""CLI tests for ``clm slides sync`` (Issue #166 engine, Phase 5).

The live command now runs the single-language authoring engine: it diffs both
decks against the structural watermark, decides direction per cell, and — by
default — writes the agreed changes to the working tree. ``--dry-run`` previews,
``--interactive`` prompts. There is no global ``--source-lang`` and no
``--apply`` / ``--trivial`` (those were removed in Phase 5).

These tests drive the CLI surface with a watermark seeded directly into the
cache and a :class:`StaticSyncJudge` patched in, so they exercise flag parsing,
engine wiring, file writes, and watermark advance without a live LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides_sync import CACHE_DB_NAME, slides_sync_cmd
from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import ordered_sync_cells


@pytest.fixture
def cli_runner():
    # Click 8.1 needs ``mix_stderr=False`` to separate stderr; Click 8.2+
    # removed the parameter and always separates stderr. Support both.
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cell(lang: str, sid: str, body: str, *, role: str = "slide") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["{role}"] slide_id="{sid}"\n{body}\n'


def _write_pair(
    tmp_path: Path, de_text: str, en_text: str, *, stem: str = "slides_intro"
) -> tuple[Path, Path]:
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de_text, encoding="utf-8")
    en_path.write_text(en_text, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(
    cache_dir: Path,
    de_path: Path,
    en_path: Path,
    *,
    de_text: str,
    en_text: str,
) -> None:
    """Record ``(de_text, en_text)`` as the last-synced baseline for the pair.

    Mirrors ``sync_apply._record_watermark`` exactly (same ``ordered_sync_cells``
    + ``content_hash``), so the classifier sees a deck whose current on-disk
    content differs from this baseline as *edited*.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    wm = SyncWatermarkCache(cache_dir / CACHE_DB_NAME)
    try:
        for lang, text in (("de", de_text), ("en", en_text)):
            cells = ordered_sync_cells(parse_cells(text), lang)
            wm.put_deck(
                de_path=str(de_path),
                en_path=str(en_path),
                lang=lang,
                cells=[(c.position, c.slide_id, c.role, c.content_hash) for c in cells],
            )
    finally:
        wm.close()


def _stub_judge(
    monkeypatch, proposed_text: str, *, verdict: str = "update", reason: str = ""
) -> None:
    """Patch the CLI's judge factory to return a static judge.

    Provider-agnostic: it replaces ``_resolve_judge`` itself, so these tests
    exercise the engine wiring identically whether the (now default) OpenRouter
    backend or ``--provider local`` is selected, with no live LLM.
    """
    from clm.cli.commands import slides_sync as cmd
    from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

    proposal = SyncProposal(verdict=verdict, proposed_text=proposed_text, reason=reason)
    monkeypatch.setattr(
        cmd,
        "_resolve_judge",
        lambda *_args, **_kwargs: StaticSyncJudge(default_proposal=proposal),
    )


def _combined(result) -> str:
    return (result.stderr or "") + (result.output or "")


def _json_payload(result) -> dict:
    # On Click 8.2+ a stderr warning can precede the JSON body when the runner
    # falls back to a merged stream; locate the object by its opening brace.
    brace = result.output.find("{")
    assert brace >= 0, f"no JSON object in CLI output:\n{result.output}"
    return json.loads(result.output[brace:])


# A reusable edit scenario: DE drifted from the watermark, EN unchanged.
_DE_BASE = _cell("de", "intro", "# ## Einleitung")
_EN_BASE = _cell("en", "intro", "# ## Introduction")
_DE_EDITED = _cell("de", "intro", "# ## Einleitung\n# - Punkt eins")
_EN_PROPOSAL = "# ## Introduction\n# - Point one"


def _edit_scenario(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Seed a watermark, then leave DE edited and EN at baseline on disk."""
    cache_dir = tmp_path / "cache"
    de_path, en_path = _write_pair(tmp_path, _DE_EDITED, _EN_BASE)
    _seed_watermark(cache_dir, de_path, en_path, de_text=_DE_BASE, en_text=_EN_BASE)
    return de_path, en_path, cache_dir


# ---------------------------------------------------------------------------
# Removed flags (Phase 5 hard break)
# ---------------------------------------------------------------------------


class TestRemovedFlags:
    @pytest.mark.parametrize("flag", ["--source-lang", "--apply", "--trivial"])
    def test_legacy_flag_is_rejected(self, cli_runner: CliRunner, tmp_path: Path, flag: str):
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        extra = ["de"] if flag == "--source-lang" else []
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), flag, *extra, "--no-cache"],
        )
        assert result.exit_code == 2
        combined = _combined(result).lower()
        assert "no such option" in combined or flag in combined


# ---------------------------------------------------------------------------
# Dry-run preview
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_previews_edit_without_writing(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        before = en_path.read_text(encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--dry-run", "--cache-dir", str(cache_dir)],
        )

        # An edit is proposed → exit 1; nothing on disk changed.
        assert result.exit_code == 1, result.output
        assert "edit" in result.output
        assert "de->en" in result.output
        assert en_path.read_text(encoding="utf-8") == before

    def test_dry_run_json_shape(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--dry-run", "--json", "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 1
        payload = _json_payload(result)
        assert payload["mode"] == "dry-run"
        assert payload["exit_code"] == 1
        assert payload["apply"] is None
        assert payload["walker"] is None
        assert payload["plan"]["baseline_source"] == "watermark"
        assert payload["plan"]["counts"]["edit"] == 1
        assert payload["plan"]["proposals"][0]["direction"] == "de->en"


# ---------------------------------------------------------------------------
# Default write-to-tree apply
# ---------------------------------------------------------------------------


class TestApply:
    def test_default_apply_writes_target(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0, result.output
        assert "Point one" in en_path.read_text(encoding="utf-8")
        assert "applied: 1 edit" in result.output
        assert "watermark advanced" in result.output

    def test_apply_json_shape(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--json", "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0
        payload = _json_payload(result)
        assert payload["mode"] == "apply"
        assert payload["apply"]["applied"]["edit"] == 1
        assert payload["apply"]["applied"]["total"] == 1
        assert payload["apply"]["watermark_recorded"] is True
        assert payload["apply"]["errors"] == []

    def test_apply_then_rerun_is_idempotent_noop(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        first = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )
        assert first.exit_code == 0, first.output

        # The watermark advanced to the now-synced state, so a second run sees
        # zero changes.
        second = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--json", "--cache-dir", str(cache_dir)],
        )
        assert second.exit_code == 0
        payload = _json_payload(second)
        assert payload["plan"]["counts"]["edit"] == 0
        assert payload["apply"]["applied"]["total"] == 0

    def test_both_sides_edited_is_deferred_conflict(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        cache_dir = tmp_path / "cache"
        de_edited = _cell("de", "intro", "# ## Einleitung\n# - DE neu")
        en_edited = _cell("en", "intro", "# ## Introduction\n# - EN new")
        de_path, en_path = _write_pair(tmp_path, de_edited, en_edited)
        _seed_watermark(cache_dir, de_path, en_path, de_text=_DE_BASE, en_text=_EN_BASE)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )

        # A both-sides edit is isolated as a conflict: deferred, both decks
        # untouched, watermark held.
        assert result.exit_code == 1, result.output
        assert "conflict" in result.output
        assert "watermark held" in result.output
        assert de_path.read_text(encoding="utf-8") == de_edited
        assert en_path.read_text(encoding="utf-8") == en_edited


# ---------------------------------------------------------------------------
# Interactive
# ---------------------------------------------------------------------------


class TestInteractive:
    def test_interactive_apply_writes_and_reports(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--interactive", "--cache-dir", str(cache_dir)],
            input="a\n",
        )

        assert result.exit_code == 0, result.output
        assert "Point one" in en_path.read_text(encoding="utf-8")
        assert "1 accepted" in result.output
        assert "applied: 1 edit" in result.output

    def test_interactive_skip_defers(self, cli_runner: CliRunner, tmp_path: Path, monkeypatch):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        before = en_path.read_text(encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--interactive", "--cache-dir", str(cache_dir)],
            input="s\n",
        )

        # Skipped → deferred → exit 1; EN untouched; watermark held.
        assert result.exit_code == 1, result.output
        assert en_path.read_text(encoding="utf-8") == before
        assert "1 skipped" in result.output
        assert "watermark held" in result.output

    def test_interactive_and_json_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--interactive", "--json", "--no-cache"],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in _combined(result)

    def test_interactive_and_dry_run_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--interactive", "--dry-run", "--no-cache"],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in _combined(result)


# ---------------------------------------------------------------------------
# LLM unavailable
# ---------------------------------------------------------------------------


class TestOllamaUnavailable:
    def test_unreachable_ollama_records_edit_errors(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--provider",
                "local",
                "--ollama-url",
                "http://127.0.0.1:1",  # nothing listens here
                "--llm-timeout",
                "1.0",
                "--cache-dir",
                str(cache_dir),
            ],
        )

        # The lone edit can't be reconciled → error → exit 2; watermark held.
        assert result.exit_code == 2, result.output
        assert "Ollama is not reachable" in (result.stderr or "")
        assert "1 error(s)" in result.output
        assert "watermark held" in result.output


# ---------------------------------------------------------------------------
# Provider switch (openrouter default, local opt-in)
# ---------------------------------------------------------------------------


class TestProvider:
    def test_default_provider_is_openrouter_and_needs_key(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # No --provider → openrouter; with no key the judge is unavailable, so
        # the lone edit is recorded as an error (exit 2) and the watermark holds.
        monkeypatch.delenv("CLM_SYNC_PROVIDER", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 2, result.output
        assert "OPENROUTER_API_KEY" in (result.stderr or "")
        assert "1 error(s)" in result.output
        assert "watermark held" in result.output

    def test_default_provider_openrouter_with_key_applies(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # With a key present the default openrouter branch builds an
        # OpenRouterSyncJudge (here swapped for a static one) and applies.
        from clm.cli.commands import slides_sync as cmd
        from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

        monkeypatch.delenv("CLM_SYNC_PROVIDER", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        proposal = SyncProposal(verdict="update", proposed_text=_EN_PROPOSAL, reason="")
        monkeypatch.setattr(
            cmd, "OpenRouterSyncJudge", lambda **_kw: StaticSyncJudge(default_proposal=proposal)
        )
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0, result.output
        assert "Point one" in en_path.read_text(encoding="utf-8")
        assert "applied: 1 edit" in result.output

    def test_env_var_selects_local_backend(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # $CLM_SYNC_PROVIDER=local routes to Ollama even without --provider; an
        # unreachable daemon proves the local branch (not openrouter) was taken.
        monkeypatch.setenv("CLM_SYNC_PROVIDER", "local")
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--ollama-url",
                "http://127.0.0.1:1",
                "--llm-timeout",
                "1.0",
                "--cache-dir",
                str(cache_dir),
            ],
        )

        assert result.exit_code == 2, result.output
        assert "Ollama is not reachable" in (result.stderr or "")

    def test_unknown_provider_is_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--provider", "bogus", "--no-cache"],
        )
        assert result.exit_code == 2
        combined = _combined(result).lower()
        assert "invalid" in combined or "bogus" in combined


# ---------------------------------------------------------------------------
# No baseline (cold, no watermark, no git)
# ---------------------------------------------------------------------------


class TestNoBaseline:
    def test_no_watermark_no_git_reports_baseline_none(self, cli_runner: CliRunner, tmp_path: Path):
        # Identical ids on both sides, no watermark, tmp dir is not a git repo:
        # the classifier can pair by id but cannot detect edits → no proposals,
        # and the no-silent-no-op summary explains why.
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--dry-run", "--no-cache"],
        )
        assert result.exit_code == 0, result.output
        assert "baseline=none" in result.output
