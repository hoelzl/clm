# Sync: Resolve-then-Apply — Design Note

Companion to [`single-language-authoring-sync.md`](single-language-authoring-sync.md)
(the #166 engine). This note re-architects the **plan / apply boundary** so that
`clm slides sync` decides *what will happen* once, at plan time, and applying is
a decision-free mechanical replay. It is the clean design for the bug cluster
found investigating **#216** (cold-start id-less doubling) and the **dry-run /
apply divergence** it exposed.

**Status:** design accepted (2 forks settled — see §3); implementation phased
(§10). **Phase 1 DONE** (the `refuse` disposition + both-directions guard moved to
the resolver; all 5 parity/doubling xfails flipped — §9). **Phase 2 DONE (scoped):**
the edit and add paths are materialize-then-execute (the model calls moved to 2b;
execute writes mechanically). The id-migration recoverer and the `sync_code`
structural translate remain inline as **explicitly-deferred follow-ups** (§10).
Phase 3 pending.

---

## 1. The problem: apply does much more than apply

Today the pipeline is two stages:

```
build_sync_plan / classify_changes   →  SyncPlan (proposals)        [pure]
apply_plan                           →  writes + ApplyResult        [decides AND executes]
```

`apply_plan` is not an executor — it is a **second decision-maker**. It re-runs
structural logic the plan never captured:

- the both-directions id-less **refusal** (`sync_apply.py:710-718`: defer + error);
- the **absence** of that refusal for id-*carrying* both-directions adds
  (`sync_apply.py:695-705`: applies both → silent doubling);
- translation success/failure → defer (`_translate`, `_add_one_direction`);
- `process_idless` gating, move-after-add ordering, watermark advance gating.

Because those decisions live in the executor, **the plan is not a faithful
description of what will happen.** Every symptom below is one cause:

| Symptom (this session) | Why |
|---|---|
| `--dry-run` prints `N add`, the writing run defers all and writes nothing (exit 1 vs 2) | the refusal is an apply-time decision, invisible to the plan |
| cold-start **mismatched-id** / **half-id'd** pairs silently **double** both decks (`errors=[]`) | the both-directions guard covers only id-*less* adds; the id-*carrying* path is unguarded |
| `#216` cannot bootstrap a fresh deck | the plan emits N bidirectional adds; apply refuses; neither pairs |

(The id-less *symmetric* case the issue cites is in fact already *refused* by the
guard — so it is a dry-run-honesty + missing-feature bug, **not** the data
corruption the issue claimed. The real corruption is the id-carrying sibling.)

## 2. The principle: decide at resolve time, execute mechanically

Split the work by **what kind of thing it is**, with a sharp purity boundary:

```
1. Classify   [pure]            structural diff vs. baseline → raw observations
2. Resolve                      turn observations into a COMPLETE, executable plan
   2a. structural  [pure*]      decide every disposition: add / pair / refuse /
                                defer / conflict / order  (no "maybe" remains)
   2b. materialize [LLM/IO]     fill in generated content: translate / rewrite /
                                correspondence-confirm  → attach, or mark Blocked
3. Apply      [mechanical]      replay the resolved steps. No decisions, no LLM,
                                no "can this apply?". It cannot defer or refuse.
```

`*` "pure" = deterministic, read-only, **no LLM**. 2a may read file state (the
classifier already reads both decks); it must not write or call a model.

The one load-bearing property: **`--dry-run` = stages 1 + 2a**, which is pure and
**shared byte-for-byte with the writing run.** A dry-run and an apply therefore
cannot disagree on any *structural* decision — the refusal, the pairing, the
deferral, and the exit class all become plan items the preview prints.

## 3. Decisions settled

**Q-A — dry-run faithfulness: structural-faithful, no LLM.** `--dry-run` runs
1 + 2a and prints every structural disposition, marking generated content
`pending` rather than calling the model. Same cost as today's dry-run. The *only*
permitted apply-time divergence is a `pending` item the model could not
materialize/verify at write time → it downgrades to `blocked`/`refused`, and
because the preview labeled it `pending`, the downgrade is never a surprise.
(A future `--dry-run --with-content` may opt into running 2b for an exact preview.)

**Q-B — cold-start pairing: mint when correspondence confirmed, else refuse.**
A never-id'd, structurally-aligned pair is a *pairing candidate*. When a provider
is configured, a cheap, cached LLM gate (§7) confirms the halves actually
correspond, then mints shared ids; with no provider, or on a "no" verdict, it
**refuses** with a clear message. This supplies the cross-language signal that
[§3.2 of the base design](single-language-authoring-sync.md) deliberately lacked
("we do not *similarity-guess* cross-language identity") — an explicit semantic
**verification** is not a blind structural guess, so the new behavior extends
that principle rather than breaking it.

## 4. Data model: dispositions, `pending`, and `Refuse` as a first-class item

Today a `Proposal` is implicitly "a thing apply will attempt." Replace the
implicit contract with an explicit **disposition** — the resolved verdict 2a
assigns to every item:

| Disposition | Meaning | Stage-3 action |
|---|---|---|
| `apply` | a concrete mechanical op (insert / replace-body / mint-id / adopt-id / delete / move / retag) | execute it |
| `refuse(reason)` | a structural decision **not** to act (both-directions cold-start, mismatched-id pair) | no-op; hold watermark for the scoped cells; **shown in the plan** |
| `conflict(reason)` | same id drifted both sides (§3.4 isolate-and-refuse) | leave untouched; hold |
| `pending(kind)` | structurally decided, awaiting 2b content/verification (translate / correspondence) | resolved before stage 3; downgrades to `blocked`/`refused` only if 2b fails |

Key change vs. today: `refuse` and `pending` are **plan items with a reason**,
not apply-time strings. The new mechanical op kinds the redesign introduces:

- **`mint_shared_id(de_cell, en_cell, id)`** — stamp a *fresh* shared id onto two
  *existing* positionally-paired id-less cells. No translation, no insertion.
  (Today **no such op exists**; the only minter, `_add_one_direction` /
  `_place_new_cell`, is fundamentally translate-and-insert.)
- **`adopt_id(src_id → target_cell)`** — copy an existing id from the id'd half
  onto its id-less twin (the half-id'd case; safer than minting — the id already
  exists on one side).

`mint_shared_id` / `adopt_id` may delegate their byte-level work to the proven
`assign_ids_in_split_pair` machinery (`assign_ids.py:823`, round-trip-verified
EN-authority paired minting) rather than re-implementing slug derivation.

## 5. The faithfulness contract (the testable invariant)

> For any input, a `--dry-run` and a writing run **agree on every structural
> disposition** — what is added / paired / refused / deferred / conflicted, and
> the resulting exit class. The writing run may additionally downgrade a
> `pending` item to `blocked`/`refused` **iff** the model is unavailable or a
> correspondence check returns "no" — and the preview labels every such item
> `pending`, so the downgrade is disclosed in advance.

This is exactly what the parity tests in §9 assert; the redesign flips them from
`xfail` to pass.

## 6. How each found issue dissolves

| Issue | Resolution under this design |
|---|---|
| dry-run dishonesty (id-less symmetric) | 2a emits a visible `refuse` (no provider) or `pending` pair candidate (provider) → dry-run shows it; exit classes match |
| id-carrying silent doubling (mismatched-id) | 2a sees a both-direction id divergence with no baseline → `refuse` (or `conflict`), **never** a bidirectional translate-insert |
| half-id'd doubling | 2a → `adopt_id` (correspondence-gated) onto the id-less half; never doubles |
| #216 bootstrap | 2a → `mint_shared_id` candidates; 2b confirms; stage 3 stamps. `clm slides sync` becomes the proper cold-start minter (no separate `assign-ids` step) |
| "no similarity-guess" (§3.2) | preserved: pairing requires an explicit 2b **verification**, not structural similarity alone |

## 7. Cold-start pairing & the correspondence gate

**2a (pure, provider-aware).** A cold-start pair is a *candidate* only when it is
structurally alignable — equal length, equal role/cell-type sequence, and
id-less-only (no interleaved id'd cells that would move the positional anchor).
Reuse the alignment idea behind `_streams_aligned`, adapted to the cold path's
role-filtered cells. 2a branches on **provider availability** (a structural,
plan-time input):

- provider configured → emit `pending(correspondence)` pair candidates (dry-run
  shows "N pair — pending verification", exit 1);
- no provider → emit `refuse("cold-start pair; no provider to verify
  correspondence; run with a provider or `assign-ids`")` (exit 2). Dry-run and
  apply agree, because both know there is no provider.

**2b (LLM, opt-in tier — mirror `--llm-recover`).** A `CorrespondenceVerifier`
mirroring `AlignmentRecoverer` (`sync_recover.py`): a cheap model (Haiku-class),
**body-free** inputs (position / role / content-hash, like region fingerprints),
**cached** by pair fingerprint + prompt version, **validated**, **safe-abort**.
Per pair it returns, for each candidate, correspond = yes/no. A "yes" → the
`mint_shared_id` / `adopt_id` op materializes; a "no" → that pair downgrades to
`refuse`. One call per cold-start deck, cached → re-runs are free.

**Classifier stays pure.** The verifier lives in stage 2b (apply-side tier),
never in `sync_plan.py`. This honors the base design's "pure analysis — no LLM"
classifier invariant.

## 8. Module changes

| Module | Change |
|---|---|
| `sync_plan.py` | becomes Classify + Resolve-2a. Owns *all* structural dispositions, incl. the both-directions refusal (idless **and** idd) and cold-start pair candidacy — **moved out of apply**. `build_sync_plan` returns a `ResolvedPlan` whose every item carries a disposition. |
| `sync_apply.py` | **2b materialize** (the LLM calls) is split from **3 execute** (decision-free writes) for the edit + add paths: `_materialize_edits` → `_EditOutcome` (judge / code re-translate), and `_materialize_idcarrying` / `_materialize_idless` → `_TransOutcome` cache (add translations). `_apply_edit` and the two add walks then write mechanically; a model-failure is a `blocked`/error outcome decided in 2b. `_apply_adds`' both-directions guard is deleted (now in 2a, Phase 1). **Still inline (deferred):** the id-migration recoverer (`--llm-recover`) and the `sync_code` structural translate — they call models inside their own helpers; a single bundled `MaterializedPlan` object was judged not worth the churn given the add materialize is ordering-sensitive (it must run between the id-carrying and id-less walks). |
| `sync_recover.py` | gains `CorrespondenceVerifier` beside `AlignmentRecoverer` (same opt-in/cached/validated/safe-abort shape). |
| `assign_ids.py` | `assign_ids_in_split_pair` reused by `mint_shared_id` / `adopt_id` for byte-level stamping. |
| `slides_sync.py` (CLI) | dry-run renders the resolved plan (incl. `refuse`/`pending`); exit codes derive from dispositions, so `_plan_exit_code` and `_apply_exit_code` converge by construction. New `--verify-cold-pairs` (default: on when a provider is set) gates 2b correspondence. |
| watermark | stage 3 advances it only over `apply`-disposition cells; `refuse`/`conflict`/`blocked` cells hold at baseline (the existing #202 per-cell partial-advance, simplified — the held set is now explicit in the plan). |

## 9. Tests pinning this

Originally landed as `xfail(strict=True)`; **Phase 1 flipped all five to passing**
(the markers are removed and the assertions now read the refusal outcome):

- `tests/slides/test_sync_dry_run_parity.py` — parity helper
  (`_assert_dry_run_predicts_apply`); `TestColdStartRefusalParity` (was
  `…KnownBugs`) now asserts the cold-start id-less and watermark both-sides
  cases **refuse** (`N refuse`, dry=apply=exit 1, decks byte-unchanged).
- `tests/cli/test_slides_sync.py::TestDryRunApplyParity::test_dry_run_promise_matches_apply_for_parallel_idless`
  — passes at the CLI surface: dry and apply both exit 1, `refuse` shown.
- `tests/slides/test_sync_apply.py` — the two id-carrying doubling cases
  (mismatched-id, half-id'd) now assert `applied_add == 0`, `refuse == 4`, no
  duplication, no error.
- `tests/slides/test_sync_plan.py::TestBothDirectionsRefusal` — **new** unit
  cases on `classify_changes` directly: cold parallel id-less / mismatched-id /
  half-id'd all refuse; one-directional cold start and id-carrying both-directions
  *against a baseline* still **add** (the key distinctions).

The parity assertion was written to survive either fix shape (it checks "a
writing-run error must have been foreseen by the dry-run"), so it needed no edits
when the redesign landed — only the `xfail` markers came off.

**Phase 2 boundary tests** (`tests/slides/test_sync_apply.py`): the judge is invoked
exactly once per edit and the translator exactly once per add cell — both in the
materialize pass, never re-called in execute. A higher count would mean the
execute walk fell back to the model (a leak across the 2b/3 boundary).

## 10. Phased implementation

Each phase is independently shippable and flips a named subset of the xfails.

**Phase 1 — `Refuse` as a first-class disposition; move the structural guards to
2a. [DONE]** Introduced `Proposal.disposition` (`"apply"` | `"refuse"`) and a
`refuse` kind; relocated the both-directions refusal (idless **and** the
previously-unguarded idd) from `apply_plan` into the resolver (`classify_changes`
→ `_refuse_cold_both_directions` / `_refuse_idless_both_directions`). `apply_plan`
now executes a `refuse` as a deferred no-op; the old `_apply_adds` guard and the
`process_idless` plumbing are deleted. *Flipped all 5 xfails* (the main-path
id-less refusal also covers the watermark both-sides case, so it was **5 of 5**,
not the 4 first estimated). No LLM, no new minting; the watermark holds over a
refusal. Also surfaced in `render_plan` / `--json` (`counts.refuse`,
`proposals[].disposition`), the walker (a `REFUSE` action, never prompted), and
the `migration.md` info topic.

**Phase 2 — Split apply into materialize (2b) + execute (3). [DONE — scoped to
edit + add].** The edit path materializes via `_materialize_edits` → `_EditOutcome`
(`update` / `in_sync` / `blocked`); `_apply_edit` writes mechanically (boundary
test: judge called once, in materialize). The add path materializes via
`_materialize_idcarrying` / `_materialize_idless` → a `_TransOutcome` cache keyed by
source-cell id; the two add walks read the cache through `_translate`, with a
model fallback for a cache miss that never fires because the materialize walks
enumerate a **superset** of what execute translates (boundary test: translator
called once per cell). Behavior-preserving (1106 sync tests green). **Deferred
follow-ups (call models inline still):** the id-migration recoverer and the
`sync_code` structural translate; and a single bundled `MaterializedPlan` object
(the two materialize seams suffice, and the add seam is ordering-sensitive).

**Phase 3 — Cold-start minting + correspondence gate.** `mint_shared_id` /
`adopt_id` candidates in 2a (provider-aware); `CorrespondenceVerifier` in 2b;
delegate stamping to `assign_ids_in_split_pair`. *Flips:* the remaining #216
bootstrap xfails. Unblocks the **1.8 PythonCourses gate** (~200 id-less split
halves; see `#158`).

## 11. Open questions / deferred

- **`--dry-run --with-content`** (run 2b in preview for an exact diff) — deferred;
  the structural-faithful default covers the reported pain.
- **Serializable resolved plan** (`--plan-only` → file → `--apply-plan FILE`) —
  not required; in-memory resolve→materialize→apply plus the existing
  translation/alignment caches already give replay-cheapness.
- **Correspondence on code cells** — out of scope: localized code is matched by
  id, neutral code is byte-identical, id-less code rides the structural pass.
  The gate targets id-less *narrative markdown* only.
- **Mismatched-id pairs with a provider** — Phase 3 may offer to *reconcile*
  (pick a source-lang, adopt one side's ids) instead of only refusing; start with
  refuse, revisit with pilot data.
