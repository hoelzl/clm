"""``clm slides polish task`` / ``accept`` — the agent-toolkit revision (#962).

Frames the cleanup of a deck's speaker notes as ONE JSON task document and
validates the agent's answer — the emit-don't-invoke revision of what
``clm slides polish`` used to do in-process through one chat completion per
slide (that path now lives behind ``polish autopilot``). The contract is the
shared kit's (``clm info agent-tasks``): read by default, freshness tokens
echoed and re-checked, a validator label naming the judge, load-bearing exit
codes, and **no model and no API key anywhere on the task/accept path**.

The framing reuses the polish engine's own classification
(:func:`clm.core.slide_text.slide_parser.parse_slides` — the same grouping
the embedded model drove) and the level prompts
(``clm/notebooks/polish_levels/*.md``), so an agent-driven polish and an
``autopilot`` run apply the exact same rules. The accept path validates
shape + freshness + coverage, then writes through the ordinary
:func:`~clm.notebooks.slide_writer.update_narrative` splice (tag ``notes``)
landed atomically — byte-identical post-conditions to autopilot.

The sync ledger is deliberately untouched: polishing one side of a recorded
pair reads as an edit off the ledger baseline, so the next
``clm slides sync report`` frames the twin's update (the §6 one-sided-trust
semantics — recording the polished side here would silently bless the stale
twin). A standalone deck (no split twin) simply has no ledger state.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clm.core.slide_text.slide_parser import SlideGroup, parse_slides
from clm.slides.agent_task import VALIDATORS, AnswerRejected, envelope

__all__ = [
    "ANSWER_SCHEMA",
    "ANSWER_VALIDATOR",
    "TASK_SCHEMA",
    "TaskUnavailable",
    "PolishAnswer",
    "build_task",
    "file_fingerprint",
    "framed_groups",
    "group_handle",
    "group_handles",
    "parse_answer",
    "parse_range",
    "prepare_accept",
]

#: The wire schema of the task envelope and the answer document.
TASK_SCHEMA = 1

#: The validator label the task document announces; registered in the shared
#: kit's registry (:data:`clm.slides.agent_task.VALIDATORS`) at import of this
#: module — :func:`parse_answer` is the function this label names.
ANSWER_VALIDATOR = "polish-notes"

_LEVELS = ("verbatim", "light", "standard", "heavy", "rewrite")

#: The answer contract: one ``{handle, body}`` row per framed slide plus the
#: echoed language, level, and freshness tokens. Validated by
#: :func:`parse_answer` (shape) and :func:`prepare_accept` (freshness +
#: coverage) — the engine validates the splice round-trip, never the quality.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "lang", "polish_level", "source_fingerprint", "polished"],
    "properties": {
        "schema": {"const": TASK_SCHEMA},
        "lang": {"enum": ["de", "en"]},
        "polish_level": {"enum": list(_LEVELS)},
        "source_fingerprint": {
            "type": "string",
            "description": "echoed verbatim from the task document: sha256 of the deck's bytes",
        },
        "twin_fingerprint": {
            "type": ["string", "null"],
            "description": "echoed verbatim: sha256 of the split twin's bytes, "
            "null/absent when the task framed no twin",
        },
        "polished": {
            "type": "array",
            "description": "one row per framed slide: the cleaned notes as plain "
            "text, one thought per line",
            "items": {
                "type": "object",
                "required": ["handle", "body"],
                "additionalProperties": False,
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "echoed verbatim: the slide's id:… or pos:… handle",
                    },
                    "body": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}

_INSTRUCTIONS_TEMPLATE = """\
# Polishing speaker notes ({level} level)

Clean up the speaker notes of `{source_name}` (language: {lang}). For every
row in `slides`, apply the level prompt below to `notes`. `slide_context` is
the slide's own-language content and `twin_context` (when present) the other
language's — context only, never part of your output.
{level_section}
Echo `source_fingerprint` (and `twin_fingerprint` when framed) verbatim from
this document. `accept` re-checks them against the live files: a stale
answer is refused wholesale, never merged. Your answer must cover EXACTLY
the framed rows — one `{{"handle", "body"}}` per slide in `polished`, no
missing rows, no extra ones; the body is plain text, one thought per line,
and must not contain a cell delimiter line (`# %%`). The engine validates
shape, freshness, and coverage only — never the quality of the edit.
"""

_VERBATIM_LEVEL_SECTION = """
This task is framed at the `verbatim` level: it performs no edits. Return
each slide's `notes` unchanged as the row's body — no model call is needed.
"""

_LEVEL_SECTION_TEMPLATE = """
## The level prompt ({level})

```
{level_prompt}```
"""


class TaskUnavailable(Exception):
    """The deck's notes cannot be framed (with the reason as the message)."""


def file_fingerprint(path: Path) -> str:
    """The freshness token for one file: sha256 over its exact bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_range(range_str: str | None) -> tuple[int, int] | None:
    """Parse a slide range like ``'5-10'`` (or ``'7'``) into ``(start, end)``."""
    if not range_str:
        return None
    if "-" in range_str:
        start, end = range_str.split("-", 1)
        return int(start), int(end)
    n = int(range_str)
    return n, n


def group_handle(group: SlideGroup) -> str:
    """The stable handle of one slide group: ``id:<slide_id>`` when the
    group's slide-start cell carries an id, else ``pos:<group index>``."""
    slide_id = group.cells[0].slide_id if group.cells else None
    return f"id:{slide_id}" if slide_id else f"pos:{group.index}"


def group_handles(groups: list[SlideGroup]) -> dict[str, SlideGroup]:
    """Resolve handles for a whole deck, collision-safe.

    Duplicate ``slide_id``\\ s (a copy-paste error nothing upstream forbids)
    would otherwise frame two rows with the same handle — a task no answer
    can satisfy. The **first** group keeps the ``id:`` handle; every later
    duplicate falls back to ``pos:<index>``. Deterministic from the parsed
    deck, so ``task`` framing and ``accept`` coverage resolve identically.
    """
    handles: dict[str, SlideGroup] = {}
    taken: set[str] = set()
    for group in groups:
        handle = group_handle(group)
        if handle in taken:
            handle = f"pos:{group.index}"
        taken.add(handle)
        handles[handle] = group
    return handles


def framed_groups(
    path: Path, lang: str, slides_range: tuple[int, int] | None = None
) -> list[SlideGroup]:
    """The slide groups whose notes a ``task`` frames — the engine's own
    classification (groups with non-empty notes, narrowed by the range)."""
    groups = parse_slides(path, lang)
    if slides_range is not None:
        start, end = slides_range
        groups = [g for g in groups if start <= g.index <= end]
    return [g for g in groups if g.has_notes and g.notes_text.strip()]


def _twin_of(lang: str) -> str:
    return "en" if lang == "de" else "de"


def _twin_context(path: Path, lang: str, framed: list[SlideGroup], twin: Path) -> dict[str, str]:
    """Per-handle other-language slide content.

    Matched by ``slide_id`` first; the fallback aligns the two halves'
    **slide sequences** (header groups excluded on both sides) — a j2
    header on only one half would otherwise shift every index by one and
    feed the agent the neighbouring slide's terminology.
    """
    twin_slides = [g for g in parse_slides(twin, _twin_of(lang)) if g.slide_type != "header"]
    by_id = {
        g.cells[0].slide_id: g for g in twin_slides if g.cells and g.cells[0].slide_id is not None
    }
    source_seq = [g for g in parse_slides(path, lang) if g.slide_type != "header"]
    # Keyed by group index (unique among non-header groups): the framed
    # groups come from a different parse of the same text, so object
    # identity would never match.
    seq_pos = {g.index: i for i, g in enumerate(source_seq)}
    handles = group_handles(framed)
    handle_of = {id(g): h for h, g in handles.items()}
    context: dict[str, str] = {}
    for group in framed:
        slide_id = group.cells[0].slide_id if group.cells else None
        twin_group = by_id.get(slide_id) if slide_id is not None else None
        if twin_group is None:
            pos = seq_pos.get(group.index)
            twin_group = twin_slides[pos] if pos is not None and pos < len(twin_slides) else None
        if twin_group is not None and twin_group.text_content.strip():
            context[handle_of[id(group)]] = twin_group.text_content
    return context


def build_task(
    path: Path,
    *,
    lang: str,
    polish_level: str = "standard",
    slides_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Frame the notes cleanup as one task document (read-only).

    Raises :class:`TaskUnavailable` when the deck has no notes to polish
    under the given language and range.
    """
    from clm.core.slide_text.pairing import derive_split_twin
    from clm.notebooks.polish_levels import PolishLevel, load_prompt

    if polish_level not in _LEVELS:
        raise TaskUnavailable(f"unknown polish level {polish_level!r}")
    groups = framed_groups(path, lang, slides_range)
    if not groups:
        raise TaskUnavailable(
            f"no slides with notes found in {path.name} (lang={lang}"
            + (f", range={slides_range[0]}-{slides_range[1]}" if slides_range else "")
            + ") — there is nothing to polish"
        )

    twin = derive_split_twin(path)
    twin_context = _twin_context(path, lang, groups, twin) if twin is not None else {}

    if polish_level == PolishLevel.verbatim:
        level_prompt = None
        level_section = _VERBATIM_LEVEL_SECTION
    else:
        level_prompt = load_prompt(PolishLevel(polish_level))
        level_section = _LEVEL_SECTION_TEMPLATE.format(
            level=polish_level, level_prompt=level_prompt
        )

    instructions = _INSTRUCTIONS_TEMPLATE.format(
        source_name=path.name,
        lang=lang,
        level=polish_level,
        level_section=level_section,
    )
    rows = []
    for handle, group in group_handles(groups).items():
        row: dict[str, Any] = {
            "handle": handle,
            "index": group.index,
            "title": group.title,
            "notes": group.notes_text,
            "slide_context": group.text_content,
        }
        if handle in twin_context:
            row["twin_context"] = twin_context[handle]
        rows.append(row)

    return envelope(
        TASK_SCHEMA,
        tool="polish",
        verb="task",
        body={
            "source": str(path),
            "twin": str(twin) if twin is not None else None,
            "lang": lang,
            "polish_level": polish_level,
            "slides_range": (
                f"{slides_range[0]}-{slides_range[1]}" if slides_range is not None else None
            ),
            "level_prompt": level_prompt,
            "instructions": instructions,
            "slides": rows,
            "source_fingerprint": file_fingerprint(path),
            "twin_fingerprint": file_fingerprint(twin) if twin is not None else None,
            "answer_schema": ANSWER_SCHEMA,
            "validator": ANSWER_VALIDATOR,
        },
    )


# ---------------------------------------------------------------------------
# The answer side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolishAnswer:
    """A parsed answer document (shape-validated; freshness pending)."""

    lang: str
    polish_level: str
    source_fingerprint: str
    twin_fingerprint: str | None
    polished: dict[str, str]  # handle -> body


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DELIMITER_LINE = re.compile(r"^\s*(?:#|//)\s*%%")


def parse_answer(payload: Any) -> PolishAnswer:
    """The ``polish-notes`` validator: shape-check the answer document.

    Registered in the shared kit's registry; the accept verb resolves this
    function through :data:`clm.slides.agent_task.VALIDATORS`. Freshness and
    coverage are judged against the live files in :func:`prepare_accept`.
    """
    if not isinstance(payload, dict):
        raise AnswerRejected("the answer must be a JSON object (see the task's answer_schema)")
    if payload.get("schema") != TASK_SCHEMA:
        raise AnswerRejected(f"answer schema must be {TASK_SCHEMA}, got {payload.get('schema')!r}")
    lang = payload.get("lang")
    if lang not in ("de", "en"):
        raise AnswerRejected(f"lang must be 'de' or 'en', got {lang!r}")
    level = payload.get("polish_level")
    if level not in _LEVELS:
        raise AnswerRejected(f"polish_level must be one of {list(_LEVELS)}, got {level!r}")
    source_fp = payload.get("source_fingerprint")
    if not isinstance(source_fp, str) or not _HEX64.match(source_fp):
        raise AnswerRejected(
            "source_fingerprint must be echoed verbatim from the task document (64 hex chars)"
        )
    twin_fp = payload.get("twin_fingerprint")
    if twin_fp is not None and (not isinstance(twin_fp, str) or not _HEX64.match(twin_fp)):
        raise AnswerRejected("twin_fingerprint must be 64 hex chars or null, echoed verbatim")
    rows = payload.get("polished")
    if not isinstance(rows, list) or not rows:
        raise AnswerRejected("'polished' must be a non-empty list of {handle, body} rows")
    polished: dict[str, str] = {}
    for i, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or set(row) - {"handle", "body"}
            or not {"handle", "body"} <= set(row)
        ):
            raise AnswerRejected(
                f"polished[{i}] must be an object with exactly 'handle' and 'body'"
            )
        handle, body = row["handle"], row["body"]
        if not isinstance(handle, str) or not handle:
            raise AnswerRejected(f"polished[{i}].handle must be a non-empty string")
        if not isinstance(body, str) or not body.strip():
            raise AnswerRejected(f"polished[{i}].body must be a non-empty string")
        if handle in polished:
            raise AnswerRejected(f"polished: duplicate answer for handle {handle}")
        if any(_DELIMITER_LINE.match(line) for line in body.split("\n")):
            raise AnswerRejected(
                f"polished[{i}].body must not contain a cell delimiter line "
                "('# %%' / '// %%') — it would re-split the deck on read-back"
            )
        polished[handle] = body
    return PolishAnswer(
        lang=lang,
        polish_level=level,
        source_fingerprint=source_fp,
        twin_fingerprint=twin_fp,
        polished=polished,
    )


VALIDATORS.register(ANSWER_VALIDATOR, parse_answer)


@dataclass(frozen=True)
class AcceptPlan:
    """The validated write: slide index -> polished body, and the target."""

    index_map: dict[int, str]
    output: Path | None


def prepare_accept(
    path: Path,
    answer: PolishAnswer,
    *,
    lang: str,
    slides_range: tuple[int, int] | None = None,
    output: Path | None = None,
) -> AcceptPlan:
    """Validate freshness + coverage against the live files; build the write.

    Returns the slide-index -> body map for
    :func:`~clm.notebooks.slide_writer.update_narrative` (the same engine
    ``autopilot`` writes through). Raises :class:`AnswerRejected` on the
    first violation; nothing is written.
    """
    from clm.core.slide_text.pairing import derive_split_twin

    if answer.lang != lang:
        raise AnswerRejected(
            f"lang mismatch: the answer polishes {answer.lang!r}, but this accept "
            f"resolves lang={lang!r} (echo the task's language verbatim)"
        )
    if file_fingerprint(path) != answer.source_fingerprint:
        raise AnswerRejected(
            "the deck changed since the task was framed (source_fingerprint "
            "mismatch) — re-run `clm slides polish task`"
        )
    twin = derive_split_twin(path)
    if twin is None:
        if answer.twin_fingerprint is not None:
            raise AnswerRejected(
                "twin mismatch: the deck now has no split twin, but the answer "
                "echoes one — re-run `clm slides polish task`"
            )
    else:
        if answer.twin_fingerprint is None:
            raise AnswerRejected(
                "twin mismatch: the deck now has a split twin the task did not "
                "frame — re-run `clm slides polish task`"
            )
        if file_fingerprint(twin) != answer.twin_fingerprint:
            raise AnswerRejected(
                "the split twin changed since the task was framed "
                "(twin_fingerprint mismatch) — re-run `clm slides polish task`"
            )

    groups = framed_groups(path, lang, slides_range)
    live = group_handles(groups)  # collision-safe: same resolution as framing
    missing = sorted(set(live) - set(answer.polished))
    extra = sorted(set(answer.polished) - set(live))
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing answers for handles {missing}")
        if extra:
            parts.append(f"answers for handles {extra} that the task did not frame")
        raise AnswerRejected(
            "the answer must cover exactly the framed slides — "
            + "; ".join(parts)
            + ". Re-run `clm slides polish task` and answer the fresh document."
        )
    return AcceptPlan(
        index_map={live[handle].index: body for handle, body in answer.polished.items()},
        output=output,
    )
