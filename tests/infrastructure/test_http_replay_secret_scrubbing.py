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
        # Pin the replacement itself, not just "it changed": a placeholder
        # of "" — or a truncated prefix of the real secret — would satisfy
        # an inequality assertion while still leaking (or breaking the
        # scanner, which recognizes clean cassettes by this exact value).
        assert out[key] == "[REDACTED-BY-CLM]"
        assert out[key] == cf.SECRET_PLACEHOLDER
        assert out["keep"] == "visible"

    @pytest.mark.parametrize("value", [21078, 0, -1, 3.5, True, False, None])
    def test_a_numeric_or_null_value_is_not_a_secret(self, value: object) -> None:
        """A token *id* is not a token (issue #875).

        ``encoder.json`` — GPT-2's BPE vocabulary, fetched by the text
        chunking deck — is a map from word to integer id, and ``secret``
        and ``password`` are ordinary words. Redacting by key name alone
        wrote the placeholder string over four integer ids, handing the
        replayed tokenizer a corrupted vocabulary *and* changing the JSON
        value type out from under whatever reads it.

        No credential this filter exists for is a number, a boolean or
        null, so exempting those types costs nothing and is the whole fix.
        """
        out = self._redact({"hello": 31373, "secret": value, "password": value})
        assert out["secret"] == value
        assert out["password"] == value
        assert out["hello"] == 31373

    def test_the_real_vocabulary_shape_survives(self) -> None:
        """The exact corpus case, as a regression pin.

        Byte-identical, not merely equal-as-JSON: nothing matched, so the
        recorder must take the untouched-body shortcut rather than
        re-serialize a 1.8 MB vocabulary and rewrite its separators.
        """
        raw = b'{"hello":31373,"secret":21078,"Secret":23725,"password":28712}'
        out = cf.build_response_filter()(_response({"content-type": ["application/json"]}, raw))
        assert out["body"]["string"] == raw

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({"value": "sk-live-abc123"}, id="dict"),
            pytest.param(["sk-live-abc123"], id="list"),
            pytest.param("sk-live-abc123", id="str"),
        ],
    )
    def test_a_container_or_string_value_is_still_redacted_wholesale(self, value: object) -> None:
        """The trap in the obvious version of the #875 fix.

        "Only redact strings" looks right and quietly leaks: the nested
        key (``value``) is not on the filter list, so recursing into the
        subtree finds nothing and the secret survives. Containers must
        keep going wholesale — only scalars that cannot carry a
        credential are exempt.
        """
        out = self._redact({"secret": value})
        assert out["secret"] == cf.SECRET_PLACEHOLDER
        assert "sk-live-abc123" not in json.dumps(out)

    def test_a_redacted_body_keeps_its_shape(self) -> None:
        """The key survives; only the value goes.

        Replayed code reads these payloads — a missing key is a different
        failure than a redacted one.
        """
        out = self._redact({"token_type": "Bearer", "access_token": "S"})
        assert set(out) == {"token_type", "access_token"}
        assert out["token_type"] == "Bearer"

    def test_content_length_follows_the_redacted_body(self) -> None:
        """A cassette whose content-length disagrees with its body lies."""
        payload = json.dumps({"access_token": "S" * 200}).encode()
        out = cf.build_response_filter()(
            _response(
                {"content-type": ["application/json"], "content-length": [str(len(payload))]},
                payload,
            )
        )
        assert out["headers"]["content-length"] == [str(len(out["body"]["string"]))]

    def test_an_untouched_body_is_byte_identical(self) -> None:
        """Not merely equal-as-JSON: the exact bytes are preserved.

        Re-serializing every clean response would rewrite separators and
        unicode escapes across the whole corpus on the next build — a
        diff nobody asked for. Asserting through ``json.loads`` would not
        notice.
        """
        raw = b'{"a":1,   "b":"caf\xc3\xa9"}'
        out = cf.build_response_filter()(_response({"content-type": ["application/json"]}, raw))
        assert out["body"]["string"] == raw

    def test_a_surrogate_bearing_body_is_still_recordable(self) -> None:
        """A lone surrogate survives ``json.loads`` but cannot be encoded.

        Raising here would drop the interaction, and a dropped response
        to a *repeated* request replays as the previous one — silently
        different output, not a miss.
        """
        raw = b'{"access_token": "S", "text": "\\ud800"}'
        out = cf.build_response_filter()(_response({"content-type": ["application/json"]}, raw))
        assert json.loads(out["body"]["string"])["access_token"] == cf.SECRET_PLACEHOLDER

    def test_body_redaction_does_not_depend_on_header_filtering(self) -> None:
        """The two rules are independent.

        They were nested once, so disabling header filtering silently
        disabled body redaction too.
        """
        out = cf.build_response_filter(filter_headers=())(
            _response({"content-type": ["application/json"]}, b'{"access_token": "S"}')
        )
        assert json.loads(out["body"]["string"])["access_token"] == cf.SECRET_PLACEHOLDER

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


class TestRequestFiltersCoverTheRecordedShapes:
    """Gaps the audit would otherwise flag forever.

    A finding the recorder cannot clear is a gate nobody can satisfy, so
    every shape the scanner reports has to be one the recorder strips.
    """

    def test_query_parameter_case_does_not_matter(self) -> None:
        """``?API_KEY=`` is the same secret as ``?api_key=``."""
        out = _filtered(_request(uri="https://api.example.com/x?API_KEY=SHHH&keep=1"))
        assert "SHHH" not in out.uri
        assert "keep=1" in out.uri

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_json_bodies_are_filtered_on_any_method(self, method: str) -> None:
        """vcrpy filtered POST only; a PUT with an api_key is ordinary."""
        request = vf.Request(
            method,
            "https://api.example.com/v1/x",
            b'{"api_key": "SHHH", "keep": 1}',
            {"content-type": "application/json"},
        )
        out = _filtered(request)
        assert b"SHHH" not in (out.body or b"")

    def test_form_encoded_bodies_are_filtered(self) -> None:
        """The OAuth password grant, which the scanner also reads."""
        request = vf.Request(
            "POST",
            "https://api.example.com/token",
            b"grant_type=password&username=bob&password=hunter2",
            {"content-type": "application/x-www-form-urlencoded"},
        )
        out = _filtered(request)
        assert b"hunter2" not in (out.body or b"")
        assert b"username=bob" in (out.body or b"")

    @pytest.mark.parametrize(
        "content_type", ["application/json", "application/x-www-form-urlencoded"]
    )
    def test_body_parameter_case_does_not_matter(self, content_type: str) -> None:
        """``API_KEY`` is the same secret as ``api_key``, in either encoding.

        Query names were made case-insensitive for exactly this reason;
        leaving body names case-sensitive meant the audit flagged a
        cassette the recorder would not have cleaned.
        """
        body = (
            b'{"API_KEY": "SHHH", "keep": 1}' if "json" in content_type else b"API_KEY=SHHH&keep=1"
        )
        out = _filtered(
            vf.Request("POST", "https://api.example.com/x", body, {"content-type": content_type})
        )
        assert b"SHHH" not in (out.body or b"")
        assert b"keep" in (out.body or b"")

    @pytest.mark.parametrize("method", ["PUT", "POST"])
    def test_a_binary_body_is_left_alone_not_refused(self, method: str) -> None:
        """A PNG upload must still be *recorded*.

        The form branch decodes every ``&``-chunk as UTF-8. Raising there
        makes ``_filter_request`` return None, which the addon treats as
        an ignore-host: the request is forwarded to the live network in
        every mode — including strict replay in CI — and never recorded.
        Silent egress, no miss, no cassette entry. Dropping the POST gate
        routed binary uploads (a presigned S3 PUT) straight into it.
        """
        body = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
        out = _filtered(
            vf.Request(method, "https://s3.example.com/o", body, {"content-type": "image/png"})
        )
        assert out is not None, "an unfilterable body must not become a network bypass"
        assert out.body == body

    def test_a_non_ascii_header_does_not_refuse_the_request(self) -> None:
        """``X-Title: Übung 3`` must not become a network bypass either.

        Header decoding happens *upstream* of the filter, so an ASCII-strict
        decode raised before any filtering — and the addon reads that as
        "unfilterable", i.e. forward to the live network in every mode and
        record nothing. A German deck setting OpenRouter's documented
        ``X-Title`` is enough to trigger it through ``requests``.
        """
        request = cf.vcr_request_from_parts(
            "POST",
            "https://api.example.com/v1/x",
            [(b"x-title", "Übung 3".encode()), (b"authorization", b"Bearer SECRET")],
            b"{}",
        )
        out = _filtered(request)
        assert out is not None, "a non-ASCII header must not become a network bypass"
        assert "authorization" not in out.headers  # filtering still happened
        assert "x-title" in {k.lower() for k in out.headers}

    def test_a_pathologically_nested_json_body_is_left_alone(self) -> None:
        """``RecursionError`` guards the request side as well as the response.

        Same reasoning as the parse guard next to it — raising here means
        the request is forwarded live and never recorded.
        """
        body = ("[" * 20000 + "]" * 20000).encode()
        out = _filtered(_request(headers={"content-type": "application/json"}, body=body))
        assert out is not None
        assert out.body == body

    def test_a_latin1_form_body_is_left_alone(self) -> None:
        out = _filtered(
            vf.Request(
                "POST",
                "https://api.example.com/x",
                "naïve=1".encode("latin-1"),
                {"content-type": "application/x-www-form-urlencoded"},
            )
        )
        assert out is not None
        assert out.body == "naïve=1".encode("latin-1")


class TestTheRecorderRefusesRatherThanLeaks:
    """``_filter_response`` returning ``None`` means "do not record".

    Tested at the addon seam because that is where the decision is acted
    on, and because the alternative — recording an unscrubbed response —
    is the failure this whole finding is about.
    """

    def _addon(self):
        from clm.infrastructure.http_replay_mitm.addon import ClmReplayAddon

        addon = ClmReplayAddon()
        addon._response_filter = None  # what running() would have built
        return addon

    def test_no_filter_means_no_recording(self) -> None:
        """Fail closed if ``running()`` somehow did not build the filter."""
        assert self._addon()._filter_response(_response({}, b"{}")) is None

    def test_a_filter_that_raises_means_no_recording(self) -> None:
        from clm.infrastructure.http_replay_mitm.addon import ClmReplayAddon

        addon = ClmReplayAddon()

        def explode(_response):
            raise RuntimeError("boom")

        addon._response_filter = explode
        assert addon._filter_response(_response({}, b"{}")) is None

    def test_a_working_filter_returns_the_scrubbed_response(self) -> None:
        from clm.infrastructure.http_replay_mitm.addon import ClmReplayAddon

        addon = ClmReplayAddon()
        addon._response_filter = cf.build_response_filter()
        out = addon._filter_response(_response({"set-cookie": ["s=1"]}, b"{}"))
        assert out is not None
        assert "set-cookie" not in out["headers"]


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
