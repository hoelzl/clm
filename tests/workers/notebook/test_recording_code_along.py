"""The ``recording-code-along`` output kind (#1023).

The notebook a trainer records: it must look exactly like the public
code-along notebook, plus ``metadata.clm.typing`` on every code cell whose
completed source differs, so the jupyterlab-clm-typing extension can replay
the code-along -> completed edit as simulated typing.
"""

import json

import pytest
from nbformat import NotebookNode

from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import (
    CodeAlongOutput,
    RecordingCodeAlongOutput,
    create_output_spec,
)

from .test_notebook_processor import make_cell, make_notebook_node, make_payload

KIND = "recording-code-along"


def _deck() -> NotebookNode:
    """A bilingual deck exercising every cell role the kind must handle."""
    return make_notebook_node(
        [
            make_cell("markdown", "# Titel", lang="de"),
            make_cell("markdown", "# Title", lang="en"),
            make_cell("code", "import math", tags=["keep"]),
            make_cell("code", "x = 6 * 7"),  # answer cell -> blanked
            make_cell("code", "def f(x):\n    pass", tags=["start"]),
            make_cell("code", "def f(x):\n    return x + 1", tags=["completed"]),
            make_cell("code", "y = f(1)  # alternative", tags=["alt"]),
            make_cell("markdown", "Sprechernotiz", tags=["notes"], lang="de"),
            make_cell("markdown", "Speaker note", tags=["notes"], lang="en"),
            make_cell("markdown", "Read this aloud", tags=["voiceover"], lang="en"),
            make_cell("code", 'print("de")', lang="de"),
            make_cell("code", 'print("en")', lang="en"),
            make_cell("code", ""),  # already empty -> nothing to type
        ]
    )


async def _render(kind: str, language: str, notebook: NotebookNode | None = None) -> dict:
    spec = create_output_spec(kind, format="notebook", language=language)
    processor = NotebookProcessor(spec)
    payload = make_payload("", kind=kind, format_="notebook", language=language)
    # The same two steps process_notebook runs after parsing the source:
    # filter/annotate the cells, then serialize to the ipynb format.
    processed = await processor._process_notebook_node(notebook or _deck(), payload)
    text = await processor.create_contents(processed, payload)
    return json.loads(text)


def _src(cell: dict) -> str:
    source = cell["source"]
    return "".join(source) if isinstance(source, list) else source


def _typing(cell: dict) -> dict | None:
    return cell.get("metadata", {}).get("clm", {}).get("typing")


def _without_typing(cell: dict) -> dict:
    cell = json.loads(json.dumps(cell))
    clm = cell.get("metadata", {}).get("clm")
    if clm is not None:
        clm.pop("typing", None)
        if not clm:
            del cell["metadata"]["clm"]
    return cell


def test_factory_creates_recording_code_along_spec():
    """Regression test for #1023: the kind must be known to the worker."""
    spec = create_output_spec(KIND, format="notebook")
    assert isinstance(spec, RecordingCodeAlongOutput)
    assert spec.get_target_subdir_fragment() == "recording-code-along"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["de", "en"])
async def test_typing_metadata_per_cell_role(language):
    nb = await _render(KIND, language)
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    by_start = {_src(c): c for c in code}

    # keep cell: retained, nothing to type
    assert _typing(by_start["import math"]) is None
    # answer cell: blanked, target = completed source
    blank = [c for c in code if _src(c) == "" and _typing(c)]
    assert [_typing(c) for c in blank] == [
        {"start": "", "target": "x = 6 * 7"},
        {"start": "", "target": f'print("{language}")'},
    ]
    # start/completed pair: ONE cell, start source, completed target
    assert _typing(by_start["def f(x):\n    pass"]) == {
        "start": "def f(x):\n    pass",
        "target": "def f(x):\n    return x + 1",
    }
    assert "def f(x):\n    return x + 1" not in by_start
    # alt, notes and voiceover cells are dropped
    sources = [_src(c) for c in nb["cells"]]
    assert not any("alternative" in s or "note" in s.lower() or "aloud" in s for s in sources)
    assert not any("Sprechernotiz" in s for s in sources)
    # an already-empty code cell has nothing to type
    empty = [c for c in code if _src(c) == "" and not _typing(c)]
    assert len(empty) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["de", "en"])
async def test_cells_match_code_along_except_typing_metadata(language):
    """The recorded notebook is shown to students: it must look like code-along."""
    recording = await _render(KIND, language)
    code_along = await _render("code-along", language)
    assert [_without_typing(c) for c in recording["cells"]] == code_along["cells"]
    assert recording["metadata"] == code_along["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["code-along", "completed", "partial", "trainer", "recording"])
async def test_no_other_kind_carries_typing_metadata(kind):
    """Typing targets are solutions: they must never leak into another kind."""
    nb = await _render(kind, "en")
    assert not any(_typing(c) for c in nb["cells"])


@pytest.mark.asyncio
async def test_targets_equal_completed_output_sources():
    """Each target is exactly what the completed notebook shows for that cell."""
    deck = make_notebook_node(
        [
            make_cell("code", "a = 1", tags=["keep"]),
            make_cell("code", "b = a + 1"),
            make_cell("code", "c = ...", tags=["start"]),
            make_cell("code", "c = b * 2", tags=["completed"]),
            make_cell("code", "for i in range(3):\n    print(i)"),
        ]
    )
    # Processing mutates the cells it is given: render each kind from a copy.
    recording = await _render(KIND, "en", make_notebook_node(list(_cells_copy(deck))))
    completed = await _render("completed", "en", make_notebook_node(list(_cells_copy(deck))))
    replayed = [
        (_typing(c) or {}).get("target", _src(c))
        for c in recording["cells"]
        if c["cell_type"] == "code"
    ]
    assert replayed == [_src(c) for c in completed["cells"]]


@pytest.mark.asyncio
async def test_start_without_completed_partner_gets_no_typing():
    """A dangling ``start`` cell (validator territory) must not guess a target."""
    deck = make_notebook_node(
        [
            make_cell("code", "x = ...", tags=["start"]),
            make_cell("code", "y = 2"),
        ]
    )
    nb = await _render(KIND, "en", deck)
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert _typing(code[0]) is None
    assert _typing(code[1]) == {"start": "", "target": "y = 2"}


@pytest.mark.asyncio
async def test_completed_partner_for_other_language_is_skipped():
    """Pairing only considers cells of the output language."""
    deck = make_notebook_node(
        [
            make_cell("code", "s = ...", tags=["start"]),
            make_cell("code", 's = "Hallo"', tags=["completed"], lang="de"),
            make_cell("code", 's = "Hello"', tags=["completed"], lang="en"),
        ]
    )
    for language, target in (("de", 's = "Hallo"'), ("en", 's = "Hello"')):
        nb = await _render(KIND, language, make_notebook_node(list(_cells_copy(deck))))
        code = [c for c in nb["cells"] if c["cell_type"] == "code"]
        assert len(code) == 1
        assert _typing(code[0]) == {"start": "s = ...", "target": target}


def test_code_along_spec_itself_has_no_typing_hook():
    """Guard: the public code-along spec must keep the no-op annotate hook."""
    cells = [make_cell("code", "x = 1")]
    CodeAlongOutput(format="notebook").annotate_cells(cells)
    assert "clm" not in cells[0]["metadata"]


def _cells_copy(nb: NotebookNode):
    for cell in nb["cells"]:
        yield NotebookNode(json.loads(json.dumps(cell)))


@pytest.mark.asyncio
@pytest.mark.parametrize("between_tags", [["keep"], [], ["del"]])
async def test_start_pairs_across_intervening_code_cells(between_tags):
    """Like the validator, ``start`` pairs with the next ``completed`` cell even
    with other code cells (kept, blanked or deleted) in between (review of #1023)."""
    deck = make_notebook_node(
        [
            make_cell("code", "p = ...", tags=["start"]),
            make_cell("code", "q = 1", tags=between_tags),
            make_cell("code", "p = q + 1", tags=["completed"]),
        ]
    )
    nb = await _render(KIND, "en", deck)
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert _typing(code[0]) == {"start": "p = ...", "target": "p = q + 1"}


@pytest.mark.asyncio
async def test_start_does_not_pair_past_another_start():
    deck = make_notebook_node(
        [
            make_cell("code", "a = ...", tags=["start"]),
            make_cell("code", "b = ...", tags=["start"]),
            make_cell("code", "b = 2", tags=["completed"]),
        ]
    )
    nb = await _render(KIND, "en", deck)
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert _typing(code[0]) is None
    assert _typing(code[1]) == {"start": "b = ...", "target": "b = 2"}
