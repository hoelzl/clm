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
from collections.abc import Iterable, Iterator, Sequence
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
        from clm.workers.notebook.http_replay_cassette import _atomic_write_text

        orphan_indexes = {o.index for o in orphans}
        keep_requests = [r for i, r in enumerate(requests) if i not in orphan_indexes]
        keep_responses = [r for i, r in enumerate(responses) if i not in orphan_indexes]
        payload = serialize_cassette({"requests": keep_requests, "responses": keep_responses})
        _atomic_write_text(path, payload)
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
