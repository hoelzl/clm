"""Tests for ``clm slides assign-ids accept`` (#963) and the verb group.

The framing is the existing ``assign-ids run --report-only
--report-refusals --context --json`` worklist (unchanged, #963
acceptance); ``accept`` takes {file, line, title, body} rows, re-checks
each cell against the live file, slugs the titles through the engine's
own slugifier, keeps split halves consistent, and stamps atomically.
The former ``--llm-suggest`` Ollama flag is gone.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from click.testing import CliRunner

from clm.slides.agent_task import VALIDATORS

# --------------------------------------------------------------------------
# Fixtures — a deck whose headingless, extractable slides soft-refuse
# --------------------------------------------------------------------------

# No headings, no bullets — prose-free cells the extractors refuse.
_DECK_HALF = '# %% [markdown] lang="{lang}" tags=["slide"]\n#\n# {body}\n\n'

_DE_FILE_BODY = "37 + 5 ??"
_EN_FILE_BODY = "!! (wow)"


def _split_pair(tmp_path: Path) -> tuple[Path, Path]:
    de = tmp_path / "slides_pair.de.py"
    en = tmp_path / "slides_pair.en.py"
    de.write_text(_DECK_HALF.format(lang="de", body=_DE_FILE_BODY), encoding="utf-8", newline="\n")
    en.write_text(_DECK_HALF.format(lang="en", body=_EN_FILE_BODY), encoding="utf-8", newline="\n")
    return de, en


def _invoke(runner: CliRunner, *args: str):
    from clm.cli.main import cli

    return runner.invoke(cli, ["slides", "assign-ids", *args])


def _solo_deck(tmp_path: Path, body: str = "37 + 5 ??") -> Path:
    """An unsplit deck (no .de/.en tag): no pair constraint applies."""
    deck = tmp_path / "solo.py"
    deck.write_text(_DECK_HALF.format(lang="de", body=body), encoding="utf-8", newline="\n")
    return deck


def _worklist(runner: CliRunner, path: Path) -> dict:
    result = _invoke(
        runner,
        "run",
        str(path),
        "--report-only",
        "--report-refusals",
        "--context",
        "--json",
    )
    assert result.exit_code in (1, 2), result.output  # refusals = work pending
    return json.loads(result.output)


def _answer_from(worklist: dict, title: str = "Mystery Math Slide") -> dict:
    rows = []
    for entry in worklist["refusals"]:
        rows.append(
            {
                "file": entry["file"],
                "line": entry["line"],
                "title": title,
                "body": entry["context"]["body"],
            }
        )
    return {"schema": 1, "answers": rows}


def _accept(runner: CliRunner, path: Path, answer: dict, *extra: str):
    answers_file = path.parent / "answers.json"
    answers_file.write_text(json.dumps(answer), encoding="utf-8")
    return _invoke(runner, "accept", str(path), "--answers", str(answers_file), *extra)


_SLIDE_ID_RE = re.compile(r'slide_id="([^"]*)"')


def _slide_ids(path: Path) -> list[str]:
    return _SLIDE_ID_RE.findall(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The framing is unchanged (acceptance criterion)
# --------------------------------------------------------------------------


def test_refusal_worklist_json_unchanged(tmp_path: Path):
    de, _ = _split_pair(tmp_path)
    runner = CliRunner()
    worklist = _worklist(runner, de)
    assert worklist["soft_refusals"] == 1
    entry = worklist["refusals"][0]
    assert entry["severity"] == "soft"
    assert entry["file"].endswith("slides_pair.de.py")
    assert entry["context"]["body"] == f"#\n# {_DE_FILE_BODY}"


# --------------------------------------------------------------------------
# run — the bare default verb keeps minting
# --------------------------------------------------------------------------


def test_bare_assign_ids_still_runs_the_minting(tmp_path: Path):
    deck = tmp_path / "solo.py"
    deck.write_text(
        '# %% [markdown] lang="de" tags=["slide"]\n#\n# ## Einführung\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = _invoke(runner, str(deck))
    assert result.exit_code == 0, result.output
    assert 'slide_id="einfuehrung"' in deck.read_text(encoding="utf-8")


def test_llm_suggest_flag_removed(tmp_path: Path):
    deck = tmp_path / "solo.py"
    deck.write_text('# %% [markdown] tags=["slide"]\n#\n# ## A\n', encoding="utf-8")
    runner = CliRunner()
    result = _invoke(runner, "run", str(deck), "--llm-suggest")
    assert result.exit_code != 0
    assert "No such option" in result.output


def test_assign_ids_cli_never_imports_ollama_client_at_top_level():
    """#963 acceptance: no Ollama import on the default path."""
    import clm.cli.commands.slides.assign_ids as cli_module

    tree = ast.parse(Path(cli_module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and "ollama" in node.module:
            raise AssertionError(f"top-level import of {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "ollama" not in alias.name


# --------------------------------------------------------------------------
# accept
# --------------------------------------------------------------------------


class TestAssignIdsAccept:
    def test_accept_stamps_ids_from_titles(self, tmp_path: Path):
        deck = _solo_deck(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        result = _accept(runner, deck, _answer_from(worklist, "Mystery Math Slide"))
        assert result.exit_code == 0, result.output
        assert _slide_ids(deck) == ["mystery-math-slide"]

    def test_accept_rejects_one_sided_answer_on_split_half(self, tmp_path: Path):
        """A split half is pair-atomic: an answer covering only one half's
        refusal is refused — frame the pair (the directory) and answer both
        cells with the same title."""
        de, en = _split_pair(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, de)  # single-half framing
        result = _accept(runner, de, _answer_from(worklist, "Odd Expressions"))
        assert result.exit_code == 2, result.output
        assert "different slide_id sequences" in result.output
        assert _slide_ids(de) == [] and _slide_ids(en) == []

    def test_accept_pair_consistency_same_title(self, tmp_path: Path):
        de, en = _split_pair(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, tmp_path)
        result = _accept(runner, tmp_path, _answer_from(worklist, "Odd Expressions"))
        assert result.exit_code == 0, result.output
        assert _slide_ids(de) == _slide_ids(en) == ["odd-expressions"]

    def test_accept_pair_divergence_rejected(self, tmp_path: Path):
        de, en = _split_pair(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, tmp_path)
        answer = _answer_from(worklist, "Odd Expressions")
        # Sabotage one half's title.
        for row in answer["answers"]:
            if row["file"].endswith(".de.py"):
                row["title"] = "A Different Title"
        result = _accept(runner, tmp_path, answer)
        assert result.exit_code == 2, result.output
        assert "different slide_id sequences" in result.output
        assert _slide_ids(de) == [] and _slide_ids(en) == []  # nothing written

    def test_accept_pair_swap_rejected(self, tmp_path: Path):
        """Same slug set, swapped order per half — the ordered-sequence guard
        catches what a set comparison would let through (#162 corruption)."""
        de = tmp_path / "slides_swap.de.py"
        en = tmp_path / "slides_swap.en.py"
        de.write_text(
            '# %% [markdown] lang="de" tags=["slide"]\n#\n# aa ??\n\n'
            '# %% [markdown] lang="de" tags=["slide"]\n#\n# bb ??\n',
            encoding="utf-8",
            newline="\n",
        )
        en.write_text(
            '# %% [markdown] lang="en" tags=["slide"]\n#\n# !! aa\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n#\n# !! bb\n',
            encoding="utf-8",
            newline="\n",
        )
        runner = CliRunner()
        worklist = _worklist(runner, tmp_path)
        assert len(worklist["refusals"]) == 4
        by_file_line = {(e["file"], e["line"]): e["context"]["body"] for e in worklist["refusals"]}
        rows = []
        for (file, line), body in by_file_line.items():
            title = "Alpha Slide" if (line == 1) != file.endswith(".en.py") else "Beta Slide"
            rows.append({"file": file, "line": line, "title": title, "body": body})
        result = _accept(runner, tmp_path, {"schema": 1, "answers": rows})
        assert result.exit_code == 2, result.output
        assert "different slide_id sequences" in result.output

    def test_accept_comment_token_deck(self, tmp_path: Path):
        """A `//` deck (C#): boundary detection needs the deck's own comment
        token — the framing and the accept both parse the cell."""
        cs = tmp_path / "solo.cs"
        cs.write_text(
            '// %% [markdown] lang="en" tags=["slide"]\n//\n// 37 + 5\n',
            encoding="utf-8",
            newline="\n",
        )
        runner = CliRunner()
        worklist = _worklist(runner, cs)
        assert worklist["refusals"][0]["context"] is not None
        result = _accept(runner, cs, _answer_from(worklist, "Cee Sharp Mystery"))
        assert result.exit_code == 0, result.output
        assert _slide_ids(cs) == ["cee-sharp-mystery"]

    def test_accept_duplicate_cell_rows_rejected(self, tmp_path: Path):
        """The same cell named through two path spellings is a duplicate."""
        deck = _solo_deck(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        entry = worklist["refusals"][0]
        row = {
            "file": entry["file"],
            "line": entry["line"],
            "title": "Once Only",
            "body": entry["context"]["body"],
        }
        answer = {"schema": 1, "answers": [row, {**row, "file": str(Path(row["file"]).resolve())}]}
        result = _accept(runner, deck, answer)
        assert result.exit_code == 2, result.output
        assert "duplicate row" in result.output

    def test_accept_body_mismatch_rejected(self, tmp_path: Path):
        deck = _solo_deck(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        answer = _answer_from(worklist)
        answer["answers"][0]["body"] = "# totally different body"
        result = _accept(runner, deck, answer)
        assert result.exit_code == 2, result.output
        assert "body echo mismatch" in result.output
        assert _slide_ids(deck) == []

    def test_accept_already_fixed_cell_rejected(self, tmp_path: Path):
        deck = _solo_deck(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        # Someone hand-fixes the cell after framing.
        deck.write_text(
            _DECK_HALF.format(lang="de", body=_DE_FILE_BODY).replace(
                'tags=["slide"]', 'tags=["slide"] slide_id="hand-written"'
            ),
            encoding="utf-8",
            newline="\n",
        )
        result = _accept(runner, deck, _answer_from(worklist))
        assert result.exit_code == 2, result.output
        assert "already carries" in result.output

    def test_accept_unslugifiable_title_rejected(self, tmp_path: Path):
        deck = _solo_deck(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        result = _accept(runner, deck, _answer_from(worklist, "!!! ???"))
        assert result.exit_code == 2, result.output
        assert "empty slug" in result.output

    def test_accept_scope_guard(self, tmp_path: Path):
        _split_pair(tmp_path)
        other_dir = tmp_path / "elsewhere"
        other_dir.mkdir()
        other = other_dir / "other.py"
        other.write_text(_DECK_HALF.format(lang="de", body="zzz"), encoding="utf-8", newline="\n")
        runner = CliRunner()
        worklist = _worklist(runner, tmp_path)
        answer = _answer_from(worklist)
        result = _accept(runner, other_dir, answer)  # rows outside the scope
        assert result.exit_code == 2, result.output
        assert "outside PATH" in result.output

    def test_accept_dry_run_writes_nothing(self, tmp_path: Path):
        deck = _solo_deck(tmp_path)
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        result = _accept(runner, deck, _answer_from(worklist), "--dry-run")
        assert result.exit_code == 0, result.output
        assert _slide_ids(deck) == []

    def test_accept_collision_with_existing_id_resolved(self, tmp_path: Path):
        deck = tmp_path / "solo.py"
        deck.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "#\n"
            "# ## Vorhanden\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "#\n"
            "# ---\n",
            encoding="utf-8",
            newline="\n",
        )
        runner = CliRunner()
        worklist = _worklist(runner, deck)
        result = _accept(runner, deck, _answer_from(worklist, "Intro"))
        assert result.exit_code == 0, result.output
        ids = _slide_ids(deck)
        assert ids == ["intro", "intro-2"]


def test_assign_ids_titles_validator_registered():
    import clm.slides.assign_ids_accept  # noqa: F401 — registration at import

    assert "assign-ids-titles" in VALIDATORS.names()
