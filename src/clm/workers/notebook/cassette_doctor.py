"""Offline diagnostics and repair for HTTP-replay cassettes.

This module backs the ``clm cassette doctor`` command (issue #125). It
detects *chain-orphan* interactions in canonical cassettes: chat-completion
responses whose extracted text is substantial enough that a downstream
request would plausibly embed it, yet no other interaction's request body
actually does. Such an interaction is almost always a chain-opener whose
chain-closer was never recorded — the canonical-poisoning failure mode that
PR #123 (issue #115) fixed going *forward* but cannot retroactively repair,
and which the completion-marker logic structurally cannot catch when a cell's
``try/except`` swallowed the closing call.

The detection heuristic is deliberately simple (substring match, no fuzzy or
LLM-based matching — see issue #125 "out of scope"):

1. For each interaction, parse the response body and extract chat-completion
   text content (``choices[].message.content`` for non-streaming JSON;
   accumulated ``delta.content`` for streaming SSE bodies).
2. Treat each extracted content of length ``>= min_text_len`` as a
   *chain-edge candidate*.
3. If no *other* interaction's request body contains that text as a
   substring, flag the interaction as a chain-orphan.

``--fix`` rewrites the cassette without the flagged interactions using the
same atomic-write helper the merge path uses, so the next build re-records
the broken chain. The repair is best-effort by design (issue #125): it only
guarantees the orphan is gone, not that the next recording is correct.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path

from attrs import define, field

logger = logging.getLogger(__name__)

#: Cassettes are named ``*.http-cassette.yaml`` and live alongside the source
#: ``.py`` files in the course tree (see ``scripts/strip_cassette_hosts.py``,
#: which uses the same glob).
CASSETTE_GLOB = "*.http-cassette.yaml"

#: Default minimum extracted-content length for an interaction to be treated
#: as a chain-edge candidate. Shorter responses (e.g. a one-word answer) are
#: too likely to appear incidentally in unrelated request bodies to flag
#: reliably.
DEFAULT_MIN_TEXT_LEN = 50

#: How much of the orphan response text to show in the human-readable report.
_EXCERPT_LEN = 120


@define
class OrphanInteraction:
    """A single chain-orphan interaction flagged in a cassette.

    Attributes:
        index: Zero-based position of the interaction within the cassette.
        uri: Request URI of the interaction.
        method: Request HTTP method.
        request_fingerprint: Short stable fingerprint of the request body,
            for correlating the report back to a specific recorded call.
        text_excerpt: Leading slice of the extracted response content that
            no downstream request embedded.
        text_len: Full length of the extracted response content.
    """

    index: int
    uri: str
    method: str
    request_fingerprint: str
    text_excerpt: str
    text_len: int

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "uri": self.uri,
            "method": self.method,
            "request_fingerprint": self.request_fingerprint,
            "text_excerpt": self.text_excerpt,
            "text_len": self.text_len,
        }


@define
class CassetteReport:
    """Per-cassette diagnostic result.

    Attributes:
        path: Cassette path.
        interaction_count: Total interactions loaded from the cassette.
        orphans: Chain-orphan interactions found.
        fixed: ``True`` when ``--fix`` rewrote the cassette to drop orphans.
        error: Human-readable load/parse error, when the cassette could not
            be inspected (it is then skipped, not counted as clean).
    """

    path: Path
    interaction_count: int = 0
    orphans: list[OrphanInteraction] = field(factory=list)
    fixed: bool = False
    error: str | None = None

    @property
    def has_orphans(self) -> bool:
        return bool(self.orphans)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "interaction_count": self.interaction_count,
            "orphan_count": len(self.orphans),
            "orphans": [o.to_dict() for o in self.orphans],
            "fixed": self.fixed,
            "error": self.error,
        }


def iter_cassette_paths(root: Path) -> Iterator[Path]:
    """Yield every ``*.http-cassette.yaml`` file under ``root`` (recursive).

    Staging (``.staging-*``) and partial (``.partial-*``) sibling files do
    not match the glob (they carry a suffix after ``.yaml``), so only
    canonical cassettes are walked.
    """
    for path in sorted(root.rglob(CASSETTE_GLOB)):
        if path.is_file():
            yield path


def _body_string(response: object) -> str | None:
    """Extract the response body text from a deserialized vcr response dict.

    vcr stores the body under ``response["body"]["string"]`` (str) — see the
    cassette format in ``tests``. ``convert_to_bytes`` may have left it as
    ``bytes``; decode defensively.
    """
    if not isinstance(response, dict):
        return None
    body = response.get("body")
    if not isinstance(body, dict):
        return None
    raw = body.get("string")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _extract_nonstreaming_contents(payload: object) -> list[str]:
    """Extract ``choices[].message.content`` from a parsed JSON response."""
    contents: list[str] = []
    if not isinstance(payload, dict):
        return contents
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return contents
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content:
                contents.append(content)
    return contents


def _extract_streaming_contents(body_text: str) -> list[str]:
    """Accumulate ``delta.content`` across SSE ``data:`` lines per choice.

    Streaming chat-completion bodies are ``text/event-stream`` payloads: one
    ``data: {json}`` line per chunk, each chunk carrying
    ``choices[].delta.content`` fragments, terminated by ``data: [DONE]``.
    Fragments are concatenated per choice index and the per-choice strings
    returned.
    """
    per_choice: dict[int, list[str]] = {}
    saw_delta = False
    for line in body_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(chunk, dict):
            continue
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            idx = choice.get("index", 0)
            if not isinstance(idx, int):
                idx = 0
            delta = choice.get("delta")
            if isinstance(delta, dict):
                fragment = delta.get("content")
                if isinstance(fragment, str) and fragment:
                    per_choice.setdefault(idx, []).append(fragment)
                    saw_delta = True
    if not saw_delta:
        return []
    return ["".join(parts) for parts in per_choice.values() if parts]


def extract_response_contents(response: object) -> list[str]:
    """Extract all chat-completion text contents from a vcr response.

    Handles both non-streaming JSON bodies (``choices[].message.content``)
    and streaming SSE bodies (accumulated ``delta.content``). Returns an
    empty list for non-chat-completion responses (e.g. telemetry, embeddings)
    or bodies that don't parse — those simply never become chain-edge
    candidates.
    """
    body_text = _body_string(response)
    if not body_text:
        return []
    stripped = body_text.lstrip()
    # Non-streaming: a single JSON object.
    if stripped.startswith("{"):
        try:
            payload = json.loads(body_text)
        except (ValueError, TypeError):
            payload = None
        contents = _extract_nonstreaming_contents(payload)
        if contents:
            return contents
    # Streaming SSE (or a body that also carries data: lines).
    if "data:" in body_text:
        return _extract_streaming_contents(body_text)
    return []


def _request_body_text(request: object) -> str:
    """Coerce a vcr request body to text for substring search."""
    body = getattr(request, "body", None)
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, bytearray):
        return bytes(body).decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    read = getattr(body, "read", None)
    if callable(read):
        try:
            data = read()
        except Exception:  # noqa: BLE001 — defensive: never crash diagnostics
            return ""
        seek = getattr(body, "seek", None)
        if callable(seek):
            try:
                seek(0)
            except Exception:  # noqa: BLE001 — best-effort rewind
                pass
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode("utf-8", errors="replace")
        return str(data)
    return str(body)


def _request_fingerprint(request: object) -> str:
    """Short, stable fingerprint of a request body for the report."""
    import hashlib

    body_text = _request_body_text(request)
    if not body_text:
        return "<empty-body>"
    digest = hashlib.sha256(body_text.encode("utf-8", errors="replace")).hexdigest()
    return digest[:12]


def find_orphans(
    requests: Sequence[object],
    responses: Sequence[object],
    *,
    min_text_len: int = DEFAULT_MIN_TEXT_LEN,
) -> list[OrphanInteraction]:
    """Find chain-orphan interactions among parallel request/response lists.

    An interaction is a chain-orphan when it has at least one extracted
    response content of length ``>= min_text_len`` and *none* of that
    content appears as a substring of any *other* interaction's request
    body. The first qualifying content per interaction is reported.
    """
    request_bodies = [_request_body_text(req) for req in requests]
    orphans: list[OrphanInteraction] = []

    for index, (request, response) in enumerate(zip(requests, responses, strict=False)):
        contents = extract_response_contents(response)
        candidates = [c for c in contents if len(c) >= min_text_len]
        if not candidates:
            continue

        orphan_text: str | None = None
        for content in candidates:
            embedded = any(
                content in request_bodies[other]
                for other in range(len(request_bodies))
                if other != index
            )
            if not embedded:
                orphan_text = content
                break

        if orphan_text is None:
            continue

        orphans.append(
            OrphanInteraction(
                index=index,
                uri=str(getattr(request, "uri", "") or ""),
                method=str(getattr(request, "method", "") or ""),
                request_fingerprint=_request_fingerprint(request),
                text_excerpt=orphan_text[:_EXCERPT_LEN],
                text_len=len(orphan_text),
            )
        )

    return orphans


def diagnose_cassette(
    path: Path,
    *,
    min_text_len: int = DEFAULT_MIN_TEXT_LEN,
    fix: bool = False,
) -> CassetteReport:
    """Diagnose (and optionally repair) a single cassette.

    Loads the cassette, finds chain-orphans, and — when
    ``fix`` is set and orphans exist — rewrites the cassette without the
    orphan interactions via the shared atomic-write helper. A cassette that
    fails to load is reported with ``error`` set and skipped (never rewritten).
    """
    from clm.infrastructure.http_replay_mitm.vcr_format import (
        load_cassette,
        serialize_cassette,
    )

    try:
        requests, responses = load_cassette(path)
    except Exception as exc:  # noqa: BLE001 — defensive: one bad file must not abort the walk
        logger.warning(f"Could not load cassette '{path}' ({type(exc).__name__}: {exc}); skipping.")
        return CassetteReport(path=path, error=f"{type(exc).__name__}: {exc}")

    orphans = find_orphans(requests, responses, min_text_len=min_text_len)
    report = CassetteReport(
        path=path,
        interaction_count=len(requests),
        orphans=orphans,
    )

    if fix and orphans:
        from clm.infrastructure.http_replay_mitm.http_replay_cassette import atomic_write_text

        orphan_indexes = {o.index for o in orphans}
        keep_requests = [r for i, r in enumerate(requests) if i not in orphan_indexes]
        keep_responses = [r for i, r in enumerate(responses) if i not in orphan_indexes]
        payload = serialize_cassette({"requests": keep_requests, "responses": keep_responses})
        atomic_write_text(path, payload)
        report.fixed = True
        logger.info(
            f"Repaired cassette '{path}': removed {len(orphans)} chain-orphan "
            f"interaction(s); the next build will re-record."
        )

    return report


def diagnose_cassettes(
    paths: Iterable[Path],
    *,
    min_text_len: int = DEFAULT_MIN_TEXT_LEN,
    fix: bool = False,
) -> list[CassetteReport]:
    """Diagnose (and optionally repair) every cassette in ``paths``."""
    return [diagnose_cassette(path, min_text_len=min_text_len, fix=fix) for path in paths]


# ---------------------------------------------------------------------------
# Secret audit (finding S9, #798)
# ---------------------------------------------------------------------------
# Tightening the record-time filters does nothing for the thousands of
# cassettes already committed across the course repos. Re-recording them
# blindly is not an option — each needs a live service, and most hold
# nothing sensitive — so this reports *which* cassette, interaction and key,
# and never rewrites anything. The record-time filter lists are the single
# source of truth for what counts, so the audit cannot drift from the policy.


@define
class SecretFinding:
    """One secret-shaped value found in a committed cassette.

    Attributes:
        index: Interaction index within the cassette.
        location: Where it sits — ``request header``, ``request query``,
            ``request body``, ``response header``, ``response body``, or
            ``response body (repeated name)`` for a name that appears twice
            in the same object, where ``json.loads`` keeps only the last
            pair and the earlier value is unreadable (issue #875).
        key: The offending key/header/parameter name.
        uri: The interaction's request URI, for orientation.
    """

    index: int
    location: str
    key: str
    uri: str = ""
    #: Set by :func:`apply_baseline` when a baseline blesses this finding.
    #: Accepted findings are still *reported* — they are only excused from
    #: the exit code, so a repo can see what it has accepted.
    accepted: bool = False

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "location": self.location,
            "key": self.key,
            "uri": self.uri,
            "accepted": self.accepted,
        }


@define
class SecretScanReport:
    """Audit result for a single cassette."""

    path: Path
    interaction_count: int = 0
    findings: list[SecretFinding] = field(factory=list)
    error: str | None = None

    @property
    def is_dirty(self) -> bool:
        return bool(self.findings)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "interaction_count": self.interaction_count,
            "findings": [f.to_dict() for f in self.findings],
            "error": self.error,
        }


def _json_or_none(
    raw: object, secret_keys: frozenset[str] = frozenset()
) -> tuple[object, frozenset[str]]:
    """Parse *raw* as JSON; ``(None, empty)`` when it is not JSON at all.

    A cassette body may be bytes or str, and may be anything from an SSE
    stream to HTML — the audit only reads the ones it can parse.

    **Bytes are handed to the parser as bytes**, exactly as the recorder
    does. Decoding them here as strict UTF-8 first used to throw away every
    body carrying a BOM or encoded as UTF-16/32 — ``json.loads`` sniffs
    those itself (RFC 8259 §8.1 / ``json.detect_encoding``) — so the
    recorder redacted a plaintext ``access_token`` while the audit reported
    the file **clean**. A false all-clear is the one outcome a gate must
    never produce, and the audit's whole target population is bodies
    recorded verbatim, BOM included (issue #875 review).

    The second element is the set of repeated names that hid a value from
    the parse tree (see
    :func:`cassette_format.load_json_noting_duplicate_secrets`). The audit
    has to ask for it because the tree alone cannot show it: the earlier of
    two identically-named pairs is dropped by ``json.loads``, so a body
    carrying a plaintext token in the shadowed pair looks clean while the
    recorder would rewrite the file.

    The ``except`` mirrors the recorder's, deliberately. ``RecursionError``
    on a pathologically nested body used to escape and abort the **whole
    repo walk** — one unparseable cassette taking down an audit is worse
    than a finding. (The traversal below can still raise it; that half is
    issue #878.)
    """
    from clm.infrastructure.http_replay_mitm.cassette_format import (
        load_json_noting_duplicate_secrets,
    )

    empty: frozenset[str] = frozenset()
    if isinstance(raw, (bytes, bytearray)):
        if not raw.strip():
            return None, empty
    elif not isinstance(raw, str) or not raw.strip():
        return None, empty
    try:
        return load_json_noting_duplicate_secrets(raw, secret_keys)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None, empty


def _iter_secret_body_keys(
    payload: object,
    keys: frozenset[str],
    placeholder: str,
    is_secret_value: Callable[[object], bool],
) -> Iterator[str]:
    """Yield each secret-shaped key whose value the recorder would redact.

    *is_secret_value* is the recorder's own
    :func:`cassette_format.is_secret_body_value`, threaded in from the
    caller (which already holds the module) rather than reimplemented here:
    a finding the recorder would not act on is a finding nobody can clear,
    and this scan gates a repo audit on its exit code.

    The walk itself is still a second copy of the recorder's, so the guard
    that matters is executable: ``test_scanner_and_recorder_agree`` runs a
    shared payload table through both and requires the same verdict. The
    two agreeing *by inspection* is what allowed issue #875 — the same
    wrong test, written twice, wrong in both places at once.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in keys and is_secret_value(value):
                if value != placeholder:
                    yield key
                continue
            yield from _iter_secret_body_keys(value, keys, placeholder, is_secret_value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_secret_body_keys(item, keys, placeholder, is_secret_value)


def _json_body_param_names(payload: object, names: frozenset[str]) -> list[str]:
    """Request-body parameter names the recorder would strip, at any depth.

    Unlike the response side this is **not** a second walk: it calls the
    recorder's own :func:`vcr_format.filter_json_parameters` and reports
    exactly the keys it acted on. The response side pays for two walks and
    a parity suite to keep them honest; the request side has one
    implementation and cannot drift by construction (issue #877).

    ``RecursionError`` is caught for the same reason it is on the response
    side: the recorder's guard leaves a pathologically nested body's bytes
    untouched, so a finding here would be one no re-record can clear — and
    letting it escape would abort the whole repo walk.

    The two guards do not cover *exactly* the same depth, and the residue is
    worth knowing rather than chasing. The recorder runs parse + walk +
    ``json.dumps`` under one guard while this runs parse + walk, so it gives
    up one nesting level earlier: at a single depth around 1000 the audit
    reports a body the recorder would no-op on. Measured; always in the
    benign direction (an unsatisfiable finding, never a false all-clear), and
    it needs a request body nested a thousand deep to reach.
    """
    from clm.infrastructure.http_replay_mitm.vcr_format import filter_json_parameters

    try:
        _, matched = filter_json_parameters(payload, dict.fromkeys(names))
    except RecursionError:
        return []
    return matched


def _form_body_keys(raw: object) -> list[str]:
    """Parameter names of a form-encoded body, or ``[]``.

    The recorder's fall-through branch strips ``password``/``token``/
    ``api_key`` from ``application/x-www-form-urlencoded`` bodies — the
    OAuth password and client-credentials grants — so the audit has to
    read them too, not just JSON.

    This mirrors :func:`vcr_format._replace_form_parameters`' name
    extraction **exactly**, rather than reaching for ``parse_qsl``, and
    every difference between the two mattered:

    * ``parse_qsl`` **percent-decodes** names; the recorder's
      ``partition(b"=")`` does not. So the audit read ``api%5Fkey=SECRET``
      as ``api_key`` and reported a finding no re-record could ever clear.
      That the recorder misses such a name is a real leak, but it is a
      *recorder* bug (issue #881) — the audit's question is only "would
      the recorder change this file today?".
      (``parse_qsl`` also turns ``+`` into a space, but that one could never
      diverge either way: no name on the filter list contains a space, so
      plus-decoding can neither create a match nor destroy one.)
    * A field with **no ``=``** still counts: the recorder's ``partition``
      yields an empty separator, not ``None``, so a bare ``token`` is
      stripped like any other name. ``parse_qsl`` needed
      ``keep_blank_values`` for that, and skipping ``=``-less bodies
      outright made them a false all-clear.
    * Decoding is **per field name**, not over the whole body. The
      recorder only ever decodes names, so a non-UTF-8 byte in a *value*
      (``password=h\\xfcnter2``) does not stop it — but decoding the whole
      body first made the audit report nothing at all, on a body the
      recorder does rewrite. A false all-clear in the replay-miss class.
    * An undecodable *name* bails the **whole body**, because that is what
      the recorder does: ``_replace_form_parameters`` lets the
      ``UnicodeDecodeError`` escape and
      ``replace_post_data_parameters`` turns it into "leave this body
      alone" — every field, not just the offending one.
    """
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    elif isinstance(raw, str):
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError:  # a lone surrogate: not a form body
            return []
    else:
        return []

    names: list[str] = []
    for chunk in data.split(b"&"):
        try:
            names.append(chunk.partition(b"=")[0].decode("utf-8"))
        except UnicodeDecodeError:
            return []
    return names


def _response_content_type(response: object) -> object | None:
    headers = (response or {}).get("headers") if isinstance(response, dict) else None
    if not isinstance(headers, dict):
        return None
    return next((v for k, v in headers.items() if str(k).lower() == "content-type"), None)


def scan_cassette_secrets(path: Path) -> SecretScanReport:
    """Report secret-shaped values in one committed cassette. Never writes.

    What counts is exactly what the recorder would strip **today**, so
    every finding is one that re-recording the deck actually clears —
    otherwise the audit's exit code would be a gate nobody can satisfy.
    That equivalence is load-bearing and easy to break: the response-body
    scan is content-type gated because the recorder's is, and the request
    body is read as JSON *or* form-encoded because the recorder filters
    both. A value already carrying the recorder's placeholder is clean.

    The flip side, worth knowing: this is not a general secret detector.
    A token in a body the recorder does not touch (an SSE stream, an
    HTML error page, a JSON body served as ``text/plain``) is not
    reported, because re-recording would not remove it either.
    """
    from clm.infrastructure.http_replay_mitm import cassette_format as cf
    from clm.infrastructure.http_replay_mitm.vcr_format import load_cassette

    try:
        requests, responses = load_cassette(path)
    except Exception as exc:  # noqa: BLE001 — one bad file must not abort the walk
        logger.warning(f"Could not load cassette '{path}' ({type(exc).__name__}: {exc}); skipping.")
        return SecretScanReport(path=path, error=f"{type(exc).__name__}: {exc}")

    request_headers = frozenset(h.lower() for h in cf.FILTER_HEADERS)
    query_params = frozenset(p.lower() for p in cf.FILTER_QUERY_PARAMETERS)
    body_params = frozenset(p.lower() for p in cf.FILTER_POST_DATA_PARAMETERS)
    response_headers = frozenset(h.lower() for h in cf.FILTER_RESPONSE_HEADERS)
    response_body_keys = frozenset(k.lower() for k in cf.FILTER_RESPONSE_BODY_KEYS)

    findings: list[SecretFinding] = []
    for index, (request, response) in enumerate(zip(requests, responses, strict=False)):
        uri = str(getattr(request, "uri", "") or "")

        for name in getattr(request, "headers", {}) or {}:
            if str(name).lower() in request_headers:
                findings.append(SecretFinding(index, "request header", str(name), uri))

        for name, _value in getattr(request, "query", []) or []:
            if str(name).lower() in query_params:
                findings.append(SecretFinding(index, "request query", str(name), uri))

        # Dispatch exactly as the recorder does, **including the order**:
        # a mapping body first (whatever the content-type says), then JSON
        # by content-type, then form-encoded for everything else. Reading
        # the body both ways would flag a JSON payload served as text/plain
        # (which the recorder leaves alone), i.e. a finding no re-record
        # can clear.
        raw_body = getattr(request, "body", None)
        request_content_type = next(
            (
                v
                for k, v in (getattr(request, "headers", {}) or {}).items()
                if str(k).lower() == "content-type"
            ),
            None,
        )
        if isinstance(raw_body, dict):
            # ``load_cassette`` yields a mapping for a hand-written
            # cassette whose ``body:`` is a YAML mapping rather than a
            # string. Nothing CLM writes produces one, but the recorder's
            # ``isinstance(request.body, dict)`` branch fires on it before
            # any content-type check — so an audit that fell through to the
            # form reader here reported such a file clean while the recorder
            # would strip it. A false all-clear, however contrived the file.
            body_names: Iterable[str] = _json_body_param_names(raw_body, body_params)
        elif cf.is_json_content_type(request_content_type):
            payload, _ = _json_or_none(raw_body)
            # At any depth, like the recorder: a nested
            # ``{"data": {"api_key": …}}`` was recorded verbatim while the
            # audit — reading top-level keys only — vouched for the file
            # (issue #877). Scanner and recorder were consistently
            # top-level, so the parity suite passed on it: a shared blind
            # spot, which is the one shape a parity test cannot catch.
            body_names = _json_body_param_names(payload, body_params)
        else:
            body_names = [k for k in _form_body_keys(raw_body) if k.lower() in body_params]
        for key in body_names:
            findings.append(SecretFinding(index, "request body", key, uri))

        headers = (response or {}).get("headers") or {}
        for name in headers:
            if str(name).lower() in response_headers:
                findings.append(SecretFinding(index, "response header", str(name), uri))

        body = (response or {}).get("body") or {}
        # Content-type gated, like the recorder: a JSON payload served as
        # text/plain is not something re-recording would redact, so
        # flagging it would be an unfixable finding.
        if cf.is_json_content_type(_response_content_type(response)):
            response_payload, shadowed_names = _json_or_none(
                body.get("string") if isinstance(body, dict) else None, response_body_keys
            )
            try:
                body_keys_found = list(
                    _iter_secret_body_keys(
                        response_payload,
                        response_body_keys,
                        cf.SECRET_PLACEHOLDER,
                        cf.is_secret_body_value,
                    )
                )
            except RecursionError:
                # A body deep enough to overflow the walk. Where the
                # *parse* overflows depends on the interpreter build —
                # CPython on Windows raises inside ``json.loads`` at a
                # depth Linux parses happily — so this and the guard in
                # ``_json_or_none`` are two halves of one case, and both
                # must hold or the behaviour is platform-dependent.
                #
                # Yield nothing rather than a finding: the recorder's twin
                # guard leaves such a body's bytes untouched, so
                # re-recording clears nothing and a finding here would be
                # unsatisfiable. Crucially it must not *escape* — this is
                # a repo-wide walk, and one pathological cassette used to
                # take down the audit of every file after it (issue #878
                # covers the remaining recorder half).
                body_keys_found = []
            for key in body_keys_found:
                findings.append(SecretFinding(index, "response body", key, uri))
            for name in sorted(shadowed_names):
                # A repeated name whose earlier pair the parse tree threw
                # away. Invisible to the walk above, and the recorder
                # rewrites the file for it, so the audit must say so or it
                # vouches for a body holding a plaintext token. The
                # *location* carries the reason so ``key`` stays what it
                # says it is — a real name from the body, which is what a
                # consumer grouping findings by key expects.
                findings.append(SecretFinding(index, "response body (repeated name)", name, uri))

    return SecretScanReport(path=path, interaction_count=len(requests), findings=findings)


def scan_cassettes_for_secrets(paths: Iterable[Path]) -> list[SecretScanReport]:
    """Audit every cassette in ``paths`` (read-only)."""
    return [scan_cassette_secrets(path) for path in paths]


# ---------------------------------------------------------------------------
# Accepted-findings baseline (issue #883)
# ---------------------------------------------------------------------------
# The audit exits non-zero on *any* finding, which is right for "should this
# deck be re-recorded?" and useless as a repo gate: PythonCourses holds 294
# findings that are all non-credential response cookies and none worth
# re-recording live teaching material to clear (#874). A check that can never
# go green gets switched off — the same failure this whole arc is about, one
# level up. A baseline blesses what is there today so that anything *new*
# fails.

#: Schema version of the baseline document. Bump on any change to the entry
#: shape, and make :func:`load_baseline` reject the versions it cannot read
#: rather than guess at them.
BASELINE_VERSION = 1


class BaselineError(ValueError):
    """A baseline file is missing, unreadable, or not in a shape we trust.

    Raised rather than degraded, always. Treating a broken baseline as
    *empty* would fail the gate on everything (unsatisfiable); treating it as
    *match-all* would vouch for the repo (a false all-clear). Both are worse
    than stopping.
    """


@define(frozen=True)
class BaselineEntry:
    """One accepted finding: a file, a location and a key name.

    The two things deliberately **not** in the key are what make this
    workable:

    * **No interaction index.** Re-recording a deck shifts every index, so an
      index-keyed baseline would report all of that deck's accepted findings
      as new the first time somebody re-records — the gate would punish the
      fix it exists to ask for.
    * **No value.** A :class:`SecretFinding` never carries one (the report
      must not print secrets), and the values this baseline mostly covers —
      Cloudflare's ``__cf_bm`` — rotate on every recording, so a value-keyed
      baseline would churn on every re-record.

    The cost, which the docs must state rather than imply away: the key is
    **name-level**. Accepting ``deck.yaml / response header / set-cookie``
    accepts *any* ``set-cookie`` in that file, including one that is a real
    session credential. The audit cannot tell those apart in any case — it
    only ever sees the header name — so what a baseline buys is narrowing
    "any cookie anywhere" to "a cookie in this file", not a value-level
    guarantee.
    """

    path: str
    location: str
    key: str

    def to_dict(self) -> dict:
        return {"path": self.path, "location": self.location, "key": self.key}


@define
class BaselineOutcome:
    """How a scan's findings landed against a baseline.

    Attributes:
        accepted: Findings a baseline entry blessed. Reported, not fatal.
        new: Everything else. These decide the exit code.
        stale: Baseline entries nothing matched this run — usually a deck
            that was re-recorded, i.e. somebody doing the right thing.
            Reported so the file can be regenerated, never fatal.
        unreadable: Cassettes that could not be parsed. Not baselineable and
            still fatal: a file the audit cannot read is not one it can
            vouch for.
    """

    accepted: list[SecretFinding] = field(factory=list)
    new: list[SecretFinding] = field(factory=list)
    stale: list[BaselineEntry] = field(factory=list)
    unreadable: int = 0
    #: Entry count of the baseline this outcome was produced against, so a
    #: caller can tell "nothing matched because the baseline is empty" from
    #: "nothing matched because we scanned the wrong tree".
    entry_count: int = 0

    @property
    def describes_nothing(self) -> bool:
        """True when a non-empty baseline matched **no** finding at all.

        The gate's own evidence that it is pointed at the wrong tree. A CI
        job with the wrong working directory, a checkout where the course
        content did not materialise, or a renamed content root all produce
        this — and without the check they produce a **green** run over a repo
        nothing looked at, which is the failure this feature exists to
        prevent, arrived at from the other side.

        A repo that legitimately re-recorded *every* baselined deck lands
        here too. That is fine: the answer in both cases is the same, and it
        is to regenerate the file rather than to trust it.
        """
        return bool(self.entry_count) and not self.accepted


def _to_posix(text: str) -> str:
    """Normalise a stored/parsed baseline path to forward slashes.

    Applied on **both** sides. Writing needs it because CLM is developed on
    Windows and its CI runs on Linux, so a native separator would match
    locally and miss in the one place the gate is meant to run; reading needs
    it to accept a hand-edited or older file. Doing it on one side only makes
    the round trip asymmetric — a POSIX file legitimately named ``a\\b.yaml``
    would be written verbatim and read back as ``a/b.yaml``, so
    ``--write-baseline`` followed by ``--baseline`` on an unchanged tree
    would not be green.

    The cost of normalising both sides is that ``a\\b.yaml`` and ``a/b.yaml``
    — two distinct files on POSIX, both pathological — collapse to one entry.
    Consistently, on both sides, which is what keeps the round trip
    satisfiable.
    """
    return text.replace("\\", "/")


def _relative_posix(path: Path, root: Path) -> str:
    """*path* relative to *root*, with forward slashes.

    Raises :class:`BaselineError` when the two share no common root — on
    Windows, different drives. Unreachable through the CLI (paths come from
    ``root.rglob``), and it raises rather than falling back to the file's
    *name* because that fallback would collapse every same-named cassette in
    the tree into one entry: in a course repo, 95 different
    ``deck.http-cassette.yaml`` files accepting each other's findings. Dead
    defensive code should not choose the unsafe degradation.
    """
    try:
        relative = Path(os.path.relpath(path, root))
    except ValueError as exc:
        raise BaselineError(f"cassette '{path}' is not under the scan root '{root}'") from exc
    return _to_posix(relative.as_posix())


def _entry_for(report_path: str, finding: SecretFinding) -> BaselineEntry:
    # The key is lowercased because the audit matches names
    # case-insensitively and PythonCourses holds both ``set-cookie`` and
    # ``Set-Cookie``. A case-sensitive baseline would read a casing flip as a
    # brand-new secret.
    return BaselineEntry(report_path, finding.location, finding.key.lower())


def build_baseline(reports: Iterable[SecretScanReport], root: Path) -> dict:
    """The baseline document blessing every finding in *reports*.

    Entries are deduplicated (the key has no index, so one cookie on three
    interactions is one thing to accept) and sorted, so committing the file
    and regenerating it later produces a readable diff instead of churn.

    An unreadable cassette contributes nothing — you cannot bless what you
    cannot read — which is why the CLI refuses to exit zero when it writes a
    baseline for a tree containing one.
    """
    entries = {
        _entry_for(_relative_posix(report.path, root), finding)
        for report in reports
        for finding in report.findings
    }
    return {
        "version": BASELINE_VERSION,
        # A hint, not an identity: entries are keyed on a path *relative* to
        # the scan root, so applying the file at a different root silently
        # re-interprets every one of them. Recording the root's name lets a
        # mismatch be reported instead of guessed at. Only the basename —
        # an absolute path would differ between a Windows dev box and Linux
        # CI, which is exactly where this needs to work.
        "root_name": Path(root).name,
        "entries": [
            entry.to_dict() for entry in sorted(entries, key=lambda e: (e.path, e.location, e.key))
        ],
    }


def baseline_entries_from_document(document: object) -> frozenset[BaselineEntry]:
    """Validate a parsed baseline document into a set of entries.

    Split from :func:`load_baseline` so an in-memory document (a freshly
    built one, a test fixture) goes through exactly the same validation as
    one read from disk.
    """
    if not isinstance(document, dict):
        raise BaselineError("baseline must be a JSON object")
    version = document.get("version")
    # ``isinstance(True, int)`` is True and ``1.0 == 1``, so a bare equality
    # check accepts ``{"version": true}`` and ``{"version": 1.0}``.
    if isinstance(version, bool) or not isinstance(version, int) or version != BASELINE_VERSION:
        raise BaselineError(
            f"unsupported baseline version {version!r} (this clm writes {BASELINE_VERSION}); "
            "regenerate it with `clm cassette scan --write-baseline`"
        )
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise BaselineError("baseline 'entries' must be a list")

    entries = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            raise BaselineError(f"baseline entry must be an object, got {type(item).__name__}")
        try:
            path, location, key = item["path"], item["location"], item["key"]
        except KeyError as exc:
            raise BaselineError(f"baseline entry is missing {exc.args[0]!r}") from exc
        if not all(isinstance(v, str) for v in (path, location, key)):
            raise BaselineError("baseline entry fields must be strings")
        # Normalised the same way the writer normalises, so a hand-edited or
        # Windows-written file matches and the round trip stays symmetric.
        entries.add(BaselineEntry(_to_posix(path), location, key.lower()))
    return frozenset(entries)


def baseline_root_name(document: object) -> str | None:
    """The root name a baseline document was built against, if it records one."""
    if isinstance(document, dict):
        name = document.get("root_name")
        if isinstance(name, str):
            return name
    return None


def load_baseline_document(path: Path) -> object:
    """Read and JSON-parse a baseline file. Raises :class:`BaselineError`.

    Bytes go straight to ``json.loads``, which sniffs BOMs and UTF-16/32
    itself (RFC 8259 §8.1) — the lesson :func:`_json_or_none` learned in
    #875, where a strict UTF-8 pre-decode threw away every body with a BOM.
    A baseline written by PowerShell's ``Out-File`` is UTF-16 by default, so
    this is the ordinary case on the platform CLM is developed on.

    The guard covers ``RecursionError`` for the same reason the recorder's
    does: a pathologically nested document must produce a legible error, not
    a traceback out of a CI gate.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise BaselineError(f"could not read baseline '{path}': {exc}") from exc
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise BaselineError(f"baseline '{path}' is not valid JSON: {exc}") from exc


def load_baseline(path: Path) -> frozenset[BaselineEntry]:
    """Read and validate a baseline file. Raises :class:`BaselineError`."""
    return baseline_entries_from_document(load_baseline_document(path))


def apply_baseline(
    reports: Iterable[SecretScanReport], root: Path, entries: frozenset[BaselineEntry]
) -> BaselineOutcome:
    """Split *reports*' findings into accepted and new against *entries*.

    Mutates each :class:`SecretFinding`'s ``accepted`` flag so the rendered
    and JSON reports can show both, and returns the split plus the baseline
    entries nothing matched.
    """
    outcome = BaselineOutcome(entry_count=len(entries))
    matched: set[BaselineEntry] = set()

    for report in reports:
        if report.error is not None:
            outcome.unreadable += 1
            continue
        relative = _relative_posix(report.path, root)
        for finding in report.findings:
            entry = _entry_for(relative, finding)
            if entry in entries:
                matched.add(entry)
                finding.accepted = True
                outcome.accepted.append(finding)
            else:
                finding.accepted = False
                outcome.new.append(finding)

    outcome.stale = sorted(entries - matched, key=lambda e: (e.path, e.location, e.key))
    return outcome
