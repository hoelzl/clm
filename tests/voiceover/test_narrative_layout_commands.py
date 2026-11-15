"""The rewriting commands keep a wrapped, nested narrative cell intact (#1162).

``clm slides polish accept``, ``clm voiceover sync`` (merge mode) and
``clm harvest autopilot`` (both through ``merge_notes``) write narrative
text back through ``update_narrative`` / ``update_companion_narrative``.
When the text they are handed carries the deck layout ``clm harvest
accept`` writes -- 80-column wrapping with hanging continuations and one
level of child bullets -- the rewritten deck must keep it: re-writing the
same narrative is byte-identical.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from clm.core.slide_text.slide_parser import parse_slides
from clm.voiceover.aligner import AlignmentResult, SlideNotes
from clm.voiceover.autopilot import merge_notes
from clm.voiceover.harvest_accept import Bullet, _render_body
from clm.voiceover.merge import MergeResult

_BULLETS = [
    Bullet("Ein kurzer Punkt."),
    Bullet(
        "Dieser Punkt ist absichtlich so lang, dass er über die Breite von achtzig "
        "Spalten hinausgeht und deshalb umbrochen werden muss, mit `inline code`.",
        (
            "Ein Unterpunkt, der ebenfalls lang genug ist, um auf mehrere Zeilen "
            "umbrochen zu werden, damit die Einrückung geprüft wird.",
            "kurzes Kind",
        ),
    ),
]


def _body(tag_token: str = "#") -> str:
    return _render_body(_BULLETS, tag_token)


def _reader_text(body: str, token: str = "#") -> str:
    out = []
    for line in body.split("\n"):
        rest = line[len(token) :]
        out.append(rest[1:] if rest.startswith(" ") else rest)
    return "\n".join(out).strip()


def _deck(tag: str) -> str:
    return (
        '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
        "# ## Einführung\n"
        "\n"
        f'# %% [markdown] lang="de" tags=["{tag}"]\n'
        f"{_body()}\n"
        "\n"
        '# %% [markdown] lang="de" tags=["slide"] slide_id="details"\n'
        "# ## Details\n"
    )


def test_body_fixture_is_wrapped_and_nested() -> None:
    lines = _body().split("\n")
    assert any(line.startswith("#   - ") for line in lines)
    assert any(line.startswith("#     ") for line in lines)
    assert any(line.startswith("#   ") and line[4] != "-" for line in lines)


def test_polish_accept_keeps_wrapped_nested_notes(tmp_path: Path) -> None:
    from clm.cli.main import cli

    deck = tmp_path / "deck.de.py"
    deck.write_text(_deck("notes"), encoding="utf-8", newline="\n")
    before = deck.read_bytes()
    runner = CliRunner()
    task = runner.invoke(cli, ["slides", "polish", "task", str(deck), "--lang", "de"])
    assert task.exit_code == 0, task.output
    payload = json.loads(task.output)
    answer = {
        "schema": payload["schema"],
        "lang": payload["lang"],
        "polish_level": payload["polish_level"],
        "source_fingerprint": payload["source_fingerprint"],
        "twin_fingerprint": payload.get("twin_fingerprint"),
        "polished": [
            {"handle": row["handle"], "body": _reader_text(_body())} for row in payload["slides"]
        ],
    }
    answer_file = tmp_path / "answer.json"
    answer_file.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(
        cli, ["slides", "polish", "accept", str(deck), "--answer", str(answer_file)]
    )
    assert result.exit_code == 0, result.output
    assert deck.read_bytes() == before


def _run_merge(slide_file: Path, *, companion: Path | None) -> None:
    slide_groups = parse_slides(slide_file, "de")
    intro = next(sg for sg in slide_groups if sg.cells[0].metadata.slide_id == "intro")
    notes_map = {intro.index: "Transkript"}
    alignment = AlignmentResult(
        slide_notes={intro.index: SlideNotes(slide_index=intro.index, segments=["x"])}
    )
    merged = [
        MergeResult(
            slide_id=f"{slide_file.stem}/{intro.index}", merged_bullets=_reader_text(_body())
        )
    ]
    with patch("clm.voiceover.merge.merge_batch", new=AsyncMock(return_value=merged)):
        asyncio.run(
            merge_notes(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=False,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=companion is not None,
                companion_file=companion,
            )
        )


def test_sync_merge_inline_keeps_wrapped_nested_voiceover(tmp_path: Path) -> None:
    """``voiceover sync`` / ``harvest autopilot`` inline-cell path."""
    slide_file = tmp_path / "slides_test.py"
    slide_file.write_text(_deck("voiceover"), encoding="utf-8", newline="\n")
    before = slide_file.read_bytes()
    _run_merge(slide_file, companion=None)
    assert slide_file.read_bytes() == before


@pytest.mark.parametrize("trailing", ["", "\n"])
def test_sync_merge_companion_keeps_wrapped_nested_voiceover(tmp_path: Path, trailing: str) -> None:
    """``voiceover sync`` / ``harvest autopilot`` companion-file path."""
    slide_file = tmp_path / "slides_test.py"
    slide_file.write_text(
        '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n# ## Einführung\n',
        encoding="utf-8",
        newline="\n",
    )
    companion = tmp_path / "voiceover_test.py"
    companion.write_text(
        f'# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n{_body()}\n{trailing}',
        encoding="utf-8",
        newline="\n",
    )
    expected = f'# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n{_body()}\n'
    _run_merge(slide_file, companion=companion)
    assert companion.read_text(encoding="utf-8") == expected
