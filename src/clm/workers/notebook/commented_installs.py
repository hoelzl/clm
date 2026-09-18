"""Keep commented-out package installs commented in built notebooks (#904).

Slide sources carry ``# !pip install …`` as a *commented* install hint that
the trainer uncomments live. jupytext's script readers run with
``comment_magics`` on, which uncomments any ``# ``-prefixed line that looks
like a Jupyter magic or shell command — so the hint came out of
``jupytext.reads`` as an *active* ``!pip install`` cell, and the build
executed pip (against whatever ``pip`` was first on PATH, and through the
HTTP-replay proxy as untagged traffic).

That same jupytext behaviour is relied on for kernel magics (``# %%time``,
``# %load_ext autoreload``, ``# %%cython`` …), and ``# # !echo`` (double
comment) is the established "show but never run" escape, so switching
``comment_magics`` off wholesale is not an option. The rule here is narrow:

    A line jupytext would reactivate that is a **package-install command**
    stays exactly as the author wrote it. Everything else keeps jupytext's
    semantics — including an install the author wrote *uncommented*, which
    is explicit intent.

Mechanism: the processor reads the source twice when the text contains a
candidate line — once normally and once with ``comment_magics`` off (the
verbatim view) — and :func:`keep_commented_installs` copies the verbatim
line back wherever the normal read uncommented an install command. Reading
twice keeps jupytext's own string/quote parsing authoritative (a regex
pre-pass over the source could not tell a docstring from code).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from jupytext.formats import long_form_one_format  # type: ignore[import-untyped]
from nbformat import NotebookNode

logger = logging.getLogger(__name__)

# ``!tool …`` / ``%tool …`` prefixes of the package managers a notebook
# cell can drive — Python's, plus the system/JS ones a course setup cell
# might mention (``# !apt install ffmpeg``). Only the *tool* is matched
# here; the verb check below distinguishes ``!pip install`` from ``!pip list``.
_INSTALLER_PREFIX_RE = re.compile(
    r"""
    ^\s*[!%]\s*
    (?:sudo\s+)?
    (?:
        pip3? | pipx
      | py(?:thon)?(?:\d+(?:\.\d+)?)?\s+-m\s+pip
      | uv
      | conda | mamba | micromamba
      | poetry
      | apt(?:-get)? | brew
      | npm | yarn | pnpm
    )
    (?=\s|$)
    """,
    re.VERBOSE,
)
_INSTALL_VERBS = frozenset({"install", "uninstall", "add", "remove", "update", "upgrade"})

# Cheap pre-check on the raw source text: is there any commented line that
# could be a package-install command? Only then is the second read worth
# doing. Deliberately looser than :data:`_INSTALLER_PREFIX_RE`.
_CANDIDATE_RE = re.compile(
    r"^\s*(?:#\s*)+[!%]\s*(?:sudo\s+)?"
    r"(?:pip|py|python|uv|conda|mamba|micromamba|poetry|apt|brew|npm|yarn|pnpm)",
    re.MULTILINE,
)


def is_install_command(line: str) -> bool:
    """True if *line* is an active ``!``/``%`` package-install command.

    ``!pip install x``, ``!python -m pip install x``, ``!uv pip install x``,
    ``!uv add x``, ``!conda install x``, ``%pip install x`` … A commented
    line (``# !pip install x``) is *not* a command and returns False; so
    does ``!pip list`` (no install verb) and ``!echo pip install`` (not a
    package manager).
    """
    m = _INSTALLER_PREFIX_RE.match(line)
    if m is None:
        return False
    # Verb within the first few words after the tool: ``install``, or
    # ``add`` for uv/poetry, ``uninstall`` … Anything else (``list``,
    # ``--version``, ``freeze``) leaves the environment alone.
    words = line[m.end() :].split()
    return any(word in _INSTALL_VERBS for word in words[:4])


def may_contain_commented_install(source_text: str) -> bool:
    """True if the raw source has a commented line worth a verbatim read."""
    return _CANDIDATE_RE.search(source_text) is not None


def verbatim_format(fmt: str | dict[str, Any]) -> dict[str, Any]:
    """The jupytext format *fmt* with ``comment_magics`` switched off.

    Reading with this format yields every code line exactly as written in
    the source file — the "verbatim view" :func:`keep_commented_installs`
    compares against.
    """
    long = dict(long_form_one_format(fmt))
    long["comment_magics"] = False
    return long


def _uncomment_once(line: str) -> str:
    """Mirror of jupytext's ``unesc``: strip one ``# `` (or ``#``) prefix."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if stripped.startswith("# "):
        return indent + stripped[2:]
    if stripped.startswith("#"):
        return indent + stripped[1:]
    return line


def keep_commented_installs(active: NotebookNode, verbatim: NotebookNode) -> int:
    """Re-comment install commands that jupytext reactivated.

    *active* is the notebook from the normal read (magics uncommented);
    *verbatim* is the same source read with ``comment_magics`` off. For
    every code line where the two differ, the active line is an install
    command, and the verbatim line is exactly its commented form, the
    verbatim line is written back into *active*.

    Lines identical in both reads are never touched — that is the raw,
    explicitly active ``!pip install`` case. Structural disagreement
    between the two reads (different cell or line counts) disables the
    rewrite for that cell rather than risk mis-aligning content.

    Returns:
        The number of lines restored.
    """
    active_cells = active.get("cells", [])
    verbatim_cells = verbatim.get("cells", [])
    if len(active_cells) != len(verbatim_cells):
        logger.debug(
            "Verbatim read has %d cells vs %d in the normal read; not restoring commented installs",
            len(verbatim_cells),
            len(active_cells),
        )
        return 0

    restored = 0
    for a_cell, v_cell in zip(active_cells, verbatim_cells, strict=True):
        if a_cell.get("cell_type") != "code" or v_cell.get("cell_type") != "code":
            continue
        a_lines = a_cell.get("source", "").split("\n")
        v_lines = v_cell.get("source", "").split("\n")
        if len(a_lines) != len(v_lines):
            continue
        changed = False
        for i, (a_line, v_line) in enumerate(zip(a_lines, v_lines, strict=True)):
            if a_line == v_line:
                continue
            if is_install_command(a_line) and _uncomment_once(v_line) == a_line:
                a_lines[i] = v_line
                restored += 1
                changed = True
        if changed:
            a_cell["source"] = "\n".join(a_lines)
    return restored
