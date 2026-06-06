"""Tests for :mod:`clm.slides.preamble_code` (issue #253).

Preamble code — executable code folded into a leading j2 header cell body
because it sits between the ``# {{ header(...) }}`` macro call and the first
``# %%`` cell — is detected by :func:`find_preamble_code` and re-homed into its
own ``# %%`` code cell by :func:`wrap_preamble_code`.
"""

from __future__ import annotations

from clm.slides.preamble_code import find_preamble_code, wrap_preamble_code
from clm.slides.raw_cells import reconstruct, split_cells

# The exact issue-#253 shape: code between the header macro and the first cell.
_DECK_WITH_PREAMBLE = (
    "# j2 from 'macros.j2' import header\n"
    '# {{ header("Regeln für Typen", "Rules for Types") }}\n'
    "from typing import Iterable\n"
    "\n"
    "\n"
    '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
    "# DE content\n"
)

# The conforming form: code already in its own cell.
_DECK_CLEAN = (
    "# j2 from 'macros.j2' import header\n"
    '# {{ header("Regeln für Typen", "Rules for Types") }}\n'
    "\n"
    "# %%\n"
    "from typing import Iterable\n"
    "\n"
    '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
    "# DE content\n"
)

# Top-of-file code (before the j2 import) lands in the split_cells preamble
# string, copied identically to both halves — render-neutral, NOT flagged.
_DECK_TOP_PREAMBLE = (
    "import os\n"
    "\n"
    "# j2 from 'macros.j2' import header\n"
    '# {{ header("DE", "EN") }}\n'
    "\n"
    '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
    "# DE content\n"
)


class TestFindPreambleCode:
    def test_detects_code_in_header_macro_body(self):
        _, cells = split_cells(_DECK_WITH_PREAMBLE)
        findings = find_preamble_code(cells)
        assert len(findings) == 1
        f = findings[0]
        assert f.first_code_line == 3
        assert f.code_lines == ["from typing import Iterable"]

    def test_no_finding_when_code_in_own_cell(self):
        _, cells = split_cells(_DECK_CLEAN)
        assert find_preamble_code(cells) == []

    def test_no_finding_for_top_of_file_preamble(self):
        # `import os` before the j2 import is in the split_cells preamble
        # string, not a j2 cell body — render-neutral, must not be flagged.
        _, cells = split_cells(_DECK_TOP_PREAMBLE)
        assert find_preamble_code(cells) == []

    def test_ignores_blank_and_comment_body_lines(self):
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("DE", "EN") }}\n'
            "# just a trailing comment\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
        )
        _, cells = split_cells(text)
        assert find_preamble_code(cells) == []

    def test_detects_code_in_import_cell(self):
        # Code folded into the import directive's cell (before the macro call).
        text = (
            "# j2 from 'macros.j2' import header\n"
            "bad_code()\n"
            '# {{ header("DE", "EN") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
        )
        _, cells = split_cells(text)
        findings = find_preamble_code(cells)
        assert len(findings) == 1
        assert findings[0].code_lines == ["bad_code()"]

    def test_clike_comment_token(self):
        text = (
            "// j2 from 'macros.j2' import header\n"
            '// {{ header("DE", "EN") }}\n'
            "using System;\n"
            "\n"
            '// %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "// DE content\n"
        )
        _, cells = split_cells(text, comment_token="//")
        findings = find_preamble_code(cells, comment_token="//")
        assert len(findings) == 1
        assert findings[0].code_lines == ["using System;"]

    def test_detects_code_in_split_header_de_body(self):
        # An already-split .de.py half edited to add preamble code: header_de is
        # still a j2 cell, so the same detection applies.
        text = (
            "# j2 from 'macros.j2' import header_de\n"
            '# {{ header_de("Regeln für Typen") }}\n'
            "from typing import Iterable\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
        )
        _, cells = split_cells(text)
        findings = find_preamble_code(cells)
        assert len(findings) == 1
        assert findings[0].code_lines == ["from typing import Iterable"]

    def test_detects_code_in_split_header_en_body(self):
        text = (
            "# j2 from 'macros.j2' import header_en\n"
            '# {{ header_en("Rules for Types") }}\n'
            "from typing import Iterable\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"] slide_id="gh"\n'
            "# EN content\n"
        )
        _, cells = split_cells(text)
        findings = find_preamble_code(cells)
        assert len(findings) == 1
        assert findings[0].code_lines == ["from typing import Iterable"]

    def test_comment_before_code_reports_code_line(self):
        # A comment line precedes the code in the j2 body: the reported line must
        # be the CODE line, not the comment.
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("DE", "EN") }}\n'
            "# explanatory comment\n"
            "from typing import Iterable\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
        )
        _, cells = split_cells(text)
        findings = find_preamble_code(cells)
        assert len(findings) == 1
        assert findings[0].first_code_line == 4  # the import, not the comment at L3
        assert findings[0].code_lines == ["from typing import Iterable"]

    def test_only_leading_j2_cells_scanned(self):
        # A mid-file `# %%` code cell with code is NOT preamble code.
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("DE", "EN") }}\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
            "\n"
            "# %%\n"
            "y = 2\n"
        )
        _, cells = split_cells(text)
        assert find_preamble_code(cells) == []


class TestWrapPreambleCode:
    def test_moves_code_into_own_cell(self):
        _, cells = split_cells(_DECK_WITH_PREAMBLE)
        wrapped = wrap_preamble_code(cells)
        assert len(wrapped) == 1
        out = reconstruct("", cells)
        # The header cell no longer carries the import …
        assert "}}\nfrom typing" not in out
        # … and a new bare `# %%` code cell now holds it.
        assert "# %%\nfrom typing import Iterable" in out

    def test_no_content_lost(self):
        _, cells = split_cells(_DECK_WITH_PREAMBLE)
        wrap_preamble_code(cells)
        out = reconstruct("", cells)
        assert "from typing import Iterable" in out
        assert "Regeln für Typen" in out
        assert "DE content" in out

    def test_idempotent(self):
        _, cells = split_cells(_DECK_WITH_PREAMBLE)
        wrap_preamble_code(cells)
        out = reconstruct("", cells)
        _, cells2 = split_cells(out)
        assert wrap_preamble_code(cells2) == []

    def test_noop_on_clean_deck(self):
        _, cells = split_cells(_DECK_CLEAN)
        assert wrap_preamble_code(cells) == []

    def test_wraps_split_header_de(self):
        text = (
            "# j2 from 'macros.j2' import header_de\n"
            '# {{ header_de("Regeln für Typen") }}\n'
            "from typing import Iterable\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
        )
        _, cells = split_cells(text)
        wrapped = wrap_preamble_code(cells)
        assert len(wrapped) == 1
        out = reconstruct("", cells)
        assert "# %%\nfrom typing import Iterable" in out
        assert "}}\nfrom typing" not in out

    def test_comment_before_code_preserved_on_j2_cell(self):
        # The leading comment stays on the j2 cell; the code moves; the new
        # cell's line_number reflects the actual code line (review finding #1).
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("DE", "EN") }}\n'
            "# explanatory comment\n"
            "from typing import Iterable\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "# DE content\n"
        )
        _, cells = split_cells(text)
        wrap_preamble_code(cells)
        # j2 macro cell keeps the comment; new code cell carries the import.
        macro_cell = cells[1]
        assert "# explanatory comment" in macro_cell.lines
        new_cell = cells[2]
        assert new_cell.lines[0] == "# %%"
        assert new_cell.lines[1] == "from typing import Iterable"
        # line_number points at the real code line (L4), not header+1 (L3).
        assert new_cell.line_number == 4
        # No content lost.
        out = reconstruct("", cells)
        assert "# explanatory comment" in out
        assert "from typing import Iterable" in out

    def test_clike_uses_slash_marker(self):
        text = (
            "// j2 from 'macros.j2' import header\n"
            '// {{ header("DE", "EN") }}\n'
            "using System;\n"
            "\n"
            '// %% [markdown] lang="de" tags=["slide"] slide_id="gh"\n'
            "// DE content\n"
        )
        _, cells = split_cells(text, comment_token="//")
        wrap_preamble_code(cells, comment_token="//")
        out = reconstruct("", cells)
        assert "// %%\nusing System;" in out
