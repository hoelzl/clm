"""CLI tests for the ``clm slides translate`` agent-toolkit verbs (#961).

``task`` frames the whole-deck cold start as a JSON task document (no model,
no API key); ``accept`` validates the answer's shape and freshness and writes
the twin through the ordinary bootstrap engine; bare ``translate`` (the
default ``report`` verb) points at ``task`` when the twin is absent. The
in-process OpenRouter path lives behind ``autopilot``.

Regression tests for #961: a cold-start bootstrap must succeed with **no API
key** via task/accept, producing output byte-identical to the autopilot
bootstrap.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides import translate as cmd
from clm.core.slide_text.raw_cells import split_cells
from clm.slides.split import split_text, unify_texts
from clm.slides.sync_translate import StaticSlideTranslator

# --------------------------------------------------------------------------
# Fixtures (mirroring tests/cli/test_slides_translate.py)
# --------------------------------------------------------------------------

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


def _localized_code_cell() -> str:
    """A lang-tagged code cell: translated through the code prompt."""
    return '# %% [code] lang="de" slide_id="demo"\nfrage = "Wie geht es dir?"\nprint(frage)\n\n'


def _shared_code(name: str = "end") -> str:
    return f'# %% tags=["keep"]\n{name} = 1\n\n'


def _companion_pair(slide_id: str) -> str:
    vo_id = f"{slide_id}-vo"
    return (
        f'# %% [markdown] lang="de" tags=["voiceover"] slide_id="{vo_id}" '
        f'for_slide="{slide_id}" vo_anchor="id:{slide_id}"\n#\n# Voiceover DE für {slide_id}\n\n'
        f'# %% [markdown] lang="en" tags=["voiceover"] slide_id="{vo_id}" '
        f'for_slide="{slide_id}" vo_anchor="id:{slide_id}"\n#\n# Voiceover EN for {slide_id}\n\n'
    )


_DECK = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction") + _shared_code("end")
_DECK_WITH_CODE = (
    HEADER_PREAMBLE
    + _slide_pair("intro", "Einleitung", "Introduction")
    + _localized_code_cell()
    + _shared_code("end")
)


def _localized_bodies(text: str) -> list[str]:
    _, cells = split_cells(text)
    return [c.body.rstrip("\n") for c in cells if c.metadata.lang is not None]


def _split(text: str) -> tuple[str, str]:
    de, en = split_text(text)
    assert unify_texts(de, en) == text
    return de, en


def _mirror_translator(de: str, en: str) -> StaticSlideTranslator:
    mapping = dict(zip(_localized_bodies(de), _localized_bodies(en)))
    mapping.update(TITLES)
    return StaticSlideTranslator(mapping=mapping)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_translator(monkeypatch, translator) -> None:
    monkeypatch.setattr(cmd, "_make_translator", lambda *_a, **_k: translator)


def _patch_key(monkeypatch, present: bool = True) -> None:
    monkeypatch.setattr(cmd, "has_openrouter_api_key", lambda *_a, **_k: present)


def _forbid_key(monkeypatch) -> None:
    """Fail loudly if the verb under test ever consults the API key."""

    def boom(*_a, **_k):  # pragma: no cover - only fires on a contract breach
        raise AssertionError("this verb must not consult the API key")

    monkeypatch.setattr(cmd, "has_openrouter_api_key", boom)


def _common(tmp_path: Path) -> list[str]:
    return ["--no-env-file", "--cache-dir", str(tmp_path / "cache")]


def _invoke_task(cli_runner, source: Path, *extra: str):
    from clm.cli.main import cli

    return cli_runner.invoke(cli, ["slides", "translate", "task", str(source), *extra])


def _build_answer(task_payload: dict, translator: StaticSlideTranslator) -> dict:
    """Answer every framed translatable cell from a body-mapping translator."""
    answer = {
        "schema": task_payload["schema"],
        "source_lang": task_payload["source_lang"],
        "target_lang": task_payload["target_lang"],
        "source_fingerprint": task_payload["source_fingerprint"],
        "companion_fingerprint": task_payload.get("companion_fingerprint"),
        "translations": [],
        "companion_translations": [],
    }
    for scope in ("deck", "companion"):
        section = task_payload.get(scope)
        if not section or section.get("cells") is None:
            continue
        rows = answer["translations"] if scope == "deck" else answer["companion_translations"]
        for cell in section["cells"]:
            if cell["kind"] not in ("translated", "header"):
                continue
            body = translator.translate(
                source_body=cell["source_body"],
                source_lang=answer["source_lang"],
                target_lang=answer["target_lang"],
                role=cell.get("role") or "markdown",
            )
            rows.append({"index": cell["index"], "body": body})
    if not answer["companion_translations"]:
        del answer["companion_translations"]
    return answer


def _invoke_accept(cli_runner, source: Path, answer: dict, *extra: str):
    from clm.cli.main import cli

    return cli_runner.invoke(
        cli,
        ["slides", "translate", "accept", str(source), "--answer", "-", *extra],
        input=json.dumps(answer),
    )


# --------------------------------------------------------------------------
# task — the framed cold start (read-only, model-free)
# --------------------------------------------------------------------------


class TestTask:
    def test_emits_task_envelope_without_key(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)

        result = _invoke_task(cli_runner, de_path)

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["schema"] == 1
        assert payload["tool"] == "translate"
        assert payload["verb"] == "task"
        assert payload["source_lang"] == "de" and payload["target_lang"] == "en"
        assert payload["source"] == str(de_path.resolve())
        assert payload["target"].endswith("slides_x.en.py")
        assert payload["validator"] == "translate-deck"
        assert payload["instructions"]
        assert payload["answer_schema"]["type"] == "object"
        assert payload["source_fingerprint"] == _fingerprint(de_path)
        assert payload["companion_fingerprint"] is None
        # The translation rules are the shared seam: all three role prompts.
        prompts = payload["prompts"]
        assert "German" in prompts["markdown"] and "English" in prompts["markdown"]
        assert "byte-identical" in prompts["code"]
        assert prompts["title"].startswith("You translate a single slide-deck title")

    def test_frames_translated_title_copy_and_code_cells(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK_WITH_CODE)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)

        payload = json.loads(_invoke_task(cli_runner, de_path).output)
        cells = payload["deck"]["cells"]
        kinds = {c["kind"] for c in cells}
        assert kinds == {"translated", "header", "copied", "import"}
        by_index = {c["index"]: c for c in cells}
        title_rows = [c for c in cells if c["kind"] == "header"]
        assert len(title_rows) == 1 and title_rows[0]["source_body"] == "Titel DE"
        code_rows = [c for c in cells if c["kind"] == "translated" and c["role"] == "code"]
        assert len(code_rows) == 1
        assert code_rows[0]["slide_id"] == "demo"
        assert 'frage = "Wie geht es dir?"' in code_rows[0]["source_body"]
        copy_rows = [c for c in cells if c["kind"] == "copied"]
        assert copy_rows and all("source_body" not in c for c in copy_rows)

    def test_frames_companion_in_lockstep(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        comp_de, _ = _split(_companion_pair("intro"))
        comp_path = _write(tmp_path / "voiceover_x.de.py", comp_de)
        _forbid_key(monkeypatch)

        payload = json.loads(_invoke_task(cli_runner, de_path).output)
        companion = payload["companion"]
        assert companion is not None
        assert companion["source"] == str(comp_path.resolve())
        assert companion["target"].endswith("voiceover_x.en.py")
        assert companion["fingerprint"] == _fingerprint(comp_path)
        assert payload["companion_fingerprint"] == _fingerprint(comp_path)
        assert [c["kind"] for c in companion["cells"]] == ["translated"]

    def test_existing_companion_target_is_not_framed(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        comp_de, comp_en = _split(_companion_pair("intro"))
        _write(tmp_path / "voiceover_x.de.py", comp_de)
        _write(tmp_path / "voiceover_x.en.py", comp_en)
        _forbid_key(monkeypatch)

        payload = json.loads(_invoke_task(cli_runner, de_path).output)
        companion = payload["companion"]
        assert companion is not None
        assert companion.get("cells") is None
        assert "skipped" in companion.get("note", "").lower()

    def test_glossary_resolved_into_prompts_and_payload(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _write(tmp_path / "clm-glossary.en.md", "Use formal English.")
        _forbid_key(monkeypatch)

        payload = json.loads(_invoke_task(cli_runner, de_path).output)
        assert payload["glossary"]["text"] == "Use formal English."
        assert payload["glossary"]["path"].endswith("clm-glossary.en.md")
        assert payload["prompts"]["markdown"].endswith("Use formal English.")

    def test_twin_present_is_error(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _write(tmp_path / "slides_x.en.py", en)
        _forbid_key(monkeypatch)

        result = _invoke_task(cli_runner, de_path)
        assert result.exit_code == 2

    def test_to_override_reverse_direction(self, cli_runner, tmp_path, monkeypatch):
        _, en = _split(_DECK)
        en_path = _write(tmp_path / "slides_x.en.py", en)
        _forbid_key(monkeypatch)

        result = _invoke_task(cli_runner, en_path, "--to", "de")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["source_lang"] == "en" and payload["target_lang"] == "de"
        assert payload["target"].endswith("slides_x.de.py")


# --------------------------------------------------------------------------
# accept — validated write through the ordinary bootstrap engine
# --------------------------------------------------------------------------


class TestAccept:
    def test_no_key_bootstrap_identical_to_autopilot(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        comp_de, comp_en = _split(_companion_pair("intro"))
        translator = _mirror_translator(de, en)
        comp_mapping = dict(zip(_localized_bodies(comp_de), _localized_bodies(comp_en)))
        translator.mapping.update(comp_mapping)

        # Reference: the autopilot bootstrap (in-process translator, key).
        ref_dir = tmp_path / "ref"
        ref_dir.mkdir()
        de_ref = _write(ref_dir / "slides_x.de.py", de)
        _write(ref_dir / "voiceover_x.de.py", comp_de)
        _patch_key(monkeypatch)
        _patch_translator(monkeypatch, translator)
        from clm.cli.main import cli

        ref = cli_runner.invoke(
            cli, ["slides", "translate", "autopilot", str(de_ref), *_common(tmp_path)]
        )
        assert ref.exit_code == 0, ref.output

        # Agent path: task -> answer -> accept, with the key forbidden.
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        de_agent = _write(agent_dir / "slides_x.de.py", de)
        _write(agent_dir / "voiceover_x.de.py", comp_de)
        _forbid_key(monkeypatch)
        task_payload = json.loads(_invoke_task(cli_runner, de_agent).output)
        answer = _build_answer(task_payload, translator)
        result = _invoke_accept(cli_runner, de_agent, answer, "--json")
        assert result.exit_code == 0, result.output

        assert (agent_dir / "slides_x.en.py").read_text(encoding="utf-8") == (
            ref_dir / "slides_x.en.py"
        ).read_text(encoding="utf-8")
        assert (agent_dir / "voiceover_x.en.py").read_text(encoding="utf-8") == (
            ref_dir / "voiceover_x.en.py"
        ).read_text(encoding="utf-8")
        outcome = json.loads(result.output)
        assert outcome["applied"] is True
        assert outcome["action"] == "bootstrapped"
        assert outcome["ledger_recorded"] is True
        assert (agent_dir / ".clm" / "sync-ledger.json").is_file()

    def test_accept_dry_run_writes_nothing(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))

        result = _invoke_accept(cli_runner, de_path, answer, "--dry-run", "--json")

        assert result.exit_code == 0, result.output
        outcome = json.loads(result.output)
        assert outcome["dry_run"] is True
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_rejects_stale_source(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        # The source moves after framing.
        de_path.write_text(
            de_path.read_text(encoding="utf-8") + '\n# %% [markdown] lang="de"\n#\n# Neu\n',
            encoding="utf-8",
            newline="\n",
        )

        result = _invoke_accept(cli_runner, de_path, answer, "--json")

        assert result.exit_code == 2
        outcome = json.loads(result.output)
        assert outcome["applied"] is False and outcome["outcome"] == "rejected"
        assert "source_fingerprint" in outcome["reason"]
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_rejects_partial_coverage(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        # Answer only the title: the translated slide cell goes unanswered.
        header_idx = next(
            c["index"] for c in task_payload["deck"]["cells"] if c["kind"] == "header"
        )
        answer["translations"] = [
            row for row in answer["translations"] if row["index"] == header_idx
        ]

        result = _invoke_accept(cli_runner, de_path, answer, "--json")

        assert result.exit_code == 2
        reason = json.loads(result.output)["reason"]
        assert "cover" in reason or "missing" in reason
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_rejects_unknown_index(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        answer["translations"][0]["index"] = 99

        result = _invoke_accept(cli_runner, de_path, answer)

        assert result.exit_code == 2
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_rejects_body_with_cell_delimiter(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        body_row = next(r for r in answer["translations"] if r["index"] != 0)
        body_row["body"] = "# %% [markdown]\n#\n# smuggled cell"

        result = _invoke_accept(cli_runner, de_path, answer)

        assert result.exit_code == 2
        assert "delimiter" in result.output or "delimiter" in result.stderr
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_rejects_multiline_title(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        answer["translations"][0]["body"] = "Title EN\nsecond line"

        result = _invoke_accept(cli_runner, de_path, answer)

        assert result.exit_code == 2
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_twin_appearing_after_framing_is_stale(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        _write(tmp_path / "slides_x.en.py", '# %% [markdown] lang="en"\n#\n# other\n\n')

        stale = _invoke_accept(cli_runner, de_path, answer)
        assert stale.exit_code == 2
        assert (tmp_path / "slides_x.en.py").read_text(encoding="utf-8") != en

        forced = _invoke_accept(cli_runner, de_path, answer, "--force")
        assert forced.exit_code == 0, forced.output
        assert (tmp_path / "slides_x.en.py").read_text(encoding="utf-8") == en

    def test_rejects_wrong_direction_echo(self, cli_runner, tmp_path, monkeypatch):
        de, en = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        task_payload = json.loads(_invoke_task(cli_runner, de_path).output)
        answer = _build_answer(task_payload, _mirror_translator(de, en))
        answer["target_lang"] = "de"

        result = _invoke_accept(cli_runner, de_path, answer)

        assert result.exit_code == 2
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_rejects_malformed_answer_document(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)

        result = _invoke_accept(cli_runner, de_path, {"schema": 1, "translations": "nope"})

        assert result.exit_code == 2
        assert not (tmp_path / "slides_x.en.py").exists()


# --------------------------------------------------------------------------
# report — the bare default verb
# --------------------------------------------------------------------------


class TestReportDefault:
    def test_twin_absent_points_at_task_without_key(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)

        from clm.cli.main import cli

        result = cli_runner.invoke(cli, ["slides", "translate", str(de_path)])

        assert result.exit_code == 1
        assert "task" in result.output
        assert "2 translatable" in result.output  # the markdown slide + the title
        assert not (tmp_path / "slides_x.en.py").exists()

    def test_twin_absent_json_carries_counts_and_pointer(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)

        from clm.cli.main import cli

        result = cli_runner.invoke(cli, ["slides", "translate", str(de_path), "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["action"] == "cold-start"
        assert payload["cells_translatable"] == 2  # the markdown slide + the title
        assert payload["verbs"]["task"].endswith("--json") or "task" in payload["verbs"]

    def test_bootstrap_alias_becomes_report(self, cli_runner, tmp_path, monkeypatch):
        de, _ = _split(_DECK)
        de_path = _write(tmp_path / "slides_x.de.py", de)
        _forbid_key(monkeypatch)

        from clm.cli.main import cli

        result = cli_runner.invoke(cli, ["slides", "bootstrap", str(de_path)])

        assert result.exit_code == 1
        assert "task" in result.output
        assert not (tmp_path / "slides_x.en.py").exists()
