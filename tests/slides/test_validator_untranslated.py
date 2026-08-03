"""#772: flag German text in shared code cells of a split pair.

A shared (no-``lang``) code cell is emitted verbatim into both language
outputs, so German comments or strings in one leak into the English deck —
and once banked as ``shared`` trust, a one-sided fix frames the mechanical
``propagate_shared_edit`` overwrite. The check scans comments and string
literals only (identifiers and keywords are English by construction), warns
on the DE side of the pair, and honors the per-cell ``allow-untranslated``
escape hatch (the DE<->EN dictionary example).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from clm.slides.validator import (
    _check_split_untranslated_text,
    _looks_german,
    validate_file,
    validate_files,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_pair(tmp_path: Path, shared_cell: str) -> tuple[Path, Path]:
    """Write a minimal ``.de.py`` / ``.en.py`` pair sharing ``shared_cell``.

    The shared cell text is inserted byte-identically into both halves, after
    one language-tagged markdown cell each — the invariant the build enforces
    via ``_check_shared_cell_parity``.
    """
    de = tmp_path / "slides_demo.de.py"
    en = tmp_path / "slides_demo.en.py"
    de.write_text(
        dedent(
            """\
            # %% [markdown] lang="de" tags=["slide"] slide_id="intro"
            # ## Einführung

            """
        )
        + shared_cell,
        encoding="utf-8",
    )
    en.write_text(
        dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Introduction

            """
        )
        + shared_cell,
        encoding="utf-8",
    )
    return de, en


def _untranslated_findings(de: Path, en: Path) -> list:
    return [f for f in _check_split_untranslated_text(de, en) if "German text" in f.message]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestGermanDetection:
    def test_german_comment_is_flagged(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, "# %%\n# Das ist ein Kommentar.\nx = 1\n")
        findings = _untranslated_findings(de, en)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert findings[0].category == "pairing"
        assert findings[0].file == str(de)
        assert "allow-untranslated" in findings[0].suggestion

    def test_german_string_literal_is_flagged(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, '# %%\nprint("Der Wert von x ist", x)\n')
        assert len(_untranslated_findings(de, en)) == 1

    def test_umlaut_alone_is_flagged(self, tmp_path: Path) -> None:
        # No function-word hits at all — the umlaut is the entire signal.
        de, en = _write_pair(tmp_path, "# %%\n# Größe berechnen\ny = 2\n")
        assert len(_untranslated_findings(de, en)) == 1

    def test_finding_reports_the_cell_line(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, "# %%\n# Das ist ein Kommentar.\nx = 1\n")
        findings = _untranslated_findings(de, en)
        # The pair template puts the shared cell's `# %%` marker on line 4.
        assert findings[0].line == 4


class TestNoFalsePositives:
    def test_english_comment_is_clean(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, "# %%\n# Compute the sum of both values.\nz = 1 + 2\n")
        assert _untranslated_findings(de, en) == []

    def test_pure_code_is_clean(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, "# %%\nresult = compute(left, right)\n")
        assert _untranslated_findings(de, en) == []

    def test_single_function_word_hit_does_not_fire(self, tmp_path: Path) -> None:
        # "mit" (as in MIT) is one hit; the threshold demands two.
        de, en = _write_pair(tmp_path, "# %%\n# mit license header follows\nx = 1\n")
        assert _untranslated_findings(de, en) == []

    def test_german_identifiers_alone_are_clean(self, tmp_path: Path) -> None:
        # Identifiers are not scanned — only comments and strings are.
        de, en = _write_pair(tmp_path, "# %%\nder_wert = ist_summe(einer, eines)\n")
        assert _untranslated_findings(de, en) == []


# ---------------------------------------------------------------------------
# Scope: what the check must NOT cover
# ---------------------------------------------------------------------------


class TestScope:
    def test_lang_tagged_code_cell_is_not_shared(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, "# %%\nx = 1\n")
        # German in a DE-tagged code cell is that half's own content.
        de.write_text(
            de.read_text(encoding="utf-8")
            + '\n# %% lang="de" tags=["keep"]\n# Das ist ein deutscher Kommentar.\na = 1\n',
            encoding="utf-8",
        )
        assert _untranslated_findings(de, en) == []

    def test_shared_markdown_cell_is_out_of_scope(self, tmp_path: Path) -> None:
        # v1 scope is shared CODE cells (the measured class); the 282 shared
        # markdown members record_neutral leaves cold are a follow-up.
        de, en = _write_pair(
            tmp_path, '# %% [markdown] tags=["notes"]\n# Das ist eine Notiz für alle.\n'
        )
        assert _untranslated_findings(de, en) == []

    def test_j2_preamble_with_german_title_is_clean(self, tmp_path: Path) -> None:
        de = tmp_path / "slides_demo.de.py"
        en = tmp_path / "slides_demo.en.py"
        header = '# j2 from "macros.j2" import header_de\n# {{ header_de("Einführung") }}\n\n'
        body = "# %%\nx = 1\n"
        de.write_text(header + body, encoding="utf-8")
        en.write_text(header.replace("_de", "_en") + body, encoding="utf-8")
        assert _untranslated_findings(de, en) == []


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------


class TestAllowUntranslatedTag:
    DICTIONARY_CELL = (
        '# %% tags=["keep", "allow-untranslated"]\nwoerter = {"Tür": "door", "Haus": "house"}\n'
    )

    def test_tag_suppresses_the_warning(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, self.DICTIONARY_CELL)
        assert _untranslated_findings(de, en) == []

    def test_without_tag_the_same_cell_warns(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, self.DICTIONARY_CELL.replace(', "allow-untranslated"', ""))
        assert len(_untranslated_findings(de, en)) == 1

    def test_tag_is_recognized_by_the_tag_check(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, self.DICTIONARY_CELL)
        result = validate_file(de, checks=["tags"])
        assert [f for f in result.findings if "Unrecognized tag" in f.message] == []


# ---------------------------------------------------------------------------
# Wiring: the check runs from both entry points, without duplication
# ---------------------------------------------------------------------------


class TestWiring:
    GERMAN_CELL = "# %%\n# Der Wert wird hier berechnet.\nx = 1\n"

    def test_validate_files_reports_once_per_pair(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, self.GERMAN_CELL)
        result = validate_files([de, en])
        findings = [f for f in result.findings if "German text" in f.message]
        assert len(findings) == 1
        assert findings[0].file == str(de)

    def test_validate_file_standalone_runs_the_pair_check(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, self.GERMAN_CELL)
        result = validate_file(en)  # either half finds its twin on disk
        assert [f for f in result.findings if "German text" in f.message]

    def test_checks_filter_excludes_it_without_pairing(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path, self.GERMAN_CELL)
        result = validate_files([de, en], checks=["format"])
        assert [f for f in result.findings if "German text" in f.message] == []


# ---------------------------------------------------------------------------
# Detector unit cases
# ---------------------------------------------------------------------------


class TestLooksGerman:
    def test_two_function_words_fire(self) -> None:
        assert _looks_german("Das ist ein Test")

    def test_one_function_word_does_not(self) -> None:
        assert not _looks_german("released under der agreement")

    def test_umlaut_fires(self) -> None:
        assert _looks_german("Größe")

    def test_empty_is_clean(self) -> None:
        assert not _looks_german("")

    def test_english_prose_is_clean(self) -> None:
        assert not _looks_german("This is a longer English sentence about the code.")
