"""The vcrpy v1 YAML cassette format, owned by CLM (issue #355 stage 2).

CLM's committed course cassettes use vcrpy's v1 YAML schema, and the
replay pipeline depends on byte-stable serialization (no-op rebuilds must
leave cassettes byte-identical). This module implements that format —
the ``Request`` model, YAML (de)serialization, the secret filters, the
response decompression, and the request matchers — **without** the vcrpy
package, so the ``[replay]`` extra no longer carries an HTTP-patching
library it only ever used as a serializer.

The implementation is vendored from vcrpy 8.1.1 (https://github.com/kevin1024/vcrpy,
MIT License, Copyright (c) 2012 Kevin McCarthy) with only cosmetic
adaptation: byte-for-byte output compatibility with vcrpy-written
cassettes is the load-bearing requirement, pinned by golden-fixture and
round-trip tests (``tests/infrastructure/test_http_replay_vcr_format.py``).
Behavioral quirks are therefore preserved deliberately — e.g.
``replace_post_data_parameters`` re-dumps a JSON body via ``json.dumps``
(pretty separators) even when no key matched, because committed cassettes
contain bodies recorded through exactly that code path.

Like its consumers :mod:`cassette_format` and the mitmproxy addon, this
module is importable two ways: as
``clm.infrastructure.http_replay_mitm.vcr_format`` inside CLM's venv, and
by bare path inside the isolated ``mitmdump`` interpreter
(``uv tool install mitmproxy --with pyyaml``). It imports only ``yaml``
(PyYAML), optionally ``brotli``, and the standard library.
"""

from __future__ import annotations

import copy
import json
import zlib
from collections.abc import Mapping, MutableMapping
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml

# Use the libYAML versions when available (vcrpy did the same; output is
# identical, libyaml is just faster).
try:
    from yaml import CDumper as _Dumper
    from yaml import CLoader as _Loader
except ImportError:  # pragma: no cover — PyYAML without libyaml
    from yaml import Dumper as _Dumper
    from yaml import Loader as _Loader

try:  # brotli is optional, exactly as in vcrpy (mitmproxy ships it)
    import brotli
except ImportError:  # pragma: no cover
    try:
        import brotlicffi as brotli
    except ImportError:
        brotli = None

# Version 1 is the only cassette schema vcrpy ever wrote for CLM.
CASSETTE_FORMAT_VERSION = 1


class CassetteNotFoundError(FileNotFoundError):
    """Raised by :func:`load_cassette` when the cassette file is absent."""


class CassetteDecodeError(ValueError):
    """Raised by :func:`load_cassette` when the file is not readable text."""


# ---------------------------------------------------------------------------
# Request model (vcr/util.py CaseInsensitiveDict + vcr/request.py)
# ---------------------------------------------------------------------------


class CaseInsensitiveDict(MutableMapping):
    """A case-insensitive ``dict``-like object (requests' implementation).

    Keys are matched case-insensitively; iteration yields the casing of the
    last key set. Vendored unchanged because header-filter semantics (which
    header counts as present, which casing survives a rewrite) feed directly
    into recorded cassette bytes.
    """

    def __init__(self, data=None, **kwargs):
        self._store: dict = {}
        if data is None:
            data = {}
        self.update(data, **kwargs)

    def __setitem__(self, key, value):
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key):
        return self._store[key.lower()][1]

    def __delitem__(self, key):
        del self._store[key.lower()]

    def __iter__(self):
        return (casedkey for casedkey, mappedvalue in self._store.values())

    def __len__(self):
        return len(self._store)

    def lower_items(self):
        return ((lowerkey, keyval[1]) for (lowerkey, keyval) in self._store.items())

    def __eq__(self, other):
        if isinstance(other, Mapping):
            other = CaseInsensitiveDict(other)
        else:
            return NotImplemented
        return dict(self.lower_items()) == dict(other.lower_items())

    def copy(self):
        return CaseInsensitiveDict(self._store.values())

    def __repr__(self):
        return str(dict(self.items()))


class HeadersDict(CaseInsensitiveDict):
    """vcrpy's header dict: single value per name, first-seen casing wins.

    Cassettes store headers as dict-of-lists; in memory vcrpy collapses each
    list to its first element and preserves the casing a key had when first
    set. Both quirks affect the serialized bytes, so they are kept.
    """

    def __setitem__(self, key, value):
        if isinstance(value, (tuple, list)):
            value = value[0]
        old = self._store.get(key.lower())
        if old:
            key = old[0]
        super().__setitem__(key, value)


def _is_nonsequence_iterator(obj) -> bool:
    return hasattr(obj, "__iter__") and not isinstance(obj, (bytearray, bytes, dict, list, str))


class Request:
    """vcrpy's representation of a recorded HTTP request (vcr/request.py)."""

    def __init__(self, method, uri, body, headers):
        self.method = method
        self.uri = uri
        self._was_file = hasattr(body, "read")
        self._was_iter = _is_nonsequence_iterator(body)
        if self._was_file:
            if hasattr(body, "tell"):
                tell = body.tell()
                self.body = body.read()
                body.seek(tell)
            else:
                self.body = body.read()
        elif self._was_iter:
            self.body = list(body)
        else:
            self.body = body
        self.headers = headers

    @property
    def uri(self):
        return self._uri

    @uri.setter
    def uri(self, uri):
        self._uri = uri
        self.parsed_uri = urlparse(uri)

    @property
    def headers(self):
        return self._headers

    @headers.setter
    def headers(self, value):
        if not isinstance(value, HeadersDict):
            value = HeadersDict(value)
        self._headers = value

    @property
    def body(self):
        if self._was_file:
            return BytesIO(self._body)
        if self._was_iter:
            return iter(self._body)
        return self._body

    @body.setter
    def body(self, value):
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._body = value

    @property
    def scheme(self):
        return self.parsed_uri.scheme

    @property
    def host(self):
        return self.parsed_uri.hostname

    @property
    def port(self):
        port = self.parsed_uri.port
        if port is None:
            with suppress(KeyError):
                port = {"https": 443, "http": 80}[self.parsed_uri.scheme]
        return port

    @property
    def path(self):
        return self.parsed_uri.path

    @property
    def query(self):
        return sorted(parse_qsl(self.parsed_uri.query))

    # Aliases vcrpy kept for backwards compatibility; cheap to preserve.
    @property
    def url(self):
        return self.uri

    @property
    def protocol(self):
        return self.scheme

    def __str__(self):
        return f"<Request ({self.method}) {self.uri}>"

    def __repr__(self):
        return self.__str__()

    def _to_dict(self):
        return {
            "method": self.method,
            "uri": self.uri,
            "body": self.body,
            "headers": {k: [v] for k, v in self.headers.items()},
        }

    @classmethod
    def _from_dict(cls, dct):
        return Request(**dct)


# ---------------------------------------------------------------------------
# bytes <-> str body conversion (vcr/serializers/compat.py)
# ---------------------------------------------------------------------------
# YAML stores utf-8 text; HTTP machinery wants bytes. Bodies that do not
# decode as utf-8 stay bytes on disk (PyYAML emits !!binary) — both
# directions must silently give up on conversion errors, exactly as vcrpy
# does, or binary-body cassettes change shape.


def _convert_string_to_unicode(string):
    with suppress(TypeError, UnicodeDecodeError, AttributeError):
        if string is not None and not isinstance(string, str):
            return string.decode("utf-8")
    return string


def convert_body_to_unicode(resp):
    if not isinstance(resp, dict):
        return _convert_string_to_unicode(resp)
    body = resp.get("body")
    if body is not None:
        try:
            body["string"] = _convert_string_to_unicode(body["string"])
        except (KeyError, TypeError, AttributeError):
            # Not the dict shape we expected (e.g. a request dict's plain
            # body) — convert the body value itself.
            resp["body"] = _convert_string_to_unicode(body)
    return resp


def convert_body_to_bytes(resp):
    try:
        if resp["body"]["string"] is not None and not isinstance(resp["body"]["string"], bytes):
            resp["body"]["string"] = resp["body"]["string"].encode("utf-8")
    except (KeyError, TypeError, UnicodeEncodeError):
        pass
    return resp


# ---------------------------------------------------------------------------
# Cassette (de)serialization (vcr/serialize.py + serializers/yamlserializer.py)
# ---------------------------------------------------------------------------


def serialize_cassette(cassette_dict: dict) -> str:
    """Serialize ``{"requests": [...], "responses": [...]}`` to v1 YAML.

    Drop-in equivalent of ``vcr.serialize.serialize(cassette_dict,
    yamlserializer)``. NOTE the vcrpy quirk this inherits:
    ``convert_body_to_unicode`` mutates the response dicts **in place**
    (bytes body -> str). Callers that keep the interactions in memory must
    deep-copy first — :func:`cassette_format.serialize_interactions` does.
    """
    interactions = [
        {
            "request": convert_body_to_unicode(request._to_dict()),
            "response": convert_body_to_unicode(response),
        }
        for request, response in zip(
            cassette_dict["requests"], cassette_dict["responses"], strict=False
        )
    ]
    data = {"version": CASSETTE_FORMAT_VERSION, "interactions": interactions}
    payload: str = yaml.dump(data, Dumper=_Dumper)
    return payload


def deserialize_cassette(cassette_string: str) -> tuple[list[Request], list[dict]]:
    """Parse v1 YAML into ``(requests, responses)`` lists."""
    data = yaml.load(cassette_string, Loader=_Loader)  # noqa: S506 — trusted repo files
    if isinstance(data, list) and data and "request" in data[0]:
        raise ValueError(
            "This cassette uses the pre-1.0 vcrpy format, which CLM has never "
            "written; re-record it."
        )
    requests = [Request._from_dict(r["request"]) for r in data["interactions"]]
    responses = [convert_body_to_bytes(r["response"]) for r in data["interactions"]]
    return requests, responses


def load_cassette(cassette_path: Path | str) -> tuple[list[Request], list[dict]]:
    """Load a cassette file — equivalent of vcrpy's ``FilesystemPersister``."""
    cassette_path = Path(cassette_path)
    if not cassette_path.is_file():
        raise CassetteNotFoundError(str(cassette_path))
    try:
        data = cassette_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise CassetteDecodeError("Can't read cassette, encoding is broken") from err
    return deserialize_cassette(data)


# ---------------------------------------------------------------------------
# Secret filters + response decompression (vcr/filters.py)
# ---------------------------------------------------------------------------


def decompress_deflate(body):
    try:
        return zlib.decompress(body)
    except zlib.error:
        return body  # assume already decompressed


def decompress_gzip(body):
    try:
        return zlib.decompress(body, zlib.MAX_WBITS | 16)
    except zlib.error:
        return body  # assume already decompressed


AVAILABLE_DECOMPRESSORS = {
    "deflate": decompress_deflate,
    "gzip": decompress_gzip,
}

if brotli is not None:

    def decompress_brotli(body):
        try:
            return brotli.decompress(body)
        except brotli.error:
            return body  # assume already decompressed

    AVAILABLE_DECOMPRESSORS["br"] = decompress_brotli


def replace_headers(request: Request, replacements) -> Request:
    """Remove/replace request headers; value ``None`` removes the header."""
    new_headers = request.headers.copy()
    for k, rv in replacements:
        if k in new_headers:
            ov = new_headers.pop(k)
            if callable(rv):
                rv = rv(key=k, value=ov, request=request)
            if rv is not None:
                new_headers[k] = rv
    request.headers = new_headers
    return request


def replace_query_parameters(request: Request, replacements) -> Request:
    """Remove/replace query parameters; value ``None`` removes the parameter."""
    query = request.query
    new_query = []
    replacements = dict(replacements)
    for k, ov in query:
        if k not in replacements:
            new_query.append((k, ov))
        else:
            rv = replacements[k]
            if callable(rv):
                rv = rv(key=k, value=ov, request=request)
            if rv is not None:
                new_query.append((k, rv))
    uri_parts = list(urlparse(request.uri))
    uri_parts[4] = urlencode(new_query)
    request.uri = urlunparse(uri_parts)
    return request


def replace_post_data_parameters(request: Request, replacements) -> Request:
    """Remove/replace form/JSON body parameters; value ``None`` removes.

    Inherits vcrpy's JSON quirk on purpose: a JSON body is re-dumped via
    ``json.dumps`` (default ``", "``/``": "`` separators) even when no key
    matched — committed cassettes contain bodies recorded exactly this way,
    and the JSON-semantic replay matcher absorbs the byte difference.
    """
    if not request.body:
        return request

    replacements = dict(replacements)
    if request.method == "POST" and not isinstance(request.body, BytesIO):
        if isinstance(request.body, dict):
            new_body = request.body.copy()
            for k, rv in replacements.items():
                if k in new_body:
                    ov = new_body.pop(k)
                    if callable(rv):
                        rv = rv(key=k, value=ov, request=request)
                    if rv is not None:
                        new_body[k] = rv
            request.body = new_body
        elif request.headers.get("Content-Type") == "application/json":
            json_data = json.loads(request.body)
            for k, rv in replacements.items():
                if k in json_data:
                    ov = json_data.pop(k)
                    if callable(rv):
                        rv = rv(key=k, value=ov, request=request)
                    if rv is not None:
                        json_data[k] = rv
            request.body = json.dumps(json_data).encode("utf-8")
        else:
            if isinstance(request.body, str):
                request.body = request.body.encode("utf-8")
            splits = [p.partition(b"=") for p in request.body.split(b"&")]
            new_splits = []
            for k, sep, ov in splits:
                if sep is None:
                    new_splits.append((k, sep, ov))
                else:
                    rk = k.decode("utf-8")
                    if rk not in replacements:
                        new_splits.append((k, sep, ov))
                    else:
                        rv = replacements[rk]
                        if callable(rv):
                            rv = rv(key=rk, value=ov.decode("utf-8"), request=request)
                        if rv is not None:
                            new_splits.append((k, sep, rv.encode("utf-8")))
            request.body = b"&".join(
                k if sep is None else b"".join([k, sep, v]) for k, sep, v in new_splits
            )
    return request


def decode_response(response: dict) -> dict:
    """Decompress a gzip/deflate/br response body, fixing the headers.

    Deep-copies the input (never mutates the caller's dict), removes the
    handled ``content-encoding`` value, and rewrites ``content-length`` to
    the decompressed length — exactly as vcrpy records responses when
    ``decode_compressed_response=True``.
    """
    response = copy.deepcopy(response)
    headers = CaseInsensitiveDict(response["headers"])
    content_encoding = headers.get("content-encoding")
    if not content_encoding:
        return response
    decompressor = AVAILABLE_DECOMPRESSORS.get(content_encoding[0])
    if not decompressor:
        return response

    headers["content-encoding"].remove(content_encoding[0])
    if not headers["content-encoding"]:
        del headers["content-encoding"]

    new_body = decompressor(response["body"]["string"])
    response["body"]["string"] = new_body
    headers["content-length"] = [str(len(new_body))]
    response["headers"] = dict(headers)
    return response


# ---------------------------------------------------------------------------
# Request matchers (vcr/matchers.py — the subset CLM's match_on uses)
# ---------------------------------------------------------------------------
# Matcher protocol: raise AssertionError on mismatch, return None on match.


def method(r1: Request, r2: Request) -> None:
    if r1.method != r2.method:
        raise AssertionError(f"{r1.method} != {r2.method}")


def scheme(r1: Request, r2: Request) -> None:
    if r1.scheme != r2.scheme:
        raise AssertionError(f"{r1.scheme} != {r2.scheme}")


def host(r1: Request, r2: Request) -> None:
    if r1.host != r2.host:
        raise AssertionError(f"{r1.host} != {r2.host}")


def port(r1: Request, r2: Request) -> None:
    if r1.port != r2.port:
        raise AssertionError(f"{r1.port} != {r2.port}")


def path(r1: Request, r2: Request) -> None:
    if r1.path != r2.path:
        raise AssertionError(f"{r1.path} != {r2.path}")


def query(r1: Request, r2: Request) -> None:
    if r1.query != r2.query:
        raise AssertionError(f"{r1.query} != {r2.query}")


def requests_match(r1: Request, r2: Request, matchers) -> bool:
    """``True`` iff every matcher passes (vcrpy's evaluation semantics)."""
    for matcher in matchers:
        try:
            match = matcher(r1, r2)
            if match is False:  # boolean-style matcher
                return False
        except AssertionError:
            return False
    return True
