"""Tests for ``clm harvest report`` (#546 Phase 2).

The report joins an injected alignment (no ASR/ffmpeg/GPU — the
``--alignment`` short-circuit) with a tiny inline deck bundle, so the
structural novelty classification and the JSON envelope are pinned end to
end through the real CLI. One slide per class:

* ``s0`` — voiceover present + speech assigned → ``transcript_adds_material``
* ``s1`` — no voiceover + speech assigned     → ``no_existing_vo``
* ``s2`` — voiceover present (DE only) + silent → ``covered``
* ``s3`` — nothing                             → ``unmatched_slide``

plus an unassigned segment and a stale slide index → ``unmatched_speech``.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _companion_cell(slug: str, lang: str, owner: str, text: str) -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["notes"] for_slide="{owner}" '
        f'vo_anchor="id:{owner}#0" slide_id="{slug}"\n#\n# - {text}\n\n'
    )


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """The deck pair + companions + a dummy video; returns (de_path, video)."""
    de = _build(
        HEADER_DE,
        _slide("s0", "de", "Alpha"),
        _slide("s1", "de", "Beta"),
        _slide("s2", "de", "Gamma"),
        _slide("s3", "de", "Delta"),
    )
    en = _build(
        HEADER_EN,
        _slide("s0", "en", "Alpha"),
        _slide("s1", "en", "Beta"),
        _slide("s2", "en", "Gamma"),
        _slide("s3", "en", "Delta"),
    )
    de_path = tmp_path / "slides_t.de.py"
    en_path = tmp_path / "slides_t.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")

    companion_dir = tmp_path / "voiceover"
    companion_dir.mkdir()
    de_comp = _build(
        _companion_cell("s0-vo", "de", "s0", "Bestand Alpha."),
        _companion_cell("s2-vo", "de", "s2", "Bestand Gamma."),
    )
    en_comp = _build(_companion_cell("s0-vo", "en", "s0", "Existing alpha."))
    (companion_dir / "voiceover_t.de.py").write_text(de_comp, encoding="utf-8")
    (companion_dir / "voiceover_t.en.py").write_text(en_comp, encoding="utf-8")

    video = tmp_path / "video.mp4"
    video.write_bytes(b"not a real video")
    return de_path, video


def _slide_indices(de_path: Path) -> dict[str, int]:
    from clm.notebooks.slide_parser import parse_slides

    groups = parse_slides(de_path, "de")
    return {
        sg.cells[0].slide_id: sg.index
        for sg in groups
        if sg.slide_type != "header" and sg.cells and sg.cells[0].slide_id
    }


def _write_alignment(tmp_path: Path, de_path: Path) -> Path:
    idx = _slide_indices(de_path)
    payload = {
        "slide_notes": {
            str(idx["s0"]): {
                "slide_index": idx["s0"],
                "segments": ["Neues zu Alpha."],
                "revisited_segments": [["Nachtrag zu Alpha."]],
            },
            str(idx["s1"]): {
                "slide_index": idx["s1"],
                "segments": ["Alles über Beta."],
                "revisited_segments": [],
            },
            "99": {
                "slide_index": 99,
                "segments": ["Geist einer gelöschten Folie."],
                "revisited_segments": [],
            },
        },
        "unassigned_segments": [{"start": 0.0, "end": 2.0, "text": "Mikrofontest."}],
    }
    path = tmp_path / "alignment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _invoke(args: list[str]):
    return CliRunner().invoke(harvest_group, args, catch_exceptions=False)


def _report_from(result) -> dict:
    text = result.output
    return json.loads(text[text.index("{") :])


class TestHarvestReport:
    def test_classifies_every_slide_and_exits_1(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--json",
            ]
        )
        assert result.exit_code == 1, result.output
        report = _report_from(result)

        by_key = {item["key"]: item for item in report["items"]}
        assert by_key["id:s0"]["class"] == "transcript_adds_material"
        assert by_key["id:s1"]["class"] == "no_existing_vo"
        assert by_key["id:s2"]["class"] == "covered"
        assert by_key["id:s3"]["class"] == "unmatched_slide"

        assert report["schema"] == 1
        assert report["video_language"] == "de"
        assert report["video_fingerprint"]
        assert report["summary"]["actionable"] is True
        assert report["summary"]["classes"] == {
            "no_existing_vo": 1,
            "transcript_adds_material": 1,
            "covered": 1,
            "unmatched_slide": 1,
        }

    def test_transcript_and_voiceover_payloads(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--json",
            ]
        )
        report = _report_from(result)
        by_key = {item["key"]: item for item in report["items"]}

        s0 = by_key["id:s0"]
        assert s0["transcript"]["segments"] == ["Neues zu Alpha."]
        assert s0["transcript"]["revisited_segments"] == [["Nachtrag zu Alpha."]]
        assert "**[Revisited]**" in s0["transcript"]["text"]
        # Both language sides of the baseline are visible to the agent.
        assert s0["voiceover"]["de"]["present"] is True
        assert s0["voiceover"]["en"]["present"] is True
        assert s0["voiceover"]["de"]["cells"][0]["key"] == "id:s0-vo"
        assert s0["voiceover"]["de"]["cells"][0]["layout"] == "companion"

        # s2's twin has no voiceover — a one-sided baseline is representable.
        s2 = by_key["id:s2"]
        assert s2["voiceover"]["de"]["present"] is True
        assert s2["voiceover"]["en"]["present"] is False

        # The silent slides carry no transcript payload.
        assert "transcript" not in by_key["id:s3"]

    def test_unmatched_speech_collects_unassigned_and_stale_indices(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--json",
            ]
        )
        report = _report_from(result)
        texts = [entry["text"] for entry in report["unmatched_speech"]]
        assert "Mikrofontest." in texts
        assert "Geist einer gelöschten Folie." in texts
        assert report["summary"]["unmatched_speech"] == 2

    def test_silent_recording_exits_0(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        empty = tmp_path / "empty-alignment.json"
        empty.write_text(json.dumps({"slide_notes": {}, "unassigned_segments": []}), "utf-8")
        result = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(empty),
                "--json",
            ]
        )
        assert result.exit_code == 0, result.output
        report = _report_from(result)
        assert report["summary"]["actionable"] is False
        classes = {item["class"] for item in report["items"]}
        assert classes == {"covered", "unmatched_slide"}

    def test_bare_deck_video_defaults_to_report(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [str(de_path), str(video), "--lang", "de", "--alignment", str(alignment), "--json"]
        )
        assert result.exit_code == 1, result.output
        assert _report_from(result)["verb"] == "report"

    def test_default_verb_works_after_group_options(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "--no-cache",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--json",
            ]
        )
        assert result.exit_code == 1, result.output

    def test_non_normalized_deck_exits_2(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        # An id-less slide makes the v3 lens refuse the bundle.
        idless = '# %% [markdown] lang="de" tags=["slide"]\n#\n# # Ohne Id\n\n'
        de_path.write_text(de_path.read_text(encoding="utf-8") + idless, encoding="utf-8")
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--json",
            ]
        )
        assert result.exit_code == 2, result.output

    def test_human_output_lists_actionable_items(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            ["report", str(de_path), str(video), "--lang", "de", "--alignment", str(alignment)]
        )
        assert result.exit_code == 1
        assert "no_existing_vo" in result.output
        assert "id:s1" in result.output
        assert "id:s3" not in result.output  # non-actionable rows stay out


class TestHarvestGroup:
    def test_help_lists_verbs(self) -> None:
        result = _invoke(["--help"])
        assert result.exit_code == 0
        for verb in (
            "report",
            "transcribe",
            "detect",
            "identify",
            "identify-rev",
            "cache",
            "trace",
        ):
            assert verb in result.output

    def test_rehomed_diagnostics_resolve(self) -> None:
        for verb in ("transcribe", "detect", "identify", "identify-rev", "trace"):
            result = _invoke([verb, "--help"])
            assert result.exit_code == 0, f"{verb}: {result.output}"

    def test_report_help_documents_exit_codes(self) -> None:
        result = _invoke(["report", "--help"])
        assert result.exit_code == 0
        assert "--lang" in result.output
        assert "--alignment" in result.output
        assert "Exit codes" in result.output
