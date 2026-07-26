"""The strict (companion-projecting) write gate — finding Y2 / decision D8.

``clm slides sync verify`` projects a pair's separated voiceover companions
before it checks anything (#501), while the ledger write gate used to run on the
raw deck halves alone. The two therefore disagreed on the one thing they exist
to agree on: **verify failed on a byte-diverged shared companion while record
blessed it**, banking the divergence as "verified" — which is what arms the
mirror-removal and preamble-propagation data-loss paths.

Pinned here:

* the Y2 shape itself — raw gate clean, strict gate not;
* gate ≡ verify on the projected pair (the property, not just an example);
* a pair CLM cannot project is a gate *error* (not a clean verdict reached by
  not looking) and a verify *warning*;
* ``--allow-diverged-companion`` overrides **only** companion-introduced
  violations, logs each one, and is not a ``--force``;
* ``clm harvest``'s deliberate exemption (proposal §6) — its one-sided
  narrative member is a pending state, and the strict gate would refuse it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from clm.slides.sync_verify import (
    gate_deck_halves,
    gate_projected_pair,
    structural_gate,
    verify_pair,
)

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(sid: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n#\n# ## {title}\n\n'


def _shared_code(value: str = "1") -> str:
    return f'# %% tags=["keep"]\nx = {value}\n\n'


def _vo_cell(sid: str, lang: str | None, owner: str, text: str) -> str:
    """A companion voiceover cell; ``lang=None`` makes it language-neutral (shared)."""
    lang_attr = f' lang="{lang}"' if lang is not None else ""
    return (
        f'# %% [markdown]{lang_attr} tags=["voiceover"] for_slide="{owner}" '
        f'slide_id="{sid}"\n#\n# - {text}\n\n'
    )


def _deck(lang: str, *, code: str = "1") -> str:
    header = HEADER_DE if lang == "de" else HEADER_EN
    titles = {"de": ("Einführung", "Ende"), "en": ("Introduction", "The End")}[lang]
    return (
        header
        + _slide("intro", lang, titles[0])
        + _shared_code(code)
        + _slide("ende", lang, titles[1])
    )


def _write_pair(
    folder: Path,
    *,
    de_comp: str | None = None,
    en_comp: str | None = None,
    de_text: str | None = None,
    en_text: str | None = None,
) -> tuple[Path, Path]:
    de = folder / "slides_t.de.py"
    en = folder / "slides_t.en.py"
    de.write_text(de_text if de_text is not None else _deck("de"), encoding="utf-8")
    en.write_text(en_text if en_text is not None else _deck("en"), encoding="utf-8")
    if de_comp is not None:
        (folder / "voiceover_t.de.py").write_text(de_comp, encoding="utf-8")
    if en_comp is not None:
        (folder / "voiceover_t.en.py").write_text(en_comp, encoding="utf-8")
    return de, en


def _texts(de: Path, en: Path) -> tuple[str, str]:
    return de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")


def _raw_gate(de: Path, en: Path) -> list[str]:
    return [v.message for v in structural_gate(*_texts(de, en), "#")]


# Symmetric companions whose ``for_slide`` names a slide the deck does not have:
# the projection refuses (never drop narration), the deck halves look perfect.
_ORPHANED_COMPANIONS = {
    "de_comp": _vo_cell("ghost-vo", "de", "ghost", "Für eine gelöschte Folie."),
    "en_comp": _vo_cell("ghost-vo", "en", "ghost", "For a deleted slide."),
}


@pytest.fixture
def cli_runner() -> CliRunner:
    # Click 8.1 needs ``mix_stderr=False``; Click 8.2+ removed the parameter.
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _payload(output: str) -> dict:
    start = output.index("{")
    payload, _end = json.JSONDecoder().raw_decode(output[start:])
    return payload


# ---------------------------------------------------------------------------
# The finding: what the raw-halves gate cannot see.
# ---------------------------------------------------------------------------


class TestCompanionBlindness:
    def test_byte_diverged_shared_companion_cell(self, tmp_path: Path) -> None:
        """Y2's exact shape: a *shared* narration cell whose halves differ.

        Language-neutral means byte-identical by definition, so this is a
        corruption — and the deck halves show no trace of it.
        """
        de, en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", None, "intro", "Neutrale Erzählung A."),
            en_comp=_vo_cell("intro-vo", None, "intro", "Neutral narration B."),
        )
        assert _raw_gate(de, en) == []
        strict = gate_projected_pair(de, en, "#")
        assert [v.kind for v in strict] == ["unify"]
        assert "diverges" in strict[0].message

    def test_one_sided_idd_narrative_member(self, tmp_path: Path) -> None:
        de, en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Nur auf Deutsch."),
        )
        assert _raw_gate(de, en) == []
        strict = gate_projected_pair(de, en, "#")
        assert [(v.kind, v.slide_id) for v in strict] == [("id-asymmetry", "intro-vo")]

    def test_duplicate_key_inside_the_companion(self, tmp_path: Path) -> None:
        twice = _vo_cell("intro-vo", "de", "intro", "Eins.") + _vo_cell(
            "intro-vo", "de", "intro", "Zwei."
        )
        de, en = _write_pair(
            tmp_path,
            de_comp=twice,
            en_comp=_vo_cell("intro-vo", "en", "intro", "One.")
            + _vo_cell("intro-vo", "en", "intro", "Two."),
        )
        assert _raw_gate(de, en) == []
        assert {v.kind for v in gate_projected_pair(de, en, "#")} == {"duplicate-id"}

    def test_symmetric_companions_still_pass(self, tmp_path: Path) -> None:
        de, en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Willkommen."),
            en_comp=_vo_cell("intro-vo", "en", "intro", "Welcome."),
        )
        assert gate_projected_pair(de, en, "#") == []

    def test_plain_pair_is_untouched_by_the_projection(self, tmp_path: Path) -> None:
        de, en = _write_pair(tmp_path)
        assert gate_projected_pair(de, en, "#") == []
        # And the strict gate still catches a deck-half corruption.
        en.write_text(
            en.read_text(encoding="utf-8").replace('slide_id="ende"', 'slide_id="fin"'),
            encoding="utf-8",
        )
        assert {v.kind for v in gate_projected_pair(de, en, "#")} == {"id-asymmetry"}


# ---------------------------------------------------------------------------
# gate ≡ verify, and the one documented asymmetry.
# ---------------------------------------------------------------------------


class TestGateVerifyParity:
    @pytest.mark.parametrize(
        "de_comp,en_comp",
        [
            (None, None),
            (
                _vo_cell("intro-vo", "de", "intro", "Willkommen."),
                _vo_cell("intro-vo", "en", "intro", "Welcome."),
            ),
            (
                _vo_cell("intro-vo", None, "intro", "Neutral A."),
                _vo_cell("intro-vo", None, "intro", "Neutral B."),
            ),
            (_vo_cell("intro-vo", "de", "intro", "Nur DE."), None),
        ],
        ids=["plain", "symmetric", "diverged-shared", "one-sided"],
    )
    def test_the_gate_reports_exactly_verifys_errors(
        self, tmp_path: Path, de_comp: str | None, en_comp: str | None
    ) -> None:
        """The property D8 asks for, over every companion shape.

        Not "both call ``structural_violations``" — they always did. The claim is
        that they run it on the *same text*, which is what Y2 disproved.
        """
        de, en = _write_pair(tmp_path, de_comp=de_comp, en_comp=en_comp)
        gate = [(v.kind, v.message) for v in gate_projected_pair(de, en, "#")]
        verify = [(v.kind, v.message) for v in verify_pair(de, en).errors]
        assert gate == verify

    def test_unprojectable_pair_is_a_gate_error_and_a_verify_warning(self, tmp_path: Path) -> None:
        """Orphaned narration: the companions name a slide the deck no longer has.

        Nothing to project, and the deck halves alone look perfect. The gate must
        refuse — falling back to the raw halves would be a clean verdict reached
        by not looking, which is the Y2 hole again. ``verify`` answers a different
        question ("did an edit corrupt this pair?"), so it says so as a warning
        and keeps its exit code.
        """
        de, en = _write_pair(tmp_path, **_ORPHANED_COMPANIONS)
        assert _raw_gate(de, en) == []
        strict = gate_projected_pair(de, en, "#")
        assert [v.kind for v in strict] == ["companion-refusal"]
        assert "cannot be projected" in strict[0].message
        assert "ghost" in strict[0].message

        result = verify_pair(de, en)
        assert result.ok, "an unprojectable layout is not an edit that corrupted the pair"
        assert [v.kind for v in result.warnings if v.kind == "companion-refusal"] == [
            "companion-refusal"
        ]

    def test_cross_language_layout_also_breaks_the_deck_halves(self, tmp_path: Path) -> None:
        """DE separated, EN inline — refused *and* asymmetric in the raw halves.

        Worth pinning because it is the shape that makes ``--allow-diverged-companion``
        scoping matter: the refusal is companion-introduced (overridable), the
        id-asymmetry is visible without any projection (never overridable).
        """
        en_inline = (
            HEADER_EN
            + _slide("intro", "en", "Introduction")
            + '# %% [markdown] lang="en" tags=["voiceover"] for_slide="intro" '
            'slide_id="intro-vo"\n#\n# - Inline narration.\n\n'
            + _shared_code()
            + _slide("ende", "en", "The End")
        )
        de, en = _write_pair(
            tmp_path,
            en_text=en_inline,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Erzählung."),
        )
        assert [v.kind for v in structural_gate(*_texts(de, en), "#")] == ["id-asymmetry"]
        assert [v.kind for v in gate_projected_pair(de, en, "#")] == [
            "companion-refusal",
            "id-asymmetry",
        ]
        assert [
            v.kind for v in gate_projected_pair(de, en, "#", allow_diverged_companion=True)
        ] == ["id-asymmetry"]


# ---------------------------------------------------------------------------
# The escape hatch: narrow, loud, and not a --force.
# ---------------------------------------------------------------------------


class TestEscapeHatch:
    def test_overrides_the_companion_violation_and_logs_it(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        de, en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Nur auf Deutsch."),
        )
        with caplog.at_level(logging.WARNING, logger="clm.slides.sync_verify"):
            assert gate_projected_pair(de, en, "#", allow_diverged_companion=True) == []
        assert any(
            "--allow-diverged-companion" in r.message and "id-asymmetry" in r.message
            for r in caplog.records
        ), caplog.text

    def test_does_not_override_a_deck_half_violation(self, tmp_path: Path) -> None:
        """The flag is scoped to what the *projection* introduced, by name and by code.

        Here the deck halves are themselves broken (an id present only on DE) and a
        companion divergence sits on top. The companion violation is dropped; the
        deck-half one still refuses.
        """
        de_broken = (
            HEADER_DE
            + _slide("intro", "de", "Einführung")
            + _slide("nur-de", "de", "Nur DE")
            + _shared_code()
            + _slide("ende", "de", "Ende")
        )
        de, en = _write_pair(
            tmp_path,
            de_text=de_broken,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Nur auf Deutsch."),
        )
        kinds = {v.kind for v in gate_projected_pair(de, en, "#", allow_diverged_companion=True)}
        assert kinds == {"id-asymmetry"}
        remaining = gate_projected_pair(de, en, "#", allow_diverged_companion=True)
        assert [v.slide_id for v in remaining] == ["nur-de"]

    def test_is_a_no_op_on_a_sound_pair(self, tmp_path: Path) -> None:
        de, en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Willkommen."),
            en_comp=_vo_cell("intro-vo", "en", "intro", "Welcome."),
        )
        assert gate_projected_pair(de, en, "#", allow_diverged_companion=True) == []


# ---------------------------------------------------------------------------
# Through the CLI: record and apply.
# ---------------------------------------------------------------------------


class TestRecordVerb:
    def test_record_refuses_a_diverged_companion(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        de, _en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", None, "intro", "Neutral A."),
            en_comp=_vo_cell("intro-vo", None, "intro", "Neutral B."),
        )
        result = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert result.exit_code == 1, result.output
        payload = _payload(result.output)
        assert payload["refused"] == 1
        assert payload["recorded"] == 0
        assert any("diverges" in r for r in payload["pairs"][0]["reasons"])
        assert not (tmp_path / ".clm" / "sync-ledger.json").exists()

    def test_record_with_the_flag_banks_it(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        de, _en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", None, "intro", "Neutral A."),
            en_comp=_vo_cell("intro-vo", None, "intro", "Neutral B."),
        )
        result = cli_runner.invoke(
            slides_sync_group,
            ["record", str(de), "--allow-diverged-companion", "--json"],
        )
        assert result.exit_code == 0, result.output
        assert _payload(result.output)["refused"] == 0
        assert (tmp_path / ".clm" / "sync-ledger.json").is_file()

    def test_record_still_refuses_a_corrupt_deck_half_with_the_flag(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        de_broken = (
            HEADER_DE
            + _slide("intro", "de", "Einführung")
            + _slide("nur-de", "de", "Nur DE")
            + _shared_code()
            + _slide("ende", "de", "Ende")
        )
        de, _en = _write_pair(tmp_path, de_text=de_broken)
        result = cli_runner.invoke(
            slides_sync_group,
            ["record", str(de), "--allow-diverged-companion", "--json"],
        )
        assert result.exit_code == 1, result.output
        assert _payload(result.output)["refused"] == 1
        assert not (tmp_path / ".clm" / "sync-ledger.json").exists()

    def test_a_symmetric_separated_deck_records_unflagged(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        de, _en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Willkommen."),
            en_comp=_vo_cell("intro-vo", "en", "intro", "Welcome."),
        )
        result = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert result.exit_code == 0, result.output
        assert _payload(result.output)["refused"] == 0


class TestApplyVerb:
    """apply's post-write ledger save runs the same strict gate."""

    def _seed(self, cli_runner: CliRunner, folder: Path) -> tuple[Path, Path]:
        """A recorded pair whose companions the gate refuses to re-verify.

        Orphaned narration: only the *companions* are at fault, so the refusal is
        exactly what ``--allow-diverged-companion`` is scoped to override — and
        apply cannot fix it on its own, which keeps the gate the deciding factor.
        """
        de, en = _write_pair(folder, **_ORPHANED_COMPANIONS)
        seed = cli_runner.invoke(
            slides_sync_group,
            ["record", str(de), "--allow-diverged-companion", "--json"],
        )
        assert seed.exit_code == 0, seed.output
        # A shared-cell edit on DE — a mechanical item for apply to land.
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), encoding="utf-8")
        return de, en

    def test_ledger_is_withheld_when_the_gate_refuses(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        de, en = self._seed(cli_runner, tmp_path)
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        payload = _payload(result.output)
        assert payload["ledger_recorded"] is False
        assert payload["verify_violations"], payload
        # Fail-safe, as before: the file write stays, only trust is withheld.
        assert "x = 42" in en.read_text(encoding="utf-8")
        assert result.exit_code == 1

    def test_the_flag_lets_the_ledger_write_proceed(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        de, en = self._seed(cli_runner, tmp_path)
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--allow-diverged-companion", "--json"],
        )
        payload = _payload(result.output)
        assert payload["verify_violations"] == []
        assert payload["ledger_recorded"] is True
        assert "x = 42" in en.read_text(encoding="utf-8")
        # Exit 1 here is the orphaned narration reported as framed residue
        # (`broken_owner`), not the gate — the flag settles the ledger write, and
        # deliberately does not make the underlying problem disappear from report.
        assert result.exit_code == 1
        assert [i["action"] for i in payload["items"] if i["status"] == "pending"] == [
            "broken_owner"
        ]


# ---------------------------------------------------------------------------
# harvest's documented exemption (proposal §6).
# ---------------------------------------------------------------------------


class TestHarvestExemption:
    def test_the_strict_gate_would_refuse_what_harvest_must_record(self, tmp_path: Path) -> None:
        """Why the two gate entry points exist, stated as a test.

        A harvest write lands narration on one side; §6 *requires* that one-sided
        member to be recorded (it is what frames the twin as ``translate_new``).
        The strict gate reads it as an id-asymmetry — so harvest uses
        ``gate_deck_halves`` and the sync verbs use ``gate_projected_pair``, and
        neither call site is free to drift on its own.
        """
        de, en = _write_pair(
            tmp_path,
            de_comp=_vo_cell("intro-vo", "de", "intro", "Frisch geerntet."),
        )
        assert [v.kind for v in gate_projected_pair(de, en, "#")] == ["id-asymmetry"]
        assert gate_deck_halves(de, en, "#") == []

    def test_deck_half_corruption_still_reaches_harvest(self, tmp_path: Path) -> None:
        """The exemption is about companions only — harvest is not ungated."""
        de_broken = (
            HEADER_DE
            + _slide("intro", "de", "Einführung")
            + _slide("nur-de", "de", "Nur DE")
            + _shared_code()
            + _slide("ende", "de", "Ende")
        )
        de, en = _write_pair(tmp_path, de_text=de_broken)
        assert [v.kind for v in gate_deck_halves(de, en, "#")] == ["id-asymmetry"]
