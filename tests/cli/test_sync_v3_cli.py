"""CLI tests for the sync verbs (#520; sole engine since the Phase 4 cutover).

The tests drive the full loop through the CLI — record → report clean →
mutate → report flags → apply → report clean — and pin the envelope's
stable booleans.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from clm.slides.sync_wire import WIRE_SCHEMA


@pytest.fixture
def cli_runner():
    # Click 8.1 needs ``mix_stderr=False``; Click 8.2+ removed the parameter.
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


def _stderr(result) -> str:
    """The runner's stderr, on Click 8.1 (split streams) and 8.2+ alike."""
    try:
        return result.stderr or ""
    except ValueError:  # streams not separated on this Click
        return result.output


def _json_payload(output: str) -> dict:
    # raw_decode: the runner may append stderr lines (e.g. apply's rejected
    # summary) after the JSON document — parse the object, ignore the rest.
    start = output.index("{")
    payload, _end = json.JSONDecoder().raw_decode(output[start:])
    return payload


class TestSyncLoop:
    def test_record_report_mutate_apply_report(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)

        # A never-recorded deck is cold: work pending (exit 1), agent needed.
        cold = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert cold.exit_code == 1, cold.output
        payload = _json_payload(cold.output)
        assert payload["schema"] == WIRE_SCHEMA and payload["engine"] == "v3"
        assert payload["is_clean"] is False
        assert payload["needs_agent"] is True
        # Members whose halves the engine can compare directly resolve
        # mechanically (§6.2.1 / #764); everything else is a framed question.
        assert {i["action"] for i in payload["items"]} == {"verify_cold", "record_neutral"}
        # id-keyed cold members also advertise `body` (inline stale-twin recovery,
        # issue #572); positional ones stay confirm-only (no addressable id); a
        # mechanical row advertises nothing at all.
        for i in payload["items"]:
            if i["action"] == "record_neutral":
                assert i["answers"] == [] and i["resolution"] == "mechanical", i
                continue
            expected = ["confirm", "body"] if i["key"].startswith("id:") else ["confirm"]
            assert i["answers"] == expected, i
        # An all-cold report is the seeding case — it says so.
        assert "sync record" in payload["hint"]

        # record blesses the current state (verify-gated).
        record = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert record.exit_code == 0, record.output
        assert (tmp_path / ".clm" / "sync-ledger.json").is_file()

        clean = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert clean.exit_code == 0, clean.output
        assert _json_payload(clean.output)["is_clean"] is True

        # One shared edit -> one mechanical item -> apply propagates it.
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        flagged = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert flagged.exit_code == 1
        payload = _json_payload(flagged.output)
        items = payload["items"]
        assert [i["action"] for i in items] == ["propagate_shared_edit"]
        # Mechanical items carry answers == [] (present, never missing) so
        # agent drivers can filter on item["answers"] without a key guard.
        assert items[0]["answers"] == []
        # A mixed/mechanical report carries no cold-seeding hint.
        assert "hint" not in payload

        applied = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert applied.exit_code == 0, applied.output
        assert "x = 42" in en.read_text(encoding="utf-8")

        again = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert again.exit_code == 0, again.output

    def test_apply_decisions_from_stdin(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        decisions = json.dumps({"decisions": [{"key": "id:s0-m", "body": "# EN new"}]})
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
        )
        assert result.exit_code == 0, result.output
        assert "# EN new" in en.read_text(encoding="utf-8")

    def test_cold_body_recovery_fixes_a_stale_twin(self, cli_runner: CliRunner, tmp_path: Path):
        # Issue #572: on a cold deck an id-keyed member whose EN was rewritten
        # (DE twin now stale) is framed verify_cold — which now also offers a
        # `body` answer. Supplying it with `side` overwrites the stale twin in
        # one pass instead of `confirm` banking the stale German.
        de, en = _write_pair(tmp_path)
        # The DE twin of s0-m is a stale placeholder relative to the EN body.
        de.write_text(
            de.read_text(encoding="utf-8").replace("# DE Text", "# *(placeholder)*"), "utf-8"
        )

        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        s0m = next(i for i in report["items"] if i["key"] == "id:s0-m")
        assert s0m["action"] == "verify_cold"
        assert s0m["answers"] == ["confirm", "body"]

        # Fix the stale DE twin inline; confirm the rest of the cold pairs.
        rows = []
        for item in report["items"]:
            if item["key"] == "id:s0-m":
                rows.append({"key": item["key"], "body": "# DE frisch übersetzt", "side": "de"})
            else:
                rows.append({"key": item["key"], "choice": "confirm"})
        applied = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps({"decisions": rows}),
        )
        assert applied.exit_code == 0, applied.output
        de_text = de.read_text(encoding="utf-8")
        assert "# DE frisch übersetzt" in de_text
        assert "placeholder" not in de_text

        clean = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert clean.exit_code == 0, clean.output
        assert _json_payload(clean.output)["is_clean"] is True

    def test_rejected_decision_is_summarized_on_stderr(self, cli_runner: CliRunner, tmp_path: Path):
        # Blanket-confirming a translate_edit is the classic wrong answer —
        # the rejection must be loud (stderr, with the reason), not just a
        # counts entry an agent's JSON filter can skip past.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps({"decisions": [{"key": "id:s0-m", "choice": "confirm"}]}),
        )
        assert result.exit_code == 1, result.output
        assert _json_payload(result.output)["counts"]["rejected"] == 1
        stderr = getattr(result, "stderr", "") or result.output
        assert "decision(s) rejected" in stderr
        assert "id:s0-m" in stderr

    def test_apply_residue_exits_one(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["counts"]["pending"] == 1
        assert "DE neu" not in en.read_text(encoding="utf-8")

    def test_record_refuses_a_structurally_corrupt_pair(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)
        # Corrupt the EN half: drop the localized twin so the ids are asymmetric.
        en.write_text(
            en.read_text(encoding="utf-8").replace('slide_id="s0-m"', 'slide_id="s0-x"'),
            "utf-8",
        )
        result = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["refused"] == 1
        assert not (tmp_path / ".clm" / "sync-ledger.json").is_file()

    def test_report_over_a_directory_aggregates(self, cli_runner: CliRunner, tmp_path: Path):
        _write_pair(tmp_path)
        result = cli_runner.invoke(slides_sync_group, ["report", str(tmp_path), "--json"])
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["engine"] == "v3"
        assert len(payload["pairs"]) == 1

    def test_confirm_only_apply_persists_the_ledger(self, cli_runner: CliRunner, tmp_path: Path):
        # Review regression: a confirm-only apply mutates no file, but its
        # ledger updates must still be saved — silently discarding them made
        # every confirmation a no-op.
        de, _en = _write_pair(tmp_path)
        cold = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        decisions = json.dumps(
            {
                "decisions": [
                    {"key": item["key"], "choice": "confirm"}
                    for item in _json_payload(cold.output)["items"]
                ]
            }
        )
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
        )
        assert result.exit_code == 0, result.output
        payload = _json_payload(result.output)
        assert payload["ledger_recorded"] is True
        assert (tmp_path / ".clm" / "sync-ledger.json").is_file()
        again = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert again.exit_code == 0, again.output

    def test_apply_never_records_a_structurally_corrupt_pair(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Review regression: the lens tolerates (observes) an id-asymmetry
        # the structural gate refuses — apply may write its mechanical items,
        # but the ledger must not bless members of a corrupt pair.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        en.write_text(
            en.read_text(encoding="utf-8").replace('slide_id="s0-m"', 'slide_id="s0-x"'),
            "utf-8",
        )
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        ledger_before = (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8")
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert result.exit_code != 0, result.output
        payload = _json_payload(result.output)
        assert payload["ledger_recorded"] is False
        assert payload["verify_violations"]
        assert (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8") == ledger_before

    def test_directory_sweep_warns_on_solo_halves(self, cli_runner: CliRunner, tmp_path: Path):
        _write_pair(tmp_path)
        (tmp_path / "slides_solo.de.py").write_text(DE, encoding="utf-8")
        result = cli_runner.invoke(slides_sync_group, ["report", str(tmp_path), "--json"])
        payload = _json_payload(result.output)
        assert any("slides_solo" in s for s in payload["skipped_solos"])
        stderr = getattr(result, "stderr", "") or result.output
        assert "no twin half found" in stderr

    def test_rerecord_sweep_is_write_free_on_clean_pairs(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Issue #555: a repo-wide re-record must not bump confirmed_commit on
        # unchanged members — the committed ledger stays byte-identical even
        # though HEAD has moved since the first record.
        import subprocess

        de, _en = _write_pair(tmp_path)

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        git("add", ".")
        git("commit", "-q", "-m", "base")

        first = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert first.exit_code == 0, first.output
        ledger_path = tmp_path / ".clm" / "sync-ledger.json"
        before = ledger_path.read_bytes()
        assert _head_sha(tmp_path) in before.decode("utf-8")  # first record stamps HEAD

        # Move HEAD without touching the pair, then re-record.
        git("add", ".")
        git("commit", "-q", "-m", "record")
        (tmp_path / "other.txt").write_text("unrelated\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "move HEAD")

        second = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert second.exit_code == 0, second.output
        payload = _json_payload(second.output)
        assert payload["unchanged"] == 1
        assert payload["pairs"][0]["ledger_changed"] is False
        assert ledger_path.read_bytes() == before

    def test_bare_deck_path_defaults_to_report(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        result = cli_runner.invoke(slides_sync_group, [str(de), "--json"])
        assert result.exit_code == 0, result.output
        assert _json_payload(result.output)["is_clean"] is True


class TestSinceView:
    def test_since_diffs_against_the_ref_not_the_ledger(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # --since is a forensic VIEW (design §12.3): the baseline is the bundle
        # at the ref; the ledger is neither consulted nor required.
        import subprocess

        de, en = _write_pair(tmp_path)

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        git("add", ".")
        git("commit", "-q", "-m", "base")

        # No ledger, unchanged since HEAD -> the window view is clean even
        # though the ledger view would report every member cold.
        clean = cli_runner.invoke(
            slides_sync_group, ["report", str(de), "--since", "HEAD", "--json"]
        )
        assert clean.exit_code == 0, clean.output
        payload = _json_payload(clean.output)
        assert payload["is_clean"] is True
        assert payload["baseline"] == "since:" + _head_sha(tmp_path)

        # A shared edit in the window shows up as exactly that item.
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        flagged = cli_runner.invoke(
            slides_sync_group, ["report", str(de), "--since", "HEAD", "--json"]
        )
        assert flagged.exit_code == 1, flagged.output
        items = _json_payload(flagged.output)["items"]
        assert [i["action"] for i in items] == ["propagate_shared_edit"]
        # The view never wrote a ledger.
        assert not (tmp_path / ".clm" / "sync-ledger.json").is_file()


def _head_sha(cwd: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class TestOrderObservationRender:
    """#654: the one observation kind that suppresses is_clean must be
    visible in the text report (an observation-only unclean report would
    otherwise read "0 item(s)" with no cause)."""

    def test_group_order_divergence_observation_is_printed(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de = tmp_path / "slides_g.de.py"
        en = tmp_path / "slides_g.en.py"
        slide_de = '# %% [markdown] lang="de" tags=["slide"] slide_id="{sid}"\n#\n# # {t}\n\n'
        slide_en = '# %% [markdown] lang="en" tags=["slide"] slide_id="{sid}"\n#\n# # {t}\n\n'
        de.write_text(
            HEADER_DE + slide_de.format(sid="s0", t="Eins") + slide_de.format(sid="s1", t="Zwei"),
            encoding="utf-8",
        )
        en.write_text(
            HEADER_EN + slide_en.format(sid="s0", t="One") + slide_en.format(sid="s1", t="Two"),
            encoding="utf-8",
        )
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        en.write_text(
            HEADER_EN + slide_en.format(sid="s1", t="Two") + slide_en.format(sid="s0", t="One"),
            encoding="utf-8",
        )
        result = cli_runner.invoke(slides_sync_group, ["report", str(de)])
        assert result.exit_code == 1
        assert "observation/group_order_divergence" in result.output
        assert "mirror_order" in result.output


class TestReportIdentity:
    """Schema 4's freshness token and deck identity (Q2 / finding C7, #649)."""

    def test_report_payload_carries_the_deck_identity_and_a_token(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, _en = _write_pair(tmp_path)
        payload = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        assert payload["deck_key"] == "slides_t"
        assert payload["ledger"].endswith("sync-ledger.json")
        assert isinstance(payload["report_id"], str) and payload["report_id"]
        # M14: the envelope says what the process exited with.
        assert payload["exit_code"] == 1

    def test_token_tracks_the_bundle_and_the_ledger(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)

        def token() -> str:
            out = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
            return _json_payload(out)["report_id"]

        cold = token()
        assert token() == cold  # stable: same bytes, same ledger

        # `record` changes no file, but it changes the trust half.
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        recorded = token()
        assert recorded != cold

        # An edit to EITHER half changes the bundle half.
        en.write_text(en.read_text(encoding="utf-8").replace("EN text", "EN edited"), "utf-8")
        assert token() != recorded

    def test_stale_token_refuses_the_whole_document_and_writes_nothing(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        before = en.read_text(encoding="utf-8")

        # The deck moves on after the report was taken (a sibling pass, an
        # editor save, the companion spelling of the same deck) — #649.
        de.write_text(de.read_text(encoding="utf-8").replace("DE neu", "DE neuer"), "utf-8")

        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {
                    "schema": WIRE_SCHEMA,
                    "report_id": report["report_id"],
                    "decisions": [{"key": "id:s0-m", "body": "# EN new"}],
                }
            ),
        )
        assert result.exit_code == 2
        assert en.read_text(encoding="utf-8") == before  # nothing written
        # The refusal is a JSON envelope, not an empty stdout (M14).
        payload = _json_payload(result.output)
        assert payload["exit_code"] == 2 and payload["wrote"] is False
        assert "report_id" in payload["error"]

    def test_matching_token_applies(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {
                    "schema": WIRE_SCHEMA,
                    "report_id": report["report_id"],
                    "decisions": [{"key": "id:s0-m", "body": "# EN new"}],
                }
            ),
        )
        assert result.exit_code == 0, result.output
        assert "# EN new" in en.read_text(encoding="utf-8")

    def test_document_without_a_token_is_accepted_with_a_warning(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Schema 3 predates the field; drivers emitting those documents keep
        # working for one release, but they are told what to add.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps({"decisions": [{"key": "id:s0-m", "body": "# EN new"}]}),
        )
        assert result.exit_code == 0, result.output
        assert "# EN new" in en.read_text(encoding="utf-8")
        assert "report_id" in _stderr(result)

    def test_unknown_document_schema_is_refused(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {"schema": 99, "decisions": [{"key": "id:s0-m", "choice": "confirm"}]}
            ),
        )
        assert result.exit_code == 2
        assert "schema 99" in _stderr(result) + result.output


class TestAnchorShapeTransition:
    """#653: a one-sided slide tag frames a tag row instead of refusing."""

    @staticmethod
    def _deck(lang: str, title: str, explain: str, *, explain_is_slide: bool) -> str:
        header = HEADER_DE if lang == "de" else HEADER_EN
        tags = ' tags=["slide"]' if explain_is_slide else ""
        return (
            header
            + f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="u-curve"\n#\n# # {title}\n\n'
            + f'# %% [markdown] lang="{lang}"{tags} slide_id="u-curve-explain"\n#\n# {explain}\n\n'
        )

    def _pair(self, tmp_path: Path) -> tuple[Path, Path]:
        de = tmp_path / "slides_b.de.py"
        en = tmp_path / "slides_b.en.py"
        de.write_text(self._deck("de", "U-Kurve", "Erklaerung", explain_is_slide=True), "utf-8")
        en.write_text(self._deck("en", "U-curve", "Explanation", explain_is_slide=True), "utf-8")
        return de, en

    def test_removing_the_slide_tag_on_one_half_frames_a_mechanical_mirror(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = self._pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0

        # The #653 edit: an explain slide becomes a continuation, DE only.
        de.write_text(self._deck("de", "U-Kurve", "Erklaerung", explain_is_slide=False), "utf-8")

        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        # It used to be: refusal duplicate_id, zero items, nothing to answer.
        assert report["refusal"] is None
        row = next(i for i in report["items"] if i["key"] == "id:u-curve-explain")
        assert (row["action"], row["direction"]) == ("mirror_tags", "de_to_en")
        assert row["resolution"] == "mechanical"
        (obs,) = [o for o in report["observations"] if o["kind"] == "anchor_shape_divergence"]
        assert obs["member"] == "id:u-curve-explain"

        # …and apply mirrors the shape onto the twin, with no decision needed.
        applied = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert applied.exit_code in (0, 1), applied.output
        assert 'tags=["slide"] slide_id="u-curve-explain"' not in en.read_text("utf-8")
        after = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        assert not [o for o in after["observations"] if o["kind"] == "anchor_shape_divergence"]

    def test_adding_the_slide_tag_on_one_half_mirrors_the_other_way(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de = tmp_path / "slides_b.de.py"
        en = tmp_path / "slides_b.en.py"
        de.write_text(self._deck("de", "U-Kurve", "Erklaerung", explain_is_slide=False), "utf-8")
        en.write_text(self._deck("en", "U-curve", "Explanation", explain_is_slide=False), "utf-8")
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        en.write_text(self._deck("en", "U-curve", "Explanation", explain_is_slide=True), "utf-8")

        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        assert report["refusal"] is None
        row = next(i for i in report["items"] if i["key"] == "id:u-curve-explain")
        assert (row["action"], row["direction"]) == ("mirror_tags", "en_to_de")


class TestActionDiscriminator:
    """Schema 4's optional `action` on a decision row (Q3)."""

    def test_matching_action_is_accepted(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {"decisions": [{"key": "id:s0-m", "action": "translate_edit", "body": "# EN new"}]}
            ),
        )
        assert result.exit_code == 0, result.output
        assert "# EN new" in en.read_text(encoding="utf-8")

    def test_answer_aimed_at_an_unframed_action_is_reported_not_silently_used(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Without the discriminator this row would have landed on whatever the
        # member happened to frame — an answer aimed at one row silently
        # executing another.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        before = en.read_text(encoding="utf-8")
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {"decisions": [{"key": "id:s0-m", "action": "verify_cold", "choice": "confirm"}]}
            ),
        )
        payload = _json_payload(result.output)
        (row,) = [i for i in payload["items"] if i["status"] == "rejected"]
        assert row["action"] == "verify_cold"
        assert "frames translate_edit, not 'verify_cold'" in row["reason"]
        assert en.read_text(encoding="utf-8") == before

    def test_duplicate_key_needs_distinct_actions(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {
                    "decisions": [
                        {"key": "id:s0-m", "choice": "confirm"},
                        {"key": "id:s0-m", "choice": "confirm"},
                    ]
                }
            ),
        )
        assert result.exit_code == 2
        assert "duplicate key" in _stderr(result) + result.output


class TestSanctionedFlows:
    """Q6a: the two flows the doctrine forbade doing by hand, in-engine."""

    def test_verify_translation_takes_a_body_for_the_named_side(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Both sides moved off base. `confirm` banks them as they are; a body
        # says "the named side is the wrong one" and fixes it in the same pass
        # instead of an out-of-band edit plus a second report (M7). The info
        # topic promised this answer; the engine used to reject it.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        en.write_text(en.read_text(encoding="utf-8").replace("EN text", "EN wrong"), "utf-8")

        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        item = next(i for i in report["items"] if i["key"] == "id:s0-m")
        assert item["action"] == "verify_translation"
        assert item["answers"] == ["confirm", "body"]

        applied = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {
                    "schema": WIRE_SCHEMA,
                    "report_id": report["report_id"],
                    "decisions": [{"key": "id:s0-m", "body": "# EN right", "side": "en"}],
                }
            ),
        )
        assert applied.exit_code == 0, applied.output
        assert "# EN right" in en.read_text(encoding="utf-8")
        assert "DE neu" in de.read_text(encoding="utf-8")  # the reviewed side stands

    def test_verify_translation_body_needs_a_side(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        en.write_text(en.read_text(encoding="utf-8").replace("EN text", "EN wrong"), "utf-8")
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps({"decisions": [{"key": "id:s0-m", "body": "# ambiguous"}]}),
        )
        payload = _json_payload(result.output)
        (row,) = [i for i in payload["items"] if i["key"] == "id:s0-m"]
        assert row["status"] == "rejected"
        assert "must name the 'side'" in row["reason"]

    def _fork_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        # A SHARED id'd cell — an id-less fork refuses the whole deck (M5).
        shared = '# %% [markdown] tags=["keep"] slide_id="shared-note"\n#\n# Shared note\n'
        de = tmp_path / "slides_f.de.py"
        en = tmp_path / "slides_f.en.py"
        de.write_text(
            HEADER_DE
            + '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n\n'
            + shared,
            encoding="utf-8",
        )
        en.write_text(
            HEADER_EN
            + '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n\n'
            + shared,
            encoding="utf-8",
        )
        return de, en

    def test_mark_twin_completes_a_fork_without_a_hand_edit(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # A fork in progress: the DE side gained a lang attribute, the twin has
        # none. The detail said "mark the twin" while the vocabulary was empty
        # and the doctrine forbids hand-editing the other language (M11 / F1).
        de, en = self._fork_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(
            de.read_text(encoding="utf-8").replace(
                '# %% [markdown] tags=["keep"]', '# %% [markdown] lang="de" tags=["keep"]'
            ),
            encoding="utf-8",
        )

        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        item = next(i for i in report["items"] if i["key"] == "id:shared-note")
        assert item["action"] == "fork_pending_twin"
        assert item["answers"] == ["mark_twin"]
        assert item["resolution"] == "decision"  # no longer a dead end

        applied = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {
                    "schema": WIRE_SCHEMA,
                    "report_id": report["report_id"],
                    "decisions": [{"key": "id:shared-note", "choice": "mark_twin"}],
                }
            ),
        )
        assert applied.exit_code == 0, applied.output
        en_text = en.read_text(encoding="utf-8")
        assert 'lang="en"' in en_text
        # ONLY the attribute — the body adaptation is the next pass's
        # translate_edit, not something mark_twin guesses.
        assert "Shared note" in en_text


class TestItemShape:
    """Schema 4's `resolution` discriminator and body-only excerpts (Q3)."""

    # A DE-only un-id'd code cell: a POSITIONAL one-sided cold member, the one
    # shape that carries no answer at all (finding M6).
    DE_EXTRA = DE + '\n# %% tags=["keep"]\ny = 2\n'

    def test_resolution_distinguishes_mechanical_decision_and_manual(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)
        de.write_text(self.DE_EXTRA, encoding="utf-8")
        payload = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        by_key = {i["key"]: i for i in payload["items"]}

        # An id-keyed cold member is answerable.
        assert by_key["id:s0"]["resolution"] == "decision"
        assert by_key["id:s0"]["answers"] == ["confirm", "body"]

        # The DE-only positional cell can be neither confirmed (confirm
        # asserts both halves agree) nor mirrored (its ordinal aliases a
        # different twin slot). It used to advertise `confirm` anyway, and the
        # rejection then blocked its whole pool with no visible cause.
        one_sided = by_key["pos:s0/code/1"]
        assert one_sided["answers"] == []
        assert one_sided["resolution"] == "manual"
        assert "half ONLY" in one_sided["detail"]
        assert "slide_id" in one_sided["detail"]  # names the repair

        # `answers: []` on a MECHANICAL row means the opposite — apply runs it.
        # (Restore the symmetric pair first: `record` refuses a structurally
        # divergent one, which is the gate doing its job.)
        de.write_text(DE, encoding="utf-8")
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(DE.replace("x = 1", "x = 42"), encoding="utf-8")
        mech = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        (row,) = [i for i in mech["items"] if i["action"] == "propagate_shared_edit"]
        assert row["answers"] == [] and row["resolution"] == "mechanical"

    def test_body_excerpts_are_valid_decision_input(self, cli_runner: CliRunner, tmp_path: Path):
        # M10: the `de`/`en` excerpts include the `# %%` delimiter that a body
        # answer must NOT contain, so report output was not decision input.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        item = next(i for i in report["items"] if i["key"] == "id:s0-m")
        assert item["de"].startswith("# %%")
        assert "# %%" not in item["de_body"]
        assert item["de_body"].strip() == "# DE neu"

        # Feed the excerpt straight back as an answer — no stripping needed.
        applied = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps(
                {
                    "schema": WIRE_SCHEMA,
                    "report_id": report["report_id"],
                    "decisions": [{"key": "id:s0-m", "body": item["de_body"]}],
                }
            ),
        )
        assert applied.exit_code == 0, applied.output
        assert "# DE neu" in en.read_text(encoding="utf-8")


class TestAlreadyApplied:
    """A decision whose effect already holds is not a rejection (#649)."""

    def test_answer_for_an_in_sync_member_is_already_applied_and_exits_zero(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        decisions = json.dumps({"decisions": [{"key": "id:s0-m", "body": "# EN new"}]})

        first = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
        )
        assert first.exit_code == 0, first.output
        assert "# EN new" in en.read_text(encoding="utf-8")

        # Re-running the SAME document: the member frames nothing now. The
        # effect the answer asks for holds, so this is success — not
        # "rejected, stale handle" while the write had demonstrably landed.
        second = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
        )
        assert second.exit_code == 0, second.output
        payload = _json_payload(second.output)
        assert payload["counts"]["already_applied"] == 1
        assert payload["counts"]["rejected"] == 0
        (item,) = [i for i in payload["items"] if i["key"] == "id:s0-m"]
        assert item["status"] == "already_applied"

    def test_answer_for_a_filtered_out_item_reads_as_skipped_not_applied(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # A decision for an item `--member` excluded is neither stale nor
        # satisfied — the filter did not run it. Classifying it as
        # `already_applied` would claim an effect that did not happen.
        de, _en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--member", "id:nothing-here", "--decisions", "-", "--json"],
            input=json.dumps({"decisions": [{"key": "id:s0-m", "body": "# EN new"}]}),
        )
        payload = _json_payload(result.output)
        assert payload["counts"]["already_applied"] == 0
        assert payload["counts"]["rejected"] == 0
        (item,) = [i for i in payload["items"] if i["key"] == "id:s0-m"]
        assert item["status"] == "skipped"
        assert "answer was not used" in item["reason"]

    def test_answer_for_an_unknown_member_is_still_rejected(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, _en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps({"decisions": [{"key": "id:no-such-member", "choice": "confirm"}]}),
        )
        assert result.exit_code == 1
        payload = _json_payload(result.output)
        assert payload["counts"]["rejected"] == 1
        (item,) = [i for i in payload["items"] if i["key"] == "id:no-such-member"]
        assert item["status"] == "rejected"
        assert "no member with this handle" in item["reason"]
