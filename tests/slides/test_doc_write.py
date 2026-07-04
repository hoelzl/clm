"""Tests for :mod:`clm.slides.doc_write` (#546 harvest Phase 1).

The extracted write surface must uphold the two properties the apply
executor relied on when the code lived inside it: emission of an unmutated
deck reproduces the on-disk bytes exactly (the lens guarantee lifted into
the writer), and :func:`~clm.slides.doc_write.write_changed_files` lands
multiple files atomically, minting a companion path for a file the bundle
did not have.
"""

from __future__ import annotations

from pathlib import Path

from attrs import evolve

from clm.slides.doc_lenses import LoadedBundle, load_bundle, parse_bundle
from clm.slides.doc_write import DeckEmitter, new_companion_path, write_changed_files
from clm.slides.voiceover_tools import COMPANION_SUBDIR

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _write_bundle(tmp_path: Path) -> tuple[LoadedBundle, str, str]:
    de = _build(
        HEADER_DE,
        _slide("s0", "de", "Titel"),
        _shared_code("x"),
        _localized("s0-m", "de", "DE Text"),
    )
    en = _build(
        HEADER_EN,
        _slide("s0", "en", "Title"),
        _shared_code("x"),
        _localized("s0-m", "en", "EN text"),
    )
    de_path = tmp_path / "slides_t.de.py"
    en_path = tmp_path / "slides_t.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return load_bundle(de_path, en_path), de, en


def test_emit_reproduces_the_on_disk_bytes(tmp_path: Path) -> None:
    bundle, de, en = _write_bundle(tmp_path)
    assert bundle.outcome.deck is not None
    emitter = DeckEmitter(deck=bundle.outcome.deck)
    assert emitter.emit("de", "deck") == de
    assert emitter.emit("en", "deck") == en
    assert emitter.emit("de", "companion") is None
    assert emitter.emit_all()[("en", "companion")] is None
    assert emitter.mutated is False


def test_set_side_mutation_round_trips_through_write(tmp_path: Path) -> None:
    bundle, _de, _en = _write_bundle(tmp_path)
    deck = bundle.outcome.deck
    assert deck is not None
    emitter = DeckEmitter(deck=deck)
    originals = emitter.emit_all()

    target = next(
        m for m in deck.members() if m.de is not None and any("DE Text" in x for x in m.de.lines)
    )
    new_lines = tuple(line.replace("DE Text", "DE Neu") for line in target.de.lines)
    emitter.set_side(target, "de", evolve(target.de, lines=new_lines))
    assert emitter.mutated is True

    finals = emitter.emit_all()
    changed = {key for key in finals if finals[key] != originals[key]}
    assert changed == {("de", "deck")}

    # The writer contract: re-parse before write, then land atomically.
    parse = parse_bundle(
        finals[("de", "deck")] or "",
        finals[("en", "deck")] or "",
        finals[("de", "companion")],
        finals[("en", "companion")],
        comment_token=bundle.comment_token,
    )
    assert parse.refusal is None

    written = write_changed_files(bundle, finals, changed)
    assert written == [bundle.de_path]
    assert "DE Neu" in bundle.de_path.read_text(encoding="utf-8")
    assert "EN text" in bundle.en_path.read_text(encoding="utf-8")


def test_write_changed_files_multi_file_and_minted_companion(tmp_path: Path) -> None:
    bundle, _de, _en = _write_bundle(tmp_path)
    assert bundle.de_companion_path is None

    vo = '# %% [markdown] lang="de" tags=["notes"] for_slide="s0"\n# - Punkt\n'
    finals = {
        ("de", "deck"): "# de deck\n",
        ("en", "deck"): "# en deck\n",
        ("de", "companion"): vo,
        ("en", "companion"): None,
    }
    changed = set(finals)
    written = write_changed_files(bundle, finals, changed)

    minted = new_companion_path(bundle, "de")
    assert minted.parent.name == COMPANION_SUBDIR
    assert set(written) == {bundle.de_path, bundle.en_path, minted}
    assert bundle.de_path.read_text(encoding="utf-8") == "# de deck\n"
    assert bundle.en_path.read_text(encoding="utf-8") == "# en deck\n"
    assert minted.read_text(encoding="utf-8") == vo
    # The None emission was skipped — a writer never deletes/creates for it.
    assert not new_companion_path(bundle, "en").exists()
