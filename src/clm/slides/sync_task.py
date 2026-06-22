"""Framed model tasks for the agent-driven sync toolkit (epic #440, decision B).

``clm slides sync task DECK --item ID`` emits a :class:`SyncTask` — everything a
model needs to reconcile *one* tier-2/3 item and nothing more — without the engine
calling a model. It is a thin, **model-free** wrapper over the prompt builders that
the embedded clients also use, so the agent path and the agent-less ``autopilot``
path frame the work identically:

* **edit** (a drifted id'd localized cell) → the sync-judge prompt
  (:data:`~clm.infrastructure.llm.sync_prompts.SYNC_SYSTEM_PROMPT` +
  :func:`~clm.infrastructure.llm.sync_prompts.build_sync_user_prompt`); the answer
  is a ``{verdict, proposed_text, reason}`` object.
* **add** (a brand-new slide) → the translation prompt
  (:func:`~clm.slides.sync_translate.build_translation_system_prompt` +
  :func:`~clm.slides.sync_translate.build_translation_user_prompt`); the answer is
  the translated cell body.
* **realign** (a drifted ``slide_id`` the deterministic id-migration cannot resolve)
  → the body-free alignment-recovery prompt
  (:func:`~clm.slides.sync_recover.build_recovery_user_prompt`); the answer is an
  ``{index → assignment}`` map. The region-level task, validated by
  :func:`~clm.slides.sync_recover.validate_alignment`.

Each task names the deterministic ``validator`` that ``clm slides sync accept`` will
run on the answer (so ``accept`` can never apply the wrong check) and carries the
``answer_schema`` the answer must match. The module imports **no** OpenRouter client
and constructs none — building a task never reaches a model (decision B is
structural here).

Cold-start correspondence (``mint`` / ``adopt`` / ``reconcile``) and the
hand-judged ambiguities (``conflict`` / ``issue``) are not framed as model tasks
yet — :func:`build_task` raises :class:`TaskUnavailable` with the right next step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from clm.infrastructure.llm.sync_prompts import (
    SYNC_RESPONSE_SCHEMA,
    SYNC_SYSTEM_PROMPT,
    build_sync_user_prompt,
)
from clm.slides.sync_recover import (
    RECOVERY_SYSTEM_PROMPT,
    build_recovery_user_prompt,
)
from clm.slides.sync_report import ReconciliationItem, build_report
from clm.slides.sync_translate import (
    build_translation_system_prompt,
    build_translation_user_prompt,
)

if TYPE_CHECKING:
    from clm.slides.sync_plan import SyncPlan
    from clm.slides.sync_recover import RegionCell

__all__ = [
    "ALIGNMENT_ANSWER_SCHEMA",
    "EDIT_ANSWER_SCHEMA",
    "TRANSLATION_ANSWER_SCHEMA",
    "SyncTask",
    "TaskUnavailable",
    "build_task",
    "build_tasks",
]

#: The proposal kinds :func:`build_task` can frame as a single model task.
_FRAMEABLE_KINDS = frozenset({"edit", "add", "realign"})

# Answer schemas (the shape `accept` enforces). The edit answer reuses the judge's
# own structured-output schema verbatim, so a task answer and an embedded-judge
# reply are interchangeable.
EDIT_ANSWER_SCHEMA: dict[str, Any] = SYNC_RESPONSE_SCHEMA
TRANSLATION_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "translated_body": {
            "type": "string",
            "description": (
                "the translated target-language cell body, in Jupyter percent-format "
                "(prose cells keep their '# ' line prefixes; code cells stay runnable), "
                "no surrounding code fences"
            ),
        }
    },
    "required": ["translated_body"],
}
ALIGNMENT_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "map EVERY current-region index (a string) to exactly one of: a base "
        "slide_id (the current cell is that base cell's continuation), 'new' (genuinely "
        "new content to mint a fresh id for — it must have a construct), or 'none' (the "
        "cell stays without a slide_id)"
    ),
    "additionalProperties": {"type": "string"},
}


class TaskUnavailable(Exception):
    """No framed model task exists for the selected item (with the reason / next step)."""


class SyncTask(BaseModel):
    """One framed model task an agent runs through a model of its choosing.

    The engine emits this; it never invokes a model. ``instructions`` is the system
    prompt (what the model must do), ``prompt`` the ready-to-send user message (the
    data), ``inputs`` the same data structured for programmatic use, ``answer_schema``
    the JSON shape the answer must take, and ``validator`` the deterministic check
    ``clm slides sync accept`` will run before it writes anything back.
    """

    item: str
    kind: str
    tier: str
    slide_id: str | None = None
    direction: str | None = None
    role: str | None = None
    # The deterministic check `accept` runs on the answer: "edit" (structural
    # cell-shape on proposed_text), "translation" (structural cell-shape on
    # translated_body), or "alignment" (validate_alignment over the region).
    validator: str
    instructions: str
    prompt: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    answer_schema: dict[str, Any] = Field(default_factory=dict)


def _other_lang(lang: str) -> str:
    return "en" if lang == "de" else "de"


def _region_dicts(region: list[RegionCell]) -> list[dict[str, Any]]:
    """Serialize a body-free region to indexed JSON rows (mirrors the prompt's view)."""
    return [
        {
            "index": idx,
            "slide_id": cell.slide_id,
            "construct": cell.construct,
            "content_hash": cell.content_hash,
        }
        for idx, cell in enumerate(region)
    ]


def _edit_task(item: ReconciliationItem) -> SyncTask:
    """Frame a drifted id'd localized-cell edit as the sync-judge task."""
    source_lang = item.source_lang or (item.direction or "de->en").split("->")[0]
    target_lang = item.target_lang or _other_lang(source_lang)
    source_text = item.source_excerpt or ""
    target_text = item.target_excerpt or ""
    prompt = build_sync_user_prompt(
        source_text=source_text,
        target_text=target_text,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    return SyncTask(
        item=item.item,
        kind=item.kind,
        tier=item.tier,
        slide_id=item.slide_id,
        direction=item.direction,
        role=item.role,
        validator="edit",
        instructions=SYNC_SYSTEM_PROMPT,
        prompt=prompt,
        inputs={
            "source_lang": source_lang,
            "target_lang": target_lang,
            "source_excerpt": item.source_excerpt,
            "target_excerpt": item.target_excerpt,
        },
        answer_schema=EDIT_ANSWER_SCHEMA,
    )


def _add_task(
    item: ReconciliationItem,
    *,
    prog_lang: str,
    guidance_by_lang: dict[str, str],
) -> SyncTask:
    """Frame a brand-new slide as the translation task."""
    source_lang = item.source_lang or (item.direction or "de->en").split("->")[0]
    target_lang = item.target_lang or _other_lang(source_lang)
    role = item.role or "slide"
    source_body = item.source_excerpt or ""
    guidance = guidance_by_lang.get(target_lang, "")
    instructions = build_translation_system_prompt(
        role=role,
        source_lang=source_lang,
        target_lang=target_lang,
        prog_lang=prog_lang,
        guidance=guidance,
    )
    return SyncTask(
        item=item.item,
        kind=item.kind,
        tier=item.tier,
        slide_id=item.slide_id,
        direction=item.direction,
        role=role,
        validator="translation",
        instructions=instructions,
        prompt=build_translation_user_prompt(source_body),
        inputs={
            "source_lang": source_lang,
            "target_lang": target_lang,
            "role": role,
            "prog_lang": prog_lang,
            "source_body": source_body,
        },
        answer_schema=TRANSLATION_ANSWER_SCHEMA,
    )


def _realign_task(item: ReconciliationItem, plan: SyncPlan) -> SyncTask:
    """Frame the stuck drifted-id region as the body-free alignment-recovery task.

    Region-level: the realign report items are *per drifted id*, but one alignment
    map covers the whole region, so every realign item yields the same region task.
    """
    from clm.slides.sync_apply import idmigration_regions

    regions = idmigration_regions(plan)
    if regions is None:
        # The detector and the report agreed this region was stuck when the report
        # was built; if it no longer is (the files changed underneath), say so plainly.
        raise TaskUnavailable(
            f"the drifted-id region for {item.slide_id!r} is no longer stuck — re-run "
            "`clm slides sync report` to refresh the plan."
        )
    base_region, current_region = regions
    return SyncTask(
        item=item.item,
        kind=item.kind,
        tier=item.tier,
        slide_id=item.slide_id,
        direction=None,
        role=item.role,
        validator="alignment",
        instructions=RECOVERY_SYSTEM_PROMPT,
        prompt=build_recovery_user_prompt(base_region, current_region),
        inputs={
            "base_region": _region_dicts(base_region),
            "current_region": _region_dicts(current_region),
        },
        answer_schema=ALIGNMENT_ANSWER_SCHEMA,
    )


def _task_for_item(
    item: ReconciliationItem,
    plan: SyncPlan,
    *,
    prog_lang: str,
    guidance_by_lang: dict[str, str],
) -> SyncTask:
    """Frame one report item, or raise :class:`TaskUnavailable` with the next step."""
    if item.kind == "edit":
        return _edit_task(item)
    if item.kind == "add":
        return _add_task(item, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang)
    if item.kind == "realign":
        return _realign_task(item, plan)
    if item.kind in ("mint", "adopt", "reconcile"):
        raise TaskUnavailable(
            f"{item.item!r} is a cold-start correspondence ({item.kind}); a per-item "
            "`task` for it is not wired yet — bootstrap the pair with "
            "`clm slides sync autopilot` (needs a key) or `clm slides assign-ids`."
        )
    raise TaskUnavailable(
        f"{item.item!r} ({item.kind}) is an ambiguity for you to resolve by hand"
        + (f": {item.reason}" if item.reason else "")
        + ". Edit the deck to resolve it, then re-run `clm slides sync report`."
    )


def build_task(
    plan: SyncPlan,
    item_id: str,
    *,
    prog_lang: str = "python",
    guidance_by_lang: dict[str, str] | None = None,
) -> SyncTask:
    """Frame the single tier-2/3 report item ``item_id`` as a model task.

    Builds the report with excerpts (a read-only ``--dry-run`` over the plan) to
    locate the item by its stable ``item`` id, then dispatches by kind. Raises
    :class:`KeyError` when no item carries ``item_id``, or :class:`TaskUnavailable`
    when the item exists but has no framed model task (a cold-start pair or a
    hand-judged ambiguity).
    """
    report = build_report(plan, with_excerpts=True)
    by_id = {it.item: it for it in (*report.mechanical, *report.assisted, *report.ambiguity)}
    item = by_id.get(item_id)
    if item is None:
        raise KeyError(item_id)
    return _task_for_item(item, plan, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang or {})


def build_tasks(
    plan: SyncPlan,
    *,
    prog_lang: str = "python",
    guidance_by_lang: dict[str, str] | None = None,
) -> tuple[list[SyncTask], list[ReconciliationItem]]:
    """Frame every frameable tier-2/3 item; return ``(tasks, unframed_items)``.

    ``tasks`` are the ``edit`` / ``add`` / ``realign`` items framed as model tasks
    (a realign region yields one task, deduplicated across its per-id items).
    ``unframed_items`` are the remaining tier-2/3 items (cold-start pairs, conflicts,
    issues) the caller surfaces with a pointer to ``report`` — they need a different
    next step, not a model prompt.
    """
    report = build_report(plan, with_excerpts=True)
    tasks: list[SyncTask] = []
    unframed: list[ReconciliationItem] = []
    seen_tasks: set[str] = set()
    for item in (*report.assisted, *report.ambiguity):
        if item.kind not in _FRAMEABLE_KINDS:
            unframed.append(item)
            continue
        try:
            task = _task_for_item(
                item, plan, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang or {}
            )
        except TaskUnavailable:
            unframed.append(item)
            continue
        # A realign region surfaces one report item per drifted id but one task; emit
        # it once (keyed by the prompt, which is identical for the whole region).
        key = task.prompt if task.kind == "realign" else task.item
        if key in seen_tasks:
            continue
        seen_tasks.add(key)
        tasks.append(task)
    return tasks, unframed
