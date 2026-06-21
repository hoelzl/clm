# Sync-Core Voiceover Anchoring Unification

**Status**: Design; pre-implementation. The deferred "sync diff/apply core" item
that [`split-voiceover-hardening.md`](split-voiceover-hardening.md) explicitly
scoped *out* ("**Not** a rewrite of the sync diff/apply core", §Scope).
**Author**: Claude (Opus 4.8), with the maintainer.
**Date**: 2026-06-21
**Scope**: how `clm slides sync` (the `#166`/`#190` engine) **keys, matches, and
places narrative cells** (`voiceover` / `notes`). Nothing about slide/code/markdown
keying changes. The goal is to let a slide own **more than one** narrative cell —
each anchored to a distinct preceding content cell (markdown **or code**) — without
the engine collapsing them into a single `(slide_id, role)` key.
**Tracking issue**: [#403](https://github.com/hoelzl/clm/issues/403).
**Motivating incident**: the AI-dev W10/W11 DE→EN propagation run
(`planning/clm-issues-aidev-de-en-sync-2026-06.md`, items **#6** and **#7**).
**Related**: [`split-voiceover-hardening.md`](split-voiceover-hardening.md) (the
perimeter work this completes), `single-language-authoring-sync.md` (#166 engine),
`sync-content-anchor-identity.md` (#190 identity), the `vo_anchor` positional-anchor
algorithm (PR #199, `voiceover_tools.py`).

---

## 1. Problem

Two field-reported failures, plus the maintainer's reframing that makes them one
problem:

- **#6 — a voiceover after a code subslide collapses onto the wrong slide.** A deck
  with code-cell subslides (`# %% tags=["keep","subslide"] slide_id="unit-tests"`)
  and a `voiceover` cell following each one ends with all those voiceovers anchored
  to the previous *markdown* slide → `unresolved duplicate slide_id
  'possible-llm-response-3'/voiceover`. Workaround used in the field: hand-stamp an
  explicit `slide_id` on each voiceover cell.
- **#7 — a leading greeting voiceover cannot anchor.** A `voiceover` cell *before*
  the first explicit slide cell (a greeting for the macro-generated title slide)
  errors `add voiceover: narrative with no preceding slide — deferred`, and (via the
  atomicity bug #5) rolls back the whole deck.
- **The reframing (maintainer):** it must be valid for **multiple code cells to each
  carry their own voiceover, with none of them starting a new slide.** This is
  already true and supported at **build / extract / inline** time (the `vo_anchor`
  model, below). It is *only* `sync` that cannot represent it.

So #6 is **not** "treat code subslides as anchors" — that special-case still breaks a
plain (non-subslide) code cell that carries a voiceover. The real defect is that the
sync engine's narrative identity is too coarse.

## 2. Root cause — `(slide_id, role)` is the join key everywhere

The `#166` engine reconciles cells **per `(slide_id, role)`**. For a narrative cell
`role ∈ {voiceover, notes}` the `slide_id` is its *owning slide's* id (companions
inherit it), so **every voiceover under one slide hashes to the same key**
`(slide_id, "voiceover")`. The key is built and consumed in (all current master):

| Site | File:line | What it does with the key |
|---|---|---|
| Plan index | `sync_plan.py:1088` `_index_by_key` | `by_key[(slide_id, role)].append(cell)`; a list >1 is a "duplicate" |
| Baseline index | `sync_plan.py:1225` `_baseline_index` | `out.setdefault((slide_id, role), cell)` — **second narrative silently dropped** |
| Duplicate resolver | `sync_plan.py:1128` `_resolve_duplicates` | a 2nd same-key narrative with no copied *slide* group → hard error "lone duplicated companion" |
| Post-apply guard | `sync_apply.py:582` `_flag_residual_duplicates` | `(sid, role)` seen twice → `unresolved duplicate slide_id …/voiceover` |
| Add walk | `sync_apply.py:1970` `_add_one_direction` | `current_slide_id` advances **only on `_SLIDE_ROLES`**; narrative placed at `(current_slide_id, role)`; leading narrative with `current_slide_id is None` → the #7 error |
| Watermark | `sync_writeback.py` `watermark_rows` / `role_of:53` | records each cell under `(slide_id, role)` |

`role_of` (`sync_writeback.py:53`) returns the bare tag (`"voiceover"`), with no
positional component. So the coarse identity is baked into the watermark schema, the
baseline diff, the duplicate logic, and the apply placement — not one spot.

### 2a. Data-flow correction (verified against current master)

The first draft of this doc assumed narratives travel the keyed `(slide_id, role)`
diff. The verified reality is more split, and it changes the sequencing:

- An **inline narrative cell is usually id-less** (`# %% [markdown] lang=… tags=["voiceover"]`,
  no `slide_id`) but **role-bearing** (`role_of → "voiceover"`). In `_index_by_key` it
  therefore lands in the **`idless` bucket** (`sync_plan.py:1100`), not `by_key`.
- Id-less narratives are emitted as **add-only** proposals by `_append_idless_adds`
  (`sync_plan.py:1566`, both warm and cold) — there is **no edit detection** for them.
  The apply (`_add_one_direction`) then translates and **places** each one, stamping
  the *owning* `current_slide_id` onto it.
- The field incidents **#6 and #7 are in that add/placement path**: the collision is
  produced when two placed narratives stamp the *same* owning `slide_id`
  (`_flag_residual_duplicates` keys `(slide_id, role)` → duplicate). When narratives
  already carry an explicit `slide_id` (the field deck after the hand-stamp workaround),
  they go through `by_key`/`_resolve_duplicates` and hit the **same** `(slide_id, role)`
  collision there.

**Consequence for sequencing:** fixing the reported #6/#7 needs the **apply-path /
duplicate-check** change (anchor-aware placement), which requires **no watermark
schema migration and no plan re-keying**. The plan re-keying + watermark `anchor`
column is only needed to *detect edits* to multiple-per-slide narratives across syncs
(a capability the field run did not exercise — its narratives were adds) and to clean
up the id-less-localized drift path that issue **#365** also targets. So the phases
below are re-ordered: **apply-path first (A), keying/watermark second (B).**

## 3. The model that already works — `vo_anchor` (PR #199)

`voiceover_tools.py` solved this exact "multiple narratives per slide group, each at
a distinct position" problem for extract/inline/build-merge. A voiceover is anchored
to its **immediate predecessor content cell**, occurrence-qualified, scoped to the
owning slide group:

- `_find_predecessor_index` (`voiceover_tools.py:508`) — walk back over narrative
  cells and conflicting-language cells to the first eligible anchor cell (markdown,
  **code**, or a mid-group j2 macro).
- `_anchor_key` (`:422`) — `id:<slide_id>` if the predecessor carries one, else
  `fp:<body-fingerprint>` (body-only, blank-line-invariant; `_body_fingerprint:394`).
- `_anchor_token` (`:464`) — append a 0-based **occurrence ordinal** counted over
  same-token, in-group, language-filtered, non-narrative candidates
  (`_anchor_candidates:430`): `id:<sid>#<n>` / `fp:<hash>#<n>`.
- **Title-macro anchor** `tm:title#0` (`:388`, `is_title_macro_cell`) — addresses the
  j2 `header` title slide directly, so a greeting authored before the title slide's
  continuation cells restores to the **start** of the title group (#246). This is
  exactly the missing anchor for **#7**.

The four "silently misplaces if regressed" invariants for this algorithm are recorded
in `[[project-voiceover-positional-anchors]]` and must be honored by any reuse:
occurrence ordinal is load-bearing; never whole-file-search an anchor; group bounds
must be language-aware; fingerprint must be blank-line-invariant.

**Key insight:** the *algorithm* is reusable, but the stored `vo_anchor="…"`
attribute is **not present at sync time** — it is author-only, stamped by `extract`
into companions and stripped on inline/build (`voiceover_tools.py:556`,
`notebook_processor.py:1586`). Sync operates on the deck's **inline** voiceover
cells, which carry no `vo_anchor`. So sync must **compute** the anchor positionally
(the same way `extract` computes it before stamping), not read an attribute.

## 4. Design

### 4.1 Narrative identity = owning slide + role + positional anchor

Give a narrative cell the composite key

```
(owning_slide_id, role, anchor)      anchor = "<kind>:<value>#<occ>"
```

where `anchor` is computed by the `vo_anchor` algorithm over the cell's own deck
(predecessor token + in-group occurrence). Non-narrative cells keep their current
`(slide_id, role)` key unchanged. Concretely:

- A new `narrative_anchor(cells, idx, lang) -> str` helper (in `sync_writeback.py`,
  beside `role_of`/`anchor_of`) wraps `voiceover_tools._find_predecessor_index` +
  `_anchor_token` (+ the `tm:` title path). Factor the four shared primitives
  (`_find_predecessor_index`, `_anchor_token`, `_anchor_candidates`,
  `_body_fingerprint`, `is_title_macro_cell`, `_slide_group_bounds`) into a small
  shared module both `voiceover_tools` and the sync engine import, so the two
  subsystems can never drift (a drift here = silent misplacement on one side only).
- The plan's per-cell key for a narrative becomes `(slide_id, role, anchor)`;
  `_index_by_key` / `_baseline_index` index narratives under the 3-tuple, slides and
  code/markdown under the existing 2-tuple. (Encode the 2-tuple as `(sid, role, "")`
  internally so the diff machinery stays uniform.)

### 4.2 Cross-language and baseline matching still holds

The key must be stable across (a) DE↔EN and (b) baseline↔now for the diff to work:

- **Predecessor carries a `slide_id`** (the #6 case: `slide_id="unit-tests"` code
  subslide, or "right after the heading") → `id:<sid>#<occ>`. `slide_id` is
  language-agnostic by the **#162 invariant** (`de_id == en_id`) and survives commits
  by the **#190** content-anchor identity, so the anchor matches across halves and
  across the watermark.
- **Predecessor is language-neutral / shared** (a bare code cell copied verbatim into
  both halves — the `unify` byte-identity invariant) → `fp:<hash>` agrees across
  halves because the bodies are byte-identical.
- **Predecessor is localized with no `slide_id`** → `fp:` diverges across languages.
  This is the residual ambiguity the #190 doc already flags for "localized markdown,
  positional only". Policy: **do not silently mispair** — when a narrative's anchor
  resolves on one half but not the other, emit a `PlanIssue`/defer (the existing
  refuse-and-surface stance), never guess. In practice this is rare: an anchorable
  code cell either carries a `slide_id` (localized) or is shared (neutral).

### 4.3 Apply: place by predecessor, not by `current_slide_id`

In `_add_one_direction` (`sync_apply.py:1970`), an added narrative is inserted
**immediately after its resolved predecessor cell in the target deck** (reusing the
`insert_after`/anchor machinery already used by `voiceover_tools` inline and the
`#166` add path), rather than appended at `(current_slide_id, role)`. `current_slide_id`
is retained only to compute `owning_slide_id` for the key.

### 4.4 #7 — leading greeting → title anchor

When `_find_predecessor_index` returns `None` (no content cell above the narrative),
the narrative is a **title greeting**: assign `tm:title#0` and anchor it to the
implicit title slide (j2 `header` macro) — exactly as `voiceover_tools` does for
#246. Only when there is genuinely no title macro *and* no following slide does it
defer. This removes the field workaround (temporary DE orphan removal).

### 4.5 Duplicate detection stops false-positiving

`_resolve_duplicates` and `_flag_residual_duplicates` must treat two narratives that
share `(slide_id, role)` but differ in `anchor` as **distinct**, not duplicates. A
genuine duplicate is now "same `(slide_id, role, anchor)`" — i.e. two voiceovers
claiming the *same* predecessor at the *same* occurrence, which is a real authoring
error worth surfacing.

## 5. Back-compat — watermark migration

The watermark schema records narrative rows under `(slide_id, role)`. After this
change `watermark_rows` records the 3-tuple (add an `anchor` column, defaulting empty
for non-narrative rows). A pre-existing watermark row has no anchor; treat a missing
anchor as a wildcard that matches the single-narrative case (occurrence 0), so a
deck that currently has **one** voiceover per slide keeps matching its old watermark
with zero migration. Decks with **multiple** narratives per slide were already
*erroring* under the old engine, so there is no silent behavior change to preserve
for them. Bump the watermark cache schema version; the migration is additive (new
nullable column), consistent with prior `sync_watermarks` migrations.

## 6. Invariants / edge cases (must not regress)

1. **Occurrence ordinal is load-bearing** — two identical code cells (`print(result)`)
   under one slide, each with a voiceover, must keep distinct anchors `…#0` / `…#1`.
2. **Group-bounds are language-aware** — in an interleaved bilingual deck the next
   slide-start may be the other-language twin with the *same* `slide_id`; a
   language-blind scan truncates the group (see PR #199 invariant 3).
3. **No whole-file anchor search** — resolution is scoped to the owning slide group;
   a missing anchor → defer/`unmatched`, never a cross-group match.
4. **Fingerprint blank-line-invariant** — reuse `_body_fingerprint` unchanged.
5. **`vo_anchor` never leaks into decks** — sync computes anchors in memory; it must
   not stamp a `vo_anchor="…"` attribute onto inline cells (that attribute is
   companion-only).
6. The byte-preserving split/unify contract and the #190 content-anchor identity are
   untouched — this change is purely about *narrative* keying/placement.

## 7. Test plan

- Extend the **edit-dynamics fault-injection harness**
  (`scripts/edit_dynamics_harness.py` / `tests/slides/test_edit_dynamics.py`) with
  mutations: `add-second-voiceover-under-slide` (code-cell predecessor),
  `voiceover-after-code-subslide` (the #6 repro), `leading-title-greeting` (the #7
  repro), `two-identical-code-cells-each-voiceovered` (occurrence ordinal). Assert
  **preserve** (no duplicate-id error, correct placement, DE/EN parity) where the old
  engine produced **break-loud**.
- Unit tests in `tests/slides/test_sync_anchor.py` for `narrative_anchor` across the
  id:/fp:/tm: cases and the cross-language/baseline matching.
- A focused regression reproducing the field decks (`slides_pe_04a` shape:
  code subslides + per-cell voiceover; `slides_pe_02a/03a` shape: leading greeting),
  driven by the no-LLM `CountingTranslator`/`CountingJudge`.
- The four PR-#199 silent-misplacement invariants get an explicit assertion each.

## 8. Sequencing

Re-ordered per §2a: the apply-path change fixes the reported incidents and is
schema-free; the keying/watermark change is the larger follow-on.

1. **Phase 0 — extract the shared anchor primitives** into a common leaf module
   (`anchor_primitives.py`), used by `voiceover_tools` unchanged (pure refactor,
   byte-identical; locked by the existing `voiceover_tools` tests). ✅ **DONE**
   (commit on `claude/issue-403-sync-voiceover-anchoring`).
2. **Phase A — apply-path anchoring (fixes #6 + #7; no schema change).**
   `_add_one_direction` places each added narrative after its **resolved predecessor**
   (computed over the full `RawCell` stream via `anchor_primitives`), routes a leading
   greeting to `tm:title#0` instead of erroring, and `_flag_residual_duplicates`
   becomes **anchor-aware** so multiple narratives under one owning slide are allowed
   (a real duplicate is now same `(slide_id, role, anchor)`). Also adjust
   `_resolve_duplicates` for the explicitly-stamped-id variant. This is the
   highest-value, lowest-risk unit and directly retires the field workarounds.
3. **Phase B — narrative keying + watermark (edit-detection; overlaps #365).** Key
   id-less narratives by `(owning_slide_id, role, anchor)` through a keyed path
   (replacing the add-only `_append_idless_adds` route for narratives), add the
   watermark `anchor` column + additive migration, and reconcile with the
   id-less-localized drift path (#365). Larger and schema-touching — gate behind an
   explicit go-ahead.
4. **Phase C — harness mutations + regressions + docs** (`clm info` unaffected; this
   is engine-internal). Update `[[project-voiceover-positional-anchors]]` and the
   `split-voiceover-hardening.md` roadmap to mark the deferred sync-core item DONE.

## 9. Open questions

1. Should a localized-markdown predecessor (`fp:` diverges across languages) ever be
   LLM-paired (the #166 heavy path), or always refuse-and-surface? Lean: refuse +
   surface; revisit only if real decks need it.
2. Does `notes` (the other narrative role) want the same treatment, or only
   `voiceover`? Default: both (they share `role_of` and the collision shape).
3. Interaction with separated-voiceover **companions**: this change is for **inline**
   narrative cells in the deck. A companion deck already carries `vo_anchor`
   attributes; confirm the sync path for companion files (if any) reuses the stamped
   anchor rather than recomputing. (Today `sync` runs on the slide deck; companions
   are handled by `split`/`unify`/`extract` — verify no overlap before Phase 2.)
