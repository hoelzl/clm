# Kickoff: honest architecture & design review of the `clm slides sync` engine

> **Paste this whole document to a fresh model instance as its opening prompt.** It is self-contained: it tells you what to decide, where everything lives, what the recurring failure looks like, how to investigate, and exactly what to deliver. Read it once end-to-end before touching the code. Where it states a fact, **verify it against the code as it is now** — the prompt may have drifted from the tree.

---

## 0. Your mission — the one question

`clm slides sync` reconciles a split bilingual deck pair (`<deck>.de.py` / `<deck>.en.py`) when an author edits only one half. Over its lifetime it has shipped a series of fixes that the maintainer groups as one recurring class of correctness bug. The maintainer's request, verbatim:

> *"I would like an honest assessment whether the chosen design is sound, and if so what we can do to improve it, or whether the basic approach the sync engine takes to the problem it is trying to solve is flawed and the sync engine needs to be replaced as a whole."*

Your deliverable is a **verdict with evidence**, in one of three buckets:

- **(A) Sound** — the architecture is fundamentally right; the bug series is normal hardening of a correct design. → Then: what concretely improves it?
- **(B) Sound core, wrong in places** — the spine is salvageable, but one or more load-bearing decisions cause the recurring bugs and should be replaced *in situ*. → Then: which decisions, replaced with what, at what cost?
- **(C) Flawed approach** — the basic strategy cannot converge; it should be redesigned/replaced wholesale. → Then: replaced with what, what does it cost, and what is *lost*?

**(A) and (C) are equally acceptable outcomes if argued to the same evidentiary bar.** The one failure mode is a hedged verdict that won't commit. Anti-bias rules — which you must follow — are in §6; the most important is that **two opposite biases are equally likely and equally fatal here**, so read this now:

> 1. **Endowment / sunk-cost bias toward (A):** ~8k lines of engine code, four design docs, and three prior redesigns can make a flawed design *look* load-bearing and inevitable.
> 2. **Novelty / iconoclasm bias toward (C):** a long bug list and an explicit invitation to "replace it" can make a *converging* design look doomed, and a blank-slate redesign always looks cleaner than a battle-tested one (because its bugs aren't discovered yet).
>
> Neither the bug count nor the code volume is evidence by itself. The maintainer's suspicion that the design may be flawed is the **prior under review, not a finding** — the existence of this review is not confirmation of its hypothesis. Whichever verdict you reach, show that you actively resisted its corresponding bias.

**B-vs-C tie-breaker:** choose **(C)** only if *no* in-situ replacement of a single load-bearing choice (§4) meaningfully shrinks the failure space; if replacing one choice does, that is **(B)**.

---

## 1. What the engine is meant to do (orientation)

A trainer authors bilingual decks as two split files per topic, e.g. `web_apis.de.py` / `web_apis.en.py` — Jupyter "percent format" (cells delimited by `# %%` headers; a leading Jinja2 `# {{ header }}` macro block). **The promise:** edit one half with full IDE/Copilot support, run `clm slides sync`, and the other half is reconciled — edits propagate, new slides are translated and inserted, removed slides drop, reorders mirror, and a shared cross-language `slide_id` is minted onto *both* halves — **without the author ever hand-managing ids or editing the mirror.**

The approach, in layers:
- A **pure structural classifier** diffs each half against a self-managed baseline and emits a typed **plan** of proposals: `add / rename / edit / move / remove / conflict / retag / mint / adopt / reconcile / refuse`.
- An **LLM layer** confined to translating new-slide content, judging how to propagate an edit, verifying cross-language correspondence (cold-start), and recovering ambiguous id alignments.
- An **apply stage** that writes both halves atomically to the working tree (**the `git diff` is the review surface**; sync never auto-commits).

The cross-language join key is `slide_id`, invariant **`de_id == en_id`**. **Identity for cells without a hand-written id is *derived* from content, not stored in the file.** The baseline is a self-managed DB **watermark** (it stores content *hashes*, not base bodies); **git HEAD** is a cold-start fallback. Run `clm info commands` for the user-facing surface.

---

## 2. Where everything lives (read these — don't rediscover)

Paths are under the worktree root (§8). The sync-owned source is `src/clm/slides/sync_*.py` (≈7.8k LOC) plus the CLI command; the rest is shared CLM. **LOC are approximate.**

### Code
| File | ~LOC | Responsibility |
|---|---|---|
| `src/clm/slides/sync_plan.py` | 2680 | **Phase-1 classifier (a churn epicentre).** `build_sync_plan` resolves the baseline (watermark > git-HEAD > none) and runs the keyed diff + every neutral/id-less/header/move detector + cold-start mint/adopt candidacy. Defines `BaselineCell`/`CurrentCell`, `Proposal`, `PlanIssue`, `TagHold`, `SyncPlan`, the renderers. Key spots: `align_anchored` :514, `classify_changes` :1238, `_pair_is_unbootstrapped` :2054, `build_sync_plan` :2086. |
| `src/clm/slides/sync_apply.py` | 3045 | **Phase-2 apply engine (the other churn epicentre; the convergence hub).** `apply_plan` runs per-kind appliers → structural pass → fail-safes → buffered atomic temp-swap → watermark advance. Key spots: `_flag_shared_cell_divergence` :666, `_flag_idless_localized_divergence` :748, `_reconcile_group_order` :2066, `_record_watermark(_partial)` :2810/:2939. |
| `src/clm/slides/sync_code.py` | 500 | **Structural pass** (`apply_code_structure`): rebuilds the cell *order* of each changed slide group from the source half — neutral cells verbatim, id-less localized cells translated. Owns the cells the `(slide_id, role)` walk can't reach. **Never reorders groups** (group misplacement is fixed elsewhere). |
| `src/clm/slides/sync_writeback.py` | 490 | Byte-faithful write primitives + the **keystone predicate `role_of()`** + `anchor_of`/`construct_of`/`cell_content_hash`. `FileState` = mutable in-memory deck. |
| `src/clm/slides/sync_recover.py` | 710 | Bounded-LLM tiers: `AlignmentRecoverer` (`--llm-recover`) and `CorrespondenceVerifier` (cold-pair/reconcile, Haiku, cached, default-on). Validators that never drop a worn id / never mint a spurious one. |
| `src/clm/slides/sync_translate.py` | 395 | `SlideTranslator` + OpenRouter impl + caching. Note `temperature = 0.2` (:119) — re-runs are stable *only* via cache. |
| `src/clm/slides/sync_plan_walker.py` | 410 | `--interactive` walker; collects decisions then calls `apply_plan` once. |
| `src/clm/cli/commands/slides_sync.py` | 1350 | The Click command: pair resolution (incl. #162 pairing guard), provider/judge/translator/verifier wiring, batch sweep, exit-code policy. |
| `src/clm/notebooks/slide_parser.py`, `src/clm/slides/raw_cells.py` | — | The shared `Cell`/`CellMetadata` model and the lossless `RawCell` round-trip `FileState` relies on. |

**Seam surface (sync does not live in a vacuum — see §5):** `src/clm/slides/split.py` + `unify` (byte-faithful round-trip that cold-mint depends on), `assign_ids.py` (twin-aware id minting; the generative `assign_ids_in_split_pair`), the voiceover/notes companion tooling (companions keyed by `for_slide == slide_id`), the validator/pre-commit gate, and the build merge (the consumer).

**The 12-step apply pipeline** (trace it in `apply_plan`): (1) build plan; (2) whole-plan mint/adopt/reconcile short-circuit *before any file load*; (3) materialize edits; (4) per-cell walk (remove/edit/retag/collect-moves/conflict/refuse); (5) adds/renames (translate+insert, mint ids); (6) moves (group reorder, gated on a clean pass); (7) drifted-id migration (+ optional `--llm-recover`); (8) structural pass; (9) `_reconcile_group_order` (gated on a clean pass); (10) parity fail-safes (most run only on an otherwise-clean pass); (11) buffered atomic flush (writes only if no errors); (12) watermark advance (full / per-cell partial / held). **Watch the gating conditions** — several bugs were "a step mutated the deck on a pass that should have held." **LLM touchpoints span the pipeline:** edit-judge at step 3, add/structural translate at steps 5/8, cold-start correspondence verify at step 2, `--llm-recover` at step 7 — map all four for §5.

### Design & history docs (read all four; second-guess each evenly)
- `docs/claude/design/single-language-authoring-sync.md` — the **#166** foundational design (this engine is itself a from-scratch replacement of a deleted v1 `sync.py` pair-walker). Watermark-not-git baseline, id-less-as-new, per-cell direction, EN-authority minting, write-to-tree review.
- `docs/claude/design/sync-content-anchor-identity.md` — the **#190** *additive re-architecture*: identity moves to DB-resident **content anchors** (`hand-id > construct-name > sha256`), never written into id-less files; adds the anchor-keyed diff pass `align_anchored`. (States the ~13k-id-churn + unify-hazard reasons for *not* writing physical ids — pressure-test whether those reasons still hold.)
- `docs/claude/design/sync-plan-resolve-apply.md` + `docs/claude/sync-plan-resolve-apply-handover.md` — the **resolve-then-apply redesign** (`Classify[pure] → Resolve-2a[pure] → Materialize-2b[LLM] → Apply-3[mechanical]`), motivated by *"apply_plan is a second decision-maker, not an executor."* Introduces a dry-run faithfulness contract. **It is on a separate, un-merged branch — review it by diff (§8), it is not your checked-out tree.** Weigh whether the split actually *removes* the second decision-maker or *relocates* it (its Phase 2 still calls models inline).
- `docs/claude/design/split-voiceover-hardening.md` — a 2026-06-02 investigation concluding the sync *core* is safety-conscious and the brittleness is edge-located; deliberately **not** a rewrite proposal. Test whether that conclusion survives #269/#281/#282 — and equally, whether the later fixes vindicate it.

### Tests that matter most for the verdict
- `tests/slides/test_sync_issue_269.py` (~1320) — the **richest behavioral matrix** and the **only file parametrized over both baselines** (`git-head`, `watermark`). Hands you your oracle: `_falsely_consistent(plan, result, propagated)` :148 (the forbidden state) and `_alerted(plan, result)` :139, plus the repro harness `_sync(...)` :103. The whole #282 family lives here.
- `tests/slides/test_sync_dry_run_parity.py` (~270) — the faithfulness contract (dry-run predicts apply).
- `tests/slides/test_sync_corpus_noop.py` (~130) — the corpus backstop. **It exercises 212 pairs but only asserts the 81 already-clean pairs don't churn** (`_PHASE0_NOOP_PAIRS = 81`) — a *churn* backstop, **not a correct-propagation oracle.** Don't over-credit it.

The remaining sync tests (`test_sync_apply.py` ~2380, `test_sync_plan.py` ~930, `test_sync_tag_sync.py` ~960, `test_sync_limitations.py` ~1030, `test_sync_code_cells.py`, `test_sync_code_e2e.py` live-CLI golden, `test_sync_plan_walker.py`, and pure units `test_sync_anchor/recover/correspondence/translate_prompts.py`) plus the **known coverage gaps** are catalogued as *input to the matrix you build in §8* — enumerate them there rather than here. (Headline gaps to confirm: baseline-parametrization exists in essentially one file, so the git-HEAD column of the matrix is largely untested; j2-header is the thinnest class; id-less-localized *remove* has no dedicated test; the engine is never exercised on a non-Python deck; propagation-direction coverage skews to EN-half edits; there is no shared cross-file harness.)

---

## 3. The bug history — the evidence you must interpret

Reconstruct and **re-tally it yourself** from `CHANGELOG.md`, `git log --oneline -- src/clm/slides/sync_*.py` (~40 commits), and the MEMORY note (path in §8). The maintainer's count is "roughly a dozen," but several entries are sub-parts of one fix series — derive your own number. The cluster, in order: **#166** (engine v2), **#162** (divergent ids break the join key — the keystone invariant), **#190** (code-only/neutral changes not propagated; non-atomic apply), **#198/#200/#201/#202** (tag-only edits invisible to a body hash), **#216/#225/#226/#228** (cold-start id-less doubling; dry-run/apply divergence; mismatched-id pairs), **#269** (one-sided neutral / id-less / header edits dropped while the run reported "consistent" and advanced the watermark over the loss), **#281** (new-group misplacement), **#282** (a group reorder on one half + a concurrent neutral/id-less edit on the other → dropped, or in the ≥2-cell case auto-healed over the edit with only a warning), **#285** (open: id-less tag-only retag dropped under a move).

**The recurring defect, stated once.** A specific point in the space

> **{change-type} × {cell-class} × {baseline-mode} × {id-state}**

was unhandled, and instead of erroring, the engine **propagated nothing (or doubled the cell) while reporting "decks already consistent" and advancing the watermark over the unpropagated change** — so the divergence persisted across later syncs. The axes (this is the canonical statement; later sections refer back to it):
- **change-type** = {edit, add, remove, move/reorder, retag, id-migration}
- **cell-class** = {narrative-md (id'd / id-less), aux-md, localized-code (id'd / id-less), language-neutral *shared* cell, j2 header}
- **baseline-mode** = {watermark, git-HEAD cold-start, none}
- **id-state** = {both id-less, half-id'd, mismatched-id, shared id}

Several fixes (**#226**, **#282**) were *re-fixes* where a new combination slipped **between two guards that each partitioned the space along a different axis** (one by id-presence, one by direction, one by baseline-mode). Underneath sits a **second-order** mechanism: the id-less/neutral drift detectors — `align_anchored` (`sync_plan.py:514`) and `_classify_idless_localized_drift` — **pair cells by position**, and a one-sided group reorder *permutes* that sequence, so "reorder vs. anything" keeps producing mis-reads. (Per the maintainer's own classification, recorded in code/MEMORY: #282's fix is a *precondition-detect-and-alert guard* rather than a correct merge, and #285 is open. **Treat those labels as claims to pressure-test, not settled facts.**)

**The maintainer has tried to get ahead of this three times:** (1) #166 replaced the v1 engine wholesale; (2) #190 re-based identity on content anchors; (3) the resolve-then-apply redesign re-architected the plan/apply boundary, plus post-apply **parity fail-safes** (`_flag_*`) and the corpus no-op backstop.

**This is the central evidence, and it genuinely cuts both ways — you must extract which, not assume:**
- **Reading 1 (converging):** each redesign hit a real root cause; if the inter-bug interval is lengthening and recent fixes are root fixes, this is a normal hardening asymptote on a hard problem → toward **(A)/(B)**.
- **Reading 2 (mis-fit):** three redesigns in, the *same class* still recurs and the latest fix is a guard with an open residual → the signature of a strategy fighting the problem → toward **(C)**.

Calibrate against a **base rate**: nontrivial bidirectional merge/sync engines are hard and ship many edge-case bugs over their life — is this count high, normal, or low for ~8k LOC of merge logic? "Lots of bugs" is not by itself a verdict.

---

## 4. The crux, and the load-bearing choices

Reduce the whole review to one question:

> **Is the §3 matrix ({change-type × cell-class × baseline-mode × id-state}) *intrinsic to the problem* — any correct sync of two independently-editable, translated, reorderable cell streams must handle it — or an *artifact of this design's load-bearing choices*?**

- If **intrinsic**, the design is about as good as the problem allows; improvement means *systematizing* coverage (making the matrix explicit and exhaustively tested) → **(A)/(B)**.
- If **artifact**, a different spine **might collapse part of the matrix — and might introduce a new failure space of its own** → deep **(B)** or **(C)**. Name the spine *and* its new failure modes; an un-built alternative has undiscovered bugs.

Answer it by interrogating each **load-bearing choice** below. For **each**, produce a fixed 3-line block: **(a)** does it *create* matrix cells, or merely *expose* cells inherent to the problem? (name the specific cells); **(b)** is it *essential to the promise in §1* or an *implementation convenience*? **(c)** which §7 alternative would erase those cells (or "none"), and at what cost to the promise? Ask both "does removing it help?" *and* "does removing it cost a promised property or just move the complexity elsewhere?"

0. **The problem framing itself.** Is "two independently-editable translated halves" the real requirement, or a self-imposed generality? How often is *more than one* half edited between two syncs (ground this in `scripts/edit_dynamics_harness.py` and the corpus)? If near-never, a one-authoritative-direction or single-source-projection model (alt v) may shrink the matrix by *not posing the harder problem* — decide whether bidirectionality is essential or assumed.
1. **Derived, file-absent identity** (content anchors `hand-id > construct > sha256`) vs. a stable id physically written to every cell. Does this *create* the id-less/half-id'd/mismatched id-states, or do those exist in any design that lets one half be edited first? Weigh the #190 doc's stated cost of physical ids (≈13k stamps + unify hazard + authoring noise) against the cells it would erase — and decide whether that cost was correctly weighted.
2. **Positional pairing** of id-less/neutral cells (and `_shared_hashes` ordered-sequence comparison) — which #282 found mishandled under reorder. Verify whether that is positional pairing *per se* being unsound, or a fixable gap in it. Is there a position-independent identity for these cells that doesn't reintroduce the duplicate-boilerplate collapse the design deliberately avoids?
3. **Two-baseline duality** (self-managed watermark *and* git-HEAD fallback), where the watermark stores only *hashes*, not base bodies — so there is **no true 3-way merge base** and `--interactive` can only show 2-up. Would a stored base snapshot collapse the conflict/divergence/reorder cases into standard 3-way merge? (Verify the "hashes not bodies" premise: `SyncWatermarkCache`, columns `slide_id / construct / content_hash`.)
4. **Apply as a (former) second decision-maker.** The redesign exists because apply re-decided structure. Reviewing it by diff (§8), decide whether the resolve-then-apply split is *complete and sufficient*, or still partial (its Phase 2 keeps id-migration recovery and structural translate inline).
5. **The `role_of()` + `lang != expected` double gate.** "Stamping an id on a neutral cell doesn't make the engine see it." Is cell-class-as-derived-predicate the right model, or should cell role be explicit/stored?
6. **LLM in the correctness path** (four tiers: translate, edit-judge, correspondence-verify, alignment-recover). Map exactly where a model decision affects *structure* and whether each point safe-aborts — and carry this into the operational lens in §5.

---

## 5. The scoring lenses (one analysis, not a separate checklist)

**§4, §5, and §7 are three views of one analysis — do not answer them three times.** Apply these lenses to the design *as a whole* and, where relevant, per load-bearing choice. For each, **show evidence** (file/line/test cite, or a repro you ran), and **mark each claim** verified-by-reading / verified-by-running / inferred / speculative.

1. **Correctness ceiling — a spectrum, not a binary.** Where does the design sit? **(a)** the bug class is *structurally inexpressible* (an invariant cannot be violated); **(b)** *prevented by construction* in the common path and *always surfaced* (alert/defer, never silently dropped) in the residual; **(c)** only *detected post-hoc* by the `_flag_*` fail-safes; **(d)** can still slip silently. **(a) may be unattainable for this problem** — if so, **(b) with a sound never-silently-drop guarantee is a legitimate ceiling, not a consolation prize.** Judge whether the design hits its own achievable ceiling, *and* whether that ceiling is good enough.
2. **Failure severity & detectability** (weight the verdict by severity × detectability × frequency — *not by bug count*). For the recurring drop/double class: is the loss visible in the **git diff before commit** (the design's own review surface), or only post-commit? Recoverable (re-sync, git history, companion) or destructive (the #282 ≥2-cell auto-heal)? Detection latency — dry-run / next sync / build / student-facing output / never? Victim — the author (self-inflicted, recoverable) or a downstream student (ships wrong content)? A high-count class of diff-visible, re-syncable, self-inflicted drops is a *different* problem from a low-count destructive overwrite.
3. **Occurrence / frequency.** The matrix is a Cartesian product; not all cells occur. Classify each cell common / occasional / rare / structurally-impossible under real authoring, grounded in `scripts/edit_dynamics_harness.py` and the corpus shape. Is the engine **under-engineered for common cells, over-engineered for rare ones, or matched?** Without this, "over-engineered for cases no author hits" is unfalsifiable.
4. **Coverage completeness.** Build the §3 matrix (format in §8); mark each cell `OK / FAILSAFE / UNTESTED / BROKEN / N/A`. Is coverage *enumerated and enforced* or *discovered bug-by-bug*? Is the matrix finite and closed, or open-ended? Define what *correct* propagation is for the cells you mark `OK` (handled ≠ handled-correctly).
5. **Convergence evidence.** Plot fixes over time; is the inter-bug interval lengthening or steady? Is each fix a root-cause fix or a guard (pressure-test the #282-guard / #285-open labels)?
6. **LLM operational soundness** (the four tiers are a first-class design surface, not a footnote): nondeterminism (`temperature=0.2`; is the cache the *only* determinism guarantee, and what happens on a mid-corpus cache miss?); cost (is there *any* budget/ceiling? estimate a cold-cache 200-deck batch); latency at scale; prompt-version/cache coupling (does a prompt edit silently re-spend the whole corpus?); the **degraded/no-provider matrix per tier** (confirm each safe-aborts vs. silently degrades). Does your chosen verdict's alternative shrink or grow this surface?
7. **Cross-command seams.** Sync is one node in a coupled graph. Evaluate: the split/unify **byte-faithful round-trip** that cold-mint depends on; the `slide_id == for_slide` **join key** shared with voiceover/notes companions; `assign-ids` / `normalize` / the pre-commit gate, which mint or enforce the same identity. Several historical "sync bugs" are seam bugs. For each §7 alternative, list which seams it changes and what new cross-command failure it could introduce — a design that fixes sync but breaks unify or orphans voiceover is not an improvement.
8. **Maintainability / blast radius.** Two files (~2.7k + ~3k) absorbed almost every fix. Can a competent engineer add a new change-type or cell-class *without* risking a new silent drop? Count the implicit cross-module contracts that must stay in agreement (e.g. `role_of` reused across ≥3 passes; `provider_available` must match in plan and apply; watermark partition positions must stay lock-step).
9. **Faithfulness contract.** Evaluate the redesign's invariant (dry-run = stages 1+2a, byte-identical structural verdict to apply) on its own terms — achieved? tested (`test_sync_dry_run_parity.py`)? *Sufficient* to prevent the bug class, or necessary-but-not-sufficient?
10. **Testability, oracle & observability.** Is there a positive *correct-propagation* oracle (not just the no-op backstop)? Is property/fuzz testing over the matrix feasible given the DB-watermark statefulness? When sync misbehaves in production, what is the forensic trail (`--explain`, plan JSON, watermark+cache state) — and can a bug even be *reproduced* given statefulness + LLM nondeterminism (a cache now holding a different verdict)?

---

## 6. Anti-bias rules (canonical — follow them)

- **Sunk cost is not evidence**, and **neither is novelty.** Code volume argues for the *cost* of replacement, not the *soundness* of the design; a long bug list argues for *scrutiny*, not automatically for replacement.
- **Argue the opposite first.** Before writing your verdict, write the strongest version of the case *against* it. The deliverable has a named slot for this (§9).
- **Separate "hard problem" from "wrong design."** Distinguish "this would bite *any* design" from "this bites *this* design specifically."
- **Price both verdicts' downside.** If (A)/(B): honestly estimate the cost of *continued recurrence* (expected future silent-drop incidents, corpus-corruption risk). If (C): estimate replacement size and what safety properties are lost. Neither verdict is free.
- **The un-built-alternative fallacy.** Compare *current-design-with-known-bugs* vs. *alternative-with-its-undiscovered-bugs-estimated* — never vs. *alternative-assumed-perfect*.
- **Respect the constraints the design is under.** The promise (single-language authoring, never hand-manage ids, git-diff-as-review, percent-format files, no `<<<<<<` markers, byte-faithful round-trip, Windows-first) is real. A "cleaner" design that breaks the promise is not a valid alternative — say so if yours does.
- **Distinguish evidence from speculation** (the four tags in §5). A replacement recommendation built on speculation is not actionable.

**What exonerating evidence looks like** (so (A) has a concrete target, not just (C)): a *documented, determined* failure to break ~6 adjacent matrix cells (all correctly handled or safely alerted); a lengthening inter-bug interval; zero fail-safe escapes on the corpus; a maintainability story where adding a cell-class is mechanical. **What damning evidence looks like:** a fresh cell #283+ that still silently drops on the current branch; a fix that regressed; a load-bearing choice that demonstrably manufactures a class of cells a named alternative would not have. Report your **hit rate, not just your hits** — a clean 6/6 probe is strong evidence *for* (A).

---

## 7. Alternatives to weigh (seed list — add your own)

Engage each concretely; run it against the §3 matrix *and* the §5 seam/severity/LLM lenses *and* the §1 promise. For each, produce a row: **[cells eliminated | cells still faced | new failure modes | breaks which promise clause?]**.

- **(i) Harden the current design** (finish resolve-then-apply; make the matrix explicit and exhaustively tested; keep `_flag_*` as a net). *Which cells does enumeration + fail-safes actually close, and what residual can the net only catch post-hoc?*
- **(ii) Stable physical ids on every cell.** `assign-ids` stamps a shared id on every cell of both halves; sync becomes a near-trivial keyed merge. *Is the #190 doc's id-churn + unify-hazard + authoring-noise cost actually fatal, or was it over-weighted? What seams does a 13k-stamp migration touch?*
- **(iii) True 3-way merge with a stored base.** Persist the last-synced base *bodies* (not just hashes) → every reconcile is base/ours/theirs, enabling real 3-up conflict UI. *Storage cost; does translation break the common-base assumption across languages?*
- **(iv) Structured/AST or line-level merge** over the percent-format text. *Does it fit Jupyter percent-format and the byte-faithful round-trip?*
- **(v) Reframe the problem:** the two halves are *projections of one source* (single-source bilingual authoring) → sync becomes a render, not a merge. *Take it seriously even if you are inclined to keep the current design; reject it on evidence, not reflex.*

---

## 8. How to do the work (order of operations + scaffolding)

1. **Trace one cell of each class** (localized-keyed code, neutral shared code, id-less localized code, j2 header) through all 12 pipeline steps. Output a numbered trace per class: `step N: <predicate/function>:<file:line> — <what it decides (identity/direction/membership) or "skips">`. Done when all 12 steps have a line.
2. **Build the coverage matrix.** The full product is ~360 cells — **do not enumerate all.** Collapse to a 2-D table: **rows = the 6 change-types, columns = the 5 cell-classes**; each entry is `status | worst baseline×id-state combo | evidence`, status ∈ `OK / FAILSAFE / UNTESTED / BROKEN / N/A`. Worked example row-cell:
   `move × neutral-shared-code → FAILSAFE | watermark, both-id-less → _flag_shared_cell_divergence (sync_apply.py:666); #282 | only caught post-apply`.
   Below the table, list every `UNTESTED`/`BROKEN` cell.
3. **Reproduce, don't trust.** The repro harness is the helper `_sync(...)` at `tests/slides/test_sync_issue_269.py:103` — copy it into a scratch test (it needs only `StaticSlideTranslator`, `judge=None`, and deck strings; **no API key**). The pass/fail oracle is `_falsely_consistent(plan, result, propagated)` at :148 (returns `plan.is_noop and not propagated and not _alerted(...)`). First confirm the env: `uv run pytest tests/slides/test_sync_issue_269.py -p no:xdist -q` (should pass). Run scratch repros with `uv run pytest <scratch>.py -p no:xdist -s`.
   - Reconfirm 2–3 *historical* bugs are fixed (#269 neutral-edit drop; #282 reorder-vs-edit; #216 cold-start doubling — gated by `_pair_is_unbootstrapped`, `sync_plan.py:2054`). If a "fixed" bug still drops, that is a major finding (record it under §9.6).
   - **Then time-box to ~6 targeted probes** of `UNTESTED`/`FAILSAFE` cells, biased toward the git-HEAD column (largely untested). For each, assert `not _falsely_consistent(...)`. **Report all 6 verdicts (dropped / alerted / propagated) even if clean** — a clean 6/6 is evidence of closure; one silent drop outranks all other evidence. Don't exhaustively sweep the matrix by repro.
4. **Evaluate the redesign by diff** (do *not* check it out): `git fetch`, then `git diff claude/issue-282-move-edit-conflict..origin/claude/sync-plan-resolve-apply-redesign -- src/clm/slides/` and `git show origin/claude/sync-plan-resolve-apply-redesign:docs/claude/design/sync-plan-resolve-apply.md`. Decide whether the faithfulness contract *prevents* the bug class or just aligns dry-run with apply, and whether its inline-model Phase 2 is a hole.
5. **Sketch ≥2 alternatives** (§7) and produce their comparison rows. Engineer-weeks *and* "matrix cells the replacement must re-prove" are the size units; risk = a one-line live-corpus worst-case.
6. **For a (C) verdict, specify the migration RUN, not just the end state:** do the ~200 live decks already satisfy the new invariant or must they be reconciled first? Is the migration a mass mutation (e.g. ~13k id stamps) — what guards it, since a bug there corrupts the whole corpus at once? Is it staged/reversible/dry-runnable? What is the gate-flip rollout (hard-error-first vs. warn-then-ramp)?

### Environment & constraints
- **Read-only review.** Do **not** fix, refactor, or commit. Repros go in a throwaway scratch file you don't commit. Your output is the assessment document, nothing else.
- **Worktree:** `C:\Users\tc\Programming\Python\Projects\clm\.claude\worktrees\elegant-juggling-truffle` (Windows; PowerShell + Bash). Current branch `claude/issue-282-move-edit-conflict`, tip `01e99875` — its message reads `(#282)` (that is the *issue*; **#284 is the PR number** that merged it to master). The #282 fix is therefore present. `git fetch` and confirm the tip before trusting any "current code" claim. **Do not switch any worktree to `master`** or to the redesign branch (review the latter by diff, step 4).
- **Run via `uv run`** so imports resolve to the worktree source (a repo-root `.venv` would shadow `clm` with the main-repo copy). Under load cap xdist: `PYTEST_XDIST_AUTO_NUM_WORKERS=4`. Interpreter is the worktree `.venv` (py3.13). If a diff shows cell-metadata *set-ordering* churn unrelated to your edit, that's known jupytext nondeterminism, **not** a sync drop.
- The corpus harness (`test_sync_corpus_noop.py`) is slow/integration and **skips when the PythonCourses corpus is absent** — the verdict must be reachable from reading + structural repros alone; note if you couldn't run it.
- **MEMORY note** (read-only context, outside the worktree): `C:/Users/tc/.claude/projects/c--Users-tc-Programming-Python-Projects-clm/memory/project_issue_269_sync_drops.md`. If unavailable, CHANGELOG + git log suffice.
- **Trust the code over the docs** when they disagree (the docs occasionally lead or lag) — and **record each drift** you find (§9.6). One to expect: the resolve-apply doc says "Phase 2 DONE (scoped)" while two model calls remain inline.

---

## 9. Deliverable

A single markdown assessment. **Target ~3,500–6,000 words of prose (≈6–10 pages) plus the two tables (coverage matrix, alternatives), which don't count toward the budget.** If a §5 lens needs more than ~250 words you're explaining the code, not judging it — cut. Structure:

1. **Verdict + crux answer — one page, readable standalone, the first line of the document.** The A/B/C call and the intrinsic-vs-artifact judgement. A maintainer who reads only page 1 has the decision.
2. **Evidence** — the §5 lenses scored (one tight paragraph each, with cites); the filled §3 coverage matrix; the result of your repro probes (the 6-probe table — did you find a fresh silent drop?).
3. **Alternatives** — the comparison table from §7.
4. **The case against your verdict** — the steelman you wrote first, and why it loses.
5. **Recommendation** —
   - If **A/B**: a prioritized, concrete improvement list (what, where, why it closes a *class* of bug not an instance), what to do about open #285 and the test gaps, and the priced cost of *not* doing it.
   - If **C**: the replacement architecture (a §7 option or your own), the migration RUN plan (§8.6), a size/risk estimate (engineer-weeks + matrix cells to re-prove), and an explicit list of the current design's safety properties the replacement **must preserve** (atomic flush, never-drop-a-worn-id, propagate-or-alert intent, the corpus backstop).
6. **Confidence, unknowns & drift** — what you couldn't verify; any doc/code drift found; any "fixed" bug that regressed; what would change your verdict.

Be specific and falsifiable. Not "refactor for clarity" — but "`align_anchored` (`sync_plan.py:514`) compares neutral cells by position and cannot distinguish a reorder from an edit, which is why #282 recurred; here is cell #283 that still breaks: `<repro>`."

---

## 10. Closing

Lead with your answer to the sharp version of the question: **does this architecture either *prevent* the recurring bug class or *guarantee it is always surfaced* (never silently dropped) — and is that the best ceiling this problem allows?** A hedged verdict is the only failure mode. (Anti-bias rules: §6.)
