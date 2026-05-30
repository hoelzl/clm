"""Worker-side cross-reference rewrite tests (Issue #17).

The worker performs only a mechanical string substitution in
``_process_markdown_cell_contents`` using the href map resolved at
payload-construction time. These tests confirm the rewrite fires for
markdown cells and honours the empty-href "drop the link" rule.
"""

from __future__ import annotations

import pytest
from nbformat import NotebookNode

from clm.infrastructure.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import CompletedOutput


def _markdown_cell(source: str) -> NotebookNode:
    return NotebookNode(
        {
            "cell_type": "markdown",
            "source": source,
            "metadata": {"tags": []},
        }
    )


def _payload(cross_references: dict[str, str], format_: str = "html") -> NotebookPayload:
    return NotebookPayload(
        input_file="/test/notebook.py",
        input_file_name="notebook.py",
        output_file="/test/output/notebook",
        data="",
        format=format_,
        kind="completed",
        language="en",
        prog_lang="python",
        correlation_id="cid",
        cross_references=cross_references,
    )


@pytest.mark.parametrize("format_", ["html", "notebook"])
def test_rewrite_produces_working_relative_link(format_: str) -> None:
    processor = NotebookProcessor(CompletedOutput(format=format_))
    ext = "html" if format_ == "html" else "ipynb"
    href = f"../Workshops/03 Functions.{ext}"
    cell = _markdown_cell("See [the workshop](clm:functions_workshop).")

    processor._process_markdown_cell_contents(
        cell,
        "img/",
        _payload({"functions_workshop": href}, format_=format_),
    )

    assert cell["source"] == f"See [the workshop]({href})."


def test_rewrite_drops_link_when_href_empty() -> None:
    processor = NotebookProcessor(CompletedOutput(format="code"))
    cell = _markdown_cell("See [the workshop](clm:functions_workshop).")

    processor._process_markdown_cell_contents(
        cell,
        "img/",
        _payload({"functions_workshop": ""}, format_="code"),
    )

    assert cell["source"] == "See the workshop."


def test_rewrite_noop_without_cross_references() -> None:
    processor = NotebookProcessor(CompletedOutput(format="html"))
    cell = _markdown_cell("See [the workshop](clm:functions_workshop).")

    processor._process_markdown_cell_contents(cell, "img/", _payload({}))

    # No href map -> left verbatim.
    assert cell["source"] == "See [the workshop](clm:functions_workshop)."
