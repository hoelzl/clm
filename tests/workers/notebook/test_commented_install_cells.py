"""Commented-out package installs stay commented in built notebooks (#904).

Slide sources use ``# !pip install …`` as a *commented* install hint that
the trainer uncomments live. jupytext's percent reader (``comment_magics``
on by default for scripts) uncomments any ``# ``-prefixed line that looks
like a magic or shell command, so the hint became an *active* ``!pip
install`` cell in every built notebook — and ``clm build`` ran pip during
execution, against whatever ``pip`` was first on PATH, through the replay
proxy untagged.

The corpus relies on the same jupytext behaviour on purpose for kernel
magics (``# %%time``, ``# %load_ext``, ``# %%cython`` …), and already uses
double-commenting (``# # !echo``) as "show but never run". So the rule is
narrow: a line jupytext would reactivate that is a *package-install
command* stays exactly as written; everything else keeps jupytext's
semantics.
"""

from __future__ import annotations

import uuid

import pytest

from clm.core.messaging.notebook_classes import NotebookPayload
from clm.workers.notebook.commented_installs import (
    is_install_command,
    keep_commented_installs,
)
from clm.workers.notebook.notebook_processor import NotebookProcessor
from clm.workers.notebook.output_spec import CompletedOutput

pytestmark = pytest.mark.asyncio


def _payload(source: str, *, name: str = "slides_010_deepeval.py") -> NotebookPayload:
    return NotebookPayload(
        input_file=f"/test/{name}",
        input_file_name=name,
        output_file="/test/output/notebook",
        data=source,
        format="notebook",
        kind="completed",
        language="en",
        prog_lang="python",
        correlation_id="test-904-" + uuid.uuid4().hex[:8],
    )


async def _code_sources(source: str) -> list[str]:
    processor = NotebookProcessor(
        CompletedOutput(format="notebook", language="en", prog_lang="python")
    )
    nb = await processor.process_notebook_for_spec(source, _payload(source))
    return [cell.source for cell in nb.cells if cell.cell_type == "code"]


# ---------------------------------------------------------------------------
# The reported case, end to end through the processor
# ---------------------------------------------------------------------------


async def test_commented_pip_install_stays_commented():
    """Regression test for #904: the install hint must not become executable."""
    sources = await _code_sources('# %%\n# !pip install "deepeval>=4.0.5,<4.1"\n')
    assert sources == ['# !pip install "deepeval>=4.0.5,<4.1"']


async def test_kernel_magics_are_still_reactivated():
    """``# %%time`` and friends are intended to run — jupytext's behaviour
    for them is unchanged (the corpus has ~80 such cells)."""
    sources = await _code_sources(
        "# %%\n# %%time\nx = 1\n\n# %%\n# %load_ext autoreload\n# %autoreload 2\n"
    )
    assert sources == ["%%time\nx = 1", "%load_ext autoreload\n%autoreload 2"]


async def test_raw_install_line_stays_active():
    """An install the author wrote *uncommented* is explicit intent."""
    sources = await _code_sources("# %%\n!pip install rawpkg\n")
    assert sources == ["!pip install rawpkg"]


async def test_double_commented_shell_escape_unchanged():
    """The existing 'show but never run' convention keeps working."""
    sources = await _code_sources("# %%\n# # !echo hi\n")
    assert sources == ["# !echo hi"]


async def test_other_shell_escapes_still_reactivated():
    """Only *installs* are pinned; ``# !python script.py`` (18 cells in the
    corpus) is meant to run."""
    sources = await _code_sources("# %%\n# !python train.py --epochs 1\n")
    assert sources == ["!python train.py --epochs 1"]


async def test_all_install_spellings_stay_commented():
    source = (
        "# %%\n"
        "# %pip install foo\n"
        "# !python -m pip install bar\n"
        "# !uv pip install baz\n"
        "# !conda install -y qux\n"
        "# !pip3 install --upgrade quux\n"
    )
    sources = await _code_sources(source)
    assert sources == [
        "# %pip install foo\n"
        "# !python -m pip install bar\n"
        "# !uv pip install baz\n"
        "# !conda install -y qux\n"
        "# !pip3 install --upgrade quux"
    ]


async def test_system_package_manager_hint_stays_commented():
    sources = await _code_sources("# %%\n# !sudo apt-get install -y ffmpeg\n")
    assert sources == ["# !sudo apt-get install -y ffmpeg"]


async def test_mixed_cell_pins_only_the_install_line():
    sources = await _code_sources("# %%\n# !pip install foo\nimport foo\n")
    assert sources == ["# !pip install foo\nimport foo"]


async def test_indented_commented_install_stays_commented():
    sources = await _code_sources("# %%\nif need_install:\n    # !pip install foo\n    pass\n")
    assert sources == ["if need_install:\n    # !pip install foo\n    pass"]


async def test_markdown_source_is_untouched():
    """The md reader never uncomments, so nothing to restore — and the
    guard must not choke on a non-percent format."""
    md = "```python\n# !pip install foo\nx = 1\n```\n"
    processor = NotebookProcessor(
        CompletedOutput(format="notebook", language="en", prog_lang="python")
    )
    nb = await processor.process_notebook_for_spec(md, _payload(md, name="setup.md"))
    assert [c.source for c in nb.cells if c.cell_type == "code"] == ["# !pip install foo\nx = 1"]


# ---------------------------------------------------------------------------
# The pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "!pip install foo",
        "!pip3 install foo",
        "  !pip install foo",
        "!pip uninstall -y foo",
        "!python -m pip install foo",
        "!python3.12 -m pip install foo",
        "!py -m pip install foo",
        "!uv pip install foo",
        "!uv add foo",
        "!conda install -c conda-forge foo",
        "!mamba install foo",
        "!poetry add foo",
        "%pip install foo",
        "%conda install foo",
        "%pip install foo #     bar",
        "!sudo apt-get install -y ffmpeg",
        "!apt install ffmpeg",
        "!brew install graphviz",
        "!npm install -g some-tool",
        "!pipx install ruff",
    ],
)
def test_is_install_command_positive(line):
    assert is_install_command(line)


@pytest.mark.parametrize(
    "line",
    [
        "!pip list",
        "!pip --version",
        "!apt list --installed",
        "!npm run build",
        "!sudo systemctl restart foo",
        "!python train.py",
        "!echo pip install",
        "%%time",
        "%load_ext autoreload",
        "pip install foo",  # not a magic/escape at all
        "# !pip install foo",  # already a comment
        "import pip",
        "",
    ],
)
def test_is_install_command_negative(line):
    assert not is_install_command(line)


def test_keep_commented_installs_ignores_cell_count_mismatch():
    """Defensive: if the two reads ever disagree on structure, do nothing
    rather than mis-align cells."""
    from nbformat.v4 import new_code_cell, new_notebook

    active = new_notebook(cells=[new_code_cell("!pip install foo")])
    verbatim = new_notebook(cells=[])
    assert keep_commented_installs(active, verbatim) == 0
    assert active.cells[0].source == "!pip install foo"


def test_keep_commented_installs_only_touches_uncommented_lines():
    """A line that is identical in both reads is never rewritten, even if
    it is an install command (that is the raw, explicit case)."""
    from nbformat.v4 import new_code_cell, new_notebook

    active = new_notebook(cells=[new_code_cell("!pip install foo\n!pip install bar")])
    verbatim = new_notebook(cells=[new_code_cell("!pip install foo\n# !pip install bar")])
    assert keep_commented_installs(active, verbatim) == 1
    assert active.cells[0].source == "!pip install foo\n# !pip install bar"
