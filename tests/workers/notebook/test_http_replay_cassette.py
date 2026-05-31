"""Unit tests for HTTP-replay cassette helpers — especially the dedup key.

The bulk of the cassette lifecycle is covered by integration tests; this
module pins behavior of the small pure-Python helpers that have caused
production incidents when they were silently wrong.
"""

from __future__ import annotations

import io

import pytest

from clm.workers.notebook.http_replay_cassette import (
    CassettePaths,
    _body_to_dedup_bytes,
    _dedup_key,
    merge_staging_into_canonical,
    write_completion_marker,
)


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


class TestMergeOverwriteExisting:
    """Mode-aware merge (issue #165 P3): ``refresh`` overwrites a stale entry.

    The default (``overwrite_existing=False``) keeps the canonical entry
    (first-seen-wins) — the long-standing behavior used by ``new-episodes`` /
    ``once`` / ``replay`` and every pre-build sweep. ``refresh`` (vcrpy ``all``)
    must instead let a freshly recorded interaction supersede the stale one.
    """

    @staticmethod
    def _interaction(cf, method, url, body, resp_body):
        request = cf.vcr_request_from_parts(method, url, [(b"content-type", b"text/plain")], body)
        response = cf.vcr_response_dict_from_parts(
            200, "OK", [(b"content-type", b"text/plain")], resp_body
        )
        return request, response

    def _write_canonical_and_markered_staging(self, tmp_path, canonical_pairs, staging_pairs):
        cf = pytest.importorskip("clm.infrastructure.http_replay_mitm.cassette_format")
        canonical = tmp_path / "slides.http-cassette.yaml"
        staging = canonical.parent / f"{canonical.name}.staging-mitm-test"
        cf.write_cassette(canonical, canonical_pairs)
        cf.write_cassette(staging, staging_pairs)
        write_completion_marker(CassettePaths(canonical=canonical, staging=staging))
        return cf, canonical, staging

    def test_overwrite_false_keeps_canonical_entry(self, tmp_path):
        cf = pytest.importorskip("clm.infrastructure.http_replay_mitm.cassette_format")
        url = "https://o.ai/v1/c"
        canonical_pairs = [self._interaction(cf, "POST", url, b"req", b"OLD")]
        staging_pairs = [self._interaction(cf, "POST", url, b"req", b"NEW")]
        cf, canonical, staging = self._write_canonical_and_markered_staging(
            tmp_path, canonical_pairs, staging_pairs
        )

        folded = merge_staging_into_canonical(
            CassettePaths(canonical=canonical, staging=staging), overwrite_existing=False
        )
        assert folded == 1
        merged = cf.load_interactions(canonical)
        assert len(merged) == 1
        # Default first-seen-wins: the stale canonical response is kept.
        assert merged[0][1]["body"]["string"] in (b"OLD", "OLD")

    def test_overwrite_true_staging_wins(self, tmp_path):
        cf = pytest.importorskip("clm.infrastructure.http_replay_mitm.cassette_format")
        url = "https://o.ai/v1/c"
        canonical_pairs = [self._interaction(cf, "POST", url, b"req", b"OLD")]
        staging_pairs = [self._interaction(cf, "POST", url, b"req", b"NEW")]
        cf, canonical, staging = self._write_canonical_and_markered_staging(
            tmp_path, canonical_pairs, staging_pairs
        )

        folded = merge_staging_into_canonical(
            CassettePaths(canonical=canonical, staging=staging), overwrite_existing=True
        )
        assert folded == 1
        merged = cf.load_interactions(canonical)
        assert len(merged) == 1
        # refresh overwrite: the freshly recorded response replaces the stale one.
        assert merged[0][1]["body"]["string"] in (b"NEW", "NEW")

    def test_overwrite_preserves_position_and_appends_new(self, tmp_path):
        cf = pytest.importorskip("clm.infrastructure.http_replay_mitm.cassette_format")
        a = "https://o.ai/a"
        b = "https://o.ai/b"
        c = "https://o.ai/c"
        canonical_pairs = [
            self._interaction(cf, "POST", a, b"ra", b"A_OLD"),
            self._interaction(cf, "POST", b, b"rb", b"B_OLD"),
        ]
        # Staging re-records A (overwrite) and adds a new C; B is untouched.
        staging_pairs = [
            self._interaction(cf, "POST", a, b"ra", b"A_NEW"),
            self._interaction(cf, "POST", c, b"rc", b"C_NEW"),
        ]
        cf, canonical, staging = self._write_canonical_and_markered_staging(
            tmp_path, canonical_pairs, staging_pairs
        )

        folded = merge_staging_into_canonical(
            CassettePaths(canonical=canonical, staging=staging), overwrite_existing=True
        )
        assert folded == 1
        merged = cf.load_interactions(canonical)
        uris = [req.uri for req, _ in merged]
        # A stays in its original position (replaced in place), B kept, C appended.
        assert uris == [a, b, c]
        by_uri = {req.uri: resp["body"]["string"] for req, resp in merged}
        assert by_uri[a] in (b"A_NEW", "A_NEW")  # overwritten
        assert by_uri[b] in (b"B_OLD", "B_OLD")  # untouched
        assert by_uri[c] in (b"C_NEW", "C_NEW")  # appended

    def test_overwrite_last_seen_within_staging_wins(self, tmp_path):
        """When one staging file holds the same request twice (vcrpy ``all``
        seeds canonical then appends the fresh recording), the LAST occurrence
        wins so the fresh response — not the seeded stale one — lands."""
        cf = pytest.importorskip("clm.infrastructure.http_replay_mitm.cassette_format")
        url = "https://o.ai/v1/c"
        canonical_pairs = [self._interaction(cf, "POST", url, b"req", b"OLD")]
        # Staging contains seeded-old THEN freshly-recorded-new (same key).
        staging_pairs = [
            self._interaction(cf, "POST", url, b"req", b"SEEDED_OLD"),
            self._interaction(cf, "POST", url, b"req", b"FRESH_NEW"),
        ]
        cf, canonical, staging = self._write_canonical_and_markered_staging(
            tmp_path, canonical_pairs, staging_pairs
        )

        merge_staging_into_canonical(
            CassettePaths(canonical=canonical, staging=staging), overwrite_existing=True
        )
        merged = cf.load_interactions(canonical)
        assert len(merged) == 1
        assert merged[0][1]["body"]["string"] in (b"FRESH_NEW", "FRESH_NEW")
