"""``clm harvest`` addressed through a course spec (#1104).

A tiny course with two topics whose ids share a suffix (``c_strings`` and
``strings``, the field case where a deck glob picked the wrong deck), split
deck pairs from ``test_harvest_cli``, and a recordings tree in the layout the
recordings workflow writes. Reports inject the fixture alignment (no ASR) by
wrapping ``_build_report_data``; everything else runs through the real CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands import harvest as harvest_cli
from clm.cli.commands.harvest import harvest_group
from tests.cli.test_harvest_cli import _write_alignment, _write_fixture

SPEC = """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Demo-Kurs</de><en>Demo Course</en></name>
    <prog-lang>python</prog-lang>
    <description><de>Demo</de><en>Demo</en></description>
    <certificate><de>.</de><en>.</en></certificate>
    <sections>
        <section>
            <name><de>01 Grundlagen</de><en>01 Basics</en></name>
            <topics>
                <topic>c_strings</topic>
                <topic>strings</topic>
            </topics>
        </section>
        <section id="w02">
            <name><de>02 Mehr</de><en>02 More</en></name>
            <topics>
                <topic>more</topic>
            </topics>
        </section>
    </sections>
</course>
"""


@pytest.fixture
def course(tmp_path: Path) -> dict:
    root = tmp_path / "course"
    spec = root / "course-specs" / "demo.xml"
    spec.parent.mkdir(parents=True)
    spec.write_text(SPEC, encoding="utf-8")
    decks = {}
    for module, topic in (
        ("module_100_basics/topic_100_c_strings", "c_strings"),
        ("module_100_basics/topic_110_strings", "strings"),
        ("module_200_more/topic_100_more", "more"),
    ):
        topic_dir = root / "slides" / module
        topic_dir.mkdir(parents=True)
        de_path, video = _write_fixture(topic_dir)
        video.unlink()
        decks[topic] = de_path
    recordings = tmp_path / "recordings"
    from clm.core.course_spec import CourseSpec

    video_root = recordings / "final" / CourseSpec.from_file(spec).output_dir_name["de"]
    return {"spec": spec, "decks": decks, "recordings": recordings, "video_root": video_root}


def _deck_names(course: dict) -> dict[str, tuple[str, str]]:
    """topic id -> (section folder, recording name), as the resolver computes them."""
    from clm.voiceover.harvest_batch import resolve_recorded_decks

    return {
        d.topic_id: (d.section_name, d.deck_name)
        for d in resolve_recorded_decks(course["spec"], "de", None)
    }


def _record(course: dict, topic: str, *suffixes: str) -> list[Path]:
    section, name = _deck_names(course)[topic]
    folder = course["video_root"] / section
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in suffixes:
        path = folder / f"{name}{suffix}.mp4"
        path.write_bytes(b"not a real video " + suffix.encode())
        paths.append(path)
    return paths


def _invoke(*args: str):
    return CliRunner().invoke(harvest_group, list(args), catch_exceptions=False)


def _json(result) -> dict:
    return json.loads(result.output[result.output.index("{") :])


def _batch(course: dict, *extra: str):
    return _invoke(
        "report",
        "--spec",
        str(course["spec"]),
        "--lang",
        "de",
        "--recordings-root",
        str(course["recordings"]),
        "--json",
        *extra,
    )


@pytest.fixture
def injected_alignment(course: dict, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every report gets the fixture alignment instead of running ASR."""
    alignment = _write_alignment(course["spec"].parent, course["decks"]["strings"])
    real = harvest_cli._build_report_data

    def fake(ctx, bundle, slides, videos, lang, _t, _a, model, backend, device, **kw):
        return real(
            ctx, bundle, slides, videos, lang, None, alignment, model, backend, device, **kw
        )

    monkeypatch.setattr(harvest_cli, "_build_report_data", fake)
    return alignment


class TestResolveOnly:
    def test_lists_every_deck_with_its_videos(self, course: dict) -> None:
        _record(course, "strings", "")
        result = _batch(course, "--resolve-only")
        assert result.exit_code == 0, result.output
        body = _json(result)
        assert body["verb"] == "batch-report"
        by_topic = {d["topic_id"]: d for d in body["decks"]}
        assert by_topic["strings"]["status"] == "recorded"
        assert by_topic["strings"]["parts"] == [0]
        assert by_topic["c_strings"]["status"] == "no_recording"
        assert by_topic["more"]["status"] == "no_recording"
        assert body["summary"]["recorded"] == 1
        assert body["summary"]["no_recording"] == 2
        assert body["summary"]["unclaimed_videos"] == []

    def test_topic_is_an_exact_id_never_a_suffix(self, course: dict) -> None:
        _record(course, "strings", "")
        _record(course, "c_strings", "")
        body = _json(_batch(course, "--resolve-only", "--topic", "strings"))
        assert [d["topic_id"] for d in body["decks"]] == ["strings"]
        assert body["decks"][0]["deck"].endswith("topic_110_strings/slides_t.de.py")

        result = _batch(course, "--resolve-only", "--topic", "string")
        assert result.exit_code == 2
        assert "exact topic id" in result.output

    def test_parts_in_part_order_and_a_gap_is_only_a_note(self, course: dict) -> None:
        third, first = _record(course, "strings", " (Teil 3)", " (Teil 1)")
        body = _json(_batch(course, "--resolve-only", "--topic", "strings"))
        deck = body["decks"][0]
        assert deck["status"] == "recorded"
        assert deck["parts"] == [1, 3]
        assert deck["videos"] == [str(first), str(third)]
        assert any("parts 2 are missing" in n for n in deck["notes"])

    def test_unnumbered_next_to_numbered_parts_is_an_error(self, course: dict) -> None:
        _record(course, "strings", "", " (Teil 1)")
        result = _batch(course, "--resolve-only")
        assert result.exit_code == 2
        body = _json(result)
        deck = next(d for d in body["decks"] if d["topic_id"] == "strings")
        assert deck["status"] == "error"
        assert "unnumbered video next to numbered parts" in deck["error"]
        # The deck's files are reported as its error, not again as unclaimed.
        assert body["summary"]["unclaimed_videos"] == []

    def test_a_video_no_deck_claims_is_listed(self, course: dict) -> None:
        section, _ = _deck_names(course)["strings"]
        stray = course["video_root"] / section / "07 Renamed Deck.mp4"
        stray.parent.mkdir(parents=True)
        stray.write_bytes(b"x")
        body = _json(_batch(course, "--resolve-only"))
        assert body["summary"]["unclaimed_videos"] == [str(stray)]

    def test_sections_by_id_or_position(self, course: dict) -> None:
        by_id = _json(_batch(course, "--resolve-only", "--section", "w02"))
        assert [d["topic_id"] for d in by_id["decks"]] == ["more"]
        by_position = _json(_batch(course, "--resolve-only", "--section", "1"))
        assert [d["topic_id"] for d in by_position["decks"]] == ["c_strings", "strings"]
        bad = _batch(course, "--resolve-only", "--section", "w09")
        assert bad.exit_code == 2
        assert "no section with id 'w09'" in bad.output


class TestBatchReport:
    def test_actionable_deck_carries_its_report_and_next_command(
        self, course: dict, injected_alignment: Path
    ) -> None:
        _record(course, "strings", "")
        result = _batch(course)
        assert result.exit_code == 1, result.output
        body = _json(result)
        deck = next(d for d in body["decks"] if d["topic_id"] == "strings")
        assert deck["status"] == "actionable"
        assert deck["report"]["summary"]["actionable"] is True
        assert deck["report"]["videos"] == deck["videos"]
        assert "clm harvest task --spec" in deck["next"]
        assert "--topic strings" in deck["next"]
        assert "--deck" not in deck["next"]
        assert body["summary"]["actionable"] == 1

    def test_one_failing_deck_does_not_stop_the_batch(
        self, course: dict, injected_alignment: Path
    ) -> None:
        _record(course, "strings", "")
        _record(course, "c_strings", "")
        # Break c_strings: an id-less slide makes the bundle refuse to load.
        broken = course["decks"]["c_strings"]
        broken.write_text(
            broken.read_text(encoding="utf-8").replace(' slide_id="s1"', "", 1),
            encoding="utf-8",
        )
        result = _batch(course)
        assert result.exit_code == 2
        statuses = {d["topic_id"]: d["status"] for d in _json(result)["decks"]}
        assert statuses["c_strings"] == "error"
        assert statuses["strings"] == "actionable"

    def test_transcript_overrides_are_refused(self, course: dict, tmp_path: Path) -> None:
        override = tmp_path / "t.json"
        override.write_text("{}", encoding="utf-8")
        result = _batch(course, "--transcript", str(override))
        assert result.exit_code == 2
        assert "single-video inputs" in result.output


class TestSingleDeckVerbs:
    def test_task_accept_record_then_unharvested_skips_the_deck(
        self, course: dict, injected_alignment: Path, tmp_path: Path
    ) -> None:
        _record(course, "strings", "")
        spec = str(course["spec"])
        task_result = _invoke(
            "task",
            "--spec",
            spec,
            "--topic",
            "strings",
            "--recordings-root",
            str(course["recordings"]),
            "--lang",
            "de",
            "--slide",
            "s1",
        )
        assert task_result.exit_code == 0, task_result.output
        task = _json(task_result)["tasks"][0]
        assert task["item"] == "id:s1"

        answer = {
            "item": task["item"],
            "kind": task["kind"],
            "video_fingerprint": task["video_fingerprint"],
            "baseline_fingerprints": task["baseline_fingerprints"],
            "updates": [{"member": None, "bullets": {"de": ["Alles über Beta."]}}],
            "dropped": [],
        }
        answer_path = tmp_path / "answer.json"
        answer_path.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
        accepted = _invoke(
            "accept",
            "--spec",
            spec,
            "--topic",
            "strings",
            "--answer",
            str(answer_path),
            "--record",
            "--json",
        )
        assert accepted.exit_code == 0, accepted.output

        verified = _invoke("verify", "--spec", spec, "--topic", "strings", "--json")
        assert verified.exit_code == 0, verified.output

        body = _json(_batch(course, "--unharvested", "--topic", "strings"))
        assert body["decks"][0]["status"] == "harvested"
        assert "report" not in body["decks"][0]

    def test_task_without_a_recording_exits_2(self, course: dict) -> None:
        result = _invoke(
            "task",
            "--spec",
            str(course["spec"]),
            "--topic",
            "strings",
            "--recordings-root",
            str(course["recordings"]),
            "--lang",
            "de",
        )
        assert result.exit_code == 2
        assert "no recording of" in result.output

    def test_spec_needs_a_topic_for_single_deck_verbs(self, course: dict) -> None:
        result = _invoke("verify", "--spec", str(course["spec"]))
        assert result.exit_code == 2
        assert "--spec needs --topic" in result.output


class TestTopicWithTwoDecks:
    @pytest.fixture
    def second_deck(self, course: dict) -> Path:
        first = course["decks"]["more"]
        for src in (first, first.with_name("slides_t.en.py")):
            src.with_name(src.name.replace("slides_t", "slides_u")).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        companions = first.parent / "voiceover"
        for lang in ("de", "en"):
            src = companions / f"voiceover_t.{lang}.py"
            (companions / f"voiceover_u.{lang}.py").write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        return first.with_name("slides_u.de.py")

    def test_ambiguous_topic_is_refused_and_deck_selects(
        self, course: dict, second_deck: Path
    ) -> None:
        spec = str(course["spec"])
        ambiguous = _invoke("verify", "--spec", spec, "--topic", "more")
        assert ambiguous.exit_code == 2
        assert "holds 2 decks" in ambiguous.output
        assert "--deck STEM" in ambiguous.output

        chosen = _invoke(
            "verify", "--spec", spec, "--topic", "more", "--deck", "slides_u", "--json"
        )
        assert chosen.exit_code == 0, chosen.output

    def test_next_command_names_the_deck(
        self, course: dict, second_deck: Path, injected_alignment: Path
    ) -> None:
        _record(course, "more", "")
        body = _json(_batch(course, "--topic", "more"))
        recorded = [d for d in body["decks"] if d["status"] == "actionable"]
        assert len(recorded) == 1
        # _record names the topic's last deck, slides_u (02 Titel DE).
        assert recorded[0]["deck"].endswith("slides_u.de.py")
        assert "--deck slides_u" in recorded[0]["next"]


class TestAddressing:
    def test_deck_and_spec_together_are_refused(self, course: dict) -> None:
        result = _invoke("verify", str(course["decks"]["strings"]), "--spec", str(course["spec"]))
        assert result.exit_code == 2
        assert "either DECK" in result.output

    def test_spec_only_options_need_spec(self, course: dict) -> None:
        result = _invoke("verify", str(course["decks"]["strings"]), "--topic", "strings")
        assert result.exit_code == 2
        assert "--topic only apply with --spec" in result.output

    def test_report_without_deck_or_spec(self) -> None:
        result = _invoke("report", "--lang", "de")
        assert result.exit_code == 2
        assert "missing DECK" in result.output

    def test_deck_form_still_needs_videos(self, course: dict) -> None:
        result = _invoke("report", str(course["decks"]["strings"]), "--lang", "de")
        assert result.exit_code == 2
        assert "missing VIDEO" in result.output
