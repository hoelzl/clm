# Handover prompt — Issue #403 Phase B (narrative edit-detection)

Paste the block below to a fresh agent. It is self-contained: it names the branch,
the design source of truth, the two hard constraints that make Phase B
**all-or-nothing**, the exact files/functions to touch, and the test gates. It also
folds in the newly-filed **report issue #10** (destructive id-less/id'd voiceover
mismatch), whose primary fix *is* Phase B's anchor-keying.

---

## Prompt

You are completing **Phase B of issue #403** in the CLM repo: narrative
*edit-detection* for `clm slides sync`, via a positional `anchor` on watermark
narrative rows. Phase 0 (shared `anchor_primitives.py`) and Phase A (id-less
narratives placed positionally — fixes report #6/#7) are **already merged**
(PRs #405). Phase B is the remaining, larger piece.

**Read first, in order:**
1. `docs/claude/design/sync-voiceover-anchoring-unification.md` — the whole design,
   but especially **§2a** (data-flow: narratives travel the *id-less add* path, not
   the keyed diff), **§4–§6** (the anchor model + invariants), and **§10** (the
   validated, step-by-step Phase B handover — this is your spec).
2. `src/clm/slides/anchor_primitives.py` — the shared positional-anchor primitives
   Phase A extracted. You will **add** `group_end`, `owning_group`, and
   `narrative_anchor_token` here (§10.4) and delete the private `_owning_group` /
   `_group_end` copies Phase A left in `sync_apply.py`, so recording and apply
   compute a narrative's identity *identically* (drift here = silent misplacement —
   a PR #199 invariant).
3. `planning/clm-issues-aidev-de-en-sync-2026-06.md` §10 in the **PythonCourses**
   repo (the motivating field incident — see below).

**Branch:** start fresh off `master` (do **not** reuse
`claude/issue-403-phase-b-narrative-edits`; that branch holds only the design-doc
handover commit and is otherwise empty). Suggested name:
`claude/issue-403-phase-b-narrative-edits-v2`.

### The two hard constraints (why this is all-or-nothing — do not fight them)

1. **record⟺consume coupling** — `tests/slides/test_sync_tag_drift.py`'s
   `CHANNEL_COVERAGE` asserts that *every* watermark channel written by
   `_record_watermark` names a real consumer function. So you **cannot** land
   "record the `anchor` column" without the narrative-edit classifier that *reads*
   it. Register `("de","anchor")` / `("en","anchor")` → the new classifier in
   `CHANNEL_COVERAGE` in the **same** change that starts recording anchors.
2. **`Cell` vs `RawCell` impedance** — the plan diff is built on
   `slide_parser.Cell` (`ordered_sync_cells`, `watermark_rows`, `_baseline_*` all
   take `Cell`), but the `fp:` anchor fingerprint must be the byte-level
   `anchor_primitives.body_fingerprint`, which needs `raw_cells.RawCell`.
   `parse_cells` and `split_cells` yield the **same** cell sequence, so positions
   align — thread a precomputed `{position: anchor}` map (built from RawCells) into
   both the current-cell keying and the baseline reconstruction. **This bridge is
   the main design work.**

### Implementation order (from §10 — each step references it)

1. **Storage (§10.2)** — in `src/clm/infrastructure/llm/cache.py`
   `SyncWatermarkCache`: add `anchor TEXT` to **both** the `CREATE TABLE` (fresh DB)
   **and** an additive `ALTER TABLE … ADD COLUMN anchor TEXT`. *(The prototype
   forgot the CREATE-TABLE side and every test failed with "no column named
   anchor".)* Add `put_deck(…, anchors: dict[int,str] | None = None)` and
   `get_deck_anchors(de, en, lang) -> {position: str}` (filter `anchor IS NOT
   NULL` — sparse, narrative rows only). Mirror `test_construct_roundtrips`.
2. **Shared helpers (§10.4)** — move `group_end` / `owning_group` /
   `narrative_anchor_token` into `anchor_primitives.py`; delete the Phase-A
   privates from `sync_apply.py` and import the shared versions.
3. **Recording (§10.3)** — add `watermark_anchor_map(cells: list[RawCell])` to
   `sync_plan.py` (mirror `watermark_tag_map`, iterate RawCells, record only for
   `meta.is_narrative` cells, partition by lang). In `_record_watermark` build
   RawCells via `split_cells` and pass `anchors=` to the de/en `put_deck` calls.
   Mirror in `_record_watermark_partial`.
4. **Consumer — the risky core (§10.5)** — give each current narrative
   `CurrentCell` an `anchor`, key it `(owning_slide_id, role, anchor)`; reconstruct
   baseline owning_slide_id by walking the ordered watermark rows; route narratives
   through the keyed diff (`_index_by_key` / `_baseline_index` / `_state` / the main
   loop) instead of `_append_idless_adds`, so edit/conflict/move are produced. Keep
   *id-carrying* narratives on their current path. Apply (`_apply_edit`) for a
   narrative locates the cell **by anchor** (Phase A's id-less placement), conflicts
   defer as today. Register the channel in `CHANNEL_COVERAGE`.
5. **#365 + tests (§10.6)** — cross-reference #365 (both-sided id-less *localized*
   drift); extend the edit-dynamics harness with `edit-voiceover-one-side` (now
   propagates) plus the §7 unit/regression set.

### Also resolve report issue #10 (it is the same root)

A second field pass (`planning/clm-issues-aidev-de-en-sync-2026-06.md` **§10**)
found `clm slides sync` is **actively destructive** when, within one deck, the two
halves disagree on whether voiceover cells carry a `slide_id` (DE id-less, EN id'd):
the default (writing) sync would insert ~11 duplicate German voiceovers. Phase B's
anchor-keying is exactly its **fix #1** ("pair voiceover cells by anchor, not by
their own id, when one half is id-less"). While here, also implement its **fix #2**
(refuse-to-write, loudly, on a mass-add of narratives whose anchor slide already
exists on the target half — a `13-add / 11-refuse / 9-in-sync` plan against a deck
that already has voiceovers is a strong "halves are mis-aligned, abort" signal).
Fix #3 (a sync-aware id-symmetrizing reconciler) is optional follow-up. **Repro** is
in §10 of that file. Confirm the anchor pairs an id-less DE voiceover with its id'd
EN twin under the same predecessor instead of classifying the EN one as a new add.

### Invariants you must not regress (design §6)

Occurrence ordinal is load-bearing; group bounds are language-aware; never
whole-file-search an anchor; fingerprint is blank-line-invariant; sync never stamps
`vo_anchor=` into a deck (it computes anchors in memory).

### Test gates

```
pytest tests/slides tests/infrastructure/llm/test_sync_cache.py -n 4
pytest tests/slides/test_sync_tag_drift.py -n 4   # the record⟺consume gate
```
Run the full fast suite before the PR. Land record + consume **together** (the
coverage gate will reject a half-landing). Commit/push/open a PR autonomously when
green. Update `docs/claude/design/sync-voiceover-anchoring-unification.md` §8 to mark
Phase B DONE, and the memory note `project-issue-403-narrative-anchoring`.
