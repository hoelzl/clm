"""CLI tests for the ``clm slides polish`` agent-toolkit verbs (#962).

``task`` frames the notes cleanup as a JSON task document (no model, no API
key); ``accept`` validates the answer's shape, freshness, and coverage and
writes through the ordinary narrative writer — byte-identical to what the
in-process ``autopilot`` path writes; bare ``polish`` (the default ``report``
verb) counts the polishable notes and points at ``task``.

Regression tests for #962: polishing a deck's notes must succeed with **no
API key** and **no ``[summarize]`` extra** via task/accept, with
``--polish-level`` semantics preserved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.slides.agent_task import VALIDATORS

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

_DE_HALF = (
    '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
    "#\n"
    "# ## Einführung\n"
    "#\n"
    "# - Erster Punkt\n"
    "\n"
    '# %% [markdown] lang="de" tags=["notes"]\n'
    "#\n"
    "# - rohe Notizen für die Einführung\n"
    "\n"
    '# %% [markdown] lang="de" tags=["slide"]\n'
    "#\n"
    "# ## Ohne ID\n"
    "#\n"
    "# - Punkt\n"
    "\n"
    '# %% [markdown] lang="de" tags=["notes"]\n'
    "#\n"
    "# - Notizen ohne id\n"
    "\n"
    "# %% [code]\n"
    "shared = 1\n"
    "\n"
)

_EN_HALF = (
    '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n'
    "#\n"
    "# ## Introduction\n"
    "#\n"
    "# - First point\n"
    "\n"
    '# %% [markdown] lang="en" tags=["slide"]\n'
    "#\n"
    "# ## Without ID\n"
    "#\n"
    "# - Point\n"
    "\n"
    "# %% [code]\n"
    "shared = 1\n"
    "\n"
)

_NOTES_FREE_DECK = (
    '# %% [markdown] lang="de" tags=["slide"] slide_id="solo"\n#\n# ## Nur eine Folie\n\n'
)

# Two slides sharing one slide_id (a copy-paste error nothing upstream
# forbids) — the handle resolution must still frame an answerable task.
_DUPLICATE_ID_DECK = (
    '# %% [markdown] lang="de" tags=["slide"] slide_id="s1"\n#\n# ## Alpha\n\n'
    '# %% [markdown] lang="de" tags=["notes"]\n#\n# - Notizen Alpha\n\n'
    '# %% [markdown] lang="de" tags=["slide"] slide_id="s1"\n#\n# ## Beta\n\n'
    '# %% [markdown] lang="de" tags=["notes"]\n#\n# - Notizen Beta\n\n'
)

# A deck with the j2 header macro: the header occupies group index 0, so
# the slides run at indexes 1+ (both in framing and in the writer).
_HEADER_DECK = (
    "# j2 from 'macros.j2' import header\n"
    '# {{ header("Titel DE", "Title EN") }}\n\n'
    '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n#\n# ## Einführung\n\n'
    '# %% [markdown] lang="de" tags=["notes"]\n#\n# - rohe Notizen\n\n'
    '# %% [markdown] lang="de" tags=["slide"]\n#\n# ## Ohne ID\n\n'
    '# %% [markdown] lang="de" tags=["notes"]\n#\n# - zweite Notizen\n\n'
)


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    de = tmp_path / "deck.de.py"
    en = tmp_path / "deck.en.py"
    de.write_text(_DE_HALF, encoding="utf-8", newline="\n")
    en.write_text(_EN_HALF, encoding="utf-8", newline="\n")
    return de, en


def _invoke(runner: CliRunner, *args: str):
    from clm.cli.main import cli

    return runner.invoke(cli, ["slides", "polish", *args])


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _invoke_task(runner: CliRunner, source: Path, *extra: str) -> dict:
    result = _invoke(runner, "task", str(source), "--lang", "de", *extra)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _build_answer(task_payload: dict, bodies: dict[str, str] | None = None) -> dict:
    answer = {
        "schema": task_payload["schema"],
        "lang": task_payload["lang"],
        "polish_level": task_payload["polish_level"],
        "source_fingerprint": task_payload["source_fingerprint"],
        "twin_fingerprint": task_payload.get("twin_fingerprint"),
        "polished": [],
    }
    for row in task_payload["slides"]:
        body = (bodies or {}).get(row["handle"], f"polished {row['handle']}")
        answer["polished"].append({"handle": row["handle"], "body": body})
    return answer


def _accept(
    runner: CliRunner,
    source: Path,
    answer: dict,
    *extra: str,
) -> object:
    """Accept without --lang: the answer's echo carries the framing (the
    invocation shape issue #962 specifies)."""
    answer_file = source.parent / "answer.json"
    answer_file.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return _accept_raw(runner, source, str(answer_file), *extra)


def _accept_raw(runner: CliRunner, source: Path, answer_src: str, *extra: str) -> object:
    return _invoke(runner, "accept", str(source), "--answer", answer_src, *extra)


# --------------------------------------------------------------------------
# report (the bare default verb)
# --------------------------------------------------------------------------


class TestPolishReport:
    def test_bare_polish_runs_report_and_writes_nothing(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        before = de.read_bytes()
        runner = CliRunner()
        result = _invoke(runner, str(de), "--lang", "de")
        assert result.exit_code == 1, result.output  # polishable notes = work pending
        assert "2" in result.output and "task" in result.output
        assert de.read_bytes() == before  # read-only

    def test_report_json_envelope(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        result = _invoke(runner, "report", str(de), "--lang", "de", "--json")
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["schema"] == 1
        assert payload["tool"] == "polish"
        assert payload["verb"] == "report"
        assert payload["slides_with_notes"] == 2
        assert payload["slides_total"] == 2
        assert "task" in payload["verbs"]

    def test_report_clean_when_no_notes(self, tmp_path: Path):
        deck = tmp_path / "deck.de.py"
        deck.write_text(_NOTES_FREE_DECK, encoding="utf-8", newline="\n")
        runner = CliRunner()
        result = _invoke(runner, str(deck), "--lang", "de")
        assert result.exit_code == 0, result.output
        assert "No notes" in result.output


# --------------------------------------------------------------------------
# task — the framed notes cleanup (read-only, model-free)
# --------------------------------------------------------------------------


class TestPolishTask:
    def test_task_frames_rows_with_handles_and_context(self, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        runner = CliRunner()
        payload = _invoke_task(runner, de)
        assert payload["tool"] == "polish"
        assert payload["verb"] == "task"
        assert payload["lang"] == "de"
        assert payload["polish_level"] == "standard"
        assert payload["source"] == str(de)
        assert payload["twin"] == str(en)
        assert payload["source_fingerprint"] == _fingerprint(de)
        assert payload["twin_fingerprint"] == _fingerprint(en)
        handles = [row["handle"] for row in payload["slides"]]
        assert handles == ["id:intro", "pos:1"]
        intro = payload["slides"][0]
        assert intro["index"] == 0
        assert "rohe Notizen" in intro["notes"]
        assert "Einführung" in intro["slide_context"]
        assert "Introduction" in intro["twin_context"]  # both language sides
        assert payload["validator"] == "polish-notes"
        assert payload["answer_schema"] is not None
        # The level prompt is the framed instruction seam.
        assert payload["level_prompt"] is not None
        assert "notes" in payload["level_prompt"].lower()

    def test_task_without_twin_has_no_twin_fields(self, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        en.unlink()
        runner = CliRunner()
        payload = _invoke_task(runner, de)
        assert payload["twin"] is None
        assert payload["twin_fingerprint"] is None
        assert "twin_context" not in payload["slides"][0]

    def test_task_slides_range_narrows_framing(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        payload = _invoke_task(runner, de, "--slides-range", "0-0")
        assert [row["handle"] for row in payload["slides"]] == ["id:intro"]

    def test_task_verbatim_level_frames_passthrough(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        payload = _invoke_task(runner, de, "--polish-level", "verbatim")
        assert payload["polish_level"] == "verbatim"
        assert payload["level_prompt"] is None  # verbatim has no prompt file
        assert "verbatim" in payload["instructions"].lower()

    def test_task_unavailable_when_no_notes(self, tmp_path: Path):
        deck = tmp_path / "deck.de.py"
        deck.write_text(_NOTES_FREE_DECK, encoding="utf-8", newline="\n")
        runner = CliRunner()
        result = _invoke(runner, "task", str(deck), "--lang", "de")
        assert result.exit_code == 2, result.output


# --------------------------------------------------------------------------
# accept — the validated write
# --------------------------------------------------------------------------


class TestPolishAccept:
    def test_accept_writes_polished_notes(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload)
        result = _accept(runner, de, answer)
        assert result.exit_code == 0, result.output
        text = de.read_text(encoding="utf-8")
        assert "# - polished id:intro" in text
        assert "# - polished pos:1" in text
        assert "# - Erster Punkt" in text  # slide content untouched

    def test_accept_json_outcome_envelope(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        result = _accept(runner, de, _build_answer(task_payload), "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["schema"] == 1
        assert payload["tool"] == "polish"
        assert payload["verb"] == "accept"
        assert payload["applied"] is True
        assert payload["slides_polished"] == 2
        assert payload["written"] == [str(de)]

    def test_accept_stale_source_rejected_wholesale(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        stale_text = _DE_HALF + "# extra line\n"
        de.write_text(stale_text, encoding="utf-8", newline="\n")
        result = _accept(runner, de, _build_answer(task_payload))
        assert result.exit_code == 2, result.output
        assert "source_fingerprint" in result.output
        assert de.read_text(encoding="utf-8") == stale_text  # refused wholesale

    def test_accept_twin_drift_rejected(self, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        en.write_text(_EN_HALF + "# drift\n", encoding="utf-8", newline="\n")
        result = _accept(runner, de, _build_answer(task_payload))
        assert result.exit_code == 2, result.output
        assert "twin_fingerprint" in result.output
        assert de.read_text(encoding="utf-8") == _DE_HALF

    def test_accept_missing_row_rejected(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload)
        answer["polished"] = answer["polished"][:1]
        result = _accept(runner, de, answer)
        assert result.exit_code == 2, result.output
        assert "exactly" in result.output

    def test_accept_unknown_handle_rejected(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload)
        answer["polished"].append({"handle": "id:ghost", "body": "boo"})
        result = _accept(runner, de, answer)
        assert result.exit_code == 2, result.output

    def test_accept_bad_schema_rejected(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload)
        answer["schema"] = 99
        result = _accept(runner, de, answer)
        assert result.exit_code == 2, result.output

    def test_accept_cell_delimiter_body_rejected(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload, bodies={"id:intro": "# %% smuggled cell"})
        result = _accept(runner, de, answer)
        assert result.exit_code == 2, result.output
        assert "delimiter" in result.output
        assert de.read_text(encoding="utf-8") == _DE_HALF

    def test_accept_lang_contradiction_rejected(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload)
        result = _accept(runner, de, answer, "--lang", "en")
        assert result.exit_code == 2, result.output
        assert "lang mismatch" in result.output

    def test_accept_level_contradiction_rejected(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        answer = _build_answer(task_payload)
        result = _accept(runner, de, answer, "--polish-level", "heavy")
        assert result.exit_code == 2, result.output
        assert "polish_level mismatch" in result.output

    def test_accept_dry_run_writes_nothing(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        result = _accept(runner, de, _build_answer(task_payload), "--dry-run")
        assert result.exit_code == 0, result.output
        assert "dry-run" in result.output.lower() or "dry run" in result.output.lower()
        assert de.read_text(encoding="utf-8") == _DE_HALF

    def test_accept_output_writes_copy(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        out = tmp_path / "polished.de.py"
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        result = _accept(runner, de, _build_answer(task_payload), "-o", str(out))
        assert result.exit_code == 0, result.output
        assert de.read_text(encoding="utf-8") == _DE_HALF  # source untouched
        assert "polished id:intro" in out.read_text(encoding="utf-8")

    def test_accept_range_coverage_uses_framed_subset(self, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de, "--slides-range", "0-0")
        result = _accept(runner, de, _build_answer(task_payload), "--slides-range", "0-0")
        assert result.exit_code == 0, result.output
        text = de.read_text(encoding="utf-8")
        assert "polished id:intro" in text
        assert "Notizen ohne id" in text  # the unframed group untouched

    def test_duplicate_slide_ids_still_frame_an_answerable_task(self, tmp_path: Path):
        deck = tmp_path / "deck.de.py"
        deck.write_text(_DUPLICATE_ID_DECK, encoding="utf-8", newline="\n")
        runner = CliRunner()
        payload = _invoke_task(runner, deck)
        # First group keeps the id: handle; the later duplicate falls back to pos:.
        assert [row["handle"] for row in payload["slides"]] == ["id:s1", "pos:1"]
        result = _accept(runner, deck, _build_answer(payload))
        assert result.exit_code == 0, result.output
        text = deck.read_text(encoding="utf-8")
        assert "polished id:s1" in text
        assert "polished pos:1" in text

    def test_j2_header_deck_indexes_slide_groups_from_one(self, tmp_path: Path):
        deck = tmp_path / "deck.de.py"
        deck.write_text(_HEADER_DECK, encoding="utf-8", newline="\n")
        runner = CliRunner()
        payload = _invoke_task(runner, deck)
        assert [row["handle"] for row in payload["slides"]] == ["id:intro", "pos:2"]
        assert payload["slides"][0]["index"] == 1
        result = _accept(runner, deck, _build_answer(payload))
        assert result.exit_code == 0, result.output
        text = deck.read_text(encoding="utf-8")
        assert "polished id:intro" in text
        assert "polished pos:2" in text

    def test_twin_context_aligns_across_header_asymmetry(self, tmp_path: Path):
        # Header-less source vs header-ful twin: naive index matching would
        # pair every slide with its neighbour (regression test for the
        # review finding — sequence alignment, not index alignment).
        de = tmp_path / "deck.de.py"
        de.write_text(
            _DUPLICATE_ID_DECK.replace('slide_id="s1"', ""), encoding="utf-8", newline="\n"
        )
        en = tmp_path / "deck.en.py"
        en.write_text(
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Alpha", "Alpha") }}\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n#\n# ## Alpha EN\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n#\n# ## Beta EN\n\n',
            encoding="utf-8",
            newline="\n",
        )
        runner = CliRunner()
        payload = _invoke_task(runner, de)
        contexts = {row["handle"]: row.get("twin_context") for row in payload["slides"]}
        assert "Alpha EN" in contexts["pos:0"]
        assert "Beta EN" in contexts["pos:1"]

    def test_accept_refuses_to_clobber_the_twin(self, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        runner = CliRunner()
        task_payload = _invoke_task(runner, de)
        result = _accept(runner, de, _build_answer(task_payload), "-o", str(en))
        assert result.exit_code == 2, result.output
        assert "twin" in result.output
        assert en.read_text(encoding="utf-8") == _EN_HALF  # untouched
        assert de.read_text(encoding="utf-8") == _DE_HALF


# --------------------------------------------------------------------------
# Engine parity — accept writes byte-identically to the autopilot engine
# --------------------------------------------------------------------------


def test_accept_bytes_match_autopilot_engine(tmp_path: Path):
    """The #961 differential, ported: accept's write is byte-identical to what
    ``write_narrative`` (the autopilot engine) produces for the same map."""
    from clm.core.utils.prog_lang_utils import comment_token_for_path
    from clm.notebooks.slide_writer import update_narrative

    de, _ = _write_pair(tmp_path)
    runner = CliRunner()
    task_payload = _invoke_task(runner, de)
    bodies = {row["handle"]: f"polished: {row['notes']}" for row in task_payload["slides"]}
    _accept(runner, de, _build_answer(task_payload, bodies))

    engine_src = tmp_path / "engine.de.py"
    engine_src.write_text(_DE_HALF, encoding="utf-8", newline="\n")
    index_map = {row["index"]: bodies[row["handle"]] for row in task_payload["slides"]}
    expected = update_narrative(
        _DE_HALF,
        index_map,
        "de",
        tag="notes",
        comment_token=comment_token_for_path(engine_src),
    )
    assert de.read_bytes() == expected.encode("utf-8")  # byte-level parity


def test_polish_notes_validator_registered():
    import clm.slides.polish_task  # noqa: F401 — registration happens at import

    assert "polish-notes" in VALIDATORS.names()


def test_task_accept_path_needs_no_summarize_extra():
    """The toolkit verbs import cleanly without the ``[summarize]`` extra —
    the polish group is registered unconditionally (#962 acceptance)."""
    import clm.cli.commands.slides.polish as polish_module

    assert polish_module.polish_group.name == "polish"
    assert "autopilot" in polish_module.polish_group.commands
