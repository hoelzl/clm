"""P1 gate (issue #165): byte-identity of the mitmproxy cassette bridge.

These tests pin ``clm.infrastructure.http_replay_mitm.cassette_format`` to
vcrpy's *own* serialization path. The transport may only flip the cassette
storage from vcrpy's in-kernel patching to an out-of-process proxy if the
bytes on disk are indistinguishable — committed course cassettes, ``clm
cassette doctor`` and ``strip_cassette_hosts.py`` must not be able to tell
which producer wrote a cassette.

Strategy: feed the *same* HTTP parts to (a) vcrpy's real httpcore-stub
helpers and (b) our bridge, then assert the serialized YAML is identical.
vcr is a hard dependency of these tests (the ``[replay]`` extra).
"""

from __future__ import annotations

import gzip

import pytest

# The whole module needs vcrpy; skip cleanly where the extra is absent.
pytest.importorskip("vcr")

import httpcore  # noqa: E402  (after importorskip)
from vcr.filters import decode_response  # noqa: E402
from vcr.request import Request  # noqa: E402
from vcr.serialize import serialize as vcr_serialize  # noqa: E402
from vcr.serializers import yamlserializer  # noqa: E402
from vcr.stubs.httpcore_stubs import (  # noqa: E402
    _make_vcr_request,
    _serialize_response,
)

from clm.infrastructure.http_replay_mitm import cassette_format as cf  # noqa: E402


def _httpcore_request(method: str, url: str, fields, body: bytes) -> httpcore.Request:
    return httpcore.Request(method, url, headers=list(fields), content=body)


def _httpcore_response(status: int, reason: str | None, fields, body: bytes) -> httpcore.Response:
    extensions = {"reason_phrase": reason.encode("ascii")} if reason is not None else {}
    return httpcore.Response(status, headers=list(fields), content=body, extensions=extensions)


# (method, url, request fields, request body, status, reason, response fields, raw body)
_POST_JSON = (
    "POST",
    "https://openrouter.ai/api/v1/chat/completions",
    [(b"content-type", b"application/json"), (b"accept", b"application/json")],
    b'{"model":"x","messages":[{"role":"user","content":"hi"}]}',
    200,
    "OK",
    [(b"content-type", b"application/json"), (b"x-request-id", b"abc123")],
    b'{"id":"chatcmpl-1","choices":[{"message":{"content":"hello"}}]}',
)


def _vcrpy_reference_yaml(case, *, decode_compressed=True) -> str:
    method, url, req_fields, req_body, status, reason, resp_fields, raw_body = case
    real_request = _httpcore_request(method, url, req_fields, req_body)
    real_response = _httpcore_response(status, reason, resp_fields, raw_body)
    vcr_request = _make_vcr_request(real_request, req_body)
    response_dict = _serialize_response(real_response, raw_body)
    if decode_compressed:
        response_dict = decode_response(response_dict)
    return vcr_serialize({"requests": [vcr_request], "responses": [response_dict]}, yamlserializer)


def _bridge_yaml(case, *, decode_compressed=True) -> str:
    method, url, req_fields, req_body, status, reason, resp_fields, raw_body = case
    request = cf.vcr_request_from_parts(method, url, req_fields, req_body)
    response = cf.vcr_response_dict_from_parts(
        status, reason, resp_fields, raw_body, decode_compressed=decode_compressed
    )
    return cf.serialize_interactions([(request, response)])


def test_request_dict_matches_vcrpy():
    method, url, req_fields, req_body, *_ = _POST_JSON
    real_request = _httpcore_request(method, url, req_fields, req_body)
    expected = _make_vcr_request(real_request, req_body)._to_dict()
    actual = cf.vcr_request_from_parts(method, url, req_fields, req_body)._to_dict()
    assert actual == expected


def test_response_dict_matches_vcrpy():
    _, _, _, _, status, reason, resp_fields, raw_body = _POST_JSON
    real_response = _httpcore_response(status, reason, resp_fields, raw_body)
    expected = decode_response(_serialize_response(real_response, raw_body))
    actual = cf.vcr_response_dict_from_parts(status, reason, resp_fields, raw_body)
    assert actual == expected


def test_response_fingerprint_distinguishes_distinct_bodies():
    """The response fingerprint keys sequence-aware dedup: two responses to the
    same request collapse only when their bodies are identical."""
    r1 = cf.vcr_response_dict_from_parts(200, "OK", [], b'{"hit":1}')
    r1_again = cf.vcr_response_dict_from_parts(200, "OK", [], b'{"hit":1}')
    r2 = cf.vcr_response_dict_from_parts(200, "OK", [], b'{"hit":2}')
    assert cf.response_fingerprint(r1) == cf.response_fingerprint(r1_again)
    assert cf.response_fingerprint(r1) != cf.response_fingerprint(r2)
    # Robust against a missing/oddly-shaped body dict.
    assert cf.response_fingerprint({}) == b""


def test_select_serve_index_replays_response_sequence_then_repeats_last():
    """The replay cursor serves a per-request response *sequence* in recorded
    order, then sticks on the last match once exhausted; a single-entry
    recording stays repeatable; a non-matching request yields no index.

    Imports the addon (which needs ``mitmproxy``); skipped where the proxy
    package isn't installed, like the integration module.
    """
    pytest.importorskip("mitmproxy")
    from clm.infrastructure.http_replay_mitm.addon import ClmReplayAddon

    def _req(body: bytes):
        return cf.vcr_request_from_parts(
            "POST", "https://api/x", [(b"content-type", b"application/json")], body
        )

    body = b'{"prompt":"same"}'
    # Two interactions for the SAME request with distinct responses (R1, R2) —
    # a non-deterministic endpoint answering one prompt two different ways.
    recorded = [
        (_req(body), cf.vcr_response_dict_from_parts(200, "OK", [], b"R1")),
        (_req(body), cf.vcr_response_dict_from_parts(200, "OK", [], b"R2")),
    ]
    incoming = _req(body)
    served: set[int] = set()

    i1 = ClmReplayAddon._select_serve_index(recorded, incoming, served)
    assert i1 == 0
    served.add(i1)
    i2 = ClmReplayAddon._select_serve_index(recorded, incoming, served)
    assert i2 == 1
    served.add(i2)
    # Sequence exhausted → stick on the last matching entry (repeatable tail).
    assert ClmReplayAddon._select_serve_index(recorded, incoming, served) == 1

    # A request that matches nothing → no index (strict-replay miss upstream).
    other = _req(b'{"prompt":"different"}')
    assert ClmReplayAddon._select_serve_index(recorded, other, set()) is None

    # Single-entry recording stays repeatable (backward-compatible).
    single = [(_req(body), cf.vcr_response_dict_from_parts(200, "OK", [], b"only"))]
    seen: set[int] = set()
    first = ClmReplayAddon._select_serve_index(single, incoming, seen)
    assert first == 0
    seen.add(first)
    assert ClmReplayAddon._select_serve_index(single, incoming, seen) == 0


def test_serialized_yaml_byte_identical_plain():
    assert _bridge_yaml(_POST_JSON) == _vcrpy_reference_yaml(_POST_JSON)


def test_serialized_yaml_byte_identical_gzip():
    """A gzip-compressed response must decode to the same bytes vcrpy stores."""
    payload = b'{"id":"chatcmpl-2","choices":[{"message":{"content":"gzipped"}}]}'
    compressed = gzip.compress(payload)
    case = (
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        [(b"content-type", b"application/json")],
        b'{"model":"x"}',
        200,
        "OK",
        [
            (b"content-type", b"application/json"),
            (b"content-encoding", b"gzip"),
            (b"content-length", str(len(compressed)).encode("ascii")),
        ],
        compressed,
    )
    bridge = _bridge_yaml(case)
    reference = _vcrpy_reference_yaml(case)
    assert bridge == reference
    # And the decoded payload is actually present (content-encoding stripped).
    assert "gzipped" in bridge
    assert "content-encoding" not in bridge


def test_multivalue_request_headers_comma_joined():
    """Repeated request headers join with ', ' like HTTPX/vcrpy."""
    case = (
        "GET",
        "https://example.com/x",
        [(b"accept", b"text/html"), (b"accept", b"application/json")],
        b"",
        200,
        "OK",
        [(b"content-type", b"text/plain")],
        b"ok",
    )
    assert _bridge_yaml(case) == _vcrpy_reference_yaml(case)


def test_multivalue_response_headers_preserved_as_list():
    """Repeated response headers (e.g. Set-Cookie) stay a multi-element list."""
    _, _, _, _, status, reason, _, raw_body = _POST_JSON
    resp_fields = [
        (b"set-cookie", b"a=1"),
        (b"set-cookie", b"b=2"),
        (b"content-type", b"text/plain"),
    ]
    real_response = _httpcore_response(status, reason, resp_fields, raw_body)
    expected = decode_response(_serialize_response(real_response, raw_body))
    actual = cf.vcr_response_dict_from_parts(status, reason, resp_fields, raw_body)
    assert actual == expected
    assert actual["headers"]["set-cookie"] == ["a=1", "b=2"]


def test_no_reason_phrase_normalises_to_none():
    """HTTP/2-style responses (no reason phrase) match vcrpy's message: None."""
    _, _, _, _, status, _, resp_fields, raw_body = _POST_JSON
    # httpcore with empty extensions -> _serialize_response yields message=None.
    real_response = _httpcore_response(status, None, resp_fields, raw_body)
    expected = decode_response(_serialize_response(real_response, raw_body))
    actual = cf.vcr_response_dict_from_parts(status, "", resp_fields, raw_body)
    assert actual == expected
    assert actual["status"]["message"] is None


def test_non_ascii_reason_phrase_dropped_to_none():
    """A non-ASCII reason phrase is dropped (vcrpy can't (de)serialize it).

    vcrpy decodes the reason as ASCII and would crash on non-ASCII bytes; a
    stored non-ASCII message could not be replayed by the in-kernel vcrpy
    transport. We keep the cassette vcrpy-replayable by storing None.
    """
    _, _, _, _, status, _, resp_fields, raw_body = _POST_JSON
    actual = cf.vcr_response_dict_from_parts(status, "Café", resp_fields, raw_body)
    assert actual["status"]["message"] is None
    # The serialized cassette is therefore plain ASCII and reloads cleanly.
    request = cf.vcr_request_from_parts("GET", "https://example.com/", [], b"")
    yaml_text = cf.serialize_interactions([(request, actual)])
    yaml_text.encode("ascii")  # must not raise


def test_serialize_does_not_mutate_in_memory_body():
    """The convert_to_unicode bytes->str mutation must not leak to the index."""
    _, _, _, _, status, reason, resp_fields, raw_body = _POST_JSON
    response = cf.vcr_response_dict_from_parts(status, reason, resp_fields, raw_body)
    request = cf.vcr_request_from_parts("GET", "https://example.com/", [], b"")
    assert isinstance(response["body"]["string"], bytes)
    cf.serialize_interactions([(request, response)])
    # Still bytes after serialization (deepcopy guard); a second serialize
    # would otherwise emit a different (str-bodied) shape.
    assert isinstance(response["body"]["string"], bytes)


def test_write_cassette_uses_lf_endings(tmp_path):
    request = cf.vcr_request_from_parts(*_POST_JSON[:3], _POST_JSON[3])
    response = cf.vcr_response_dict_from_parts(
        _POST_JSON[4], _POST_JSON[5], _POST_JSON[6], _POST_JSON[7]
    )
    path = tmp_path / "slides.http-cassette.yaml"
    cf.write_cassette(path, [(request, response)])
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_round_trip_load(tmp_path):
    request = cf.vcr_request_from_parts(*_POST_JSON[:3], _POST_JSON[3])
    response = cf.vcr_response_dict_from_parts(
        _POST_JSON[4], _POST_JSON[5], _POST_JSON[6], _POST_JSON[7]
    )
    path = tmp_path / "slides.http-cassette.yaml"
    cf.write_cassette(path, [(request, response)])

    loaded = cf.load_interactions(path)
    assert len(loaded) == 1
    loaded_req, loaded_resp = loaded[0]
    assert loaded_req.method == "POST"
    assert loaded_req.uri == _POST_JSON[1]
    # Loaded body is bytes again (FilesystemPersister -> convert_to_bytes).
    assert loaded_resp["body"]["string"] == _POST_JSON[7]


def test_filter_byte_identical_to_vcrpy_before_record_request():
    """Our request filter must drop exactly what vcrpy's own
    ``before_record_request`` drops, byte-for-byte — so a mitmproxy-recorded
    cassette is indistinguishable from a vcrpy-recorded one after filtering."""
    import vcr

    ignore = ("api.smith.langchain.com",)
    vcrpy_filter = vcr.VCR(
        filter_headers=cf.FILTER_HEADERS,
        filter_query_parameters=cf.FILTER_QUERY_PARAMETERS,
        filter_post_data_parameters=cf.FILTER_POST_DATA_PARAMETERS,
        ignore_hosts=ignore,
        decode_compressed_response=True,
    )._build_before_record_request({})
    ours = cf.build_request_filter(ignore_hosts=ignore)

    cases = [
        (
            "POST",
            "https://openrouter.ai/api/v1/chat?api_key=S&model=x&token=T",
            [
                (b"authorization", b"Bearer S"),
                (b"content-type", b"application/json"),
                (b"x-api-key", b"K"),
                (b"accept", b"application/json"),
            ],
            b'{"model":"x","api_key":"S","password":"p","messages":[{"role":"user","content":"hi"}]}',
        ),
        ("GET", "https://example.com/x?token=T&q=1", [(b"cookie", b"sid=abc")], b""),
        (
            "POST",
            "https://o.ai/v1",
            [(b"content-type", b"application/json; charset=utf-8")],
            b'{"k":1}',
        ),
    ]
    for method, url, fields, body in cases:
        ref = vcrpy_filter(cf.vcr_request_from_parts(method, url, fields, body))
        got = ours(cf.vcr_request_from_parts(method, url, fields, body))
        assert ref._to_dict() == got._to_dict(), url


def test_filter_removes_secrets():
    """The filtered request carries no secret headers / query / body params."""
    f = cf.build_request_filter()
    req = cf.vcr_request_from_parts(
        "POST",
        "https://openrouter.ai/api/v1/chat?api_key=SECRET&model=x&token=TT",
        [
            (b"authorization", b"Bearer SECRET"),
            (b"x-api-key", b"K"),
            (b"content-type", b"application/json"),
        ],
        b'{"model":"x","api_key":"SECRET","password":"pw"}',
    )
    filtered = f(req)
    header_names = {k.lower() for k in filtered.headers}
    assert "authorization" not in header_names
    assert "x-api-key" not in header_names
    assert "api_key" not in filtered.uri and "token" not in filtered.uri
    body = filtered.body or b""
    assert b"SECRET" not in body and b"api_key" not in body and b"password" not in body


def test_filter_ignore_host_returns_none():
    f = cf.build_request_filter(ignore_hosts=("api.smith.langchain.com",))
    ignored = cf.vcr_request_from_parts("POST", "https://api.smith.langchain.com/runs", [], b"{}")
    kept = cf.vcr_request_from_parts("POST", "https://openrouter.ai/v1", [], b"{}")
    assert f(ignored) is None
    assert f(kept) is not None


def test_json_body_matcher_semantic_and_byte_fallback():
    json_h = [(b"content-type", b"application/json")]
    compact = cf.vcr_request_from_parts("POST", "https://o.ai/c", json_h, b'{"a":1,"b":2}')
    reordered = cf.vcr_request_from_parts("POST", "https://o.ai/c", json_h, b'{"b": 2, "a": 1}')
    different = cf.vcr_request_from_parts("POST", "https://o.ai/c", json_h, b'{"a":9}')
    assert cf.requests_match(compact, reordered)  # JSON value equality
    assert not cf.requests_match(compact, different)

    text_h = [(b"content-type", b"text/plain")]
    t1 = cf.vcr_request_from_parts("POST", "https://o.ai/t", text_h, b"hello")
    t2 = cf.vcr_request_from_parts("POST", "https://o.ai/t", text_h, b"hello")
    t3 = cf.vcr_request_from_parts("POST", "https://o.ai/t", text_h, b"world")
    assert cf.requests_match(t1, t2)  # byte equality
    assert not cf.requests_match(t1, t3)


def test_requests_match_query_order_insensitive_and_path_sensitive():
    a = cf.vcr_request_from_parts("GET", "https://o.ai/x?a=1&b=2", [], b"")
    b = cf.vcr_request_from_parts("GET", "https://o.ai/x?b=2&a=1", [], b"")
    other_path = cf.vcr_request_from_parts("GET", "https://o.ai/y?a=1&b=2", [], b"")
    assert cf.requests_match(a, b)  # query matcher sorts
    assert not cf.requests_match(a, other_path)


def test_filter_constants_and_matchers_are_pinned():
    """Pin the secret-filter constants and the replay matcher chain.

    These used to be drift-guarded against the in-kernel vcrpy bootstrap
    (removed in #355); cassette_format is now the single source of truth, so
    pin the literals directly — committed course cassettes were recorded with
    exactly these filters, and replay matching must keep treating them the
    same. Widening the filters is fine; narrowing or reordering silently
    changes what gets recorded/matched and needs a deliberate decision.
    """
    assert cf.FILTER_HEADERS == ["authorization", "cookie", "x-api-key", "set-cookie"]
    assert cf.FILTER_POST_DATA_PARAMETERS == ["password", "token", "api_key"]
    assert cf.FILTER_QUERY_PARAMETERS == ["api_key", "token"]
    names = [m.__name__ for m in cf.REPLAY_MATCHERS]
    assert names[:6] == ["method", "scheme", "host", "port", "path", "query"]
    assert "json_body" in names[6]


def test_merge_helper_loads_bridge_cassette(tmp_path):
    """The host-side merge must consume a bridge-written cassette unchanged."""
    from clm.workers.notebook.http_replay_cassette import (
        CassettePaths,
        merge_staging_into_canonical,
        write_completion_marker,
    )

    canonical = tmp_path / "slides.http-cassette.yaml"
    staging = canonical.parent / f"{canonical.name}.staging-mitm-test"

    request = cf.vcr_request_from_parts(*_POST_JSON[:3], _POST_JSON[3])
    response = cf.vcr_response_dict_from_parts(
        _POST_JSON[4], _POST_JSON[5], _POST_JSON[6], _POST_JSON[7]
    )
    cf.write_cassette(staging, [(request, response)])
    write_completion_marker(CassettePaths(canonical=canonical, staging=staging))

    folded = merge_staging_into_canonical(CassettePaths(canonical=canonical, staging=staging))
    assert folded == 1
    assert canonical.exists()
    # Canonical now holds the interaction and the staging file is consumed.
    merged = cf.load_interactions(canonical)
    assert len(merged) == 1
    assert not staging.exists()
