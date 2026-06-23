"""Framed model tasks for the agent-driven sync toolkit (epic #440, decision B).

``clm slides sync task DECK --item ID`` emits a :class:`SyncTask` — everything a
model needs to reconcile *one* tier-2/3 item and nothing more — without the engine
calling a model. It is a thin, **model-free** wrapper over the prompt builders that
the embedded clients also use, so the agent path and the agent-less ``autopilot``
path frame the work identically:

* **edit** (a drifted localized cell) → split by how the engine reconciles it: a
  **prose** (markdown / narrative) edit → the sync-judge prompt
  (:data:`~clm.infrastructure.llm.sync_prompts.SYNC_SYSTEM_PROMPT` +
  :func:`~clm.infrastructure.llm.sync_prompts.build_sync_user_prompt`), answer
  ``{verdict, proposed_text, reason}``; a **code** edit → the translation prompt (the
  judge's prose prompt does not fit runnable code), answer the translated body. This
  mirrors the code/judge branches of ``sync_apply._resolve_edit``.
* **add** (a brand-new slide) → the translation prompt
  (:func:`~clm.slides.sync_translate.build_translation_system_prompt` +
  :func:`~clm.slides.sync_translate.build_translation_user_prompt`); the answer is
  the translated cell body.
* **realign** (a drifted ``slide_id`` the deterministic id-migration cannot resolve)
  → the body-free alignment-recovery prompt
  (:func:`~clm.slides.sync_recover.build_recovery_user_prompt`); the answer is an
  ``{index → assignment}`` map. The region-level task, validated by
  :func:`~clm.slides.sync_recover.validate_alignment`.
* **mint** / **adopt** / **reconcile** (a pair whose correspondence is unconfirmed) →
  the batch correspondence-verification prompt
  (:func:`~clm.slides.sync_recover.build_correspondence_user_prompt`); the answer is a
  ``{pair_index → bool}`` verdict map (validator ``correspondence``). A cold-start
  ``mint`` / ``adopt`` frames every aligned slide pair (one **deck-level** task); a
  committed mismatched-id ``reconcile`` (#228) frames the DE×EN suspect **cross-product**
  (one task for the whole bucket). Either way the model confirms which halves correspond
  before any shared id is minted / stamped / rewritten.

Each task names the deterministic ``validator`` that ``clm slides sync accept`` will
run on the answer (so ``accept`` can never apply the wrong check) and carries the
``answer_schema`` the answer must match. The module imports **no** OpenRouter client
and constructs none — building a task never reaches a model (decision B is
structural here).

The hand-judged ambiguities (``conflict`` / ``issue``) and a degenerate one-directional
``reconcile`` (no cross-product — its suspects are one-sided ``add``s) are not framed as
model tasks — :func:`build_task` raises :class:`TaskUnavailable` with the right next step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from clm.infrastructure.llm.sync_prompts import (
    SYNC_RESPONSE_SCHEMA,
    SYNC_SYSTEM_PROMPT,
    build_sync_user_prompt,
)
from clm.slides.sync_plan import LOCALIZED_CODE_ROLE
from clm.slides.sync_recover import (
    CORRESPONDENCE_SYSTEM_PROMPT,
    RECOVERY_SYSTEM_PROMPT,
    build_correspondence_user_prompt,
    build_recovery_user_prompt,
)
from clm.slides.sync_report import ReconciliationItem, build_report
from clm.slides.sync_translate import (
    build_translation_system_prompt,
    build_translation_user_prompt,
)
from clm.slides.sync_writeback import CODE_ROLE

if TYPE_CHECKING:
    from clm.slides.sync_plan import SyncPlan
    from clm.slides.sync_recover import RegionCell, SlidePair

__all__ = [
    "ALIGNMENT_ANSWER_SCHEMA",
    "CORRESPONDENCE_ANSWER_SCHEMA",
    "EDIT_ANSWER_SCHEMA",
    "TRANSLATION_ANSWER_SCHEMA",
    "SyncTask",
    "TaskUnavailable",
    "build_task",
    "build_tasks",
]

#: The proposal kinds :func:`build_task` can frame as a single model task.
_FRAMEABLE_KINDS = frozenset({"edit", "add", "realign", "mint", "adopt", "reconcile"})

#: Edit roles the engine reconciles by **re-translating** the source cell (rather than
#: by the prose judge) — runnable code, keyed or id-less localized. Mirrors the
#: code branches of :func:`clm.slides.sync_apply._resolve_edit`, so a ``task`` frames a
#: code edit the same way the engine would reconcile it (and ``accept`` checks the same
#: answer shape). Everything else (markdown, narrative) is a prose/judge edit.
_CODE_EDIT_ROLES = frozenset({CODE_ROLE, LOCALIZED_CODE_ROLE})

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
CORRESPONDENCE_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "map EVERY pair index (a string) to a boolean: true when the DE and EN slide are "
        "the SAME slide in the two languages (a faithful translation — same topic and "
        "intent), false when they are different slides (the decks are misaligned at that "
        "position). When genuinely unsure, return false — a wrong pairing bakes a wrong "
        "shared identifier"
    ),
    "additionalProperties": {"type": "boolean"},
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


def _edit_task(
    item: ReconciliationItem,
    *,
    prog_lang: str,
    guidance_by_lang: dict[str, str],
) -> SyncTask:
    """Frame a drifted localized-cell edit — split by how the engine reconciles it.

    A **code** edit (runnable code, keyed or id-less localized) is reconciled by
    re-translating the source, so it is framed as a translation task (validator
    ``translation``, answer ``{translated_body}``) — the judge's prose prompt does not
    fit code. A **prose** edit (markdown / narrative) goes through the sync judge
    (validator ``edit``, answer ``{verdict, proposed_text}``). This matches the
    code/judge branches of :func:`clm.slides.sync_apply._resolve_edit`, so the framed
    task and the embedded-client reconciliation are interchangeable.
    """
    if item.role in _CODE_EDIT_ROLES:
        return _code_edit_task(item, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang)
    return _prose_edit_task(item)


def _prose_edit_task(item: ReconciliationItem) -> SyncTask:
    """Frame a markdown / narrative edit as the sync-judge task."""
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


def _code_edit_task(
    item: ReconciliationItem,
    *,
    prog_lang: str,
    guidance_by_lang: dict[str, str],
) -> SyncTask:
    """Frame a localized **code** edit as a re-translation task.

    The engine reconciles a code edit by re-translating the (drifted) source code cell
    — ``translator.translate(role="code")`` — so the task is the same translation
    framing as a new code cell: translate the source body to the target language, keep
    it runnable. The answer is the translated body (``{translated_body}``), validated by
    ``accept``'s ``translation`` check.
    """
    source_lang = item.source_lang or (item.direction or "de->en").split("->")[0]
    target_lang = item.target_lang or _other_lang(source_lang)
    source_body = item.source_excerpt or ""
    guidance = guidance_by_lang.get(target_lang, "")
    instructions = build_translation_system_prompt(
        role=CODE_ROLE,
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
        role=item.role,
        validator="translation",
        instructions=instructions,
        prompt=build_translation_user_prompt(source_body),
        inputs={
            "source_lang": source_lang,
            "target_lang": target_lang,
            "role": CODE_ROLE,
            "prog_lang": prog_lang,
            "source_body": source_body,
        },
        answer_schema=TRANSLATION_ANSWER_SCHEMA,
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


def _pair_dicts(pairs: list[SlidePair]) -> list[dict[str, Any]]:
    """Serialize the aligned cold-start slide pairs to indexed JSON rows (the prompt's view)."""
    return [
        {
            "index": idx,
            "role": p.role,
            "de_heading": p.de_heading,
            "en_heading": p.en_heading,
            "de_snippet": p.de_snippet,
            "en_snippet": p.en_snippet,
        }
        for idx, p in enumerate(pairs)
    ]


def _correspondence_task(item: ReconciliationItem, pairs: list[SlidePair]) -> SyncTask:
    """Frame a list of candidate slide pairs as the correspondence-verification task.

    Shared by the cold-start (``mint`` / ``adopt``) and committed-``reconcile`` paths —
    the only difference is *which* pairs are verified (the positional cold pairs vs the
    reconcile DE×EN cross-product); the prompt, validator, and answer schema are identical.
    """
    return SyncTask(
        item=item.item,
        kind=item.kind,
        tier=item.tier,
        slide_id=item.slide_id,
        direction=item.direction,
        role=item.role,
        validator="correspondence",
        instructions=CORRESPONDENCE_SYSTEM_PROMPT,
        prompt=build_correspondence_user_prompt(pairs),
        inputs={"pairs": _pair_dicts(pairs)},
        answer_schema=CORRESPONDENCE_ANSWER_SCHEMA,
    )


def _cold_pair_task(item: ReconciliationItem, plan: SyncPlan) -> SyncTask:
    """Frame a cold-start ``mint`` / ``adopt`` as the batch correspondence task.

    A both-id-less (``mint``) or half-id'd (``adopt``) cold pair carries no shared
    identity, so before the engine mints fresh ids / stamps the authority's ids it must
    confirm the DE and EN halves actually correspond. That judgement is the model's, so
    the agent path frames it as a single **deck-level** task: every aligned slide pair at
    once (mirroring how :func:`~clm.slides.sync_apply._apply_cold_mint` /
    ``_apply_cold_adopt`` verify before writing), answered by a ``{pair_index → bool}``
    verdict map (validator ``correspondence``). One mint/adopt item ⇒ one task.
    """
    from clm.slides.sync_apply import cold_slide_pairs

    pairs = cold_slide_pairs(plan)
    if not pairs:
        # The candidacy gate admitted this cold pair with slides on both halves; if there
        # are none now, the files changed underneath — say so plainly (cf. realign).
        raise TaskUnavailable(
            f"{item.item!r}: the cold-start pair has no slides to verify — re-run "
            "`clm slides sync report` to refresh the plan."
        )
    return _correspondence_task(item, pairs)


def _reconcile_task(item: ReconciliationItem, plan: SyncPlan) -> SyncTask:
    """Frame a committed mismatched-id ``reconcile`` bucket (#228) as a correspondence task.

    The verification is over the DE×EN suspect **cross-product** (one task for the whole
    bucket — the per-suspect report items dedup to it), answered by a verdict map keyed by
    the flat ``i*m+j`` index. A *one-directional* bucket has no cross-product (its suspects
    are one-sided slides, not twins), so it is unavailable as a correspondence task —
    handle those as ``add``s.
    """
    from clm.slides.sync_apply import reconcile_pairs

    pairs = reconcile_pairs(plan)
    if not pairs:
        raise TaskUnavailable(
            f"{item.item!r}: the reconcile bucket has no cross-product to verify (its "
            "suspects are one-sided) — handle them as `add`s; re-run `clm slides sync report`."
        )
    return _correspondence_task(item, pairs)


def _task_for_item(
    item: ReconciliationItem,
    plan: SyncPlan,
    *,
    prog_lang: str,
    guidance_by_lang: dict[str, str],
) -> SyncTask:
    """Frame one report item, or raise :class:`TaskUnavailable` with the next step."""
    if item.kind == "edit":
        return _edit_task(item, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang)
    if item.kind == "add":
        return _add_task(item, prog_lang=prog_lang, guidance_by_lang=guidance_by_lang)
    if item.kind == "realign":
        return _realign_task(item, plan)
    if item.kind in ("mint", "adopt"):
        return _cold_pair_task(item, plan)
    if item.kind == "reconcile":
        return _reconcile_task(item, plan)
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

    ``tasks`` are the framed model tasks (``edit`` / ``add`` / ``realign`` /
    ``mint`` / ``adopt`` / ``reconcile``). A *batch* task — a ``realign`` region or a
    ``reconcile`` cross-product — surfaces one report item per drifted id / per suspect
    but yields a single task, deduplicated by its (identical) prompt. ``unframed_items``
    are the remaining tier-2/3 items (conflicts, issues, a one-sided reconcile) the caller
    surfaces with a pointer to ``report`` — they need a different next step, not a prompt.
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
        # A batch task (a realign region, a reconcile cross-product) surfaces one report
        # item per drifted id / per suspect but ONE task; emit it once, keyed by the prompt
        # (identical for the whole batch). Per-item tasks are keyed by their unique item id.
        key = task.prompt if task.kind in ("realign", "reconcile") else task.item
        if key in seen_tasks:
            continue
        seen_tasks.add(key)
        tasks.append(task)
    return tasks, unframed
