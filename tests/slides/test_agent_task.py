"""The shared agent-task kit (#959): envelope, freshness, registry, exits.

These pin the kit's own behavior plus its wiring: the validator labels the
task documents announce must resolve through the registry to the parsers
the accept paths run.
"""

from __future__ import annotations

import json

import pytest

from clm.slides.agent_task import (
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_WORK_PENDING,
    VALIDATORS,
    AnswerRejected,
    ValidatorRegistry,
    envelope,
    freshness_mismatch,
    rejection_payload,
)


class TestExitCodes:
    def test_convention_values(self):
        assert (EXIT_CLEAN, EXIT_WORK_PENDING, EXIT_ERROR) == (0, 1, 2)


class TestEnvelope:
    def test_tool_verb_identity_leads_in_order(self):
        payload = envelope(1, tool="harvest", verb="task", body={"tasks": [], "extra": 1})
        assert list(payload) == ["schema", "tool", "verb", "tasks", "extra"]
        assert payload["schema"] == 1

    def test_engine_identity(self):
        payload = envelope(5, engine="v3", body={"exit_code": 0})
        assert list(payload) == ["schema", "engine", "exit_code"]

    def test_bare_schema_identity(self):
        assert list(envelope(3, body={"a": 1})) == ["schema", "a"]

    def test_body_is_copied_not_aliased(self):
        body = {"x": 1}
        payload = envelope(1, tool="t", body=body)
        payload["x"] = 2
        assert body["x"] == 1

    def test_roundtrips_through_json(self):
        payload = envelope(1, tool="harvest", verb="accept", body={"applied": True})
        assert json.loads(json.dumps(payload)) == payload


class TestRejectionPayload:
    def test_shape_and_order(self):
        payload = rejection_payload(1, tool="harvest", verb="accept", reason="stale")
        assert list(payload) == ["schema", "tool", "verb", "applied", "outcome", "reason"]
        assert payload["applied"] is False
        assert payload["outcome"] == "rejected"
        assert payload["reason"] == "stale"


class TestFreshnessMismatch:
    def test_exact_match_is_fresh(self):
        tokens = {"id:a": {"de": "x", "en": None}}
        assert freshness_mismatch(tokens, dict(tokens)) is None

    def test_changed_value(self):
        mismatch = freshness_mismatch({"id:a": "1"}, {"id:a": "2"})
        assert mismatch is not None
        assert "changed: id:a" in mismatch

    def test_missing_member_is_staleness(self):
        mismatch = freshness_mismatch({"id:a": "1", "id:b": "2"}, {"id:a": "1"})
        assert mismatch is not None
        assert "missing: id:b" in mismatch

    def test_added_member_is_staleness(self):
        mismatch = freshness_mismatch({"id:a": "1"}, {"id:a": "1", "id:c": "3"})
        assert mismatch is not None
        assert "new: id:c" in mismatch


class TestValidatorRegistry:
    def test_register_and_get(self):
        registry = ValidatorRegistry()
        fn = lambda payload: payload  # noqa: E731
        registry.register("x-bullets", fn)
        assert registry.get("x-bullets") is fn
        assert registry.names() == ("x-bullets",)

    def test_decorator_form(self):
        registry = ValidatorRegistry()

        @registry.register("x-decisions")
        def validate(payload):
            return payload

        assert registry.get("x-decisions") is validate

    def test_duplicate_registration_refused(self):
        registry = ValidatorRegistry()
        registry.register("x", lambda p: p)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("x", lambda p: p)

    def test_unknown_names_the_registered(self):
        registry = ValidatorRegistry()
        registry.register("harvest-bullets", lambda p: p)
        with pytest.raises(KeyError, match="unknown validator 'nope'.*harvest-bullets"):
            registry.get("nope")


class TestRegisteredValidators:
    """The claimed-wired half: the labels on the wire must resolve here."""

    def test_harvest_bullets_resolves_to_the_accept_parser(self):
        from clm.voiceover import harvest_accept, harvest_task

        assert VALIDATORS.get(harvest_task.ANSWER_VALIDATOR) is harvest_accept.parse_answer
        assert harvest_task.ANSWER_VALIDATOR == "harvest-bullets"

    def test_harvest_bullets_validates(self):
        from clm.voiceover.harvest_accept import AcceptRejected

        validator = VALIDATORS.get("harvest-bullets")
        with pytest.raises(AcceptRejected):
            validator({"item": 42})

    def test_sync_decisions_validates_a_document(self):
        from clm.slides import doc_apply

        validator = VALIDATORS.get(doc_apply.SYNC_DECISIONS_VALIDATOR)
        document = validator({"schema": 5, "report_id": "abc", "decisions": []})
        assert document.report_id == "abc"

    def test_sync_decisions_rejects_an_unknown_schema(self):
        from clm.slides import doc_apply  # noqa: F401 — registers the validator

        validator = VALIDATORS.get("sync-decisions")
        with pytest.raises(AnswerRejected, match="schema"):
            validator({"schema": 99, "decisions": []})
