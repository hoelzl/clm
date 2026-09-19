"""The shared agent-task kit — one contract for every agent toolkit (#959).

``clm slides sync`` and ``clm harvest`` each implemented the agent-toolkit
contract separately: the JSON envelope identity, the freshness-token echo,
the validator registry, the accept-rejection payload, and the 0/1/2 exit
convention. This module is the single home for that machinery so the Tier-1
conversions (translate, polish, coverage, harvest history — umbrella #970)
become plumbing plus a prompt instead of a third dialect.

The contract (see ``clm info agent-tasks``):

* **Read by default, emit-don't-invoke.** A toolkit's read verbs frame
  judgment as a JSON task (instructions + inputs + ``answer_schema`` +
  freshness tokens); the engine never calls a model on the main path.
* **Envelope identity** — :func:`envelope` builds the ordered payload head
  (``schema``, then ``tool``/``engine``/``verb``) so the CLI and the MCP
  mirror cannot drift apart (they once hand-duplicated the harvest task
  envelope).
* **Freshness tokens** — framed into the task, echoed verbatim by the
  answer, re-checked against the live state before any write
  (:func:`freshness_mismatch`). A stale answer is refused wholesale,
  never merged.
* **Validator registry** — a task document names its answer validator
  (e.g. ``harvest-bullets``); :data:`VALIDATORS` maps that label to the
  function the accept path runs, so the label in the wire format names
  the very code that will judge the answer.
* **Exit codes** — :data:`EXIT_CLEAN` (0), :data:`EXIT_WORK_PENDING` (1),
  :data:`EXIT_ERROR` (2), load-bearing for scripting agents.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "EXIT_CLEAN",
    "EXIT_ERROR",
    "EXIT_WORK_PENDING",
    "VALIDATORS",
    "AnswerRejected",
    "ValidatorRegistry",
    "envelope",
    "freshness_mismatch",
    "rejection_payload",
]

#: Nothing to do: the report is clean / the answer was applied in full.
EXIT_CLEAN = 0

#: Work remains: the report frames items / the answer applied but a
#: follow-up (e.g. the ledger record) was withheld.
EXIT_WORK_PENDING = 1

#: Error or rejection: nothing was written.
EXIT_ERROR = 2


class AnswerRejected(Exception):
    """An agent's answer failed validation; the message names the reason.

    Nothing was written. Accept-style verbs catch this (or a subclass)
    and emit :func:`rejection_payload` with exit :data:`EXIT_ERROR`.
    """


def envelope(
    schema: int,
    *,
    tool: str | None = None,
    engine: str | None = None,
    verb: str | None = None,
    body: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a wire payload: the identity head, then ``body``, in order.

    Key order is part of the readable contract (agents brace-locate and
    diff these payloads), so the identity fields always lead: ``schema``,
    then ``tool`` (harvest-style) or ``engine`` (sync-style), then ``verb``.
    """
    payload: dict[str, Any] = {"schema": schema}
    if tool is not None:
        payload["tool"] = tool
    if engine is not None:
        payload["engine"] = engine
    if verb is not None:
        payload["verb"] = verb
    payload.update(body)
    return payload


def rejection_payload(schema: int, *, tool: str, verb: str, reason: str) -> dict[str, Any]:
    """The accept-style rejection envelope (exit :data:`EXIT_ERROR`).

    A rejection must be distinguishable from a crash by a JSON consumer,
    so the refusal path emits the same envelope identity as a success.
    """
    return envelope(
        schema,
        tool=tool,
        verb=verb,
        body={"applied": False, "outcome": "rejected", "reason": reason},
    )


def freshness_mismatch(expected: Mapping[str, Any], echoed: Mapping[str, Any]) -> str | None:
    """Compare live freshness tokens against the answer's echo.

    Returns ``None`` when the echo matches exactly — including the key
    set itself (a member added or removed since the task was framed is
    staleness, not a merge opportunity). Otherwise a short description
    of the divergence, for the tool's own refusal message.
    """
    if expected == echoed:
        return None
    parts: list[str] = []
    missing = sorted(set(expected) - set(echoed))
    extra = sorted(set(echoed) - set(expected))
    changed = sorted(k for k in set(expected) & set(echoed) if expected[k] != echoed[k])
    if missing:
        parts.append(f"missing: {', '.join(missing)}")
    if extra:
        parts.append(f"new: {', '.join(extra)}")
    if changed:
        parts.append(f"changed: {', '.join(changed)}")
    return "; ".join(parts)


#: A validator takes the decoded JSON answer and returns the parsed
#: document, raising :class:`AnswerRejected` (or a subclass) with a
#: precise reason on the first violation.
ValidatorFn = Callable[[Any], Any]


class ValidatorRegistry:
    """Maps the ``validator`` label a task document announces to the
    function the accept path runs — keyed by schema/validator name so a
    toolkit's task verb and accept verb cannot disagree about who judges
    the answer.
    """

    def __init__(self) -> None:
        self._validators: dict[str, ValidatorFn] = {}

    def register(self, name: str, fn: ValidatorFn | None = None) -> Any:
        """Register ``fn`` under ``name``; usable as a decorator."""
        if fn is None:
            return functools.partial(self.register, name)
        if name in self._validators:
            raise ValueError(f"validator {name!r} is already registered")
        self._validators[name] = fn
        return fn

    def get(self, name: str) -> ValidatorFn:
        """The validator for ``name``; :class:`KeyError` naming the known
        validators when the label is unregistered."""
        try:
            return self._validators[name]
        except KeyError:
            known = ", ".join(self.names()) or "none"
            raise KeyError(f"unknown validator {name!r} (registered: {known})") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._validators))


#: The process-wide registry. Toolkits register at module import:
#: ``harvest-bullets`` (:mod:`clm.voiceover.harvest_accept`),
#: ``sync-decisions`` (:mod:`clm.slides.doc_apply`).
VALIDATORS = ValidatorRegistry()
