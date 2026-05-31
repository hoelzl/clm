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
