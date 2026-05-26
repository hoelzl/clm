"""Unit tests for HTTP-replay cassette helpers — especially the dedup key.

The bulk of the cassette lifecycle is covered by integration tests; this
module pins behavior of the small pure-Python helpers that have caused
production incidents when they were silently wrong.
"""

from __future__ import annotations

import io

from clm.workers.notebook.http_replay_cassette import _body_to_dedup_bytes, _dedup_key


class _StubRequest:
    """Minimal stand-in for ``vcr.request.Request``."""

    def __init__(self, method: str, uri: str, body):
        self.method = method
        self.uri = uri
        self.body = body


class TestBodyToDedupBytes:
    def test_none_body(self):
        assert _body_to_dedup_bytes(None) == b""

    def test_bytes_passthrough(self):
        assert _body_to_dedup_bytes(b"abc") == b"abc"

    def test_bytearray_normalized_to_bytes(self):
        out = _body_to_dedup_bytes(bytearray(b"xyz"))
        assert isinstance(out, bytes)
        assert out == b"xyz"

    def test_str_encoded_utf8(self):
        assert _body_to_dedup_bytes("héllo") == "héllo".encode()

    def test_bytesio_read_and_rewound(self):
        stream = io.BytesIO(b"payload")
        out = _body_to_dedup_bytes(stream)
        assert out == b"payload"
        # The stream must be rewound so other consumers (e.g. requests.Session)
        # can still read it after dedup keying.
        assert stream.read() == b"payload"

    def test_two_bytesio_with_identical_content_produce_equal_keys(self):
        # Regression for the production growth bug: vcrpy YAML serializes
        # a BytesIO request body via ``!!python/object/new:_io.BytesIO``;
        # loading the cassette twice produces two distinct BytesIO
        # instances with the same content. ``str(BytesIO_instance)``
        # contains the object's memory address, so the previous dedup
        # logic treated them as distinct entries and merged both into
        # canonical on every build, growing the cassette by the same
        # number of LangSmith uploads each rebuild.
        a = io.BytesIO(b"identical payload")
        b = io.BytesIO(b"identical payload")
        assert _body_to_dedup_bytes(a) == _body_to_dedup_bytes(b)


class TestDedupKey:
    def test_method_uri_body_form_the_key(self):
        r = _StubRequest("POST", "https://example.com/x", b"hello")
        assert _dedup_key(r) == ("POST", "https://example.com/x", b"hello")

    def test_bytesio_bodies_with_same_content_dedup(self):
        # Two requests that the merge SHOULD recognize as identical even
        # though their bodies are distinct BytesIO instances (the case
        # that surfaced as cassette growth on every LangChain build).
        r1 = _StubRequest(
            "POST",
            "https://api.smith.langchain.com/runs/multipart",
            io.BytesIO(b"compressed multipart payload"),
        )
        r2 = _StubRequest(
            "POST",
            "https://api.smith.langchain.com/runs/multipart",
            io.BytesIO(b"compressed multipart payload"),
        )
        assert _dedup_key(r1) == _dedup_key(r2)

    def test_different_uri_yields_different_key(self):
        r1 = _StubRequest("GET", "https://a.example.com/", None)
        r2 = _StubRequest("GET", "https://b.example.com/", None)
        assert _dedup_key(r1) != _dedup_key(r2)

    def test_different_body_yields_different_key(self):
        r1 = _StubRequest("POST", "https://api.example.com/", b"one")
        r2 = _StubRequest("POST", "https://api.example.com/", b"two")
        assert _dedup_key(r1) != _dedup_key(r2)
