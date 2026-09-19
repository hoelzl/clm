"""CLI tests for ``clm harvest align report`` / ``align accept`` (#960).

Drives both verbs through the real CLI with an injected alignment carrying
assignment records (no ASR/GPU): report frames the uncertain items, accept
validates the answer (freshness included) and writes a full corrected
alignment file that a following ``report --alignment`` run picks up.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group
from tests.cli.test_harvest_cli import _slide_indices, _write_fixture


def _invoke(args: list[str]):
    return CliRunner().invoke(harvest_group, args, catch_exceptions=False)


def _json_from(result) -> dict:
    text = result.output
    return json.loads(text[text.index("{") :])


def _write_alignment_with_records(tmp_path: Path, de_path: Path) -> Path:
    """An injected alignment with one straddled segment (s1/s2 boundary)."""
    idx = _slide_indices(de_path)
    payload = {
        "slide_notes": {
            str(idx["s1"]): {
                "slide_index": idx["s1"],
                "segments": ["Alles über Beta."],
                "revisited_segments": [],
            },
        },
        "unassigned_segments": [],
        "assignments": [
            {
                "segment": {"start": 8.0, "end": 18.0, "text": "Alles über Beta."},
                "slide_index": idx["s1"],
                "reason": "previous_slide_bias",
                "overlap_fraction": 0.6,
                "runner_up_index": idx["s2"],
                "runner_up_fraction": 0.4,
            },
        ],
    }
    path = tmp_path / "alignment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _align_report(tmp_path: Path, de_path: Path, video: Path, alignment: Path):
    return _invoke(
        ["align", "report", str(de_path), str(video), "--lang", "de", "--alignment", str(alignment)]
    )


class TestAlignReportCli:
    def test_frames_the_straddled_segment(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment_with_records(tmp_path, de_path)
        result = _align_report(tmp_path, de_path, video, alignment)
        assert result.exit_code == 1, result.output
        report = _json_from(result)
        assert report["verb"] == "align-report"
        assert report["validator"] == "harvest-align"
        (item,) = report["items"]
        assert item["kind"] == "segment_assignment"
        assert item["segment_index"] == 0
        assert item["reason"] == "previous_slide_bias"
        assert item["assigned_title"] == "Beta"
        assert item["runner_up_title"] == "Gamma"

    def test_legacy_alignment_without_records_notes_refresh(self, tmp_path: Path) -> None:
        from tests.cli.test_harvest_cli import _write_alignment

        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)  # no assignment records
        result = _align_report(tmp_path, de_path, video, alignment)
        assert result.exit_code == 1, result.output  # the unmatched segment still frames
        report = _json_from(result)
        assert "refresh-cache" in report["note"]
        assert all(i["segment_index"] is None for i in report["items"])

    def test_clean_alignment_exits_0(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        idx = _slide_indices(de_path)
        payload = {
            "slide_notes": {},
            "unassigned_segments": [],
            "assignments": [
                {
                    "segment": {"start": 1.0, "end": 3.0, "text": "Klar."},
                    "slide_index": idx["s1"],
                    "reason": "clear",
                    "overlap_fraction": 1.0,
                    "runner_up_index": None,
                    "runner_up_fraction": None,
                },
            ],
        }
        alignment = tmp_path / "alignment.json"
        alignment.write_text(json.dumps(payload), encoding="utf-8")
        result = _align_report(tmp_path, de_path, video, alignment)
        assert result.exit_code == 0, result.output
        assert _json_from(result)["items"] == []


class TestAlignAcceptCli:
    def _answer(self, report: dict, *moves: tuple[int, int | None]) -> dict:
        return {
            "video_fingerprint": report["video_fingerprint"],
            "alignment_fingerprint": report["alignment_fingerprint"],
            "reassignments": [{"segment_index": i, "to_slide": target} for i, target in moves],
        }

    def test_accept_writes_a_loadable_corrected_alignment(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment_with_records(tmp_path, de_path)
        idx = _slide_indices(de_path)
        report = _json_from(_align_report(tmp_path, de_path, video, alignment))

        answer = tmp_path / "answer.json"
        answer.write_text(json.dumps(self._answer(report, (0, idx["s2"]))), encoding="utf-8")
        out = tmp_path / "corrected.json"
        result = _invoke(
            [
                "align",
                "accept",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--answer",
                str(answer),
                "--output",
                str(out),
                "--json",
            ]
        )
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["applied"] is True
        assert payload["written"] == str(out)

        # The corrected file loads as an alignment override and the next
        # report reflects the move (s2 now carries the speech).
        report2 = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(out),
                "--json",
            ]
        )
        assert report2.exit_code == 1, report2.output
        by_key = {item["key"]: item for item in _json_from(report2)["items"]}
        assert by_key["id:s1"]["class"] in ("covered", "unmatched_slide")
        assert by_key["id:s2"]["transcript"]["segments"] == ["Alles über Beta."]

    def test_default_output_path_beside_the_deck(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment_with_records(tmp_path, de_path)
        idx = _slide_indices(de_path)
        report = _json_from(_align_report(tmp_path, de_path, video, alignment))
        answer = tmp_path / "answer.json"
        answer.write_text(json.dumps(self._answer(report, (0, idx["s2"]))), encoding="utf-8")
        result = _invoke(
            [
                "align",
                "accept",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--answer",
                str(answer),
            ]
        )
        assert result.exit_code == 0, result.output
        assert de_path.with_suffix(".alignment.json").exists()

    def test_stale_fingerprint_rejects_with_envelope(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment_with_records(tmp_path, de_path)
        idx = _slide_indices(de_path)
        report = _json_from(_align_report(tmp_path, de_path, video, alignment))
        answer = self._answer(report, (0, idx["s2"]))
        answer["alignment_fingerprint"] = "stale"
        answer_path = tmp_path / "answer.json"
        answer_path.write_text(json.dumps(answer), encoding="utf-8")
        result = _invoke(
            [
                "align",
                "accept",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--answer",
                str(answer_path),
                "--json",
            ]
        )
        assert result.exit_code == 2, result.output
        payload = _json_from(result)
        assert payload["applied"] is False
        assert payload["outcome"] == "rejected"
        assert "alignment_fingerprint mismatch" in payload["reason"]

    def test_legacy_alignment_rejects_with_refresh_hint(self, tmp_path: Path) -> None:
        from tests.cli.test_harvest_cli import _write_alignment

        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)  # no assignment records
        report = _json_from(_align_report(tmp_path, de_path, video, alignment))
        idx = _slide_indices(de_path)
        answer = tmp_path / "answer.json"
        answer.write_text(json.dumps(self._answer(report, (0, idx["s1"]))), encoding="utf-8")
        result = _invoke(
            [
                "align",
                "accept",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--answer",
                str(answer),
            ]
        )
        assert result.exit_code == 2, result.output
        assert "no assignment records" in result.output

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment_with_records(tmp_path, de_path)
        idx = _slide_indices(de_path)
        report = _json_from(_align_report(tmp_path, de_path, video, alignment))
        answer = tmp_path / "answer.json"
        answer.write_text(json.dumps(self._answer(report, (0, idx["s2"]))), encoding="utf-8")
        out = tmp_path / "corrected.json"
        result = _invoke(
            [
                "align",
                "accept",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--answer",
                str(answer),
                "--output",
                str(out),
                "--dry-run",
            ]
        )
        assert result.exit_code == 0, result.output
        assert not out.exists()
