"""Y9 (adversarial review 2026-07-24): ``clm slides sync record`` never
consulted the diff — a warm deck with pending FRAMED items (localized-pair
frames are structurally clean, so the verify gate cannot see them) was
wholesale-blessed with no signal. Record stays the trust verb (it warns,
never refuses), but the warning names what the blessing short-circuits.
"""

from __future__ import annotations

import json
from pathlib import Path

from clm.cli.commands.slides.sync_v3 import run_record_v3

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _write_deck(tmp_path: Path, de_cells: list[str], en_cells: list[str]) -> tuple[Path, Path]:
    de = tmp_path / "slides_t.de.py"
    en = tmp_path / "slides_t.en.py"
    de.write_text(_build(HEADER_DE, _slide("s0", "de", "Titel"), *de_cells), encoding="utf-8")
    en.write_text(_build(HEADER_EN, _slide("s0", "en", "Title"), *en_cells), encoding="utf-8")
    return de, en


def _record(de: Path, en: Path, *, as_json: bool = False) -> int:
    return run_record_v3(de, en, members=(), provenance="record", as_json=as_json)


class TestRecordPendingFramesWarning:
    def test_warns_on_pending_framed_items(self, tmp_path: Path, capsys):
        de, en = _write_deck(tmp_path, [_shared_code("a")], [_shared_code("a")])
        assert _record(de, en) == 0
        capsys.readouterr()
        # A cold LOCALIZED pair: structurally clean, frames verify_cold.
        _write_deck(
            tmp_path,
            [_shared_code("a"), _localized("m", "de", "DE Text")],
            [_shared_code("a"), _localized("m", "en", "EN text")],
        )
        assert _record(de, en) == 0
        err = capsys.readouterr().err
        assert "pending framed" in err
        assert "verify_cold" in err
        assert "id:m" in err

    def test_json_row_carries_pending_framed(self, tmp_path: Path, capsys):
        de, en = _write_deck(tmp_path, [_shared_code("a")], [_shared_code("a")])
        assert _record(de, en, as_json=True) == 0
        capsys.readouterr()
        # A cold LOCALIZED pair: structurally clean, frames verify_cold.
        _write_deck(
            tmp_path,
            [_shared_code("a"), _localized("m", "de", "DE Text")],
            [_shared_code("a"), _localized("m", "en", "EN text NEW")],
        )
        # The ledger never knew the pair: cold, not verify_translation.
        assert _record(de, en, as_json=True) == 0
        payload = json.loads(capsys.readouterr().out)
        pair = payload["pairs"][0]
        assert pair["pending_framed"] == ["verify_cold id:m"]

    def test_silent_when_only_mechanical_pending(self, tmp_path: Path, capsys):
        de, en = _write_deck(tmp_path, [_shared_code("a")], [_shared_code("a")])
        assert _record(de, en) == 0
        capsys.readouterr()
        # A cold NEUTRAL pair frames record_neutral — mechanical, by design
        # (#764): record blessing it needs no warning.
        _write_deck(
            tmp_path,
            [_shared_code("a"), _shared_code("b", 2)],
            [_shared_code("a"), _shared_code("b", 2)],
        )
        assert _record(de, en) == 0
        err = capsys.readouterr().err
        assert "pending framed" not in err

    def test_silent_on_cold_bootstrap(self, tmp_path: Path, capsys):
        # No ledger at all: the first record IS the bootstrap — every
        # member is unknown, and warning would be pure noise.
        de, en = _write_deck(
            tmp_path,
            [_localized("m", "de", "DE Text")],
            [_localized("m", "en", "EN text")],
        )
        assert _record(de, en) == 0
        err = capsys.readouterr().err
        assert "pending framed" not in err

    def test_silent_when_clean(self, tmp_path: Path, capsys):
        de, en = _write_deck(tmp_path, [_shared_code("a")], [_shared_code("a")])
        assert _record(de, en) == 0
        capsys.readouterr()
        assert _record(de, en) == 0  # second record: nothing pending
        err = capsys.readouterr().err
        assert "pending framed" not in err

    def test_subset_record_does_not_claim_unblessed_frames(self, tmp_path: Path, capsys):
        # Round 1 (Important): a subset record blesses only the named
        # handles — claiming it blessed a pending framed item outside the
        # subset would be a false receipt for the silent-blessing failure
        # mode Y9(a) exists to kill.
        de, en = _write_deck(tmp_path, [_shared_code("a")], [_shared_code("a")])
        assert _record(de, en) == 0
        capsys.readouterr()
        _write_deck(
            tmp_path,
            [_shared_code("a"), _localized("m", "de", "DE Text")],
            [_shared_code("a"), _localized("m", "en", "EN text")],
        )
        rc = run_record_v3(de, en, members=("id:s0",), provenance="record", as_json=False)
        assert rc == 0
        err = capsys.readouterr().err
        assert "blesses" not in err  # no wholesale-blessing claim at all
        assert "outside the recorded subset stay pending" in err

    def test_subset_record_warns_for_blessed_frames(self, tmp_path: Path, capsys):
        # A subset record naming the framed member DOES bless it wholesale
        # — the warning says exactly that.
        de, en = _write_deck(tmp_path, [_shared_code("a")], [_shared_code("a")])
        assert _record(de, en) == 0
        capsys.readouterr()
        _write_deck(
            tmp_path,
            [_shared_code("a"), _localized("m", "de", "DE Text")],
            [_shared_code("a"), _localized("m", "en", "EN text")],
        )
        rc = run_record_v3(de, en, members=("id:m",), provenance="record", as_json=False)
        assert rc == 0
        err = capsys.readouterr().err
        assert "record blesses 1 pending framed item(s) wholesale: verify_cold id:m" in err
        assert "outside the recorded subset" not in err
