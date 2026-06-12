"""Byte-stability tests for the CLM-owned vcrpy v1 cassette format (#355 stage 2).

``vcr_format`` replaced the vcrpy dependency. The replacement was validated
against real vcrpy 8.1.1 with a one-time differential check (serialization
bytes, load round-trips, filters, decode_response, matchers over a diverse
case set — recorded in the stage-2 PR) plus a round-trip of all 2072
committed PythonCourses cassettes. These tests pin that validated behavior
permanently, without needing vcrpy installed:

* the **golden fixture** pins the exact serialization bytes (a vcrpy-format
  cassette generated at migration time and committed). If this test ever
  fails after a PyYAML upgrade, PyYAML changed its emitter formatting — that
  would rewrite every committed course cassette on the next merge, so treat
  it as a real incident (pin PyYAML or adapt deliberately), not a fixture to
  regenerate casually;
* round-trip tests pin load→serialize byte-stability (the no-op-rebuild
  invariant);
* unit tests pin the filter/matcher/decompression quirks committed cassettes
  depend on.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from clm.infrastructure.http_replay_mitm import cassette_format as cf
from clm.infrastructure.http_replay_mitm import vcr_format as vf

FIXTURE = Path(__file__).parent / "fixtures" / "golden.http-cassette.yaml"

# The exact case set the committed golden fixture was generated from.
# DO NOT edit one without the other.
GOLDEN_CASES = [
    (
        "GET",
        "https://restcountries.com/v3.1/name/germany",
        b"",
        [("accept", "*/*"), ("user-agent", "python-requests/2.32")],
        200,
        "OK",
        [("content-type", "application/json")],
        b'[{"name":"Germany"}]',
    ),
    (
        "POST",
        "https://api.example.com/v1/chat/completions",
        b'{"messages":[{"role":"user","content":"hi"}],"stream":false}',
        [("content-type", "application/json")],
        200,
        None,
        [("content-type", "application/json"), ("set-cookie", "a=1"), ("set-cookie", "b=2")],
        '{"choices":[{"text":"hällo wörld ☃"}]}'.encode(),
    ),
    (
        "GET",
        "http://example.com:8080/binary",
        b"",
        [],
        200,
        "OK",
        [("content-type", "application/octet-stream")],
        bytes(range(256)),
    ),
    (
        "GET",
        "https://example.com/gzip",
        b"",
        [],
        200,
        "OK",
        [("content-encoding", "gzip"), ("content-type", "text/plain")],
        gzip.compress(b"hello compressed world " * 10),
    ),
    ("GET", "https://example.com/crlf", b"", [], 204, "No Content", [], b"line1\r\nline2\nline3"),
]


def _golden_interactions() -> list:
    interactions = []
    for method, uri, body, headers, status, message, resp_headers, resp_body in GOLDEN_CASES:
        req = cf.vcr_request_from_parts(method, uri, headers, body)
        resp = cf.vcr_response_dict_from_parts(status, message, resp_headers, resp_body)
        interactions.append((req, resp))
    return interactions


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n")


class TestGoldenFixture:
    def test_serialization_matches_golden_bytes(self):
        assert cf.serialize_interactions(_golden_interactions()) == _fixture_text()

    def test_load_then_serialize_is_byte_stable(self):
        # The no-op-rebuild invariant: reading a cassette and writing it back
        # must not change a single byte (otherwise every build churns git).
        reqs, resps = vf.load_cassette(FIXTURE)
        assert cf.serialize_interactions(list(zip(reqs, resps))) == _fixture_text()

    def test_load_recovers_typed_fields(self):
        reqs, resps = vf.load_cassette(FIXTURE)
        assert [r.method for r in reqs] == ["GET", "POST", "GET", "GET", "GET"]
        # utf-8 bodies come back as bytes (cassettes store text, HTTP wants bytes)
        assert resps[0]["body"]["string"] == b'[{"name":"Germany"}]'
        # binary (non-utf-8) bodies survive via !!binary
        assert resps[2]["body"]["string"] == bytes(range(256))
        # gzip body was stored decompressed with content-encoding dropped
        assert resps[3]["body"]["string"] == b"hello compressed world " * 10
        assert "content-encoding" not in resps[3]["headers"]
        assert resps[3]["headers"]["content-length"] == [str(len(b"hello compressed world " * 10))]
        # absent reason phrase round-trips as None
        assert resps[1]["status"]["message"] is None


class TestRequestModel:
    def test_headers_collapse_lists_and_keep_first_casing(self):
        req = vf.Request("GET", "https://x.example/", b"", {"Content-Type": ["a", "b"]})
        assert req.headers["content-type"] == "a"
        req.headers["CONTENT-TYPE"] = "c"
        assert dict(req.headers.items()) == {"Content-Type": "c"}

    def test_str_body_is_encoded_to_bytes(self):
        req = vf.Request("POST", "https://x.example/", "körper", {})
        assert req.body == "körper".encode()

    def test_url_components_and_default_ports(self):
        req = vf.Request("GET", "https://host.example/path?b=2&a=1", b"", {})
        assert (req.scheme, req.host, req.port, req.path) == (
            "https",
            "host.example",
            443,
            "/path",
        )
        assert req.query == [("a", "1"), ("b", "2")]  # sorted
        assert vf.Request("GET", "http://host.example/x", b"", {}).port == 80


class TestFilters:
    def test_replace_headers_removes_secrets_case_insensitively(self):
        req = vf.Request(
            "GET", "https://x.example/", b"", {"Authorization": "Bearer s", "Keep": "1"}
        )
        out = vf.replace_headers(req, [("authorization", None)])
        assert "authorization" not in out.headers
        assert out.headers["keep"] == "1"

    def test_replace_query_parameters_rewrites_uri(self):
        req = vf.Request("GET", "https://x.example/p?api_key=S&keep=1", b"", {})
        out = vf.replace_query_parameters(req, [("api_key", None)])
        assert out.uri == "https://x.example/p?keep=1"

    def test_replace_post_data_json_redump_quirk(self):
        # The inherited vcrpy quirk: a JSON body is re-dumped with the default
        # (", ", ": ") separators even when no key matched. Committed cassettes
        # contain bodies recorded through exactly this path, so it must stay.
        req = vf.Request(
            "POST", "https://x.example/", b'{"keep":"1"}', {"Content-Type": "application/json"}
        )
        out = vf.replace_post_data_parameters(req, [("password", None)])
        assert out.body == b'{"keep": "1"}'

    def test_replace_post_data_form_encoded(self):
        req = vf.Request(
            "POST",
            "https://x.example/",
            b"password=p&keep=1",
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        out = vf.replace_post_data_parameters(req, [("password", None)])
        assert out.body == b"keep=1"

    def test_decode_response_gzip_and_header_fixup(self):
        body = b"payload " * 10
        response = {
            "status": {"code": 200, "message": "OK"},
            "headers": {"Content-Encoding": ["gzip"], "Content-Type": ["text/plain"]},
            "body": {"string": gzip.compress(body)},
        }
        out = vf.decode_response(response)
        assert out["body"]["string"] == body
        assert "Content-Encoding" not in out["headers"]
        assert out["headers"]["content-length"] == [str(len(body))]
        # input dict untouched (decode_response deep-copies)
        assert response["headers"]["Content-Encoding"] == ["gzip"]

    def test_decode_response_unknown_encoding_passthrough(self):
        response = {
            "status": {"code": 200, "message": "OK"},
            "headers": {"content-encoding": ["zstd"]},
            "body": {"string": b"opaque"},
        }
        assert vf.decode_response(response) == response


class TestMatchers:
    def _req(self, method, uri):
        return vf.Request(method, uri, b"", {})

    MATCHERS = (vf.method, vf.scheme, vf.host, vf.port, vf.path, vf.query)

    @pytest.mark.parametrize(
        ("u1", "u2", "expected"),
        [
            ("https://a.com/x?b=2&a=1", "https://a.com/x?a=1&b=2", True),  # query order
            ("https://a.com/x?a=1", "https://a.com/y?a=1", False),  # path
            ("https://a.com:443/x", "https://a.com/x", True),  # default port
            ("http://a.com:80/x", "http://a.com/x", True),
            ("https://a.com/x", "http://a.com/x", False),  # scheme
            ("https://A.example/x", "https://a.example/x", True),  # host casing
        ],
    )
    def test_uri_equivalence_classes(self, u1, u2, expected):
        assert (
            vf.requests_match(self._req("GET", u1), self._req("GET", u2), self.MATCHERS) is expected
        )

    def test_method_mismatch(self):
        assert not vf.requests_match(
            self._req("GET", "https://a.com/x"), self._req("POST", "https://a.com/x"), self.MATCHERS
        )


class TestLoadErrors:
    def test_missing_file_raises_filenotfound_subclass(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            vf.load_cassette(tmp_path / "absent.yaml")

    def test_pre_v1_format_rejected(self, tmp_path):
        legacy = tmp_path / "old.yaml"
        legacy.write_text("- request:\n    method: GET\n", encoding="utf-8")
        with pytest.raises(ValueError, match="pre-1.0"):
            vf.load_cassette(legacy)
