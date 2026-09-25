"""CLI + end-to-end tests for ``clm slides rename-id`` (issue #572).

The money test (:meth:`TestRenameThenEdit.test_rename_then_edit_frames_translate_edit`)
reproduces the reported footgun and proves the fix: after a manual id rename the
ledger stays warm, so a subsequent edit of the renamed cell frames
``translate_edit`` (with a fresh twin body answer) — never the silent
``verify_cold`` that would bank the stale twin on ``confirm``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.rename_id import rename_id_cmd
from clm.cli.commands.slides.sync import slides_sync_group


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"

DE = (
    HEADER_DE
    + '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n\n'
    + '# %% tags=["keep"]\nx = 1\n\n'
    + '# %% [markdown] lang="de" slide_id="s0-m"\n# DE Text\n'
)
EN = (
    HEADER_EN
    + '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n\n'
    + '# %% tags=["keep"]\nx = 1\n\n'
    + '# %% [markdown] lang="en" slide_id="s0-m"\n# EN text\n'
)


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    de = tmp_path / "slides_t.de.py"
    en = tmp_path / "slides_t.en.py"
    de.write_text(DE, encoding="utf-8")
    en.write_text(EN, encoding="utf-8")
    return de, en


def _json_payload(output: str) -> dict:
    return json.loads(output[output.index("{") :])


def _record(cli_runner, de: Path) -> None:
    assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0


def _report(cli_runner, de: Path) -> dict:
    res = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
    return _json_payload(res.output)


class TestRenameThenEdit:
    def test_rename_then_edit_frames_translate_edit(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        _record(cli_runner, de)
        assert _report(cli_runner, de)["is_clean"] is True

        # Rename the non-anchor member id on BOTH halves + the ledger.
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["ledger_migrated"] is True
        assert payload["slide_id_hits"] == {"de": 1, "en": 1}
        assert 'slide_id="s0-x"' in de.read_text(encoding="utf-8")
        assert 'slide_id="s0-x"' in en.read_text(encoding="utf-8")

        # The ledger stayed warm: a pure rename reports clean, NOT cold.
        after_rename = _report(cli_runner, de)
        assert after_rename["is_clean"] is True, after_rename
        assert not any(i["action"] == "verify_cold" for i in after_rename["items"])

        # Now edit the EN body of the renamed cell. Because the baseline was
        # migrated (not dropped to cold), this frames translate_edit — the twin
        # can be re-translated — instead of a verify_cold that would confirm the
        # stale German.
        en.write_text(
            en.read_text(encoding="utf-8").replace("EN text", "EN text rewritten"), "utf-8"
        )
        after_edit = _report(cli_runner, de)
        actions = {i["action"] for i in after_edit["items"]}
        assert "verify_cold" not in actions, after_edit
        edited = next(i for i in after_edit["items"] if i["key"] == "id:s0-x")
        assert edited["action"] == "translate_edit"
        assert "body" in edited["answers"]

        # And the loop closes: supplying the fresh DE twin resolves it to clean.
        # The freshness token is mandatory since schema 3's grace ended (Q2).
        decisions = json.dumps(
            {
                "report_id": after_edit["report_id"],
                "decisions": [{"key": "id:s0-x", "body": "# DE neu"}],
            }
        )
        applied = cli_runner.invoke(
            slides_sync_group, ["apply", str(de), "--decisions", "-", "--json"], input=decisions
        )
        assert applied.exit_code == 0, applied.output
        assert "# DE neu" in de.read_text(encoding="utf-8")
        assert _report(cli_runner, de)["is_clean"] is True

    def test_rename_anchor_stays_clean(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        _record(cli_runner, de)

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new"])
        assert res.exit_code == 0, res.output
        assert 'slide_id="s0-new"' in de.read_text(encoding="utf-8")

        # The positional `x = 1` under the s0 group cascaded its group token —
        # the deck is still fully in sync.
        report = _report(cli_runner, de)
        assert report["is_clean"] is True, report


class TestRenameGuards:
    def test_missing_old_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "nope", "whatever", "--json"])
        assert res.exit_code == 2
        assert "no cell carries" in _json_payload(res.output)["error"]

    def test_collision_with_existing_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0", "--json"])
        assert res.exit_code == 2
        assert "already exists" in _json_payload(res.output)["error"]

    def test_same_old_new_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-m", "--json"])
        assert res.exit_code == 2
        assert "same id" in _json_payload(res.output)["error"]

    def test_invalid_new_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "bad id", "--json"])
        assert res.exit_code == 2
        assert "not a usable slide_id" in _json_payload(res.output)["error"]

    def test_dry_run_writes_nothing(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        _record(cli_runner, de)
        before_de, before_en = de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")
        ledger_before = (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8")

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--report-only", "--json"])
        assert res.exit_code == 0, res.output
        assert _json_payload(res.output)["report_only"] is True

        assert de.read_text(encoding="utf-8") == before_de
        assert en.read_text(encoding="utf-8") == before_en
        assert (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8") == ledger_before

    def test_rename_without_ledger_still_rewrites_files(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)  # never recorded — no ledger
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["ledger_migrated"] is False
        assert 'slide_id="s0-x"' in de.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Issue #990: separated voiceover companions are part of the deck.
# ---------------------------------------------------------------------------


def _companion(lang: str) -> str:
    """A separated companion: two narration cells owned by ``s0`` (anchored to
    it), each carrying its own ``slide_id`` as the sync engine requires."""
    text = {"de": "Erzählung", "en": "Narration"}[lang]
    return (
        f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="s0-vo-a" for_slide="s0"'
        f' vo_anchor="id:s0#0"\n'
        f"#\n# - {text} s0\n\n"
        f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="s0-vo" for_slide="s0"'
        f' vo_anchor="id:s0#0"\n'
        f"#\n# - {text} zwei\n"
    )


def _write_separated_pair(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    de, en = _write_pair(tmp_path)
    vo_dir = tmp_path / "voiceover"
    vo_dir.mkdir()
    de_vo = vo_dir / "voiceover_t.de.py"
    en_vo = vo_dir / "voiceover_t.en.py"
    de_vo.write_text(_companion("de"), encoding="utf-8")
    en_vo.write_text(_companion("en"), encoding="utf-8")
    return de, en, de_vo, en_vo


class TestRenameFollowsCompanions:
    """Regression tests for #990."""

    def test_rename_rewrites_companion_references(self, cli_runner: CliRunner, tmp_path: Path):
        de, en, de_vo, en_vo = _write_separated_pair(tmp_path)
        _record(cli_runner, de)
        assert _report(cli_runner, de)["is_clean"] is True

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)

        # The companions were rewritten: owner references AND anchors.
        for vo in (de_vo, en_vo):
            text = vo.read_text(encoding="utf-8")
            assert 'for_slide="s0"' not in text, text
            assert 'vo_anchor="id:s0#0"' not in text, text
            assert text.count('for_slide="s0-new"') == 2, text
            assert text.count('vo_anchor="id:s0-new#0"') == 2, text
        # ...and the report counts say so (2 owner refs + 2 anchors per side).
        assert payload["for_slide_hits"] == {"de": 2, "en": 2}, payload
        assert payload["vo_anchor_hits"] == {"de": 2, "en": 2}, payload
        assert payload["companions"]["de"]["path"] == str(de_vo)
        assert payload["companions"]["de"]["for_slide_hits"] == 2
        assert payload["companions"]["de"]["vo_anchor_hits"] == 2

        # No narration was orphaned: the pair stays clean, nothing frames broken_owner.
        report = _report(cli_runner, de)
        assert report["is_clean"] is True, report
        assert not any(i["action"] == "broken_owner" for i in report["items"])

    def test_dry_run_counts_companion_hits_without_writing(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en, de_vo, en_vo = _write_separated_pair(tmp_path)
        before = {p: p.read_text(encoding="utf-8") for p in (de, en, de_vo, en_vo)}

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--dry-run", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["for_slide_hits"] == {"de": 2, "en": 2}, payload
        assert payload["vo_anchor_hits"] == {"de": 2, "en": 2}, payload
        for p, text in before.items():
            assert p.read_text(encoding="utf-8") == text

    def test_rename_companion_cells_own_slide_id(self, cli_runner: CliRunner, tmp_path: Path):
        """The second gap from the issue thread: an id that lives only in the
        companions used to be refused with "no cell carries slide_id"."""
        de, en, de_vo, en_vo = _write_separated_pair(tmp_path)
        _record(cli_runner, de)

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-vo", "s0-narration", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["slide_id_hits"] == {"de": 1, "en": 1}, payload
        assert payload["ledger_migrated"] is True, payload
        for vo in (de_vo, en_vo):
            text = vo.read_text(encoding="utf-8")
            assert 'slide_id="s0-narration"' in text
            assert 'slide_id="s0-vo"' not in text
        # Deck halves untouched, ledger warm.
        assert de.read_text(encoding="utf-8") == DE
        assert en.read_text(encoding="utf-8") == EN
        report = _report(cli_runner, de)
        assert report["is_clean"] is True, report
        assert not any(i["action"] == "verify_cold" for i in report["items"])

    def test_collision_with_companion_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en, _de_vo, _en_vo = _write_separated_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-vo", "--json"])
        assert res.exit_code == 2
        assert "already exists" in _json_payload(res.output)["error"]

    def test_plain_pair_reports_no_companions(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["companions"] == {"de": None, "en": None}
        assert payload["vo_anchor_hits"] == {"de": 0, "en": 0}

    def test_edited_companion_cell_is_not_refingerprinted(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        """The migration invariant: a rename carries fingerprints only for cells
        sitting on their baseline. A companion cell edited *before* the rename
        keeps its stale record, so the edit still frames on the next report."""
        de, en, de_vo, en_vo = _write_separated_pair(tmp_path)
        _record(cli_runner, de)
        en_vo.write_text(
            en_vo.read_text(encoding="utf-8").replace("Narration zwei", "Narration two, edited"),
            encoding="utf-8",
        )

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--json"])
        assert res.exit_code == 0, res.output

        report = _report(cli_runner, de)
        by_key = {i["key"]: i for i in report["items"]}
        # The untouched narration cell migrated cleanly...
        assert "id:s0-vo-a" not in by_key, report
        # ...the edited one frames exactly the edit: EN moved, DE on base.
        assert by_key["id:s0-vo"]["action"] == "translate_edit", report
        assert by_key["id:s0-vo"]["direction"] == "en_to_de", report


class TestRenameReviewFindings:
    """Adversarial-review findings on the #990 fix."""

    def test_repoint_after_hand_rename(self, cli_runner: CliRunner, tmp_path: Path):
        """The state broken_owner advertises rename-id for: the deck already
        carries NEW (hand rename), the companions still reference OLD."""
        de, en, de_vo, en_vo = _write_separated_pair(tmp_path)
        _record(cli_runner, de)
        for half in (de, en):
            half.write_text(
                half.read_text(encoding="utf-8").replace('slide_id="s0"', 'slide_id="s0-new"'),
                encoding="utf-8",
            )

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["repointed"] is True, payload
        assert payload["slide_id_hits"] == {"de": 0, "en": 0}, payload
        assert payload["for_slide_hits"] == {"de": 2, "en": 2}, payload
        assert payload["ledger_migrated"] is True, payload
        for vo in (de_vo, en_vo):
            text = vo.read_text(encoding="utf-8")
            assert 'for_slide="s0"' not in text and 'vo_anchor="id:s0#0"' not in text, text
        # The hand rename's cold state is recovered too: the pair reports clean.
        report = _report(cli_runner, de)
        assert report["is_clean"] is True, report

    def test_repoint_human_output_says_so(self, cli_runner: CliRunner, tmp_path: Path):
        de, en, _de_vo, _en_vo = _write_separated_pair(tmp_path)
        for half in (de, en):
            half.write_text(
                half.read_text(encoding="utf-8").replace('slide_id="s0"', 'slide_id="s0-new"'),
                encoding="utf-8",
            )
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--dry-run"])
        assert res.exit_code == 0, res.output
        assert "would re-point" in res.output
        assert "repoint:" in res.output

    def test_plain_missing_old_still_refused(self, cli_runner: CliRunner, tmp_path: Path):
        """Repoint needs a dangling reference AND the target present; a bare
        unknown OLD is still a usage error."""
        de, _en, _de_vo, _en_vo = _write_separated_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "nope", "s0", "--json"])
        assert res.exit_code == 2
        assert "no cell carries" in _json_payload(res.output)["error"]

    def test_dangling_orphan_does_not_shift_fingerprint_pairing(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        """A companion cell already pointing at NEW re-homes from the orphans
        into the group after the rewrite; the genuinely rewritten cells must
        still carry their fingerprints (pairing by cell handle, not sequence)."""
        de, en, de_vo, en_vo = _write_separated_pair(tmp_path)
        _record(cli_runner, de)
        for vo, lang in ((de_vo, "de"), (en_vo, "en")):
            vo.write_text(
                vo.read_text(encoding="utf-8")
                + f'\n# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="s0-vo-late"'
                f' for_slide="s0-new" vo_anchor="id:s0-new#0"\n#\n# - late\n',
                encoding="utf-8",
            )

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--json"])
        assert res.exit_code == 0, res.output

        report = _report(cli_runner, de)
        keys = {i["key"] for i in report["items"]}
        # The two rewritten narration cells migrated cleanly...
        assert "id:s0-vo-a" not in keys, report
        assert "id:s0-vo" not in keys, report
        # ...only the never-recorded late cell needs attention.
        assert keys <= {"id:s0-vo-late"}, report

    def test_companion_in_both_layouts_refused(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en, de_vo, _en_vo = _write_separated_pair(tmp_path)
        (tmp_path / de_vo.name).write_text(de_vo.read_text(encoding="utf-8"), encoding="utf-8")
        before = de_vo.read_text(encoding="utf-8")

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new", "--json"])
        assert res.exit_code == 2, res.output
        assert "both layouts" in _json_payload(res.output)["error"]
        assert de_vo.read_text(encoding="utf-8") == before
