"""vcrpy-YAML cassette bridge for the mitmproxy transport (issue #165, P1).

The mitmproxy addon records and replays at the network layer, but the
*on-disk* format must stay byte-compatible with the existing vcrpy v1
YAML cassette schema so that committed course cassettes, ``clm cassette
doctor``, ``strip_cassette_hosts.py`` and
:func:`clm.workers.notebook.http_replay_cassette.merge_staging_into_canonical`
keep working unchanged.

This module is **pure**: it imports only :mod:`vcr` (used purely as a
serializer — importing the serializer surface does *not* activate any
httpcore/urllib3 patching) and the standard library. It has **no**
``clm`` package imports, so it can be imported two ways:

* as ``clm.infrastructure.http_replay_mitm.cassette_format`` inside CLM's
  own virtualenv (unit tests, host code), and
* by bare path import inside the isolated ``mitmdump`` interpreter
  (``uv tool install mitmproxy --with vcrpy``), where the ``clm`` package
  is not installed but ``vcr`` is.

The conversion functions deliberately mirror vcrpy's own
``vcr.stubs.httpcore_stubs._make_vcr_request`` /
``_serialize_response`` and ``vcr.filters.decode_response`` so a
mitmproxy-recorded interaction serializes to bytes identical to what
vcrpy would have written for the same HTTP exchange.
"""

from __future__ import annotations

import copy
import os
import uuid
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

# vcr is used purely as a serializer here. Importing these names does not
# patch httpcore/urllib3 (verified: patching only activates when a VCR
# cassette context is entered). The isolated mitmdump environment must
# therefore have vcrpy installed alongside mitmproxy.
from vcr.filters import decode_response
from vcr.persisters.filesystem import FilesystemPersister
from vcr.request import Request
from vcr.serialize import serialize as _vcr_serialize
from vcr.serializers import yamlserializer

# A single recorded HTTP interaction: a vcr Request paired with the
# serialized-response dict (``{"status", "headers", "body"}``) that vcrpy
# stores. The body string is kept as ``bytes`` in memory; it is only
# decoded to ``str`` at serialization time (see :func:`serialize_interactions`).
Interaction = tuple[Request, dict]

# Header fields as mitmproxy exposes them: an iterable of ``(name, value)``
# pairs that may be ``bytes`` or ``str``. vcrpy decodes header bytes as
# ASCII, so we mirror that.
HeaderFields = Iterable[tuple[object, object]]


def _decode_ascii(value: object) -> str:
    """Decode a header name/value to ``str`` the way vcrpy does (ASCII)."""
    if isinstance(value, bytes):
        return value.decode("ascii")
    return str(value)


def _ascii_reason(reason_phrase: str | None) -> str | None:
    """Normalise an HTTP reason phrase to an ASCII ``str`` or ``None``.

    vcrpy decodes the reason as ASCII and stores ``None`` when absent. We
    map empty/missing to ``None`` and drop a non-ASCII reason to ``None``
    too (it could not round-trip through vcrpy's ASCII (de)serialization).
    """
    if not reason_phrase:
        return None
    try:
        reason_phrase.encode("ascii")
    except UnicodeEncodeError:
        return None
    return reason_phrase


def vcr_request_from_parts(
    method: str,
    url: str,
    header_fields: HeaderFields,
    body: bytes,
) -> Request:
    """Build a vcr :class:`~vcr.request.Request` matching vcrpy's httpcore stub.

    Mirrors ``vcr.stubs.httpcore_stubs._make_vcr_request``: multiple
    headers with the same name are concatenated with ``", "`` (as HTTPX
    does), then handed to :class:`~vcr.request.Request`, whose
    ``HeadersDict`` collapses them to a single value while preserving the
    first-seen casing. Going through the real ``Request`` constructor
    guarantees ``_to_dict()`` produces the exact shape vcrpy serializes.
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    for name, value in header_fields:
        grouped[_decode_ascii(name)].append(_decode_ascii(value))
    headers = {name: ", ".join(values) for name, values in grouped.items()}
    return Request(method, url, body, headers)


def vcr_response_dict_from_parts(
    status_code: int,
    reason_phrase: str | None,
    header_fields: HeaderFields,
    raw_body: bytes,
    *,
    decode_compressed: bool = True,
) -> dict:
    """Build the vcr response dict matching vcrpy's httpcore stub.

    Mirrors ``vcr.stubs.httpcore_stubs._serialize_response`` (status +
    headers-as-dict-of-lists + ``body.string`` raw bytes) and then, when
    ``decode_compressed`` is true (CLM always sets
    ``decode_compressed_response=True``), runs vcrpy's own
    :func:`vcr.filters.decode_response` so a gzip/deflate/br body is
    decompressed, the ``content-encoding`` header dropped, and
    ``content-length`` rewritten — exactly as vcrpy records it.

    ``raw_body`` must be the **encoded** bytes as they came off the wire
    (mitmproxy's ``flow.response.raw_content``), not the decoded content,
    so :func:`decode_response` sees the ``content-encoding`` header and
    decompresses identically to vcrpy.

    The reason phrase is forced to ASCII (or dropped to ``None``): vcrpy
    decodes it as ASCII (``extensions["reason_phrase"].decode("ascii")``)
    and would *raise* on a non-ASCII reason — and a stored non-ASCII
    ``status.message`` cannot be replayed by the in-kernel vcrpy transport
    (its ``_deserialize_response`` does ``message.encode("ascii")``). So for
    the common ASCII case this is byte-identical to vcrpy; for the rare
    non-ASCII HTTP/1.1 reason (real LLM endpoints use ASCII reasons or
    HTTP/2, which has none) we drop it to ``None`` rather than crash or
    write a cassette vcrpy cannot read back.
    """
    headers: dict[str, list[str]] = defaultdict(list)
    for name, value in header_fields:
        headers[_decode_ascii(name)].append(_decode_ascii(value))

    response = {
        # vcrpy stores ``message: None`` when there is no reason phrase
        # (e.g. HTTP/2). Normalise empty strings to None to match, and keep
        # the stored message ASCII-clean so the cassette stays vcrpy-replayable.
        "status": {"code": status_code, "message": _ascii_reason(reason_phrase)},
        "headers": dict(headers),
        "body": {"string": raw_body},
    }
    if decode_compressed:
        # decode_response deep-copies its input, so this never mutates the
        # dict we just built; it returns the decompressed equivalent.
        response = decode_response(response)
    return response


def fingerprint(request: Request) -> tuple[str, str, bytes]:
    """Dedup/replay fingerprint: ``(method, uri, body-bytes)``.

    Matches :func:`clm.workers.notebook.http_replay_cassette._dedup_key`
    so the addon's in-build dedup and the host-side merge agree on what
    counts as "the same request". (JSON-semantic body matching is P3.)
    """
    body = request.body
    if body is None:
        body_bytes = b""
    elif isinstance(body, bytes):
        body_bytes = body
    elif isinstance(body, bytearray):
        body_bytes = bytes(body)
    elif isinstance(body, str):
        body_bytes = body.encode("utf-8", errors="replace")
    else:  # BytesIO / iterator — read defensively
        read = getattr(body, "read", None)
        if callable(read):
            data = read()
            seek = getattr(body, "seek", None)
            if callable(seek):
                try:
                    seek(0)
                except Exception:  # noqa: BLE001 — best-effort rewind
                    pass
            body_bytes = data if isinstance(data, bytes) else str(data).encode("utf-8", "replace")
        else:
            body_bytes = repr(body).encode("utf-8", "replace")
    return (
        getattr(request, "method", ""),
        getattr(request, "uri", ""),
        body_bytes,
    )


def serialize_interactions(interactions: Iterable[Interaction]) -> str:
    """Serialize interactions to vcrpy v1 YAML.

    Routes through ``vcr.serialize.serialize`` + the YAML serializer so the
    output is byte-identical to a vcrpy-written cassette. vcrpy's
    ``convert_to_unicode`` mutates ``response["body"]["string"]`` from
    ``bytes`` to ``str`` **in place** during serialization; we deep-copy
    first (exactly as CLM's ``_ClmDeepCopyPersister`` does in the kernel
    bootstrap) so the caller's in-memory interactions keep their ``bytes``
    bodies and stay valid for subsequent replays/writes.
    """
    requests = []
    responses = []
    for request, response in interactions:
        requests.append(request)
        responses.append(response)
    cassette_dict = copy.deepcopy({"requests": requests, "responses": responses})
    payload: str = _vcr_serialize(cassette_dict, yamlserializer)
    return payload


def load_interactions(path: Path) -> list[Interaction]:
    """Load a vcrpy YAML cassette into ``(Request, response-dict)`` pairs.

    Uses vcrpy's ``FilesystemPersister`` so the in-memory shape matches
    what the merge helper and the kernel produce. Returns an empty list
    when the cassette does not exist.
    """
    path = Path(path)
    if not path.exists():
        return []
    requests, responses = FilesystemPersister.load_cassette(path, serializer=yamlserializer)
    return list(zip(requests, responses, strict=False))


def write_cassette(path: Path, interactions: Iterable[Interaction]) -> None:
    """Serialize and atomically write a cassette with LF line endings.

    LF-only writes are required (see
    :func:`clm.workers.notebook.http_replay_cassette._atomic_write_text`):
    the repo's ``eol=lf`` gitattributes would otherwise flap the cassette
    CRLF↔LF between builds and checkouts and perturb its bytes.
    """
    atomic_write_lf(Path(path), serialize_interactions(interactions))


def response_dict_to_reply_parts(response: dict) -> tuple[int, list[tuple[str, str]], bytes]:
    """Decompose a stored vcr response dict for replay reconstruction.

    Returns ``(status_code, header_pairs, content_bytes)`` suitable for
    building a mitmproxy ``Response``. The stored body may be ``str`` (as
    loaded from YAML) or ``bytes`` (freshly recorded this build); it is
    normalised to ``bytes``. Headers are flattened from the dict-of-lists
    schema to repeated ``(name, value)`` pairs.
    """
    status_code = int(response["status"]["code"])
    body_string = response.get("body", {}).get("string")
    if body_string is None:
        content = b""
    elif isinstance(body_string, bytes):
        content = body_string
    else:
        content = str(body_string).encode("utf-8")

    header_pairs: list[tuple[str, str]] = []
    for name, values in (response.get("headers") or {}).items():
        if isinstance(values, (list, tuple)):
            for value in values:
                header_pairs.append((str(name), str(value)))
        else:
            header_pairs.append((str(name), str(values)))
    return status_code, header_pairs, content


def atomic_write_lf(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically with LF line endings.

    Self-contained reimplementation of CLM's ``_atomic_write_text`` (the
    mitmdump interpreter cannot import the ``clm`` package): writes to a
    sibling temp file with ``newline="\\n"`` then ``os.replace``.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
