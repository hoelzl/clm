"""Tests for the shared bullet schema used by port and compare."""

from __future__ import annotations

import json

from clm.voiceover.bullet_schema import (
    BulletOutcome,
    BulletStatus,
    PerSlidePack,
    parse_structured_response,
)


class TestBulletStatus:
    def test_values_are_strings(self):
        assert BulletStatus.COVERED.value == "covered"
        assert BulletStatus.REWRITTEN.value == "rewritten"
        assert BulletStatus.ADDED.value == "added"
        assert BulletStatus.DROPPED.value == "dropped"
        assert BulletStatus.MANUAL_REVIEW.value == "manual_review"

    def test_roundtrip_through_string(self):
        for status in BulletStatus:
            assert BulletStatus(status.value) is status


class TestBulletOutcome:
    def test_to_json_omits_none_fields(self):
        outcome = BulletOutcome(status=BulletStatus.ADDED, target="- new bullet")
        payload = outcome.to_json()
        assert payload == {"status": "added", "target": "- new bullet"}

    def test_to_json_includes_all_fields(self):
        outcome = BulletOutcome(
            status=BulletStatus.REWRITTEN,
            target="- corrected bullet",
            source="- original bullet",
            note="factual correction",
        )
        payload = outcome.to_json()
        assert payload["status"] == "rewritten"
        assert payload["note"] == "factual correction"

    def test_from_json_recognised_status(self):
        outcome = BulletOutcome.from_json({"status": "covered", "target": "- x"})
        assert outcome.status is BulletStatus.COVERED
        assert outcome.target == "- x"

    def test_from_json_unknown_status_maps_to_manual_review(self):
        outcome = BulletOutcome.from_json({"status": "bogus"})
        assert outcome.status is BulletStatus.MANUAL_REVIEW


class TestPerSlidePack:
    def test_build_user_message_includes_all_sections(self):
        pack = PerSlidePack(
            slide_id="topic/3",
            language="de",
            baseline_bullets="- existing point",
            prior_bullets="- prior point",
            slide_content_head="## Head Title\nsome text",
        )
        msg = pack.build_user_message()
        assert "SLIDE ID: topic/3" in msg
        assert "Head Title" in msg
        assert "PRIOR BULLETS" in msg
        assert "prior point" in msg
        assert "BASELINE BULLETS" in msg
        assert "existing point" in msg

    def test_prior_content_shown_only_when_changed(self):
        pack = PerSlidePack(
            slide_id="x/1",
            language="en",
            baseline_bullets="",
            prior_bullets="- prior",
            slide_content_head="head content",
            slide_content_prior="prior content",
            content_changed=True,
        )
        msg = pack.build_user_message()
        assert "prior content" in msg
        assert "prior/source version" in msg

    def test_prior_content_hidden_when_unchanged(self):
        pack = PerSlidePack(
            slide_id="x/1",
            language="en",
            baseline_bullets="",
            prior_bullets="- prior",
            slide_content_head="head content",
            slide_content_prior="prior content",
            content_changed=False,
        )
        msg = pack.build_user_message()
        assert "prior content" not in msg

    def test_empty_bullets_placeholder(self):
        pack = PerSlidePack(
            slide_id="x/1",
            language="en",
            baseline_bullets="",
            prior_bullets="",
            slide_content_head="",
        )
        msg = pack.build_user_message()
        assert "(empty)" in msg
        assert "empty -- no existing voiceover" in msg


class TestParseStructuredResponse:
    def test_basic_response(self):
        raw = json.dumps(
            {
                "bullets": "- a\n- b",
                "outcomes": [
                    {"status": "covered", "target": "- a", "source": "- a"},
                    {"status": "added", "target": "- b"},
                ],
                "notes": "Straightforward port.",
            }
        )
        bullets, outcomes, notes = parse_structured_response(raw)
        assert bullets == "- a\n- b"
        assert len(outcomes) == 2
        assert outcomes[0].status is BulletStatus.COVERED
        assert outcomes[1].status is BulletStatus.ADDED
        assert notes == "Straightforward port."

    def test_strips_code_fences(self):
        raw = "```json\n" + json.dumps({"bullets": "- x"}) + "\n```"
        bullets, _, _ = parse_structured_response(raw)
        assert bullets == "- x"

    def test_accepts_merged_bullets_alias(self):
        raw = json.dumps({"merged_bullets": "- alias"})
        bullets, _, _ = parse_structured_response(raw)
        assert bullets == "- alias"

    def test_accepts_wrapped_result(self):
        raw = json.dumps({"result": {"bullets": "- wrapped"}})
        bullets, _, _ = parse_structured_response(raw)
        assert bullets == "- wrapped"

    def test_invalid_json_returns_default(self):
        bullets, outcomes, notes = parse_structured_response(
            "not json at all", default_bullets="- fallback"
        )
        assert bullets == "- fallback"
        assert outcomes == []
        assert notes is None

    def test_non_object_returns_default(self):
        raw = json.dumps(["a", "list", "not", "an", "object"])
        bullets, outcomes, notes = parse_structured_response(raw, default_bullets="")
        assert bullets == ""
        assert outcomes == []

    def test_notes_missing_returns_none(self):
        raw = json.dumps({"bullets": "- x", "outcomes": []})
        _, _, notes = parse_structured_response(raw)
        assert notes is None

    def test_empty_notes_returns_none(self):
        raw = json.dumps({"bullets": "- x", "notes": "   "})
        _, _, notes = parse_structured_response(raw)
        assert notes is None
