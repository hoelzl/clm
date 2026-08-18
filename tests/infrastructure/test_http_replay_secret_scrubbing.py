"""Cassette secret scrubbing: the record-time filters (finding S9, #798).

Cassettes are **committed** files in course repositories, so whatever the
recorder writes is what lands in a PR. The request side already stripped
``authorization``/``cookie``/``x-api-key`` and two query params; the
adversarial review found the rest of the surface unguarded:

* the Azure (``api-key``), Gemini (``x-goog-api-key``), proxy and AWS
  header spellings;
* the query-parameter spellings those services use (``key``,
  ``access_token``, ``apikey``, ``subscription-key``, AWS's signature);
* a JSON request body whose content-type carries a charset — the check
  was ``== "application/json"``, so ``application/json; charset=utf-8``
  skipped body filtering entirely;
* **the whole response side**: ``Set-Cookie`` and OAuth-shaped token
  bodies were committed verbatim, because the recorder passed no
  response filter at all.

The trap this file exists to pin: redaction is by **exact key name**.
An LLM response legitimately contains ``completion_tokens`` and
``total_tokens``; a substring match on ``token`` would corrupt the usage
data of every replayed cassette.
"""

from __future__ import annotations

import json

import pytest

from clm.infrastructure.http_replay_mitm import cassette_format as cf
from clm.infrastructure.http_replay_mitm import vcr_format as vf


def _request(uri: str = "https://api.example.com/v1/x", headers=None, body: bytes = b""):
    return vf.Request("POST", uri, body, headers or {})


def _filtered(request):
    return cf.build_request_filter()(request)


def _response(headers: dict[str, list[str]], body: bytes) -> dict:
    return {"status": {"code": 200, "message": "OK"}, "headers": headers, "body": {"string": body}}


class TestRequestHeaderFilters:
    @pytest.mark.parametrize(
        "header",
        [
            "authorization",
            "cookie",
            "x-api-key",
            # Added by S11's sibling finding S9 — one spelling per provider
            # family that CLM's own LLM client or a course notebook can hit.
            "api-key",  # Azure OpenAI
            "x-goog-api-key",  # Gemini
            "proxy-authorization",
            "x-amz-security-token",
            "x-auth-token",
        ],
    )
    def test_secret_header_never_reaches_the_cassette(self, header: str) -> None:
        out = _filtered(_request(headers={header: "SUPERSECRET", "accept": "*/*"}))
        assert header not in out.headers
        assert "SUPERSECRET" not in json.dumps(dict(out.headers.items()))
        assert out.headers["accept"] == "*/*"


class TestRequestQueryFilters:
    @pytest.mark.parametrize(
        "param",
        ["api_key", "token", "key", "access_token", "apikey", "subscription-key"],
    )
    def test_secret_query_parameter_never_reaches_the_cassette(self, param: str) -> None:
        out = _filtered(_request(uri=f"https://api.example.com/v1/x?{param}=SHHH&keep=1"))
        assert "SHHH" not in out.uri
        assert param not in out.uri
        assert "keep=1" in out.uri

    def test_aws_signature_is_stripped(self) -> None:
        out = _filtered(_request(uri="https://s3.example.com/o?X-Amz-Signature=DEAD&keep=1"))
        assert "DEAD" not in out.uri
        assert "keep=1" in out.uri


class TestJsonContentTypeIsMatchedByPrefix:
    """``application/json; charset=utf-8`` is a JSON body."""

    def test_charset_suffixed_json_body_is_filtered(self) -> None:
        out = _filtered(
            _request(
                headers={"content-type": "application/json; charset=utf-8"},
                body=b'{"password": "hunter2", "keep": 1}',
            )
        )
        payload = json.loads(out.body)
        assert "password" not in payload
        assert payload["keep"] == 1

    def test_a_body_that_is_not_valid_json_is_still_recorded(self) -> None:
        """A parse failure must not silently stop the recording.

        ``_filter_request`` treats an exception as "forward without
        recording", so a JSON content-type on a non-JSON body would have
        turned into a silently missing interaction.
        """
        out = _filtered(
            _request(headers={"content-type": "application/json"}, body=b"not json at all")
        )
        assert out is not None
        assert out.body == b"not json at all"


class TestResponseHeaderFilter:
    def test_set_cookie_is_stripped(self) -> None:
        out = cf.build_response_filter()(
            _response({"content-type": ["application/json"], "set-cookie": ["s=1", "s=2"]}, b"{}")
        )
        assert "set-cookie" not in {k.lower() for k in out["headers"]}
        assert out["headers"]["content-type"] == ["application/json"]

    def test_the_original_response_is_not_mutated(self) -> None:
        original = _response({"set-cookie": ["s=1"]}, b"{}")
        cf.build_response_filter()(original)
        assert "set-cookie" in original["headers"]


class TestResponseBodyRedaction:
    def _redact(self, payload: dict, content_type: str = "application/json") -> dict:
        out = cf.build_response_filter()(
            _response({"content-type": [content_type]}, json.dumps(payload).encode())
        )
        return json.loads(out["body"]["string"])

    @pytest.mark.parametrize(
        "key",
        [
            "access_token",
            "refresh_token",
            "id_token",
            "client_secret",
            "api_key",
            "apikey",
            "authorization",
            "password",
            "secret",
            "session_token",
        ],
    )
    def test_secret_key_values_are_redacted(self, key: str) -> None:
        out = self._redact({key: "ya29.SUPERSECRET", "keep": "visible"})
        assert out[key] != "ya29.SUPERSECRET"
        assert out["keep"] == "visible"

    def test_redaction_is_recursive_through_dicts_and_lists(self) -> None:
        out = self._redact(
            {
                "data": {"nested": {"access_token": "S1"}},
                "items": [{"password": "S2"}, {"keep": "ok"}],
            }
        )
        assert out["data"]["nested"]["access_token"] != "S1"
        assert out["items"][0]["password"] != "S2"
        assert out["items"][1]["keep"] == "ok"

    @pytest.mark.parametrize("key", ["completion_tokens", "prompt_tokens", "total_tokens"])
    def test_llm_usage_fields_survive(self, key: str) -> None:
        """The substring trap.

        Matching ``token`` as a substring would clip the usage counters of
        every replayed LLM cassette — silently wrong numbers in cached
        output rather than a loud failure.
        """
        out = self._redact({"usage": {key: 42}})
        assert out["usage"][key] == 42

    def test_charset_suffixed_json_response_is_redacted(self) -> None:
        out = self._redact({"access_token": "S"}, content_type="application/json; charset=utf-8")
        assert out["access_token"] != "S"

    def test_non_json_bodies_are_left_alone(self) -> None:
        body = b"access_token=SUPERSECRET"
        out = cf.build_response_filter()(_response({"content-type": ["text/plain"]}, body))
        assert out["body"]["string"] == body

    def test_a_json_content_type_with_an_unparseable_body_is_left_alone(self) -> None:
        body = b"<html>not json</html>"
        out = cf.build_response_filter()(_response({"content-type": ["application/json"]}, body))
        assert out["body"]["string"] == body

    def test_a_bare_secret_string_body_is_left_alone(self) -> None:
        """Only keyed values are redacted; a scalar body has no key."""
        out = cf.build_response_filter()(
            _response({"content-type": ["application/json"]}, b'"just-a-string"')
        )
        assert json.loads(out["body"]["string"]) == "just-a-string"


class TestFilterListsArePinned:
    """Widening these lists is fine; reordering or narrowing is not.

    Committed course cassettes were recorded with exactly these filters,
    so the leading entries must keep their identity and order — a replay
    lookup filters the *incoming* request the same way before matching.
    """

    def test_request_lists_keep_their_original_prefix(self) -> None:
        assert cf.FILTER_HEADERS[:4] == ["authorization", "cookie", "x-api-key", "set-cookie"]
        assert cf.FILTER_QUERY_PARAMETERS[:2] == ["api_key", "token"]
        assert cf.FILTER_POST_DATA_PARAMETERS == ["password", "token", "api_key"]

    def test_response_lists_are_exact(self) -> None:
        assert cf.FILTER_RESPONSE_HEADERS == ["set-cookie"]
        assert cf.FILTER_RESPONSE_BODY_KEYS == [
            "access_token",
            "refresh_token",
            "id_token",
            "client_secret",
            "api_key",
            "apikey",
            "authorization",
            "password",
            "secret",
            "session_token",
        ]
