"""Tests for the revision-history task framing (#960): port and compare.

Drives the agent loop through the real CLI: ``task --kind port|compare
--source FILE`` frames; port answers land through the ordinary
``harvest accept`` (validator ``harvest-bullets``), compare answers through
``compare-accept`` (validator ``harvest-compare``) which writes the
canonical report JSON. No video, no model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group
from clm.slides.agent_task import VALIDATORS
from clm.voiceover.harvest_compare import (
    CompareRejected,
    build_compare_report_payload,
    build_compare_tasks,
    parse_compare_answer,
)
from clm.voiceover.harvest_port import build_port_tasks
from clm.voiceover.harvest_task import TaskUnavailable
from tests.cli.test_harvest_cli import HEADER_DE, _build, _slide, _write_fixture


def _notes_cell(lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["notes"]\n#\n# - {text}\n\n'


def _write_source(tmp_path: Path, *, s2_notes: bool = False) -> Path:
    """An older revision: same slide ids, inline notes (the port/compare source)."""
    parts = [
        HEADER_DE,
        _slide("s0", "de", "Alpha"),
        _notes_cell("de", "Alte Alpha-Notiz."),
        _slide("s1", "de", "Beta"),
        _notes_cell("de", "Alte Beta-Notiz."),
        _slide("s2", "de", "Gamma"),
    ]
    if s2_notes:
        parts.append(_notes_cell("de", "Alte Gamma-Notiz."))
    src = tmp_path / "slides_at_rev.de.py"
    src.write_text(_build(*parts), encoding="utf-8")
    return src


def _groups(path: Path) -> list:
    from clm.core.slide_text.slide_parser import parse_slides

    return parse_slides(path, "de", include_header=True)


def _deck(de_path: Path):
    from clm.slides.doc_lenses import load_bundle

    bundle = load_bundle(de_path)
    assert bundle.outcome.deck is not None
    return bundle.outcome.deck


def _invoke(args: list[str]):
    return CliRunner().invoke(harvest_group, args, catch_exceptions=False)


def _json_from(result) -> dict:
    text = result.output
    return json.loads(text[text.index("{") :])


# ---------------------------------------------------------------------------
# port framing
# ---------------------------------------------------------------------------


class TestPortTasks:
    def test_sweep_frames_pairs_with_source_bullets(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        tasks = build_port_tasks(_deck(de_path), _groups(de_path), _groups(source), lang="de")
        assert [t["item"] for t in tasks] == ["id:s0", "id:s1"]
        task = tasks[0]
        assert task["kind"] == "port"
        assert task["validator"] == "harvest-bullets"
        assert task["class"] in ("unchanged", "modified")
        # s0 has a bilingual baseline (companion cells) -> fingerprints framed
        assert set(task["baseline_fingerprints"]) == {"id:s0-vo"}
        assert "BASELINE" in task["instructions"] or "Baseline" in task["instructions"]
        assert task["inputs"]["prior_bullets"] == "- Alte Alpha-Notiz."
        # s1 has no baseline voiceover
        assert tasks[1]["baseline_fingerprints"] == {}

    def test_named_slide(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        tasks = build_port_tasks(
            _deck(de_path), _groups(de_path), _groups(source), lang="de", slide="s1"
        )
        assert [t["item"] for t in tasks] == ["id:s1"]

    def test_slide_without_source_bullets_unframeable(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        with pytest.raises(TaskUnavailable, match="no voiceover"):
            build_port_tasks(
                _deck(de_path), _groups(de_path), _groups(source), lang="de", slide="s2"
            )

    def test_unknown_slide(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        with pytest.raises(TaskUnavailable, match="no slide"):
            build_port_tasks(
                _deck(de_path), _groups(de_path), _groups(source), lang="de", slide="nope"
            )


class TestPortCli:
    def test_task_port_envelope(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        result = _invoke(
            ["task", str(de_path), "--lang", "de", "--kind", "port", "--source", str(source)]
        )
        assert result.exit_code == 0, result.output
        envelope = _json_from(result)
        assert envelope["verb"] == "task"
        assert envelope["kind"] == "port"
        assert envelope["source"] == str(source)
        assert [t["item"] for t in envelope["tasks"]] == ["id:s0", "id:s1"]

    def test_port_requires_source(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        result = _invoke(["task", str(de_path), "--lang", "de", "--kind", "port"])
        assert result.exit_code != 0
        assert "--source" in result.output

    def test_port_rejects_videos(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        result = _invoke(
            [
                "task",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--kind",
                "port",
                "--source",
                str(source),
            ]
        )
        assert result.exit_code != 0
        assert "no VIDEO" in result.output

    def test_curate_still_requires_videos(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        result = _invoke(["task", str(de_path), "--lang", "de"])
        assert result.exit_code != 0
        assert "VIDEO" in result.output

    def test_full_port_loop_lands_via_accept(self, tmp_path: Path) -> None:
        """task --kind port -> bullet answer -> harvest accept writes the deck."""
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        framed = _json_from(
            _invoke(
                [
                    "task",
                    str(de_path),
                    "--lang",
                    "de",
                    "--kind",
                    "port",
                    "--source",
                    str(source),
                    "--slide",
                    "s1",
                ]
            )
        )
        (task,) = framed["tasks"]
        answer = {
            "item": task["item"],
            "kind": "port",
            "baseline_fingerprints": task["baseline_fingerprints"],
            "updates": [{"member": None, "bullets": {"de": ["Alte Beta-Notiz, übernommen."]}}],
            "dropped": [],
        }
        answer_path = tmp_path / "answer.json"
        answer_path.write_text(json.dumps(answer), encoding="utf-8")
        result = _invoke(["accept", str(de_path), "--answer", str(answer_path), "--json"])
        assert result.exit_code == 0, result.output
        outcome = _json_from(result)
        assert outcome["applied"] is True
        assert outcome["members"][0]["created"] is True
        # The written member is readable through the v3 bundle.
        deck = _deck(de_path)
        texts = [
            cell.body
            for member in deck.members()
            if member.role in ("voiceover", "notes")
            for cell in (member.de, member.en)
            if cell is not None
        ]
        assert any("Alte Beta-Notiz, übernommen." in t for t in texts)


# ---------------------------------------------------------------------------
# compare framing + accept
# ---------------------------------------------------------------------------


class TestCompareTasks:
    def test_frames_judged_pairs_only(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        tasks = build_compare_tasks(_groups(source), _groups(de_path), lang="de")
        # s2/s3 have no bullets in the *files* on either side (the target's
        # companion voiceover is invisible to the file-level compare) and
        # s3 has no source counterpart at all -> only s0/s1 are framed.
        assert [t["item"] for t in tasks] == ["id:s0", "id:s1"]
        task = tasks[0]
        assert task["kind"] == "compare"
        assert task["validator"] == "harvest-compare"
        assert task["inputs"]["prior_bullets"] == "- Alte Alpha-Notiz."
        assert task["inputs"]["baseline_bullets"] == ""

    def test_compare_envelope_has_freshness_tokens(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        result = _invoke(
            ["task", str(de_path), "--lang", "de", "--kind", "compare", "--source", str(source)]
        )
        assert result.exit_code == 0, result.output
        envelope = _json_from(result)
        assert envelope["kind"] == "compare"
        assert envelope["source_fingerprint"]
        assert envelope["target_fingerprint"]


def _answer_for(envelope: dict, verdicts: list[dict]) -> dict:
    return {
        "kind": "compare",
        "source_fingerprint": envelope["source_fingerprint"],
        "target_fingerprint": envelope["target_fingerprint"],
        "verdicts": verdicts,
    }


def _all_covered_verdicts(envelope: dict) -> list[dict]:
    return [
        {
            "item": t["item"],
            "outcomes": [
                {"status": "dropped", "source": t["inputs"]["prior_bullets"], "note": "superseded"}
            ],
            "notes": None,
        }
        for t in envelope["tasks"]
    ]


class TestCompareAnswer:
    def _envelope(self, tmp_path: Path) -> dict:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        return _json_from(
            _invoke(
                ["task", str(de_path), "--lang", "de", "--kind", "compare", "--source", str(source)]
            )
        )

    def test_valid(self, tmp_path: Path) -> None:
        envelope = self._envelope(tmp_path)
        answer = parse_compare_answer(_answer_for(envelope, _all_covered_verdicts(envelope)))
        assert len(answer.verdicts) == 2

    def test_bad_status_rejected(self, tmp_path: Path) -> None:
        envelope = self._envelope(tmp_path)
        verdicts = _all_covered_verdicts(envelope)
        verdicts[0]["outcomes"][0]["status"] = "bogus"
        with pytest.raises(CompareRejected, match="status"):
            parse_compare_answer(_answer_for(envelope, verdicts))

    def test_duplicate_item_rejected(self, tmp_path: Path) -> None:
        envelope = self._envelope(tmp_path)
        verdicts = _all_covered_verdicts(envelope)
        verdicts.append(verdicts[0])
        with pytest.raises(CompareRejected, match="duplicate"):
            parse_compare_answer(_answer_for(envelope, verdicts))

    def test_registered_in_the_kit_registry(self):
        assert VALIDATORS.get("harvest-compare") is parse_compare_answer

    def test_report_requires_full_coverage(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        envelope = _json_from(
            _invoke(
                ["task", str(de_path), "--lang", "de", "--kind", "compare", "--source", str(source)]
            )
        )
        answer = parse_compare_answer(_answer_for(envelope, _all_covered_verdicts(envelope)[:1]))
        with pytest.raises(CompareRejected, match="no verdict"):
            build_compare_report_payload(
                source, de_path, _groups(source), _groups(de_path), answer, lang="de"
            )

    def test_report_rejects_unframed_verdicts(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        envelope = _json_from(
            _invoke(
                ["task", str(de_path), "--lang", "de", "--kind", "compare", "--source", str(source)]
            )
        )
        verdicts = _all_covered_verdicts(envelope)
        verdicts.append({"item": "id:ghost", "outcomes": []})
        answer = parse_compare_answer(_answer_for(envelope, verdicts))
        with pytest.raises(CompareRejected, match="unframed"):
            build_compare_report_payload(
                source, de_path, _groups(source), _groups(de_path), answer, lang="de"
            )


class TestCompareAcceptCli:
    def test_full_compare_loop_writes_report(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        envelope = _json_from(
            _invoke(
                ["task", str(de_path), "--lang", "de", "--kind", "compare", "--source", str(source)]
            )
        )
        answer_path = tmp_path / "answer.json"
        answer_path.write_text(
            json.dumps(_answer_for(envelope, _all_covered_verdicts(envelope))), encoding="utf-8"
        )
        out = tmp_path / "report.json"
        result = _invoke(
            [
                "compare-accept",
                str(source),
                str(de_path),
                "--lang",
                "de",
                "--answer",
                str(answer_path),
                "-o",
                str(out),
            ]
        )
        assert result.exit_code == 0, result.output
        report = json.loads(out.read_text(encoding="utf-8"))
        # The canonical compare-report shape (compare-report can re-render it).
        assert set(report) >= {
            "source",
            "target",
            "language",
            "slide_count",
            "status_totals",
            "kind_totals",
            "slides",
        }
        assert report["status_totals"]["dropped"] == 2
        judged = {s["key"]: s for s in report["slides"] if s["outcomes"]}
        assert set(judged) == {"id:s0", "id:s1"}
        # Deterministic rows still appear: s2 (matched, no bullets anywhere)
        # and s3 (new_at_head — no source counterpart).
        assert any(s["key"] == "id:s2" for s in report["slides"])
        assert any(s["key"] == "id:s3" and s["kind"] == "new_at_head" for s in report["slides"])

    def test_stale_target_fingerprint_rejects(self, tmp_path: Path) -> None:
        de_path, _ = _write_fixture(tmp_path)
        source = _write_source(tmp_path)
        envelope = _json_from(
            _invoke(
                ["task", str(de_path), "--lang", "de", "--kind", "compare", "--source", str(source)]
            )
        )
        # Touch the target so its content hash changes.
        de_path.write_text(de_path.read_text(encoding="utf-8") + "\n# comment\n", encoding="utf-8")
        answer_path = tmp_path / "answer.json"
        answer_path.write_text(
            json.dumps(_answer_for(envelope, _all_covered_verdicts(envelope))), encoding="utf-8"
        )
        result = _invoke(
            [
                "compare-accept",
                str(source),
                str(de_path),
                "--lang",
                "de",
                "--answer",
                str(answer_path),
                "--json",
            ]
        )
        assert result.exit_code == 2, result.output
        payload = _json_from(result)
        assert payload["applied"] is False
        assert payload["outcome"] == "rejected"
        assert "target_fingerprint mismatch" in payload["reason"]
