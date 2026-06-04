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
                cells=[
                    (c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells
                ],
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


def _stub_translator(monkeypatch, *, default: str = "# ## Translated\n#\n# - point") -> None:
    """Patch the CLI's translator factory to a non-failing static translator.

    Mirrors :func:`_stub_judge`: it replaces the ``OpenRouterSlideTranslator``
    symbol the command constructs, so a writing run translates id-less adds
    offline. A translator that always succeeds means a deferral can only come from
    the engine's own decision, never a missing translation.
    """
    from clm.cli.commands import slides_sync as cmd
    from clm.slides.sync_translate import StaticSlideTranslator

    monkeypatch.setattr(
        cmd, "OpenRouterSlideTranslator", lambda **_kwargs: StaticSlideTranslator(default=default)
    )


def _idless_slide(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


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


@pytest.fixture
def restore_api_keys():
    """Snapshot and restore key env vars a ``.env`` load would mutate.

    ``load_dotenv`` writes straight into ``os.environ`` (monkeypatch can't undo
    that), so without this a loaded test key would leak into later tests.
    """
    import os

    keys = ("OPENROUTER_API_KEY", "OPENAI_API_KEY")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestEnvFileLoading:
    """Bug: sync checked only the process env, so keys kept in the project
    ``.env`` (the usual course-repo layout, read by notebooks via
    ``load_dotenv``) were invisible — every add deferred and every edit errored
    as 'LLM unavailable'."""

    def _swap_openrouter_judge(self, monkeypatch) -> None:
        from clm.cli.commands import slides_sync as cmd
        from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

        proposal = SyncProposal(verdict="update", proposed_text=_EN_PROPOSAL, reason="")
        monkeypatch.setattr(
            cmd, "OpenRouterSyncJudge", lambda **_kw: StaticSyncJudge(default_proposal=proposal)
        )

    def test_key_in_dotenv_is_loaded_so_default_judge_runs(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch, restore_api_keys
    ):
        # Key lives ONLY in .env, not exported. Before the fix the openrouter
        # judge sees no key → records the edit as an error (exit 2). After the
        # fix .env is loaded → the judge runs → the edit applies (exit 0).
        monkeypatch.delenv("CLM_SYNC_PROVIDER", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        self._swap_openrouter_judge(monkeypatch)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-from-dotenv\n", encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0, _combined(result)
        assert "Point one" in en_path.read_text(encoding="utf-8")
        assert "applied: 1 edit" in result.output

    def test_dotenv_found_by_walking_up_from_deck_dir(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch, restore_api_keys
    ):
        # .env at the project root, decks in a nested topic dir (the real layout).
        monkeypatch.delenv("CLM_SYNC_PROVIDER", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        self._swap_openrouter_judge(monkeypatch)
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-root\n", encoding="utf-8")
        deck_dir = tmp_path / "slides" / "topic_010"
        deck_dir.mkdir(parents=True)
        cache_dir = tmp_path / "cache"
        de_path, en_path = _write_pair(deck_dir, _DE_EDITED, _EN_BASE)
        _seed_watermark(cache_dir, de_path, en_path, de_text=_DE_BASE, en_text=_EN_BASE)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0, _combined(result)
        assert "applied: 1 edit" in result.output

    def test_no_env_file_flag_skips_dotenv(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch, restore_api_keys
    ):
        # With --no-env-file the .env key stays invisible → judge unavailable.
        monkeypatch.delenv("CLM_SYNC_PROVIDER", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        self._swap_openrouter_judge(monkeypatch)
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-from-dotenv\n", encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--no-env-file", "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 2, _combined(result)
        assert "OPENROUTER_API_KEY" in (result.stderr or "")


class TestResolveTimeout:
    """A non-positive --llm-timeout must fall back to the provider default
    (a negative value would otherwise crash urllib with an uncaught ValueError
    on the local path)."""

    def test_clamps_non_positive_to_provider_default(self):
        from clm.cli.commands.slides_sync import _resolve_timeout

        assert _resolve_timeout(None, 120.0) == 120.0
        assert _resolve_timeout(0, 120.0) == 120.0
        assert _resolve_timeout(-5.0, 300.0) == 300.0
        assert _resolve_timeout(45.0, 120.0) == 45.0  # a positive value is honored


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


class TestExplain:
    """`--explain` is a read-only anchor-diff diagnostic (Issue #190 Phase 6)."""

    def test_explain_prints_anchor_diff_and_writes_nothing(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        before_de = de_path.read_text(encoding="utf-8")
        before_en = en_path.read_text(encoding="utf-8")
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--explain", "--cache-dir", str(cache_dir)],
        )
        out = _combined(result)
        assert result.exit_code in (0, 1), out  # read-only: clean / would-change
        assert "anchor diff" in out
        assert "legend:" in out
        assert "plan:" in out
        # Writes nothing — both decks are byte-identical afterwards.
        assert de_path.read_text(encoding="utf-8") == before_de
        assert en_path.read_text(encoding="utf-8") == before_en

    def test_explain_and_interactive_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [
                str(de_path),
                str(en_path),
                "--explain",
                "--interactive",
                "--cache-dir",
                str(cache_dir),
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in _combined(result)

    def test_explain_and_json_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path, cache_dir = _edit_scenario(tmp_path)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--explain", "--json", "--cache-dir", str(cache_dir)],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in _combined(result)


class TestPairingGuard:
    """``clm slides sync`` rejects an invalid DE/EN pair up front (#162 Tier-2)
    and auto-corrects a swapped order. The guard runs before any read/write, so
    ``--dry-run --no-cache`` is enough to exercise it.
    """

    def test_same_file_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        p = tmp_path / "slides_x.de.py"
        p.write_text(_DE_BASE, encoding="utf-8")
        result = cli_runner.invoke(slides_sync_cmd, [str(p), str(p), "--dry-run", "--no-cache"])
        assert result.exit_code != 0
        assert "same file" in _combined(result)

    def test_same_language_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de1 = tmp_path / "slides_x.de.py"
        de2 = tmp_path / "slides_y.de.py"
        de1.write_text(_DE_BASE, encoding="utf-8")
        de2.write_text(_DE_BASE, encoding="utf-8")
        result = cli_runner.invoke(slides_sync_cmd, [str(de1), str(de2), "--dry-run", "--no-cache"])
        assert result.exit_code != 0
        assert "same language" in _combined(result)

    def test_non_split_half_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        bilingual = tmp_path / "slides_x.py"
        en = tmp_path / "slides_x.en.py"
        bilingual.write_text(_DE_BASE, encoding="utf-8")
        en.write_text(_EN_BASE, encoding="utf-8")
        result = cli_runner.invoke(
            slides_sync_cmd, [str(bilingual), str(en), "--dry-run", "--no-cache"]
        )
        assert result.exit_code != 0
        assert "not a split-format slide half" in _combined(result)

    def test_cross_deck_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de = tmp_path / "slides_x.de.py"
        en = tmp_path / "slides_other.en.py"
        de.write_text(_DE_BASE, encoding="utf-8")
        en.write_text(_EN_BASE, encoding="utf-8")
        result = cli_runner.invoke(slides_sync_cmd, [str(de), str(en), "--dry-run", "--no-cache"])
        assert result.exit_code != 0
        assert "different decks" in _combined(result)

    def test_swapped_order_auto_corrected(self, cli_runner: CliRunner, tmp_path: Path):
        # EN passed first, DE second: the guard reorders and proceeds (exit 0
        # on a dry-run), emitting a note rather than erroring.
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(en_path), str(de_path), "--dry-run", "--no-cache"],
        )
        assert result.exit_code == 0, _combined(result)
        assert "swapped" in _combined(result)

    def test_well_formed_pair_passes_guard(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--dry-run", "--no-cache"],
        )
        assert result.exit_code == 0, _combined(result)
        assert "swapped" not in _combined(result)


class TestSinglePath:
    """`clm slides sync` single-path contract: EN_PATH is optional and the twin
    (or both halves from a deck stem) is derived from disk. The two-path form
    stays valid (backward compatible).
    """

    def test_single_half_derives_twin(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, _en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(slides_sync_cmd, [str(de_path), "--dry-run", "--no-cache"])
        assert result.exit_code == 0, _combined(result)

    def test_single_half_prefix_less(self, cli_runner: CliRunner, tmp_path: Path):
        # Prefix-agnostic: an un-prefixed deck (apis.de.py) derives its twin too.
        de_path, _en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE, stem="apis")
        result = cli_runner.invoke(slides_sync_cmd, [str(de_path), "--dry-run", "--no-cache"])
        assert result.exit_code == 0, _combined(result)

    def test_single_en_half_derives_twin_no_swap_note(self, cli_runner: CliRunner, tmp_path: Path):
        # The .en half alone derives the .de twin. The derived pair is returned
        # already (de, en)-ordered, so the pairing guard's "swapped" note must NOT
        # fire (the author passed a single path — nothing was swapped).
        _de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(slides_sync_cmd, [str(en_path), "--dry-run", "--no-cache"])
        assert result.exit_code == 0, _combined(result)
        assert "swapped" not in _combined(result)

    def test_deck_stem_derives_both_halves(self, cli_runner: CliRunner, tmp_path: Path):
        _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        stem = tmp_path / "slides_intro.py"  # bilingual stem present on disk
        stem.write_text(_DE_BASE + _EN_BASE, encoding="utf-8")
        result = cli_runner.invoke(
            slides_sync_cmd, [str(stem), "--dry-run", "--json", "--no-cache"]
        )
        assert result.exit_code == 0, _combined(result)
        # The plan acted on the derived split halves, not the stem itself.
        data = _json_payload(result)
        assert data["de_path"].endswith("slides_intro.de.py")
        assert data["en_path"].endswith("slides_intro.en.py")

    def test_missing_twin_errors(self, cli_runner: CliRunner, tmp_path: Path):
        de_path = tmp_path / "slides_intro.de.py"
        de_path.write_text(_DE_BASE, encoding="utf-8")  # no .en twin on disk
        result = cli_runner.invoke(slides_sync_cmd, [str(de_path), "--dry-run", "--no-cache"])
        assert result.exit_code != 0
        assert "twin" in _combined(result)

    def test_deck_stem_missing_half_errors(self, cli_runner: CliRunner, tmp_path: Path):
        # An untagged stem whose halves are not both present → the Branch-B usage
        # error naming both expected halves (not a silent or contradictory failure).
        stem = tmp_path / "slides_intro.py"
        stem.write_text(_DE_BASE + _EN_BASE, encoding="utf-8")
        (tmp_path / "slides_intro.de.py").write_text(_DE_BASE, encoding="utf-8")  # no .en
        result = cli_runner.invoke(slides_sync_cmd, [str(stem), "--dry-run", "--no-cache"])
        assert result.exit_code != 0
        out = _combined(result)
        assert ".de.py" in out and ".en.py" in out

    def test_companion_alone_errors_with_hint(self, cli_runner: CliRunner, tmp_path: Path):
        # A voiceover companion is never a sync target; passing one alone gives a
        # companion-specific error (sync never derives a twin for a companion).
        comp = tmp_path / "voiceover_intro.de.py"
        comp.write_text(_DE_BASE, encoding="utf-8")
        result = cli_runner.invoke(slides_sync_cmd, [str(comp), "--dry-run", "--no-cache"])
        assert result.exit_code != 0
        assert "companion" in _combined(result)

    def test_two_path_form_still_works(self, cli_runner: CliRunner, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd, [str(de_path), str(en_path), "--dry-run", "--no-cache"]
        )
        assert result.exit_code == 0, _combined(result)

    def test_keys_watermark_by_resolved_path(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # The single-path surface must hand build_sync_plan **resolved** paths, so
        # its watermark key matches the directory-batch surface (whose enumerator
        # resolves every file). Otherwise the same pair gets two keys across
        # surfaces and the second silently misses the first's watermark.
        from clm.cli.commands import slides_sync as cmd

        captured: dict[str, Path] = {}
        real = cmd.build_sync_plan

        def cap(de_path, en_path, **kw):
            captured["de"], captured["en"] = de_path, en_path
            return real(de_path, en_path, **kw)

        monkeypatch.setattr(cmd, "build_sync_plan", cap)
        de_path, en_path = _write_pair(tmp_path, _DE_BASE, _EN_BASE)
        result = cli_runner.invoke(
            slides_sync_cmd, [str(de_path), str(en_path), "--dry-run", "--no-cache"]
        )
        assert result.exit_code == 0, _combined(result)
        assert captured["de"] == de_path.resolve()
        assert captured["en"] == en_path.resolve()


class TestBatchMode:
    """`clm slides sync DIR` — sweep every split pair under a directory (§8a).

    A directory triggers batch mode: prefix-agnostic enumeration, solo halves
    skipped with a warning, continue-on-error, a max-severity aggregate exit
    code, a `--yes` gate on writes, and a `{mode, root, exit_code, pairs:[...]}`
    JSON envelope whose per-pair entries reuse the single-pair object shape.
    """

    def _make_tree(
        self, tmp_path: Path, *, with_solo: bool = True
    ) -> tuple[Path, Path, dict[str, tuple[Path, Path]]]:
        """A directory holding two prefix-less pairs (``apis`` in sync, ``web``
        with DE edited vs the watermark) and — optionally — a solo DE half.

        Watermarks are seeded with **resolved** paths because batch enumeration
        resolves every file (so the watermark key matches what ``build_sync_plan``
        looks up). Returns ``(root, cache_dir, {stem: (de, en)})``.
        """
        root = tmp_path / "decks"
        root.mkdir()
        cache_dir = tmp_path / "cache"
        apis_de, apis_en = _write_pair(root, _DE_BASE, _EN_BASE, stem="apis")
        web_de, web_en = _write_pair(root, _DE_EDITED, _EN_BASE, stem="web")
        _seed_watermark(
            cache_dir, apis_de.resolve(), apis_en.resolve(), de_text=_DE_BASE, en_text=_EN_BASE
        )
        _seed_watermark(
            cache_dir, web_de.resolve(), web_en.resolve(), de_text=_DE_BASE, en_text=_EN_BASE
        )
        if with_solo:
            (root / "orphan.de.py").write_text(_DE_BASE, encoding="utf-8")
        return root, cache_dir, {"apis": (apis_de, apis_en), "web": (web_de, web_en)}

    def test_dry_run_json_envelope_skips_solo_and_aggregates(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        root, cache_dir, _decks = self._make_tree(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--dry-run", "--json", "--cache-dir", str(cache_dir)],
        )

        # apis is in sync (0), web has a DE edit (1) -> aggregate max == 1.
        assert result.exit_code == 1, _combined(result)
        # The solo half is skipped with a warning, never synced against a phantom.
        assert "skipping orphan.de.py" in (result.stderr or "")
        payload = _json_payload(result)
        assert payload["mode"] == "dry-run"
        assert payload["root"] == str(root)
        assert payload["exit_code"] == 1
        assert len(payload["pairs"]) == 2
        by_stem = {Path(p["de_path"]).name: p for p in payload["pairs"]}
        # Each pair entry is the single-pair object shape verbatim.
        assert by_stem["apis.de.py"]["plan"]["counts"]["edit"] == 0
        assert by_stem["apis.de.py"]["exit_code"] == 0
        assert by_stem["web.de.py"]["plan"]["counts"]["edit"] == 1
        assert by_stem["web.de.py"]["plan"]["proposals"][0]["direction"] == "de->en"
        assert by_stem["web.de.py"]["exit_code"] == 1

    def test_dry_run_human_one_liner_and_rollup(self, cli_runner: CliRunner, tmp_path: Path):
        root, cache_dir, _decks = self._make_tree(tmp_path)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--dry-run", "--cache-dir", str(cache_dir)],
        )

        out = result.output
        assert result.exit_code == 1, _combined(result)
        assert "OK     apis.de.py: nothing to do" in out
        assert "REVIEW web.de.py: would change: 1 edit" in out
        assert "2 pair(s): 1 clean, 1 review, 0 errored." in out

    def test_apply_writes_every_pair_with_yes(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        _stub_judge(monkeypatch, _EN_PROPOSAL)
        # Both pairs edited so both apply.
        root = tmp_path / "decks"
        root.mkdir()
        cache_dir = tmp_path / "cache"
        a_de, a_en = _write_pair(root, _DE_EDITED, _EN_BASE, stem="apis")
        w_de, w_en = _write_pair(root, _DE_EDITED, _EN_BASE, stem="web")
        _seed_watermark(
            cache_dir, a_de.resolve(), a_en.resolve(), de_text=_DE_BASE, en_text=_EN_BASE
        )
        _seed_watermark(
            cache_dir, w_de.resolve(), w_en.resolve(), de_text=_DE_BASE, en_text=_EN_BASE
        )

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--yes", "--cache-dir", str(cache_dir)],
        )

        assert result.exit_code == 0, _combined(result)
        assert "Point one" in a_en.read_text(encoding="utf-8")
        assert "Point one" in w_en.read_text(encoding="utf-8")
        assert "2 pair(s): 2 clean, 0 review, 0 errored." in result.output
        assert "Review the propagated changes with `git diff`" in result.output

    def test_apply_without_yes_and_json_errors(self, cli_runner: CliRunner, tmp_path: Path):
        root, cache_dir, _decks = self._make_tree(tmp_path, with_solo=False)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--json", "--cache-dir", str(cache_dir)],
        )
        assert result.exit_code != 0
        assert "needs --yes" in _combined(result)

    def test_apply_without_yes_prompts_and_abort_writes_nothing(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        root, cache_dir, decks = self._make_tree(tmp_path, with_solo=False)
        before = {stem: en.read_text(encoding="utf-8") for stem, (_de, en) in decks.items()}

        # Decline the confirm: the sweep aborts before any write.
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--cache-dir", str(cache_dir)],
            input="n\n",
        )

        assert result.exit_code != 0
        for stem, (_de, en) in decks.items():
            assert en.read_text(encoding="utf-8") == before[stem]

    def test_continue_on_error_aggregates_and_isolates(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # One pair raises during classification; the sweep records it as errored
        # (exit 2) and still processes the other pair.
        from clm.cli.commands import slides_sync as cmd

        root, cache_dir, _decks = self._make_tree(tmp_path, with_solo=False)
        real_build = cmd.build_sync_plan

        def flaky_build(de_path, en_path, **kw):
            if "web" in de_path.name:
                raise RuntimeError("boom")
            return real_build(de_path, en_path, **kw)

        monkeypatch.setattr(cmd, "build_sync_plan", flaky_build)

        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--dry-run", "--cache-dir", str(cache_dir)],
        )

        out = result.output
        assert result.exit_code == 2, _combined(result)
        assert "OK     apis.de.py: nothing to do" in out
        assert "ERROR  web.de.py: RuntimeError: boom" in out
        assert "2 pair(s): 1 clean, 0 review, 1 errored." in out

    def test_explain_prints_per_pair_anchor_diff(self, cli_runner: CliRunner, tmp_path: Path):
        root, cache_dir, _decks = self._make_tree(tmp_path, with_solo=False)
        result = cli_runner.invoke(
            slides_sync_cmd,
            [str(root), "--explain", "--cache-dir", str(cache_dir)],
        )
        out = _combined(result)
        assert result.exit_code in (0, 1), out
        assert "=== apis.de.py ===" in out
        assert "=== web.de.py ===" in out
        assert "anchor diff" in out
        assert "2 pair(s):" in out

    def test_empty_directory_reports_nothing(self, cli_runner: CliRunner, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = cli_runner.invoke(slides_sync_cmd, [str(empty), "--dry-run", "--no-cache"])
        assert result.exit_code == 0, _combined(result)
        assert "no split-format deck pairs found" in result.output

    def test_empty_directory_json_envelope(self, cli_runner: CliRunner, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = cli_runner.invoke(
            slides_sync_cmd, [str(empty), "--dry-run", "--json", "--no-cache"]
        )
        assert result.exit_code == 0, _combined(result)
        payload = _json_payload(result)
        assert payload["pairs"] == []
        assert payload["exit_code"] == 0

    def test_directory_with_second_path_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        root = tmp_path / "decks"
        root.mkdir()
        de_path, en_path = _write_pair(root, _DE_BASE, _EN_BASE, stem="apis")
        result = cli_runner.invoke(
            slides_sync_cmd, [str(root), str(en_path), "--dry-run", "--no-cache"]
        )
        assert result.exit_code != 0
        assert "single" in _combined(result) and "directory" in _combined(result)

    def test_directory_with_interactive_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        root = tmp_path / "decks"
        root.mkdir()
        _write_pair(root, _DE_BASE, _EN_BASE, stem="apis")
        result = cli_runner.invoke(slides_sync_cmd, [str(root), "--interactive", "--no-cache"])
        assert result.exit_code != 0
        assert "--interactive" in _combined(result)

    def test_excludes_pairs_under_ignored_dirs(self, cli_runner: CliRunner, tmp_path: Path):
        # A vendored deck under .venv must not be synced by a directory run.
        root = tmp_path / "decks"
        root.mkdir()
        _write_pair(root, _DE_BASE, _EN_BASE, stem="real")
        vend = root / ".venv" / "pkg"
        vend.mkdir(parents=True)
        _write_pair(vend, _DE_BASE, _EN_BASE, stem="vendored")
        result = cli_runner.invoke(
            slides_sync_cmd, [str(root), "--dry-run", "--json", "--no-cache"]
        )
        assert result.exit_code == 0, _combined(result)
        payload = _json_payload(result)
        names = {Path(p["de_path"]).name for p in payload["pairs"]}
        assert names == {"real.de.py"}  # vendored.de.py under .venv is pruned

    def test_loads_env_from_nested_deck_dir(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch, restore_api_keys
    ):
        # A .env above a nested deck (but below root, with no root .env) must be
        # found in batch mode — the writing path searches per-deck, not only root.
        monkeypatch.delenv("CLM_SYNC_PROVIDER", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from clm.cli.commands import slides_sync as cmd
        from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

        proposal = SyncProposal(verdict="update", proposed_text=_EN_PROPOSAL, reason="")
        monkeypatch.setattr(
            cmd, "OpenRouterSyncJudge", lambda **_kw: StaticSyncJudge(default_proposal=proposal)
        )
        root = tmp_path / "decks"
        deck_dir = root / "mod" / "topic"
        deck_dir.mkdir(parents=True)
        cache_dir = tmp_path / "cache"
        de_path, en_path = _write_pair(deck_dir, _DE_EDITED, _EN_BASE, stem="apis")
        _seed_watermark(
            cache_dir, de_path.resolve(), en_path.resolve(), de_text=_DE_BASE, en_text=_EN_BASE
        )
        # The key lives only in a .env beside the nested deck; root has none.
        (deck_dir / ".env").write_text("OPENROUTER_API_KEY=sk-nested\n", encoding="utf-8")

        result = cli_runner.invoke(
            slides_sync_cmd, [str(root), "--yes", "--cache-dir", str(cache_dir)]
        )

        # Key found → the judge runs → the edit applies (exit 0). Before the fix
        # the nested .env was missed → judge unavailable → exit 2.
        assert result.exit_code == 0, _combined(result)
        assert "Point one" in en_path.read_text(encoding="utf-8")


class TestDryRunApplyParity:
    """``--dry-run`` must predict the writing run (#216).

    A preview that promises changes a writing run silently refuses (or that exits
    "clean / changes-pending" when the writing run errors and writes nothing) is a
    misleading preview. These tests drive the real command twice — once with
    ``--dry-run``, once writing — over the same fixture, with the judge/translator
    stubbed so the only thing under test is whether the preview matches the act.
    """

    def test_dry_run_add_count_matches_apply(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # Watermark baseline; the author appends ONE id-less slide on DE only.
        cache_dir = tmp_path / "cache"
        de_base = _cell("de", "a", "# ## A")
        en_base = _cell("en", "a", "# ## A")
        de_path, en_path = _write_pair(tmp_path, de_base, en_base, stem="parity")
        _seed_watermark(
            cache_dir, de_path.resolve(), en_path.resolve(), de_text=de_base, en_text=en_base
        )
        de_path.write_text(de_base + _idless_slide("de", "# ## Neu"), encoding="utf-8")

        dry = cli_runner.invoke(
            slides_sync_cmd,
            [str(de_path), str(en_path), "--dry-run", "--cache-dir", str(cache_dir)],
        )
        assert dry.exit_code == 1, _combined(dry)  # one change pending
        assert "1 add" in dry.output

        _stub_judge(monkeypatch, _EN_PROPOSAL)
        _stub_translator(monkeypatch)
        applied = cli_runner.invoke(
            slides_sync_cmd, [str(de_path), str(en_path), "--cache-dir", str(cache_dir)]
        )
        # The preview's promise (1 add, applicable) is what the writing run did.
        assert applied.exit_code == 0, _combined(applied)
        assert "1 add" in applied.output
        assert "# ## Neu" in de_path.read_text(encoding="utf-8")

    def test_dry_run_promise_matches_apply_for_parallel_idless(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # A cold-start parallel id-less pair: the resolver refuses the
        # both-directions adds at plan time (#216), so the dry-run shows the
        # refusal (exit 1, "changes pending") and a writing run defers it
        # (exit 1) — they agree, and nothing is written.
        de = _idless_slide("de", "# ## Eins") + _idless_slide("de", "# ## Zwei")
        en = _idless_slide("en", "# ## One") + _idless_slide("en", "# ## Two")
        de_path, en_path = _write_pair(tmp_path, de, en, stem="cold")

        dry = cli_runner.invoke(
            slides_sync_cmd, [str(de_path), str(en_path), "--dry-run", "--no-cache"]
        )
        assert dry.exit_code == 1, _combined(dry)  # "changes pending"
        assert "baseline=none" in dry.output
        assert "refuse" in dry.output  # the refusal is shown in the preview

        _stub_judge(monkeypatch, _EN_PROPOSAL)
        _stub_translator(monkeypatch)
        applied = cli_runner.invoke(slides_sync_cmd, [str(de_path), str(en_path), "--no-cache"])
        # The writing run does not corrupt the decks...
        assert de_path.read_text(encoding="utf-8") == de
        assert en_path.read_text(encoding="utf-8") == en
        # ...and the dry-run preview matched it: both "needs review" (exit 1), the
        # refusal foreseen — never a clean/pending preview that errors on write.
        assert applied.exit_code == 1, _combined(applied)
        assert applied.exit_code == dry.exit_code
        assert "refuse" in applied.output
