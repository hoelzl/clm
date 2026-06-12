"""vcrpy-YAML cassette bridge for the mitmproxy transport (issue #165, P1).

The mitmproxy addon records and replays at the network layer, but the
*on-disk* format must stay byte-compatible with the vcrpy v1 YAML
cassette schema so that committed course cassettes, ``clm cassette
doctor``, ``strip_cassette_hosts.py`` and
:func:`clm.workers.notebook.http_replay_cassette.merge_staging_into_canonical`
keep working unchanged.

The format itself (Request model, YAML (de)serialization, secret filters,
matchers) lives in :mod:`vcr_format` — CLM-owned code vendored from vcrpy
(issue #355 stage 2); the vcrpy package is no longer used. This module
adds the mitmproxy-flow conversion, the CLM filter/matcher policy, and
the cassette read/write helpers on top.

Both modules are **pure** (PyYAML + stdlib, no ``clm`` package imports),
so they can be imported two ways:

* as ``clm.infrastructure.http_replay_mitm.*`` inside CLM's own
  virtualenv (unit tests, host code), and
* by bare path import inside the isolated ``mitmdump`` interpreter
  (``uv tool install mitmproxy --with pyyaml``), where the ``clm``
  package is not installed.

The conversion functions deliberately mirror vcrpy's own
``vcr.stubs.httpcore_stubs._make_vcr_request`` / ``_serialize_response``
and ``vcr.filters.decode_response`` so a mitmproxy-recorded interaction
serializes to bytes identical to what vcrpy would have written for the
same HTTP exchange.
"""

from __future__ import annotations

import copy
import functools
import json
import os
import sys
import uuid
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

# The format core: CLM-owned, vendored from vcrpy (issue #355 stage 2).
# Same dual-import dance the addon does for this module: package import in
# the CLM venv, bare path import inside the mitmdump interpreter (the addon
# already put this directory on sys.path in that case, but be self-reliant).
try:  # CLM venv
    from clm.infrastructure.http_replay_mitm import vcr_format as vf
except ImportError:  # mitmdump interpreter — import the sibling by path
    sys.path.insert(0, str(Path(__file__).parent))
    import vcr_format as vf  # type: ignore[import-not-found, no-redef]

# Re-exported names: consumers (the addon, merge, doctor, strip script,
# tests) treat cassette_format as the format's facade.
Request = vf.Request
decode_response = vf.decode_response

# ---------------------------------------------------------------------------
# Secret/telemetry filtering + matching parity (issue #165, P3)
# ---------------------------------------------------------------------------
# The single source of truth for cassette secret-filtering (the in-kernel
# vcrpy bootstrap these once mirrored was removed in #355). Committed course
# cassettes were recorded with exactly these filters, so narrowing or
# reordering them silently changes what gets recorded/matched — a pin test
# (``test_http_replay_mitm_cassette_format.py``) locks the literals.
#
# ``filter_headers`` removes secret-bearing *request* headers (vcrpy filters
# request headers only; response ``set-cookie`` is left as vcrpy leaves it).
# ``filter_query_parameters`` / ``filter_post_data_parameters`` strip secrets
# from the URL query and JSON/form request body respectively.
FILTER_HEADERS = ["authorization", "cookie", "x-api-key", "set-cookie"]
FILTER_POST_DATA_PARAMETERS = ["password", "token", "api_key"]
FILTER_QUERY_PARAMETERS = ["api_key", "token"]

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


def response_fingerprint(response: dict) -> bytes:
    """Stable fingerprint of a recorded response body (sequence-aware dedup).

    A non-deterministic endpoint (an LLM at temperature > 0, or OpenRouter
    routing the same request to different providers) returns a *different*
    response to the **same** request on each call. Sequence-aware recording
    keeps those distinct responses as separate ordered interactions — so a
    downstream request that embedded the second response still replay-matches —
    while a byte-identical re-recording of the same ``(request, response)`` pair
    still collapses. Pairing ``fingerprint(request)`` with this response
    fingerprint is the dedup key for that: same request + same response → one
    entry; same request + different response → two ordered entries.
    """
    body = (response or {}).get("body") or {}
    raw = body.get("string", b"")
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray):
        return bytes(raw)
    return str(raw).encode("utf-8", errors="replace")


def build_request_filter(
    *,
    filter_headers: Iterable[object] = FILTER_HEADERS,
    filter_query_parameters: Iterable[object] = FILTER_QUERY_PARAMETERS,
    filter_post_data_parameters: Iterable[object] = FILTER_POST_DATA_PARAMETERS,
    ignore_hosts: Iterable[str] = (),
):
    """Return ``before_record_request(Request) -> Request | None``.

    Reconstructs vcrpy's own ``VCR._build_before_record_request`` closure from
    the *public* ``vcr.filters`` functions so a request is filtered exactly as
    the in-kernel vcrpy path filters it:

    * each filter-list entry maps to a ``(name, None)`` removal,
    * filters run header → query → post-data (the order vcrpy uses), each
      after a defensive ``copy.deepcopy`` so the caller's request is untouched,
    * an ignore-host check then returns ``None`` to signal "do not record" —
      the addon treats that as "pass straight through to the network", the
      out-of-process analogue of vcrpy's ``ignore_hosts``.

    Because the recorded request is the *filtered* one and the replay lookup
    filters the incoming request the same way before matching, secret removal
    never breaks matching (both sides drop the same headers/params) and the
    cassette never carries ``authorization``/``cookie``/``x-api-key`` request
    headers, ``api_key``/``token`` query params, or ``password``/``token``/
    ``api_key`` body params.
    """
    funcs = []
    if filter_headers:
        replacements = [h if isinstance(h, tuple) else (h, None) for h in filter_headers]
        funcs.append(functools.partial(vf.replace_headers, replacements=replacements))
    if filter_query_parameters:
        replacements = [p if isinstance(p, tuple) else (p, None) for p in filter_query_parameters]
        funcs.append(functools.partial(vf.replace_query_parameters, replacements=replacements))
    if filter_post_data_parameters:
        replacements = [
            p if isinstance(p, tuple) else (p, None) for p in filter_post_data_parameters
        ]
        funcs.append(functools.partial(vf.replace_post_data_parameters, replacements=replacements))
    hosts_to_ignore = set(ignore_hosts)

    def before_record_request(request: Request) -> Request | None:
        request = copy.deepcopy(request)
        for fn in funcs:
            if request is None:
                break
            request = fn(request)
        if request is not None and getattr(request, "host", None) in hosts_to_ignore:
            return None
        return request

    return before_record_request


def clm_json_body_matcher(r1: Request, r2: Request) -> None:
    """JSON-semantic request-body matcher (vcr matcher protocol).

    When both requests carry a JSON content-type (case-insensitive), parse
    both bodies as JSON and compare the parsed values; otherwise
    byte-compare. Raises ``AssertionError`` on mismatch (the convention
    vcr's matcher chain expects).

    This is why a real LLM JSON ``POST`` replay-hits instead of missing: a
    byte-exact body key would diverge whenever vcrpy's
    ``filter_post_data_parameters`` re-dumped the JSON with default separators
    at record time but the live body uses compact separators — committed
    cassettes contain such re-dumped bodies, so JSON-semantic matching must
    stay (a pin test asserts the matcher chain).
    """

    def _body_bytes(req: Request) -> bytes:
        body = req.body
        if body is None:
            return b""
        if isinstance(body, (bytes, bytearray)):
            return bytes(body)
        if isinstance(body, str):
            return body.encode("utf-8", errors="replace")
        read = getattr(body, "read", None)
        if callable(read):
            data = read()
            seek = getattr(body, "seek", None)
            if callable(seek):
                try:
                    seek(0)
                except Exception:  # noqa: BLE001 — best-effort rewind
                    pass
            return data if isinstance(data, bytes) else str(data).encode("utf-8", errors="replace")
        return str(body).encode("utf-8", errors="replace")

    def _is_json(req: Request) -> bool:
        headers = getattr(req, "headers", {}) or {}
        for k, v in headers.items():
            if str(k).lower() == "content-type":
                val = v[0] if isinstance(v, (list, tuple)) and v else v
                return "application/json" in str(val).lower()
        return False

    b1 = _body_bytes(r1)
    b2 = _body_bytes(r2)
    if _is_json(r1) and _is_json(r2):
        try:
            p1 = json.loads(b1) if b1 else None
            p2 = json.loads(b2) if b2 else None
            if p1 != p2:
                raise AssertionError
            return
        except (ValueError, TypeError):
            pass  # fall through to byte comparison
    if b1 != b2:
        raise AssertionError


# The replay match key:
# ``("method","scheme","host","port","path","query","clm_json_body")`` —
# the request *body* (JSON-semantic) is part of the key so a stale
# cassette fails loudly rather than serving the wrong recorded interaction.
REPLAY_MATCHERS = (
    vf.method,
    vf.scheme,
    vf.host,
    vf.port,
    vf.path,
    vf.query,
    clm_json_body_matcher,
)


def requests_match(incoming: Request, recorded: Request) -> bool:
    """Return ``True`` iff ``incoming`` matches ``recorded`` under CLM's
    match_on. The matcher set keeps vcrpy's replay equivalence classes
    (e.g. ``query`` is order-insensitive, JSON bodies compare by value) so
    committed cassettes keep matching exactly as they always did."""
    return bool(vf.requests_match(incoming, recorded, list(REPLAY_MATCHERS)))


def serialize_interactions(interactions: Iterable[Interaction]) -> str:
    """Serialize interactions to vcrpy v1 YAML.

    Routes through :func:`vcr_format.serialize_cassette` so the output is
    byte-identical to a vcrpy-written cassette. The serializer mutates
    ``response["body"]["string"]`` from ``bytes`` to ``str`` **in place**
    (the inherited vcrpy quirk); we deep-copy first so the caller's
    in-memory interactions keep their ``bytes`` bodies and stay valid for
    subsequent replays/writes.
    """
    requests = []
    responses = []
    for request, response in interactions:
        requests.append(request)
        responses.append(response)
    cassette_dict = copy.deepcopy({"requests": requests, "responses": responses})
    payload: str = vf.serialize_cassette(cassette_dict)
    return payload


def load_interactions(path: Path) -> list[Interaction]:
    """Load a vcrpy YAML cassette into ``(Request, response-dict)`` pairs.

    Returns an empty list when the cassette does not exist.
    """
    path = Path(path)
    if not path.exists():
        return []
    requests, responses = vf.load_cassette(path)
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
