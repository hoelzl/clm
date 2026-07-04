"""Tests for ``clm harvest task`` / ``accept`` / ``verify`` (#546 Phase 3/4).

Drives the full agent loop through the real CLI with an injected alignment
(no ASR/GPU): report → task → (synthesized judgment) → accept [--record] →
verify. The §6 hard requirement is pinned at the engine level: after a
one-sided ``accept --record``, the v3 sync differ (ledger baseline) must
frame the twin as *translation work* (``translate_new``/``translate_edit``)
— never ``in_sync`` (silently blessing the stale twin) and never a cold or
corrupt state.

Answers address narrative members individually (slides routinely carry one
narrative cell per code cell): ``updates`` entries name an existing member
or create a new one (optionally placed ``after`` an existing member).

Fixture decks come from ``test_harvest_cli`` (s0 has bilingual companion
voiceover, s1 has none, s2 has DE-only voiceover, s3 is silent).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group
from tests.cli.test_harvest_cli import (
    _companion_cell,
    _write_alignment,
    _write_fixture,
)


def _invoke(args: list[str]):
    return CliRunner().invoke(harvest_group, args, catch_exceptions=False)


def _json_from(result) -> dict:
    text = result.output
    return json.loads(text[text.index("{") :])


def _task_for(tmp_path: Path, de_path: Path, video: Path, slide: str, kind: str = "curate") -> dict:
    alignment = _write_alignment(tmp_path, de_path)
    result = _invoke(
        [
            "task",
            str(de_path),
            str(video),
            "--lang",
            "de",
            "--alignment",
            str(alignment),
            "--slide",
            slide,
            "--kind",
            kind,
        ]
    )
    assert result.exit_code == 0, result.output
    envelope = _json_from(result)
    assert len(envelope["tasks"]) == 1
    return envelope["tasks"][0]


def _answer_from_task(task: dict, updates: list[dict]) -> dict:
    return {
        "item": task["item"],
        "kind": task["kind"],
        "video_fingerprint": task["video_fingerprint"],
        "baseline_fingerprints": task["baseline_fingerprints"],
        "updates": updates,
        "dropped": ["Mikrofontest."],
    }


def _single_update_answer(task: dict, bullets: dict[str, list[str]], member: str | None) -> dict:
    return _answer_from_task(task, [{"member": member, "bullets": bullets}])


def _accept(tmp_path: Path, de_path: Path, answer: dict, *extra: str):
    answer_path = tmp_path / "answer.json"
    answer_path.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return _invoke(["accept", str(de_path), "--answer", str(answer_path), "--json", *extra])


def _ledger_diff(de_path: Path):
    """The v3 differ's verdicts against the committed ledger baseline."""
    from clm.slides import doc_ledger
    from clm.slides.doc_lenses import load_bundle
    from clm.slides.sync_diff import diff_outcome

    bundle = load_bundle(de_path)
    ledger = doc_ledger.load(doc_ledger.ledger_path_for(de_path))
    deck_ledger = ledger.decks.get(doc_ledger.deck_key_for(de_path))
    assert deck_ledger is not None, "the ledger has no deck section"
    base = doc_ledger.baseline_from_ledger(deck_ledger)
    return diff_outcome(bundle.outcome, base)


class TestHarvestTask:
    def test_curate_task_frames_instructions_inputs_and_tokens(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        assert task["item"] == "id:s1"
        assert task["kind"] == "curate"
        assert task["validator"] == "harvest-bullets"
        assert task["baseline_fingerprints"] == {}
        assert task["video_fingerprint"]
        assert "Preserve every substantive baseline bullet" in task["instructions"]
        assert task["inputs"]["baseline"] == []
        assert task["inputs"]["transcript"]["segments"] == ["Alles über Beta."]
        assert "# Beta" in task["inputs"]["slide"]["content"]
        assert task["answer_schema"]["required"] == [
            "item",
            "kind",
            "baseline_fingerprints",
            "updates",
            "dropped",
        ]

    def test_task_lists_every_narrative_cell_with_tokens(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        assert [c["member"] for c in task["inputs"]["baseline"]] == ["id:s0-vo"]
        cell = task["inputs"]["baseline"][0]
        assert "Bestand Alpha." in cell["de"]
        assert "Existing alpha." in cell["en"]
        tokens = task["baseline_fingerprints"]["id:s0-vo"]
        assert tokens["de"] and tokens["en"] and tokens["de"] != tokens["en"]

    def test_sweep_frames_every_actionable_item(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            ["task", str(de_path), str(video), "--lang", "de", "--alignment", str(alignment)]
        )
        assert result.exit_code == 0, result.output
        envelope = _json_from(result)
        assert sorted(t["item"] for t in envelope["tasks"]) == ["id:s0", "id:s1"]

    def test_silent_slide_cannot_be_framed(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "task",
                str(de_path),
                str(video),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--slide",
                "s3",
            ]
        )
        assert result.exit_code == 2
        assert "nothing to curate" in result.output

    def test_translate_task_frames_the_twin(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s2", kind="translate")
        assert task["inputs"]["source_language"] == "de"
        assert task["inputs"]["target_language"] == "en"
        cell = task["inputs"]["baseline"][0]
        assert cell["member"] == "id:s2-vo"
        assert "Bestand Gamma." in cell["de"]
        assert cell["en"] is None
        assert task["baseline_fingerprints"]["id:s2-vo"]["de"] is not None
        assert task["baseline_fingerprints"]["id:s2-vo"]["en"] is None


class TestHarvestAccept:
    def test_creates_a_new_vo_member_for_no_existing_vo(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(
            task, {"de": ["Beta ist wichtig.", "Beta hat Kanten."]}, member=None
        )
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["applied"] is True
        assert payload["members"] == [{"member": "id:s1-vo", "created": True}]

        companion = tmp_path / "voiceover" / "voiceover_t.de.py"
        text = companion.read_text(encoding="utf-8")
        assert 'for_slide="s1"' in text
        assert 'slide_id="s1-vo"' in text
        assert "# - Beta ist wichtig." in text

        # The written bundle re-parses and shows the member (lens gate held).
        from clm.slides.doc_lenses import load_bundle

        bundle = load_bundle(de_path)
        assert bundle.outcome.deck is not None
        keys = {m.key.render() for m in bundle.outcome.deck.members()}
        assert "id:s1-vo" in keys

    def test_one_sided_create_with_record_frames_translate_new(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(task, {"de": ["Beta ist wichtig."]}, member=None)
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 0, result.output
        assert _json_from(result)["recorded"] is True

        ledger_text = (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8")
        assert f"harvest:{task['video_fingerprint']}" in ledger_text

        diff = _ledger_diff(de_path)
        verdicts = {i.key: i.action for i in diff.items}
        # The §6 invariant: the recorded one-sided member frames the twin as
        # translation work — not cold, not in_sync.
        assert verdicts.get("id:s1-vo") == "translate_new"

    def test_one_sided_edit_with_record_frames_translate_edit(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        answer = _single_update_answer(task, {"de": ["Alpha, neu kuratiert."]}, member="id:s0-vo")
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["members"] == [{"member": "id:s0-vo", "created": False}]
        assert payload["recorded"] is True

        diff = _ledger_diff(de_path)
        items = {i.key: i for i in diff.items}
        assert "id:s0-vo" in items, "the one-sided edit must stay visible as translate work"
        assert items["id:s0-vo"].action == "translate_edit"
        assert items["id:s0-vo"].direction == "de_to_en"

    def test_bilingual_answer_with_record_is_in_sync(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        answer = _single_update_answer(
            task,
            {"de": ["Alpha, neu kuratiert."], "en": ["Alpha, freshly curated."]},
            member="id:s0-vo",
        )
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 0, result.output

        en_companion = tmp_path / "voiceover" / "voiceover_t.en.py"
        assert "# - Alpha, freshly curated." in en_companion.read_text(encoding="utf-8")

        diff = _ledger_diff(de_path)
        verdicts = {i.key: i.action for i in diff.items}
        assert "id:s0-vo" not in verdicts, "a bilingual accept records a clean pair"

    def test_stale_baseline_fingerprint_rejects(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        answer = _single_update_answer(task, {"de": ["Alpha."]}, member="id:s0-vo")
        answer["baseline_fingerprints"]["id:s0-vo"]["de"] = "0" * 64
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 2
        assert "changed since the task" in result.output

    def test_schema_violations_reject_without_writing(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        before = de_path.read_text(encoding="utf-8")
        bad_updates = [
            [],  # no updates
            [{"member": None, "bullets": {}}],  # no sides
            [{"member": None, "bullets": {"de": []}}],  # empty side
            [{"member": None, "bullets": {"de": ["a\nb"]}}],  # newline
            [{"member": None, "bullets": {"fr": ["x"]}}],  # unknown side
            [{"member": "id:nope", "bullets": {"de": ["x"]}}],  # unknown member
            [{"member": None, "after": "id:nope", "bullets": {"de": ["x"]}}],  # bad after
            [  # duplicate member updates
                {"member": "id:s0-vo", "bullets": {"de": ["x"]}},
                {"member": "id:s0-vo", "bullets": {"de": ["y"]}},
            ],
        ]
        for updates in bad_updates:
            answer = _answer_from_task(task, updates)
            result = _accept(tmp_path, de_path, answer)
            assert result.exit_code == 2, f"{updates}: {result.output}"
        assert de_path.read_text(encoding="utf-8") == before

    def test_record_needs_the_video_fingerprint(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(task, {"de": ["Beta."]}, member=None)
        del answer["video_fingerprint"]
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 2
        assert "video_fingerprint" in result.output

    def test_slide_option_must_match_the_answer(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(task, {"de": ["Beta."]}, member=None)
        result = _accept(tmp_path, de_path, answer, "--slide", "s0")
        assert result.exit_code == 2
        assert "does not match" in result.output

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        companion = tmp_path / "voiceover" / "voiceover_t.de.py"
        before = companion.read_text(encoding="utf-8")
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(task, {"de": ["Beta."]}, member=None)
        result = _accept(tmp_path, de_path, answer, "--dry-run")
        assert result.exit_code == 0, result.output
        assert _json_from(result)["dry_run"] is True
        assert companion.read_text(encoding="utf-8") == before
        assert not (tmp_path / ".clm" / "sync-ledger.json").exists()

    def test_unknown_slide_rejects(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(task, {"de": ["Beta."]}, member=None)
        answer["item"] = "id:missing"
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 2
        assert "no slide id:missing" in result.output


class TestMultiNarrativeSlides:
    """Slides with several narrative cells (one per code cell) — the
    ubiquitous shape the answer's per-member ``updates`` exist for."""

    def _fixture(self, tmp_path: Path) -> tuple[Path, Path]:
        de_path, video = _write_fixture(tmp_path)
        companion_dir = tmp_path / "voiceover"
        # A second narrative cell for s0 on both sides.
        extra_de = _companion_cell("s0-vo-code", "de", "s0", "Bestand Code-Zelle.")
        extra_en = _companion_cell("s0-vo-code", "en", "s0", "Existing code cell.")
        de_comp = companion_dir / "voiceover_t.de.py"
        en_comp = companion_dir / "voiceover_t.en.py"
        de_comp.write_text(
            de_comp.read_text(encoding="utf-8").rstrip("\n")
            + "\n\n"
            + extra_de.rstrip("\n")
            + "\n",
            encoding="utf-8",
        )
        en_comp.write_text(
            en_comp.read_text(encoding="utf-8").rstrip("\n")
            + "\n\n"
            + extra_en.rstrip("\n")
            + "\n",
            encoding="utf-8",
        )
        return de_path, video

    def test_task_frames_all_cells_and_accept_updates_each(self, tmp_path: Path) -> None:
        de_path, video = self._fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        assert [c["member"] for c in task["inputs"]["baseline"]] == ["id:s0-vo", "id:s0-vo-code"]
        assert set(task["baseline_fingerprints"]) == {"id:s0-vo", "id:s0-vo-code"}

        answer = _answer_from_task(
            task,
            [
                {"member": "id:s0-vo", "bullets": {"de": ["Alpha-Intro, neu."]}},
                {"member": "id:s0-vo-code", "bullets": {"de": ["Code-Zelle, neu."]}},
                {
                    "member": None,
                    "after": "id:s0-vo",
                    "bullets": {"de": ["Neue Zelle nach dem Intro."]},
                },
            ],
        )
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["members"] == [
            {"member": "id:s0-vo", "created": False},
            {"member": "id:s0-vo-code", "created": False},
            {"member": "id:s0-vo2", "created": True},
        ]

        text = (tmp_path / "voiceover" / "voiceover_t.de.py").read_text(encoding="utf-8")
        assert "# - Alpha-Intro, neu." in text
        assert "# - Code-Zelle, neu." in text
        # `after` placement: the new cell sits between the two existing ones.
        assert (
            text.index("Alpha-Intro, neu.")
            < text.index("Neue Zelle nach dem Intro.")
            < text.index("Code-Zelle, neu.")
        )

    def test_mixed_record_keeps_per_member_semantics(self, tmp_path: Path) -> None:
        de_path, video = self._fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        answer = _answer_from_task(
            task,
            [
                # one-sided edit over an existing twin -> translate_edit
                {"member": "id:s0-vo", "bullets": {"de": ["Nur DE geändert."]}},
                # bilingual edit -> clean pair, no diff item
                {
                    "member": "id:s0-vo-code",
                    "bullets": {"de": ["Beide Seiten."], "en": ["Both sides."]},
                },
            ],
        )
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 0, result.output
        assert _json_from(result)["recorded"] is True

        diff = _ledger_diff(de_path)
        verdicts = {i.key: i.action for i in diff.items}
        assert verdicts.get("id:s0-vo") == "translate_edit"
        assert "id:s0-vo-code" not in verdicts

    def test_new_cell_since_task_is_staleness(self, tmp_path: Path) -> None:
        de_path, video = self._fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        # A narrative cell appears after the task was framed.
        companion = tmp_path / "voiceover" / "voiceover_t.de.py"
        companion.write_text(
            companion.read_text(encoding="utf-8").rstrip("\n")
            + "\n\n"
            + _companion_cell("s0-vo-late", "de", "s0", "Nachzügler.").rstrip("\n")
            + "\n",
            encoding="utf-8",
        )
        answer = _answer_from_task(task, [{"member": "id:s0-vo", "bullets": {"de": ["Egal."]}}])
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 2
        assert "changed since the task" in result.output


class TestMultiPartVideos:
    def test_report_accepts_alignment_override_for_multi_part(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        video2 = tmp_path / "video-part2.mp4"
        video2.write_bytes(b"also not a real video")
        alignment = _write_alignment(tmp_path, de_path)
        result = _invoke(
            [
                "report",
                str(de_path),
                str(video),
                str(video2),
                "--lang",
                "de",
                "--alignment",
                str(alignment),
                "--json",
            ]
        )
        assert result.exit_code == 1, result.output
        report = _json_from(result)
        assert len(report["videos"]) == 2

        from clm.voiceover.cache import MultiVideoKey

        expected = MultiVideoKey.from_paths([video, video2]).hash
        assert report["video_fingerprint"] == expected


class TestHarvestVerify:
    def test_clean_pair_passes_with_pending_twin_listed(self, tmp_path: Path) -> None:
        de_path, _video = _write_fixture(tmp_path)
        result = _invoke(["verify", str(de_path), "--json"])
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["ok"] is True
        # The fixture's s2-vo exists in DE only: a representable pending
        # state, never a structural failure (§6).
        assert {"member": "id:s2-vo", "present": "de", "missing": "en"} in payload["pending_twins"]

    def test_pair_stays_verifiable_after_accept(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _single_update_answer(task, {"de": ["Beta ist wichtig."]}, member=None)
        assert _accept(tmp_path, de_path, answer, "--record").exit_code == 0
        result = _invoke(["verify", str(de_path)])
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output
        assert "id:s1-vo" in result.output  # the fresh harvest write is pending
