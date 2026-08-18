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

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "location": self.location,
            "key": self.key,
            "uri": self.uri,
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


def _form_body_keys(raw: object) -> list[str]:
    """Parameter names of a form-encoded body, or ``[]``.

    The recorder's fall-through branch strips ``password``/``token``/
    ``api_key`` from ``application/x-www-form-urlencoded`` bodies — the
    OAuth password and client-credentials grants — so the audit has to
    read them too, not just JSON.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return []
    if not isinstance(raw, str) or "=" not in raw:
        return []
    from urllib.parse import parse_qsl

    try:
        return [name for name, _ in parse_qsl(raw, keep_blank_values=True)]
    except ValueError:
        return []


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

        # Dispatch on the request content-type exactly as the recorder
        # does — JSON there, form-encoded everywhere else. Reading the
        # body both ways would flag a JSON payload served as text/plain
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
        if cf.is_json_content_type(request_content_type):
            payload, _ = _json_or_none(raw_body)
            body_names: Iterable[str] = (
                [k for k in payload if isinstance(k, str)] if isinstance(payload, dict) else []
            )
        else:
            body_names = _form_body_keys(raw_body)
        for key in body_names:
            if key.lower() in body_params:
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
