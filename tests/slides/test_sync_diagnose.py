"""Tests for the ``sync diagnose`` classifier (sync_diagnose).

Covers each root-cause catalog row, the verify-invisible narrative cases the
classifier must surface independently of ``verify``, the mechanical ``--apply``
fixes (re-gated by structure), and the anti-rename invariant.
"""

from __future__ import annotations

from pathlib import Path

from clm.slides.sync_diagnose import (
    AUTHORING,
    CONTENT_GAP,
    DUPLICATE_NARRATION_OVERSTAMP,
    ID_LESS_TWIN,
    MECHANICAL,
    MIS_TAG,
    NARRATIVE_ID_DISAGREEMENT,
    WHOLE_DECK_GAP,
    apply_mechanical_fixes,
    diagnose_pair,
)

# German prose long enough for the detector to be confident.
_DE_PROSE = "Dies ist ein deutscher Absatz mit vielen Woertern und Umlauten wie schoen"
_EN_PROSE = "This is an english paragraph with many common words and it is clearly english"


def _deck(lang: str, cells: list[tuple[list[str], str | None, str]]) -> str:
    head = f"# j2 from 'macros.j2' import header_{lang}\n# {{{{ header_{lang}(\"T\") }}}}\n"
    out = [head]
    for tags, sid, body in cells:
        tagstr = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
        idstr = f' slide_id="{sid}"' if sid else ""
        out.append(f'# %% [markdown] lang="{lang}" tags={tagstr}{idstr}\n# {body}\n')
    return "".join(out)


def _write(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    dp, ep = tmp_path / "x.de.py", tmp_path / "x.en.py"
    dp.write_text(de, encoding="utf-8")
    ep.write_text(en, encoding="utf-8")
    return dp, ep


def _causes(result) -> list[str]:
    return [d.root_cause for d in result.diagnoses]


def test_clean_pair_has_no_findings(tmp_path):
    dp, ep = _write(
        tmp_path,
        _deck("de", [(["slide"], "intro", "Hallo Welt")]),
        _deck("en", [(["slide"], "intro", "Hello world")]),
    )
    result = diagnose_pair(dp, ep)
    assert result.ok
    assert result.diagnoses == []


def test_symmetric_narration_overstamp_is_mechanical_and_autofixable(tmp_path):
    de = _deck(
        "de",
        [
            (["slide"], "intro", "Hallo"),
            (["voiceover"], "intro", "Eins"),
            (["voiceover"], "intro", "Zwei"),
        ],
    )
    en = _deck(
        "en",
        [
            (["slide"], "intro", "Hello"),
            (["voiceover"], "intro", "One"),
            (["voiceover"], "intro", "Two"),
        ],
    )
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    assert DUPLICATE_NARRATION_OVERSTAMP in _causes(result)
    assert all(
        d.fix_class == MECHANICAL
        for d in result.diagnoses
        if d.root_cause == DUPLICATE_NARRATION_OVERSTAMP
    )
    ar = apply_mechanical_fixes(dp, ep)
    assert ar.written and not ar.refused
    assert ar.collapsed_duplicates == 4  # both halves, 2 cells each
    assert ar.residual is not None and ar.residual.ok


def test_asymmetric_narration_disagreement_is_verify_invisible_but_diagnosed(tmp_path):
    # DE voiceover id'd, EN voiceover id-less → NO verify violation (the slide id is on
    # the slide cell in both halves), but reconcile-pairing surfaces it.
    de = _deck("de", [(["slide"], "intro", "Hallo"), (["voiceover"], "intro", "Notiz")])
    en = _deck("en", [(["slide"], "intro", "Hello"), (["voiceover"], None, "Note")])
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    assert NARRATIVE_ID_DISAGREEMENT in _causes(result)
    ar = apply_mechanical_fixes(dp, ep)
    assert ar.written and ar.reconcile_changes == 1
    assert ar.residual is not None and ar.residual.ok


def test_mis_tag_detected_by_content_language(tmp_path):
    # EN half carries a slide whose content is German but tagged lang="en".
    de = _deck("de", [(["slide"], "intro", "Hallo Welt")])
    en = _deck("en", [(["slide"], "intro", "Hello world"), (["slide"], "misT", _DE_PROSE)])
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    mis = [d for d in result.diagnoses if d.root_cause == MIS_TAG]
    assert mis, _causes(result)
    assert mis[0].fix_class == AUTHORING
    assert mis[0].evidence["content_lang"] == "de"
    assert mis[0].evidence["lang_tag"] == "en"


def test_content_gap_is_authoring_and_warns_against_rename(tmp_path):
    de = _deck("de", [(["slide"], "intro", "Hallo"), (["slide"], "gap", _DE_PROSE)])
    en = _deck("en", [(["slide"], "intro", "Hello")])
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    gap = [d for d in result.diagnoses if d.root_cause == CONTENT_GAP]
    assert gap, _causes(result)
    assert gap[0].fix_class == AUTHORING
    assert "rename" in gap[0].prescribed_fix.lower()  # mentioned only to forbid it
    assert "never rename" in gap[0].prescribed_fix.lower()


def test_id_less_twin_when_other_half_has_an_idless_slide(tmp_path):
    de = _deck("de", [(["slide"], "intro", "Hallo"), (["slide"], "ztwin", _DE_PROSE)])
    en = _deck("en", [(["slide"], "intro", "Hello"), (["slide"], None, _EN_PROSE)])
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    twin = [d for d in result.diagnoses if d.root_cause == ID_LESS_TWIN]
    assert twin, _causes(result)
    assert twin[0].fix_class == AUTHORING
    assert "do not rename" in twin[0].prescribed_fix.lower()


def test_whole_deck_gap_suppresses_per_slide_asymmetries(tmp_path):
    de = _deck("de", [(["slide"], "intro", "Hallo"), (["slide"], "two", "Zwei Inhalt")])
    en = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"T\") }}\n"
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    causes = _causes(result)
    assert WHOLE_DECK_GAP in causes
    # The two id-asymmetries are folded into the one whole-deck finding, not N gaps.
    assert CONTENT_GAP not in causes


def test_apply_never_touches_a_content_gap(tmp_path):
    # A content gap is authoring-only: --apply must not write or resolve it.
    de = _deck("de", [(["slide"], "intro", "Hallo"), (["slide"], "gap", _DE_PROSE)])
    en = _deck("en", [(["slide"], "intro", "Hello")])
    dp, ep = _write(tmp_path, de, en)
    de_before, en_before = dp.read_text(encoding="utf-8"), ep.read_text(encoding="utf-8")
    ar = apply_mechanical_fixes(dp, ep)
    assert not ar.written  # nothing mechanical to do
    assert dp.read_text(encoding="utf-8") == de_before
    assert ep.read_text(encoding="utf-8") == en_before
    assert ar.residual is not None and CONTENT_GAP in _causes(ar.residual)


def test_no_diagnosis_ever_prescribes_a_bare_rename(tmp_path):
    # The anti-pattern guard: no fix says "rename X to Y" without forbidding it.
    de = _deck("de", [(["slide"], "intro", "Hallo"), (["slide"], "gap", _DE_PROSE)])
    en = _deck("en", [(["slide"], "intro", "Hello"), (["slide"], None, _EN_PROSE)])
    dp, ep = _write(tmp_path, de, en)
    result = diagnose_pair(dp, ep)
    for d in result.diagnoses:
        fix = d.prescribed_fix.lower()
        if "rename" in fix:
            assert "never rename" in fix or "do not rename" in fix
