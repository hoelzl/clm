"""Narration reaches the model as logical bullets (#1280).

Since #1162 the narrative *writers* share one bullet model
(:func:`~clm.core.slide_text.bullet_wrap.parse_narrative_text`): an indented
line continues the bullet above, an indented ``- `` line is a child bullet.
The text *sent to the model* used to come from ``Cell.text_content()``,
which stripped every comment prefix and joined all lines with one space --
so ``clm slides polish``, ``clm voiceover sync`` and ``clm harvest
autopilot`` handed the LLM one run-on paragraph per cell.

Now the existing narration is rendered as logical bullets: one line per
bullet, children indented two spaces under their parent, wrapped
continuations unwrapped. These tests pin that for ``#`` and ``//`` decks,
inline cells and companion files, and for the three LLM call sites.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from clm.core.slide_text.slide_parser import parse_cells, parse_slides
from clm.slides.voiceover_tools import read_companion_baselines
from clm.voiceover.aligner import AlignmentResult, SlideNotes
from clm.voiceover.autopilot import _extract_baseline, merge_notes
from clm.voiceover.harvest_accept import Bullet, _render_body
from clm.voiceover.merge import MergeResult

_LONG = (
    "Dieser Punkt ist absichtlich so lang, dass er über die Breite von achtzig "
    "Spalten hinausgeht und deshalb umbrochen werden muss, mit `inline code`."
)
_CHILD = (
    "Ein Unterpunkt, der ebenfalls lang genug ist, um auf mehrere Zeilen "
    "umbrochen zu werden, damit die Einrückung geprüft wird."
)
_BULLETS = [
    Bullet("Ein kurzer Punkt."),
    Bullet(_LONG, (_CHILD, "kurzes Kind")),
    Bullet("Noch ein **fetter** Punkt.", ("einziges Kind",)),
]

#: What the model must see for ``_BULLETS``: one line per bullet, children
#: indented under their parent, no 80-column continuation breaks, markdown
#: kept verbatim.
_LOGICAL = (
    "- Ein kurzer Punkt.\n"
    f"- {_LONG}\n"
    f"  - {_CHILD}\n"
    "  - kurzes Kind\n"
    "- Noch ein **fetter** Punkt.\n"
    "  - einziges Kind"
)

_EXT = {"#": "py", "//": "cpp"}


def _body(token: str) -> str:
    """The cell body ``clm harvest accept`` writes (wrapped, nested)."""
    return _render_body(_BULLETS, token)


def _deck(token: str, tag: str) -> str:
    return (
        f'{token} %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
        f"{token} ## Einführung\n"
        "\n"
        f'{token} %% [markdown] lang="de" tags=["{tag}"]\n'
        f"{_body(token)}\n"
        "\n"
        f'{token} %% [markdown] lang="de" tags=["slide"] slide_id="details"\n'
        f"{token} ## Details\n"
    )


def _write_deck(tmp_path: Path, token: str, tag: str, stem: str = "slides_test") -> Path:
    deck = tmp_path / f"{stem}.{_EXT[token]}"
    deck.write_text(_deck(token, tag), encoding="utf-8", newline="\n")
    return deck


@pytest.mark.parametrize("token", ["#", "//"])
def test_fixture_body_is_wrapped_and_nested(token: str) -> None:
    lines = _body(token).split("\n")
    assert any(line.startswith(f"{token}   - ") for line in lines)  # child
    assert any(
        line.startswith(f"{token}   ") and not line.startswith(f"{token}   - ") for line in lines
    )  # continuation


# ---------------------------------------------------------------------------
# The helper and the cell / group readers
# ---------------------------------------------------------------------------


def narrative_prompt_text(text: str) -> str:
    from clm.core.slide_text.bullet_wrap import narrative_prompt_text as helper

    return helper(text)


class TestNarrativePromptText:
    def test_unwraps_and_nests(self) -> None:
        text = "- eins\n  weiter\n  - kind\n    kind weiter\n- zwei"
        assert narrative_prompt_text(text) == "- eins weiter\n  - kind kind weiter\n- zwei"

    def test_plain_lines_become_bullets(self) -> None:
        assert narrative_prompt_text("Erster Gedanke.\nZweiter.") == (
            "- Erster Gedanke.\n- Zweiter."
        )

    def test_blank_runs_collapse_and_markers_are_kept(self) -> None:
        text = "- a\n\n\n**[Revision]**\n- b\n"
        assert narrative_prompt_text(text) == "- a\n\n**[Revision]**\n- b"

    def test_empty(self) -> None:
        assert narrative_prompt_text("") == ""
        assert narrative_prompt_text("\n  \n") == ""


@pytest.mark.parametrize("token", ["#", "//"])
class TestCellAndGroupReaders:
    def test_cell_narrative_text(self, token: str) -> None:
        cells = parse_cells(_deck(token, "voiceover"), token)
        (cell,) = [c for c in cells if c.metadata.is_narrative]
        assert cell.narrative_text() == _LOGICAL

    def test_notes_text_is_logical_bullets(self, tmp_path: Path, token: str) -> None:
        deck = _write_deck(tmp_path, token, "notes")
        (group,) = [g for g in parse_slides(deck, "de") if g.has_notes]
        assert group.notes_text == _LOGICAL

    def test_extract_baseline_is_logical_bullets(self, tmp_path: Path, token: str) -> None:
        deck = _write_deck(tmp_path, token, "voiceover")
        (group,) = [g for g in parse_slides(deck, "de") if g.has_notes]
        assert _extract_baseline(group, "voiceover") == _LOGICAL

    def test_companion_baseline_is_logical_bullets(self, tmp_path: Path, token: str) -> None:
        companion = tmp_path / f"voiceover_test.{_EXT[token]}"
        companion.write_text(
            f'{token} %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n'
            f"{_body(token)}\n",
            encoding="utf-8",
            newline="\n",
        )
        assert read_companion_baselines(companion, "de") == {"intro": _LOGICAL}

    def test_text_content_stays_plain_for_ocr(self, token: str) -> None:
        """OCR matching keeps the flat plain text (#1280 leaves it alone)."""
        cells = parse_cells(_deck(token, "voiceover"), token)
        (cell,) = [c for c in cells if c.metadata.is_narrative]
        assert "\n" not in cell.text_content()


# ---------------------------------------------------------------------------
# The structure reaches each LLM call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["#", "//"])
def test_polish_autopilot_sends_logical_bullets(
    tmp_path: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``clm slides polish autopilot``: the user prompt carries the bullets."""
    from clm.cli.main import cli

    deck = _write_deck(tmp_path, token, "notes", stem="deck.de")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="- poliert"))]
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    with patch("clm.infrastructure.llm.client.build_client", return_value=client):
        result = CliRunner().invoke(
            cli, ["slides", "polish", "autopilot", str(deck), "--lang", "de", "--dry-run"]
        )
    assert result.exit_code == 0, result.output
    user_message = create.await_args.kwargs["messages"][1]["content"]
    assert f"```\n{_LOGICAL}\n```" in user_message


def _run_merge(slide_file: Path, *, companion: Path | None) -> AsyncMock:
    slide_groups = parse_slides(slide_file, "de")
    intro = next(sg for sg in slide_groups if sg.cells[0].metadata.slide_id == "intro")
    alignment = AlignmentResult(
        slide_notes={intro.index: SlideNotes(slide_index=intro.index, segments=["x"])}
    )
    merged = [MergeResult(slide_id=f"{slide_file.stem}/{intro.index}", merged_bullets=_LOGICAL)]
    mock = AsyncMock(return_value=merged)
    with patch("clm.voiceover.merge.merge_batch", new=mock):
        asyncio.run(
            merge_notes(
                slides=slide_file,
                notes_map={intro.index: "Transkript"},
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=True,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=companion is not None,
                companion_file=companion,
            )
        )
    return mock


@pytest.mark.parametrize("token", ["#", "//"])
def test_sync_merge_inline_sends_logical_bullets(tmp_path: Path, token: str) -> None:
    """``voiceover sync`` / ``harvest autopilot`` (``merge_notes``), inline cells."""
    deck = _write_deck(tmp_path, token, "voiceover")
    mock = _run_merge(deck, companion=None)
    (slide_input,) = mock.await_args.args[0]
    assert slide_input.baseline == _LOGICAL


@pytest.mark.parametrize("token", ["#", "//"])
def test_sync_merge_companion_sends_logical_bullets(tmp_path: Path, token: str) -> None:
    """``voiceover sync`` / ``harvest autopilot`` (``merge_notes``), companion file."""
    ext = _EXT[token]
    deck = tmp_path / f"slides_test.{ext}"
    deck.write_text(
        f'{token} %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n{token} ## Einführung\n',
        encoding="utf-8",
        newline="\n",
    )
    companion = tmp_path / f"voiceover_test.{ext}"
    companion.write_text(
        f'{token} %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n{_body(token)}\n',
        encoding="utf-8",
        newline="\n",
    )
    mock = _run_merge(deck, companion=companion)
    (slide_input,) = mock.await_args.args[0]
    assert slide_input.baseline == _LOGICAL
