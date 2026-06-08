"""Unit tests for the shared glossary-discovery helpers (translate + sync).

The discovery walk and per-language resolution are pure filesystem logic, so
they are exercised directly here without a CLI runner or an LLM. The CLI wiring
that *uses* them lives in ``tests/cli/test_slides_translate.py`` (single target)
and ``tests/cli/test_slides_sync.py`` (bidirectional).
"""

from __future__ import annotations

from pathlib import Path

from clm.slides.glossary import (
    GLOSSARY_STEM,
    discover_glossary,
    glossary_name,
    resolve_guidance,
    resolve_guidance_by_lang,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Name + discovery walk
# ---------------------------------------------------------------------------


def test_glossary_name_is_lang_parameterized() -> None:
    assert glossary_name("de") == f"{GLOSSARY_STEM}.de.md"
    assert glossary_name("en") == f"{GLOSSARY_STEM}.en.md"


def test_discover_finds_in_start_dir(tmp_path: Path) -> None:
    g = _write(tmp_path / "clm-glossary.de.md", "Sie")
    assert discover_glossary(tmp_path, "de") == g


def test_discover_walks_up_nearest_first(tmp_path: Path) -> None:
    # A glossary at the root AND one nearer the deck: the nearer one wins.
    _write(tmp_path / "clm-glossary.de.md", "root")
    deck_dir = tmp_path / "slides" / "topic"
    nearer = _write(deck_dir / "clm-glossary.de.md", "nearer")
    assert discover_glossary(deck_dir, "de") == nearer


def test_discover_returns_none_when_absent(tmp_path: Path) -> None:
    assert discover_glossary(tmp_path, "de") is None


def test_discover_is_language_specific(tmp_path: Path) -> None:
    _write(tmp_path / "clm-glossary.de.md", "Sie")
    assert discover_glossary(tmp_path, "en") is None  # only .de present


# ---------------------------------------------------------------------------
# Single-target resolution (translate / bootstrap)
# ---------------------------------------------------------------------------


def test_resolve_guidance_explicit_wins(tmp_path: Path) -> None:
    _write(tmp_path / "clm-glossary.de.md", "auto")
    explicit = _write(tmp_path / "custom.md", "explicit conventions")
    text, path = resolve_guidance(explicit, tmp_path, "de")
    assert text == "explicit conventions"
    assert path == explicit  # the explicit path bypasses discovery


def test_resolve_guidance_auto_discovers(tmp_path: Path) -> None:
    g = _write(tmp_path / "clm-glossary.de.md", "  Address with 'Sie'.\n")
    text, path = resolve_guidance(None, tmp_path, "de")
    assert text == "Address with 'Sie'."  # trimmed
    assert path == g


def test_resolve_guidance_none_found(tmp_path: Path) -> None:
    assert resolve_guidance(None, tmp_path, "de") == ("", None)


# ---------------------------------------------------------------------------
# Bidirectional resolution (sync)
# ---------------------------------------------------------------------------


def test_resolve_by_lang_auto_discovers_both(tmp_path: Path) -> None:
    de = _write(tmp_path / "clm-glossary.de.md", "Sie")
    en = _write(tmp_path / "clm-glossary.en.md", "formal")
    guidance, used = resolve_guidance_by_lang(tmp_path, explicit={"de": None, "en": None})
    assert guidance == {"de": "Sie", "en": "formal"}
    assert used == {"de": de, "en": en}


def test_resolve_by_lang_asymmetric_only_de(tmp_path: Path) -> None:
    # The common course shape: only a DE glossary exists. EN is simply absent —
    # a DE->EN add translates with no conventions.
    de = _write(tmp_path / "clm-glossary.de.md", "Sie")
    guidance, used = resolve_guidance_by_lang(tmp_path, explicit={"de": None, "en": None})
    assert guidance == {"de": "Sie"}
    assert used == {"de": de}
    assert "en" not in guidance


def test_resolve_by_lang_explicit_overrides_one_side(tmp_path: Path) -> None:
    _write(tmp_path / "clm-glossary.de.md", "auto-de")
    explicit_en = _write(tmp_path / "my-en.md", "explicit-en")
    guidance, used = resolve_guidance_by_lang(tmp_path, explicit={"de": None, "en": explicit_en})
    assert guidance == {"de": "auto-de", "en": "explicit-en"}
    assert used["en"] == explicit_en


def test_resolve_by_lang_empty_file_is_no_glossary(tmp_path: Path) -> None:
    # A whitespace-only glossary file appends nothing and is reported as absent.
    _write(tmp_path / "clm-glossary.de.md", "   \n\n")
    guidance, used = resolve_guidance_by_lang(tmp_path, explicit={"de": None, "en": None})
    assert guidance == {}
    assert used == {}


def test_resolve_by_lang_none_found(tmp_path: Path) -> None:
    assert resolve_guidance_by_lang(tmp_path, explicit={"de": None, "en": None}) == ({}, {})
