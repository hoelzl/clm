"""Tests for ``clm harvest task`` / ``accept`` / ``verify`` (#546 Phase 3).

Drives the full agent loop through the real CLI with an injected alignment
(no ASR/GPU): report → task → (synthesized judgment) → accept [--record] →
verify. The §6 hard requirement is pinned at the engine level: after a
one-sided ``accept --record``, the v3 sync differ (ledger baseline) must
frame the twin as *translation work* (``translate_new``/``translate_edit``)
— never ``in_sync`` (silently blessing the stale twin) and never a cold or
corrupt state.

Fixture decks come from ``test_harvest_cli`` (s0 has bilingual companion
voiceover, s1 has none, s2 has DE-only voiceover, s3 is silent).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group
from tests.cli.test_harvest_cli import _write_alignment, _write_fixture


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


def _answer_from_task(task: dict, bullets: dict[str, list[str]]) -> dict:
    return {
        "item": task["item"],
        "kind": task["kind"],
        "video_fingerprint": task["video_fingerprint"],
        "baseline_fingerprint": task["baseline_fingerprint"],
        "bullets": bullets,
        "dropped": ["Mikrofontest."],
    }


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
        assert task["baseline_fingerprint"] == {"de": None, "en": None}
        assert task["video_fingerprint"]
        assert "Preserve every substantive baseline bullet" in task["instructions"]
        assert task["inputs"]["transcript"]["segments"] == ["Alles über Beta."]
        assert "# Beta" in task["inputs"]["slide"]["content"]
        assert task["answer_schema"]["required"] == [
            "item",
            "kind",
            "baseline_fingerprint",
            "bullets",
            "dropped",
        ]

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
        assert "Bestand Gamma." in task["inputs"]["source"][0]
        assert task["inputs"]["target_baseline"] == []
        assert task["baseline_fingerprint"]["de"] is not None
        assert task["baseline_fingerprint"]["en"] is None


class TestHarvestAccept:
    def test_creates_a_new_vo_member_for_no_existing_vo(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _answer_from_task(task, {"de": ["Beta ist wichtig.", "Beta hat Kanten."]})
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["applied"] is True
        assert payload["created"] is True
        assert payload["member"] == "id:s1-vo"

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
        answer = _answer_from_task(task, {"de": ["Beta ist wichtig."]})
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
        answer = _answer_from_task(task, {"de": ["Alpha, neu kuratiert."]})
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 0, result.output
        payload = _json_from(result)
        assert payload["created"] is False
        assert payload["member"] == "id:s0-vo"
        assert payload["recorded"] is True

        diff = _ledger_diff(de_path)
        items = {i.key: i for i in diff.items}
        assert "id:s0-vo" in items, "the one-sided edit must stay visible as translate work"
        assert items["id:s0-vo"].action == "translate_edit"
        assert items["id:s0-vo"].direction == "de_to_en"

    def test_bilingual_answer_with_record_is_in_sync(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s0")
        answer = _answer_from_task(
            task,
            {"de": ["Alpha, neu kuratiert."], "en": ["Alpha, freshly curated."]},
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
        answer = _answer_from_task(task, {"de": ["Alpha."]})
        answer["baseline_fingerprint"] = {
            "de": "0" * 64,
            "en": answer["baseline_fingerprint"]["en"],
        }
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 2
        assert "changed since the task" in result.output

    def test_schema_violations_reject_without_writing(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        before = de_path.read_text(encoding="utf-8")
        for bad_bullets in ({}, {"de": []}, {"de": ["a\nb"]}, {"fr": ["x"]}):
            answer = _answer_from_task(task, {"de": ["ok"]})
            answer["bullets"] = bad_bullets
            result = _accept(tmp_path, de_path, answer)
            assert result.exit_code == 2, f"{bad_bullets}: {result.output}"
        assert de_path.read_text(encoding="utf-8") == before

    def test_record_needs_the_video_fingerprint(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _answer_from_task(task, {"de": ["Beta."]})
        del answer["video_fingerprint"]
        result = _accept(tmp_path, de_path, answer, "--record")
        assert result.exit_code == 2
        assert "video_fingerprint" in result.output

    def test_slide_option_must_match_the_answer(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _answer_from_task(task, {"de": ["Beta."]})
        result = _accept(tmp_path, de_path, answer, "--slide", "s0")
        assert result.exit_code == 2
        assert "does not match" in result.output

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        companion = tmp_path / "voiceover" / "voiceover_t.de.py"
        before = companion.read_text(encoding="utf-8")
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _answer_from_task(task, {"de": ["Beta."]})
        result = _accept(tmp_path, de_path, answer, "--dry-run")
        assert result.exit_code == 0, result.output
        assert _json_from(result)["dry_run"] is True
        assert companion.read_text(encoding="utf-8") == before
        assert not (tmp_path / ".clm" / "sync-ledger.json").exists()

    def test_unknown_slide_rejects(self, tmp_path: Path) -> None:
        de_path, video = _write_fixture(tmp_path)
        task = _task_for(tmp_path, de_path, video, "s1")
        answer = _answer_from_task(task, {"de": ["Beta."]})
        answer["item"] = "id:missing"
        result = _accept(tmp_path, de_path, answer)
        assert result.exit_code == 2
        assert "no slide id:missing" in result.output


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
        answer = _answer_from_task(task, {"de": ["Beta ist wichtig."]})
        assert _accept(tmp_path, de_path, answer, "--record").exit_code == 0
        result = _invoke(["verify", str(de_path)])
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output
        assert "id:s1-vo" in result.output  # the fresh harvest write is pending
