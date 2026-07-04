"""``clm harvest task`` — frame judgment work for the driving agent (#546 Phase 3).

A task document packages ONE slide's curation (or translation) judgment:
the caller instructions (the old embedded-model merge rules restated for an
agent), the structured inputs (baseline voiceover on both sides, the aligned
transcript with its ``revisited_segments`` backtracking groups, the slide
content for context), the ``answer_schema``, and the freshness tokens
(per-side ``baseline_fingerprint`` of the voiceover member plus the
``video_fingerprint``) that ``accept`` validates before writing. Read-only;
the engine emits, it never invokes a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clm.slides.bilingual_doc import BilingualDeck

__all__ = [
    "ANSWER_SCHEMA",
    "TASK_KINDS",
    "TaskUnavailable",
    "build_tasks",
]

TASK_KINDS = ("curate", "translate")

#: The bullet-list answer contract (proposal §4/§8): per-language ordered
#: bullet strings plus the `dropped` audit list, echoing the freshness
#: tokens the task framed. Validated by `harvest accept` (validator
#: "harvest-bullets").
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["item", "kind", "baseline_fingerprint", "bullets", "dropped"],
    "properties": {
        "item": {"type": "string", "description": "the slide handle, e.g. id:intro"},
        "kind": {"enum": list(TASK_KINDS)},
        "video_fingerprint": {"type": "string"},
        "baseline_fingerprint": {
            "type": "object",
            "description": "echoed verbatim from the task document",
            "properties": {
                "de": {"type": ["string", "null"]},
                "en": {"type": ["string", "null"]},
            },
        },
        "bullets": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": False,
            "properties": {
                "de": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "en": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            },
        },
        "dropped": {
            "type": "array",
            "items": {"type": "string"},
            "description": "transcript passages deliberately discarded (audit trail)",
        },
    },
}


class TaskUnavailable(Exception):
    """This slide cannot be framed (with the reason as the message)."""


def _instructions(kind: str) -> str:
    name = "harvest_curate.md" if kind == "curate" else "harvest_translate.md"
    return (Path(__file__).parent / "prompts" / name).read_text(encoding="utf-8")


def _baseline_fingerprint(item: dict[str, Any]) -> dict[str, str | None]:
    """The per-side freshness token: the single vo member's fingerprint.

    A slide with more than one narrative member per side is ambiguous —
    ``accept`` could not know which cell the answer replaces (P8: never
    guess), so the task is refused with a pointer at manual editing.
    """
    fingerprint: dict[str, str | None] = {}
    for side in ("de", "en"):
        cells = item["voiceover"][side]["cells"]
        if len(cells) > 1:
            keys = ", ".join(c["key"] for c in cells)
            raise TaskUnavailable(
                f"slide {item['key']} has {len(cells)} narrative cells on the "
                f"{side} side ({keys}) — edit the files directly, then re-run report"
            )
        fingerprint[side] = cells[0]["fingerprint"] if cells else None
    return fingerprint


def _slide_content(deck: BilingualDeck, slide_id: str, lang: str) -> str:
    for group in deck.groups:
        if group.anchor_id != slide_id:
            continue
        parts = []
        for member in group.all_members():
            if member.role in ("voiceover", "notes"):
                continue
            cell = member.side(lang)  # type: ignore[arg-type]
            if cell is not None:
                parts.append(cell.body)
        return "\n".join(parts)
    return ""


def _frame_one(
    item: dict[str, Any], report: dict[str, Any], deck: BilingualDeck, kind: str
) -> dict[str, Any]:
    lang = report["video_language"]
    slide_id = item["key"].split(":", 1)[1]
    if kind == "curate":
        if item["class"] in ("covered", "unmatched_slide"):
            raise TaskUnavailable(
                f"slide {item['key']} is '{item['class']}' — the recording "
                "contributed no speech for it; nothing to curate"
            )
        inputs: dict[str, Any] = {
            "language": lang,
            "baseline": {
                side: [c["text"] for c in item["voiceover"][side]["cells"]] for side in ("de", "en")
            },
            "transcript": item.get("transcript"),
            "slide": {"title": item["title"], "content": _slide_content(deck, slide_id, lang)},
        }
    else:  # translate
        twin = "en" if lang == "de" else "de"
        if not item["voiceover"][lang]["present"]:
            raise TaskUnavailable(
                f"slide {item['key']} has no {lang} voiceover to translate — curate it first"
            )
        inputs = {
            "source_language": lang,
            "target_language": twin,
            "source": [c["text"] for c in item["voiceover"][lang]["cells"]],
            "target_baseline": [c["text"] for c in item["voiceover"][twin]["cells"]],
            "slide": {"title": item["title"]},
        }
    return {
        "item": item["key"],
        "kind": kind,
        "class": item["class"],
        "validator": "harvest-bullets",
        "video_language": lang,
        "video_fingerprint": report["video_fingerprint"],
        "baseline_fingerprint": _baseline_fingerprint(item),
        "instructions": _instructions(kind),
        "inputs": inputs,
        "answer_schema": ANSWER_SCHEMA,
    }


def build_tasks(
    report: dict[str, Any],
    deck: BilingualDeck,
    *,
    kind: str = "curate",
    slide: str | None = None,
) -> list[dict[str, Any]]:
    """Frame the judgment tasks for ``slide`` (or every actionable item).

    ``slide`` accepts the bare slide id or the ``id:...`` handle. Raises
    :class:`TaskUnavailable` when the named slide cannot be framed; in the
    all-items sweep, unframeable items are skipped (report shows why).
    """
    if kind not in TASK_KINDS:
        raise TaskUnavailable(f"unknown task kind '{kind}' (choose from {', '.join(TASK_KINDS)})")
    items = report["items"]
    if slide is not None:
        handle = slide if slide.startswith(("id:", "pos:")) else f"id:{slide}"
        matches = [i for i in items if i["key"] == handle]
        if not matches:
            raise TaskUnavailable(f"no slide {handle} in the report")
        return [_frame_one(matches[0], report, deck, kind)]
    tasks = []
    for item in items:
        if item["key"] is None:
            continue  # id-less slides carry their own normalize note
        if kind == "curate" and item["class"] not in (
            "no_existing_vo",
            "transcript_adds_material",
        ):
            continue
        if kind == "translate" and not item["voiceover"][report["video_language"]]["present"]:
            continue
        try:
            tasks.append(_frame_one(item, report, deck, kind))
        except TaskUnavailable:
            continue
    return tasks
