"""CLI tests for ``clm slides translate`` (alias ``bootstrap``) — Issue #232, Phase 4.

The command wraps the bootstrap engine. These tests drive the Click surface with
the translator factory and judge patched to static fakes, so they exercise flag
parsing, the bootstrap-vs-sync dispatch, file writes and exit codes without a
live LLM or an API key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands import slides_translate as cmd
from clm.cli.commands.slides_translate import slides_translate_cmd
from clm.slides.raw_cells import split_cells
from clm.slides.split import split_text, unify_texts
from clm.slides.sync_translate import StaticSlideTranslator


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HEADER_PREAMBLE = (
    '# j2 from \'macros.j2\' import header\n# {{ header("Titel DE", "Title EN") }}\n\n'
)
TITLES = {"Titel DE": "Title EN"}


def _slide_pair(slug: str, de_title: str, en_title: str) -> str:
    return (
        f'# %% [markdown] lang="de" tags=["slide"] slide_id="{slug}"\n'
        f"#\n# ## {de_title}\n#\n# - DE Bullet\n\n"
        f'# %% [markdown] lang="en" tags=["slide"] slide_id="{slug}"\n'
        f"#\n# ## {en_title}\n#\n# - EN Bullet\n\n"
    )


def _shared_code(name: str = "end") -> str:
    return f'# %% tags=["keep"]\n{name} = 1\n\n'


_DECK = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction") + _shared_code("end")


def _localized_bodies(text: str) -> list[str]:
    _, cells = split_cells(text)
    return [c.body.rstrip("\n") for c in cells if c.metadata.lang is not None]


def _mirror_translator(de: str, en: str) -> StaticSlideTranslator:
    mapping = dict(zip(_localized_bodies(de), _localized_bodies(en)))
    mapping.update(TITLES)
    return StaticSlideTranslator(mapping=mapping)


def _split(text: str) -> tuple[str, str]:
    de, en = split_text(text)
    assert unify_texts(de, en) == text
    return de, en


def _patch_translator(monkeypatch, translator) -> None:
    monkeypatch.setattr(cmd, "_make_translator", lambda *_a, **_k: translator)


def _patch_key(monkeypatch, present: bool = True) -> None:
    monkeypatch.setattr(cmd, "has_openrouter_api_key", lambda *_a, **_k: present)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _common(tmp_path: Path) -> list[str]:
    """Flags every writing test wants: own cache dir, no .env walk."""
    return ["--no-env-file", "--cache-dir", str(tmp_path / "cache")]


# ---------------------------------------------------------------------------
# Bootstrap path
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_writes_twin_exit_0(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _patch_key(monkeypatch)
        _patch_translator(monkeypatch, _mirror_translator(de, en))

        result = cli_runner.invoke(slides_translate_cmd, [str(de_path), *_common(tmp_path)])

        assert result.exit_code == 0, result.output
        twin = tmp_path / "slides_x.en.py"
        assert twin.exists()
        assert twin.read_text(encoding="utf-8") == en
        assert "Bootstrapped slides_x.en.py" in result.output

    def test_no_key_exits_1_writes_nothing(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _patch_key(monkeypatch, present=False)
        _patch_translator(monkeypatch, _mirror_translator(de, en))

        result = cli_runner.invoke(slides_translate_cmd, [str(de_path), *_common(tmp_path)])

        assert result.exit_code == 1
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_json_output(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _patch_key(monkeypatch)
        _patch_translator(monkeypatch, _mirror_translator(de, en))

        result = cli_runner.invoke(
            slides_translate_cmd, [str(de_path), "--json", *_common(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["action"] == "bootstrapped"
        assert payload["target"].endswith("slides_x.en.py")
        assert payload["cells_translated"] == 1
        assert payload["source_lang"] == "de" and payload["target_lang"] == "en"

    def test_to_override_reverse_direction(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        en_path = _write(tmp_path / "slides_x.en.py", en)
        rev = dict(zip(_localized_bodies(en), _localized_bodies(de)))
        rev["Title EN"] = "Titel DE"
        _patch_key(monkeypatch)
        _patch_translator(monkeypatch, StaticSlideTranslator(mapping=rev))

        result = cli_runner.invoke(
            slides_translate_cmd, [str(en_path), "--to", "de", *_common(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "slides_x.de.py").read_text(encoding="utf-8") == de


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_writes_nothing_no_key_needed(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        # No key, no translator patch — dry-run must not touch either.
        _patch_key(monkeypatch, present=False)

        result = cli_runner.invoke(
            slides_translate_cmd, [str(de_path), "--dry-run", "--no-env-file"]
        )
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "slides_x.en.py").exists()
        assert "Would bootstrap slides_x.en.py" in result.output

    def test_json(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        result = cli_runner.invoke(
            slides_translate_cmd, [str(de_path), "--dry-run", "--json", "--no-env-file"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["mode"] == "dry-run"
        assert payload["action"] == "bootstrap"
        assert payload["cells_translatable"] == 1
        assert payload["cells_copied"] >= 1  # header + shared code


# ---------------------------------------------------------------------------
# Present-twin → sync delegation
# ---------------------------------------------------------------------------


class TestSyncDelegation:
    def test_second_run_delegates_to_sync(self, cli_runner, tmp_path, monkeypatch):
        from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal

        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _patch_key(monkeypatch)
        _patch_translator(monkeypatch, _mirror_translator(de, en))
        monkeypatch.setattr(
            cmd,
            "_resolve_judge",
            lambda *_a, **_k: StaticSyncJudge(
                default_proposal=SyncProposal(verdict="in_sync", proposed_text="")
            ),
        )

        first = cli_runner.invoke(slides_translate_cmd, [str(de_path), *_common(tmp_path)])
        assert first.exit_code == 0, first.output
        twin = tmp_path / "slides_x.en.py"
        before = twin.read_text(encoding="utf-8")

        second = cli_runner.invoke(slides_translate_cmd, [str(de_path), *_common(tmp_path)])
        assert second.exit_code == 0, second.output
        assert "incremental sync" in second.output
        assert twin.read_text(encoding="utf-8") == before  # not doubled

    def test_force_overwrites_existing_twin(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _write(tmp_path / "slides_x.en.py", '# %% [markdown] lang="en"\n#\n# stale\n\n')
        _patch_key(monkeypatch)
        _patch_translator(monkeypatch, _mirror_translator(de, en))

        result = cli_runner.invoke(
            slides_translate_cmd, [str(de_path), "--force", *_common(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert "stale" not in (tmp_path / "slides_x.en.py").read_text(encoding="utf-8")
        assert "Bootstrapped" in result.output


# ---------------------------------------------------------------------------
# Errors / rejection
# ---------------------------------------------------------------------------


class TestErrors:
    def test_bilingual_stem_is_usage_error(self, cli_runner, tmp_path, monkeypatch):
        # No .de/.en tag on the source -> UsageError (exit 2).
        src = _write(tmp_path / "slides_x.py", _DECK)
        result = cli_runner.invoke(slides_translate_cmd, [str(src), *_common(tmp_path)])
        assert result.exit_code == 2

    def test_missing_source_is_error(self, cli_runner, tmp_path):
        result = cli_runner.invoke(
            slides_translate_cmd, [str(tmp_path / "nope.de.py"), *_common(tmp_path)]
        )
        assert result.exit_code == 2  # click.Path(exists=True)


# ---------------------------------------------------------------------------
# bootstrap alias (via the slides group)
# ---------------------------------------------------------------------------


def test_bootstrap_alias_is_registered(cli_runner, tmp_path, monkeypatch):
    from clm.cli.main import cli

    de, en = _split(_DECK)
    de_path = _write(tmp_path / "slides_x.de.py", de)
    _patch_key(monkeypatch)
    _patch_translator(monkeypatch, _mirror_translator(de, en))

    result = cli_runner.invoke(cli, ["slides", "bootstrap", str(de_path), *_common(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "slides_x.en.py").exists()


# ---------------------------------------------------------------------------
# Phase 5 acceptance gate: a generated deck passes the pre-commit validator
# ---------------------------------------------------------------------------


def test_generated_deck_passes_validate_fail_on_warning(cli_runner, tmp_path, monkeypatch):
    """End-to-end: bootstrap a deck via the CLI, then `clm validate ... --fail-on
    warning` (the pre-commit gate) must pass — slide_id set/order parity,
    shared-cell byte parity, pairing adjacency, companion for_slide parity."""
    from clm.cli.main import cli

    de, en = _split(_DECK)
    topic = tmp_path / "slides" / "module_010" / "topic_100_intro"
    topic.mkdir(parents=True)
    de_path = _write(topic / "slides_intro.de.py", de)
    _patch_key(monkeypatch)
    _patch_translator(monkeypatch, _mirror_translator(de, en))

    boot = cli_runner.invoke(slides_translate_cmd, [str(de_path), *_common(tmp_path)])
    assert boot.exit_code == 0, boot.output
    assert (topic / "slides_intro.en.py").exists()

    # The generated pair is immediately valid for the split-pair tooling.
    val = cli_runner.invoke(cli, ["validate", str(topic), "--fail-on", "warning"])
    assert val.exit_code == 0, val.output
