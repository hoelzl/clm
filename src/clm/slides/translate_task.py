"""``clm slides translate task`` / ``accept`` — the agent-toolkit cold start (#961).

Frames the whole-deck bootstrap of a missing split half as ONE JSON task
document and validates the agent's answer — the emit-don't-invoke revision of
what ``clm slides translate`` used to do in-process through OpenRouter (that
path now lives behind ``translate autopilot``). The contract is the shared
kit's (``clm info agent-tasks``): read by default, freshness tokens echoed and
re-checked, a validator label naming the judge, load-bearing exit codes, and
**no model and no API key anywhere on the task/accept path**.

The framing reuses the engine's own classification
(:func:`clm.slides.translate_deck.plan_cells`) and the shared prompt seam
(:func:`clm.slides.sync_translate.build_translation_system_prompt`), so an
agent-driven bootstrap and an ``autopilot`` run apply the exact same rules.
The accept path validates shape + freshness + coverage, then writes through
the ordinary :func:`~clm.slides.translate_bootstrap.bootstrap_deck` — the
post-conditions (twin + companion written atomically, EN-authority shared ids
minted on both halves, pair recorded in the committed sync ledger) are those
of today's bootstrap, byte for byte.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clm.core.utils.path_utils import path_to_prog_lang
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.slides.agent_task import VALIDATORS, AnswerRejected, envelope
from clm.slides.translate_bootstrap import (
    BootstrapPaths,
    TranslateBootstrapError,
    derive_bootstrap_paths,
)
from clm.slides.translate_deck import CellPlan, plan_cells

if TYPE_CHECKING:
    from clm.slides.sync_translate import StaticSlideTranslator

__all__ = [
    "ANSWER_SCHEMA",
    "ANSWER_VALIDATOR",
    "TASK_SCHEMA",
    "TaskUnavailable",
    "TranslateAnswer",
    "build_task",
    "file_fingerprint",
    "prepare_accept",
]

#: The wire schema of the task envelope and the answer document.
TASK_SCHEMA = 1

#: The validator label the task document announces; registered in the shared
#: kit's registry (:data:`clm.slides.agent_task.VALIDATORS`) at import of this
#: module — :func:`parse_answer` is the function this label names.
ANSWER_VALIDATOR = "translate-deck"

_ROLES = ("markdown", "code", "title")

#: The answer contract: per-scope lists of ``(index, body)`` rows plus the
#: echoed direction and freshness tokens. Validated by :func:`parse_answer`
#: (shape) and :func:`prepare_accept` (freshness + coverage) — the engine
#: validates the result through its round-trip guard, never the quality.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema",
        "source_lang",
        "target_lang",
        "source_fingerprint",
        "translations",
    ],
    "properties": {
        "schema": {"const": TASK_SCHEMA},
        "source_lang": {"enum": ["de", "en"]},
        "target_lang": {"enum": ["de", "en"]},
        "source_fingerprint": {
            "type": "string",
            "description": "echoed verbatim from the task document: sha256 of the source half's bytes",
        },
        "companion_fingerprint": {
            "type": ["string", "null"],
            "description": "echoed verbatim: sha256 of the source companion's bytes, null/absent when the source has no companion",
        },
        "translations": {
            "type": "array",
            "description": "one row per framed deck cell of kind translated/header: "
            "the target-language body WITHOUT the '# %%' delimiter line",
            "items": {
                "type": "object",
                "required": ["index", "body"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "body": {"type": "string", "minLength": 1},
                },
            },
        },
        "companion_translations": {
            "type": "array",
            "description": "required iff the task framed companion cells; same row shape",
            "items": {
                "type": "object",
                "required": ["index", "body"],
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "body": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}

_INSTRUCTIONS_TEMPLATE = """\
# Cold-start translation of a split deck

Produce the missing {target_language} half of `{source_name}` ({source_lang} -> {target_lang}).\n
For every cell in `deck.cells` (and `companion.cells` when framed):\n
- kind `translated` — translate `source_body` into {target_language}, applying the
  rules of `prompts.<role>` (`markdown` for prose slides and notes, `code` for
  code cells). Return the body WITHOUT its delimiter line (`# %%` / `// %%`),
  WITH the comment prefixes, preserving Markdown structure, heading levels,
  URLs, and — in code cells — every identifier byte-identically. Do not add,
  drop, or reorder lines.
- kind `header` — the deck title: translate the bare phrase per `prompts.title`
  (no comment prefix, no quotes, keep terminal punctuation). One single line.
- kinds `copied` and `import` — the engine copies them verbatim; answer nothing.\n
{guidance_note}\
Echo `source_fingerprint` (and `companion_fingerprint`) and the direction
verbatim from this document. `accept` re-checks them against the live files:
a stale answer is refused wholesale, never merged. Your answer must cover
EXACTLY the framed translated/header cells — no missing rows, no extra ones.
The engine validates shape and the split/unify round-trip; a malformed twin is
rejected with nothing written.
"""


class TaskUnavailable(Exception):
    """This deck cannot be framed (with the reason as the message)."""


def file_fingerprint(path: Path) -> str:
    """The freshness token for one file: sha256 over its exact bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _lang_name(lang: str) -> str:
    return {"de": "German", "en": "English"}.get(lang, lang)


def _cell_row(plan: CellPlan) -> dict[str, Any]:
    row: dict[str, Any] = {"index": plan.index, "kind": plan.kind}
    if plan.role is not None:
        row["role"] = plan.role
    if plan.slide_id is not None:
        row["slide_id"] = plan.slide_id
    if plan.source_body is not None:
        row["source_body"] = plan.source_body
    return row


def _companion_section(paths: BootstrapPaths) -> dict[str, Any] | None:
    """Frame the source half's voiceover companion, or explain its skip."""
    from clm.core.voiceover_companions import companion_name, resolve_companion

    source_companion = resolve_companion(paths.source_path)
    if source_companion is None:
        return None
    target = source_companion.parent / companion_name(paths.twin_path)
    section: dict[str, Any] = {
        "source": str(source_companion),
        "target": str(target),
        "fingerprint": file_fingerprint(source_companion),
    }
    if target.exists() and target.stat().st_size > 0:
        # Mirrors the bootstrap's D5 skip: an existing companion twin is left
        # untouched (only `autopilot --force` regenerates one).
        section["cells"] = None
        section["note"] = (
            "skipped: the target companion already exists and is left untouched "
            "(remove it, or use `clm slides translate autopilot --force`, to regenerate)"
        )
        return section
    section["cells"] = [
        _cell_row(p)
        for p in plan_cells(
            source_companion.read_text(encoding="utf-8"),
            source_lang=paths.source_lang,
            comment_token=comment_token_for_path(paths.source_path),
        )
    ]
    return section


def build_task(
    source_path: Path,
    *,
    target_lang: str | None = None,
    glossary: Path | None = None,
) -> dict[str, Any]:
    """Frame the whole-deck cold start as one task document (read-only).

    Raises :class:`TaskUnavailable` when the deck cannot be framed — the twin
    already exists (nothing to cold-start; see ``clm slides sync report``) or
    the source is not a single split half
    (:class:`~clm.slides.translate_bootstrap.TranslateBootstrapError`).
    """
    from clm.slides.glossary import resolve_guidance
    from clm.slides.sync_translate import build_translation_system_prompt

    try:
        paths = derive_bootstrap_paths(source_path, target_lang)
    except TranslateBootstrapError as exc:
        raise TaskUnavailable(str(exc)) from exc
    if paths.twin_exists:
        raise TaskUnavailable(
            f"{paths.twin_path.name} already exists — there is nothing to cold-start. "
            "Read the pair's state with `clm slides sync report`, or re-bootstrap over "
            "the twin with `clm slides translate autopilot --force`."
        )

    prog_lang = path_to_prog_lang(paths.source_path)
    guidance, glossary_path = resolve_guidance(
        glossary, paths.source_path.parent, paths.target_lang
    )
    prompts = {
        role: build_translation_system_prompt(
            role=role,
            source_lang=paths.source_lang,
            target_lang=paths.target_lang,
            prog_lang=prog_lang,
            guidance=guidance,
        )
        for role in _ROLES
    }
    deck_cells = [
        _cell_row(p)
        for p in plan_cells(
            paths.source_path.read_text(encoding="utf-8"),
            source_lang=paths.source_lang,
            comment_token=comment_token_for_path(paths.source_path),
        )
    ]
    companion = _companion_section(paths)
    instructions = _INSTRUCTIONS_TEMPLATE.format(
        source_name=paths.source_path.name,
        source_lang=paths.source_lang,
        target_lang=paths.target_lang,
        target_language=_lang_name(paths.target_lang),
        guidance_note=(
            f"Translation conventions (glossary `{glossary_path.name}`) already apply — "
            "they are appended to every role prompt:\n\n```\n"
            f"{guidance.strip()}\n```\n\n"
            if glossary_path is not None and guidance.strip()
            else ""
        ),
    )
    return envelope(
        TASK_SCHEMA,
        tool="translate",
        verb="task",
        body={
            "source": str(paths.source_path),
            "target": str(paths.twin_path),
            "source_lang": paths.source_lang,
            "target_lang": paths.target_lang,
            "prog_lang": prog_lang,
            "glossary": (
                {"path": str(glossary_path), "text": guidance.strip()}
                if glossary_path is not None and guidance.strip()
                else None
            ),
            "deck": {"cells": deck_cells},
            "companion": companion,
            "source_fingerprint": file_fingerprint(paths.source_path),
            "companion_fingerprint": (companion["fingerprint"] if companion is not None else None),
            "prompts": prompts,
            "instructions": instructions,
            "answer_schema": ANSWER_SCHEMA,
            "validator": ANSWER_VALIDATOR,
        },
    )


# ---------------------------------------------------------------------------
# The answer side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslateAnswer:
    """A parsed answer document (shape-validated; freshness pending)."""

    source_lang: str
    target_lang: str
    source_fingerprint: str
    companion_fingerprint: str | None
    translations: dict[int, str]  # deck: index -> body
    companion_translations: dict[int, str]  # companion: index -> body


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DELIMITER_LINE = re.compile(r"^\s*(?:#|//)\s*%%")


def _parse_rows(payload: Any, key: str, seen_indexes: set[int]) -> dict[int, str]:
    rows = payload.get(key, [])
    if not isinstance(rows, list):
        raise AnswerRejected(f"{key} must be a list of {{index, body}} rows")
    out: dict[int, str] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) - {"index", "body"}
            or not {"index", "body"} <= set(row)
        ):
            raise AnswerRejected(
                f"each {key} row must be an object with exactly 'index' and 'body'"
            )
        index, body = row["index"], row["body"]
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise AnswerRejected(f"{key}: index must be a non-negative integer, got {index!r}")
        if not isinstance(body, str) or not body.strip():
            raise AnswerRejected(f"{key}[{index}]: body must be a non-empty string")
        if index in out or index in seen_indexes:
            raise AnswerRejected(f"{key}: duplicate answer for index {index}")
        if _DELIMITER_LINE.match(body):
            raise AnswerRejected(
                f"{key}[{index}]: the body must not contain a cell delimiter line "
                "('# %%' / '// %%') — the body is the cell content without its delimiter"
            )
        out[index] = body
        seen_indexes.add(index)
    return out


def parse_answer(payload: Any) -> TranslateAnswer:
    """The ``translate-deck`` validator: shape-check the answer document.

    Registered in the shared kit's registry; the accept verb resolves this
    function through :data:`clm.slides.agent_task.VALIDATORS`. Freshness and
    coverage are judged against the live files in :func:`prepare_accept`.
    """
    if not isinstance(payload, dict):
        raise AnswerRejected("the answer must be a JSON object")
    if payload.get("schema") != TASK_SCHEMA:
        raise AnswerRejected(f"answer schema must be {TASK_SCHEMA}, got {payload.get('schema')!r}")
    langs = {}
    for key in ("source_lang", "target_lang"):
        value = payload.get(key)
        if value not in ("de", "en"):
            raise AnswerRejected(f"{key} must be 'de' or 'en', got {value!r}")
        langs[key] = value
    if langs["source_lang"] == langs["target_lang"]:
        raise AnswerRejected(f"source_lang and target_lang are both {langs['source_lang']!r}")
    source_fp = payload.get("source_fingerprint")
    if not isinstance(source_fp, str) or not _HEX64.match(source_fp):
        raise AnswerRejected(
            "source_fingerprint must be echoed verbatim from the task document (64 hex chars)"
        )
    companion_fp = payload.get("companion_fingerprint")
    if companion_fp is not None and (
        not isinstance(companion_fp, str) or not _HEX64.match(companion_fp)
    ):
        raise AnswerRejected("companion_fingerprint must be 64 hex chars or null, echoed verbatim")
    if not payload.get("translations") and not payload.get("companion_translations"):
        raise AnswerRejected("the answer translates nothing — no translations rows")
    seen: set[int] = set()
    return TranslateAnswer(
        source_lang=langs["source_lang"],
        target_lang=langs["target_lang"],
        source_fingerprint=source_fp,
        companion_fingerprint=companion_fp,
        translations=_parse_rows(payload, "translations", seen),
        companion_translations=_parse_rows(payload, "companion_translations", seen),
    )


VALIDATORS.register(ANSWER_VALIDATOR, parse_answer)


def _require_coverage(
    answer_rows: dict[int, str],
    plans: list[CellPlan],
    *,
    scope: str,
) -> dict[int, CellPlan]:
    """The answer must cover exactly the framed translatable cells."""
    answerable = {
        p.index: p
        for p in plans
        if p.kind in ("translated", "header") and p.source_body is not None
    }
    missing = sorted(set(answerable) - set(answer_rows))
    extra = sorted(set(answer_rows) - set(answerable))
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing answers for indexes {missing}")
        if extra:
            parts.append(f"answers for indexes {extra} that the task did not frame")
        raise AnswerRejected(
            f"{scope}: the answer must cover exactly the framed cells — "
            + "; ".join(parts)
            + ". Re-run `clm slides translate task` and answer the fresh document."
        )
    for index, body in answer_rows.items():
        if answerable[index].kind == "header" and "\n" in body:
            raise AnswerRejected(
                f"{scope}[{index}]: the title answer must be a single line "
                "(a bare translated phrase, no comment prefix, no quotes)"
            )
    return answerable


def prepare_accept(
    source_path: Path,
    answer: TranslateAnswer,
    *,
    target_lang: str | None = None,
    force: bool = False,
) -> tuple[BootstrapPaths, StaticSlideTranslator]:
    """Validate freshness + coverage against the live files; build the writer.

    Returns the resolved :class:`BootstrapPaths` and a
    :class:`~clm.slides.sync_translate.SlideTranslator` whose mapping is the
    answer — feeding it to
    :func:`~clm.slides.translate_bootstrap.bootstrap_deck` performs the same
    write as today's in-process bootstrap (twin + companion, EN-authority
    ids, ledger record). Raises :class:`AnswerRejected` on the first
    violation; nothing is written.
    """
    from clm.core.voiceover_companions import companion_name, resolve_companion

    try:
        paths = derive_bootstrap_paths(source_path, target_lang)
    except TranslateBootstrapError as exc:
        raise AnswerRejected(str(exc)) from exc
    if paths.twin_exists and not force:
        raise AnswerRejected(
            f"{paths.twin_path.name} now exists — the framed task is stale. Re-frame "
            "after removing it, or pass --force to re-bootstrap over the twin."
        )
    if (answer.source_lang, answer.target_lang) != (paths.source_lang, paths.target_lang):
        raise AnswerRejected(
            f"direction mismatch: the answer translates {answer.source_lang}->"
            f"{answer.target_lang}, but this accept resolves {paths.source_lang}->"
            f"{paths.target_lang} (echo the task's direction verbatim)"
        )
    if file_fingerprint(paths.source_path) != answer.source_fingerprint:
        raise AnswerRejected(
            "the source file changed since the task was framed (source_fingerprint "
            "mismatch) — re-run `clm slides translate task`"
        )

    comment_token = comment_token_for_path(paths.source_path)
    deck_plans = plan_cells(
        paths.source_path.read_text(encoding="utf-8"),
        source_lang=paths.source_lang,
        comment_token=comment_token,
    )
    deck_answerable = _require_coverage(answer.translations, deck_plans, scope="translations")

    companion = resolve_companion(paths.source_path)
    companion_answerable: dict[int, CellPlan] = {}
    framed_companion = False
    if companion is None:
        if answer.companion_fingerprint is not None or answer.companion_translations:
            raise AnswerRejected(
                "companion mismatch: the source has no voiceover companion now, but "
                "the answer carries one — re-run `clm slides translate task`"
            )
    else:
        if answer.companion_fingerprint is None:
            raise AnswerRejected(
                "companion mismatch: the source now has a voiceover companion the "
                "task did not frame — re-run `clm slides translate task`"
            )
        if file_fingerprint(companion) != answer.companion_fingerprint:
            raise AnswerRejected(
                "the voiceover companion changed since the task was framed "
                "(companion_fingerprint mismatch) — re-run `clm slides translate task`"
            )
        companion_target = companion.parent / companion_name(paths.twin_path)
        framed_companion = not (companion_target.exists() and companion_target.stat().st_size > 0)
        if framed_companion:
            companion_plans = plan_cells(
                companion.read_text(encoding="utf-8"),
                source_lang=paths.source_lang,
                comment_token=comment_token,
            )
            companion_answerable = _require_coverage(
                answer.companion_translations,
                companion_plans,
                scope="companion_translations",
            )
        elif answer.companion_translations:
            raise AnswerRejected(
                "companion_translations: the target companion already exists (the "
                "task framed no companion cells) — nothing to translate"
            )

    # One body-keyed mapping drives the engine (a cell's translation is a
    # function of its body — the same semantics the embedded translator has).
    mapping: dict[str, str] = {}
    for scope_rows, answerable in (
        (answer.translations, deck_answerable),
        (answer.companion_translations, companion_answerable),
    ):
        for index, body in scope_rows.items():
            source_body = answerable[index].source_body
            assert source_body is not None  # coverage proved it framed
            normalized = body.strip() if answerable[index].kind == "header" else body.strip("\n")
            if source_body in mapping and mapping[source_body] != normalized:
                raise AnswerRejected(
                    f"two framed cells share the source body {source_body[:40]!r}… but "
                    "the answer gives different translations for them"
                )
            mapping[source_body] = normalized

    from clm.slides.sync_translate import StaticSlideTranslator

    return paths, StaticSlideTranslator(mapping=mapping)
