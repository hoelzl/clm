# `clm slides sync` — Architecture & Design Assessment 2

**Date**: 2026-07-01 · **Tree reviewed**: `3980f9ab` (master tip; #501 Phase 1
present, pre-#515)
**Reviewer**: Claude (Fable 5), at the maintainer's request
**Method**: multi-agent review — ten parallel readers (full reads of
`sync_plan.py`/`sync_apply.py` and the identity/write-back/CLI modules, the
design-doc arc, the prior assessment, the ~24k-LOC test suite, and the GitHub
issue history via `gh`), then four independent critics (salvage advocate,
replacement advocate, neutral abstraction analyst, agent-ergonomics analyst)
arguing over the pooled evidence with claims re-verified in the tree. Evidence
slices: `docs/claude/analysis/sync-engine-assessment-2/`.
**Predecessor**: `sync-engine-architecture-assessment.md` (2026-06-09, verdict
B "sound core — keep the engine"). This assessment re-answers the same two
questions three weeks later, with the dogfooding record and the post-remediation
bug arc as new evidence.

The maintainer's questions: **(1)** can the current design be salvaged into a
solid sync/reconciliation engine? **(2)** are the abstractions correct — the
cell referencing/alignment feels ad-hoc; would a more systematic approach
resolve the issues?

---

## 1. Verdict

**(1) Partially salvageable, with a sharp boundary.** The *perimeter* is sound,
verified, and must be kept: the parsers, the `split`/`unify` lens with its
Hypothesis-pinned round-trip law, the atomic write machinery
(`atomic_write_all`, buffered temp-swap, error-gated flush), the `verify`
structural gate, the validated LLM task framing (`validate_alignment`'s hard
gates, body-free recovery, the accept-path smuggling guards), the translation
prompts, the committed ledger *concept*, and above all the behavioral test
oracles (no-op ⇒ zero bytes + zero LLM; propagate-or-alert; dry-run/apply
parity; the corpus mutation oracle). That is roughly 4–5k lines of hard-won,
probe-verified engineering. The *core* — the ~9.2k lines of
`sync_plan.py` + `sync_apply.py` that hold the classifier channels, the
identity layer, and the watermark lifecycle — is **not salvageable by further
patching**, because its defects are generative: each fix is structurally forced
to add another identity scheme, another hand-enumerated drift detector, or
another state store. That is empirically what the last five weeks did.

**(2) No — the abstractions are wrong in three identifiable places**, and the
maintainer's instinct that cell referencing/alignment is ad-hoc is precise. It
is ad-hoc *by accretion*, not by carelessness: every mechanism is individually
careful, documented, and guarded; what is missing is a single answer to "which
cell is this?". A cell's identity **regime** is selected by incidental metadata
(does it carry a `slide_id`? a `lang` attr? a narrative tag? which file does it
live in?), and the bug history concentrates almost entirely at the seams
*between* regimes, not inside them.

**Recommendation:** neither "keep patching" nor a greenfield v3. Replace the
identity + baseline + channel core *inside* the existing perimeter, gated by
the existing black-box oracles, per the companion design note
**`docs/claude/design/sync-total-identity-document-model.md`**. The salvage and
replacement critics — argued independently and adversarially — converged on
this same program from opposite directions; they differ only in what they call
it.

### Relationship to the 2026-06-09 verdict

This is not a repudiation of assessment 1; it is assessment 1's own framework
producing a different answer on new evidence. Assessment 1 attached three
explicit flip-to-replace conditions (its §4). Two have since fired, and the
post-remediation failures landed *outside the assessment's own model*:

- **Condition (i)** — "a body/structure-channel silent drop surviving the
  parity fail-safes on an otherwise-clean pass" — was met by **#443**: a
  one-sided edit/removal on an asymmetrically-id'd voiceover pair (id-less DE /
  id'd EN) reported "decks already consistent" and advanced the watermark. No
  fail-safe caught it, and the P0 channel-coverage meta-test *structurally
  could not*: it enumerates watermark **channels**, while #443 was a
  **routing** gap between the keyed diff and the anchor-diversion path, inside
  an already-covered channel. The trigger cell class (id'd on one half, id-less
  on the other) has **no column in assessment 1's coverage matrix** — the
  matrix assumes id-state is symmetric across halves.
- **Condition (iii)** — "field evidence that alert frequency makes the tool
  unusable" — was met by the 2026-06-23 dogfooding (§4 below): ~96% of flagged
  items on the one real production reconcile were false positives.
- **#501** (separated voiceover companions invisible to every sync verb) falls
  entirely outside assessment 1's evidence base: it reviewed a 2-file engine
  while the product is a 4-file problem; its matrix has no axis for companion
  files.

The failure mode has therefore moved from "uncovered **cell** in the matrix"
(fixable by enumeration, assessment 1's thesis) to "**axis the matrix does not
have**" (#443: cross-half id-asymmetry; #501: file scope). That is what it
looks like when the model, not the enumeration, is wrong — and it is the
specific observation that changes the verdict. Assessment 1's remediations
(P0/P1/P2/P4) all shipped within two days and all *worked as specified*; they
could not have prevented #443 or #501 because both were outside the model the
remediations hardened.

One correction for the record: at review time the engine was ~4.5 weeks old and
assessment 1 three weeks old — this is a young engine under daily dogfooding,
not a years-old failure. That cuts both ways: the defect *rate* is partially
burn-in, but the defect *classes* (three model axes failing, §3) are not the
kind that burn-in retires.

---

## 2. The evidence in brief

Full detail per slice in `docs/claude/analysis/sync-engine-assessment-2/`.

**Scale and churn.** ~17.6k lines across 18 `sync_*` modules; ~24k LOC of sync
tests (~920 test functions). Since the v2 engine shipped (2026-05-31): 173 of
758 commits (~23%) mention sync; 27+ `fix(sync)` commits; 77 commits touched
the two core files. Four state-model iterations in four weeks (path-keyed
sqlite watermark → git-HEAD demotion → committed ledger overlay →
representation marker).

**Defect distribution.** ~35 distinct defects since v2, classified by root
cause: identity/alignment model ≈ 31%, state management ≈ 29%, unmodeled
system parts ≈ 20%, UX/agent comprehension ≈ 23%, ordinary implementation
bugs ≈ **11%** (dual-classed items counted once by dominant cause). Only ~1 in
9 defects is ordinary coding error; the failure mass is squarely in the
abstractions. Re-fix chains are documented (#216→#225→#226→`e646fca5`; #282
re-fixed positional assumptions in three earlier fixes at once; #458 repeated
the enumeration miss *inside* the #429 fix; #443's invariant had already been
fixed once in `verify` but not in plan/apply — the same invariant maintained
independently in two places).

**Dogfooding (2026-06-23, AZAV ML W10, the one real production reconcile).**
Report at a pre-edit baseline over 52 deck pairs: 56 conflicts + 17 assisted
edits flagged; after hand-reading all 70 flagged cells, **3 genuine changes**
(~96% noise). Conflict items shipped empty excerpts (#451), so the agent wrote
its own cell-extractor over the report. The batch `--baseline` needed for the
exercise did not exist (PR #445 was written during the exercise). The noise was
not classifier error — it was the baseline scheme: against an early ref, every
already-consistent bilingual edit reads as a both-edited conflict
(`commands.md` concedes "most early-baseline conflicts are false"). Separately,
the git-HEAD default made a week of *committed* single-language edits read as
already-consistent — falsely reassuring in the opposite direction.

**Agent behavior (the maintainer's observation, confirmed in the record).**
#451: the dogfooding agent hand-extracted every flagged cell. #430: "turned a
~30s sync into ~15 min", and the user's `--no-cache` workaround *hid the one
real divergence*. #403: field workarounds included hand-stamping ids and
"temporarily delete the DE greeting, sync, re-add + hand-translate". #198:
authors hand-added `tags=["keep"]` to dodge the engine. The 406-line
`sync-agents` info topic — required orientation *before first use* — is itself
the tell: the tool externalizes its complexity onto the agent.

**Surface.** ~20 entry points; ~82 option slots (~33 distinct flags); autopilot
alone has 26 options and an ~80-line mutual-exclusion matrix (17+ rejected
combinations) and duplicates four toolkit verbs as flags. Three overlapping
trust stores; six watermark repair mechanisms; six ledger write paths with
different gating; a `seed` verb existing solely to bridge two stores.

**Test suite as symptom.** 38 distinct issue numbers referenced 189 times
across 30 sync test files; whole files named after incidents; ~30 enumerated
cold-start cases across {baseline-source × id-state × provider × commit-state};
15 files carrying divergent private re-implementations of watermark seeding
across 3 schema generations; ≥15 private engine symbols imported by tests; no
property-based tests in the sync suite. The corpus oracles are the best
artifacts but run only outside CI (the CI fallback is a single 14-line
synthetic pair). And the embedded Phase-0 measurement is damning context: only
**81 of 212** real corpus pairs are post-sync-clean — the engine cannot bring
~62% of the real corpus to a clean state, and the regression floor (40)
tolerates losing half of what works.

---

## 3. The three generative defects

### 3.1 Identity is optional and heterogeneous instead of total

At least eight identity/alignment mechanisms coexist, selected per cell by
incidental metadata: `(slide_id, role)` keys; occurrence-under-slide for
id-less narratives — implemented **three times with divergent semantics**
(`sync_plan._index_narratives_by_anchor`, `sync_apply._narrative_keys_by_index`,
`reconcile_vo_ids._narrative_index`); content anchors (`id:` > `construct:` >
`hash:`) with a "must stay in lockstep" prose contract between live and stored
sides; demoted-but-persisted `vo_anchor` predecessor tokens — including code at
`sync_plan.py:2686` that reads the channel and *discards the result* purely so
a coverage gate sees it consumed; companion `for_slide` with a 4-step legacy
fallback chain; at least **four incompatible position spaces** aligned by
"lock-step iteration, identical predicate/order" comments; two different
notions of "same content" (`hash_cell` vs `body_fingerprint`) answering the
question differently by design; and — the sharpest tell — apply decisions keyed
by **Python `id(proposal)`**, valid only against the exact in-memory plan
object, with the "rebuild after any re-plan or every key misses" landmine
documented in a docstring rather than fixed (it bit anyway: #447). The
classifier imports its core identity predicates (`role_of`, `hash_cell`,
`construct_of`) *from `sync_writeback`, the apply-side write module* — identity
has no owning abstraction anywhere.

Everything downstream is compensation: the ~10 parallel drift channels each
re-implementing the same 3-way state machine; the six post-passes that mutate
the already-emitted proposal list by object identity (the plan is not derived
from a model — it is emitted, then patched); the cold-start
mint/adopt/reconcile/refuse matrix; the whole-deck deferral cascades. The issue
history maps ~1:1 onto identity seams (#216/#225/#226, #365, #403, #443,
#282/#285, #429/#458).

The critical structural property: **a cell's identity regime changes when its
metadata changes.** Adding or removing a `slide_id` moves a narrative between
two classifiers with different conflict semantics; the two halves can *disagree*
on id-ness — that is exactly #443. The same holds for `lang`-ness (a neutral
cell becoming localized or vice versa migrates between the shared-partition
alignment and the keyed/localized machinery) and for layout (inline vs
companion — #501). Regime-transition edges are precisely where the engine
breaks, and no amount of per-regime hardening covers the transitions.

### 3.2 The baseline is out-of-band mutable state

The watermark — the load-bearing bet of the #166 design — became "the source of
nearly every active area:sync bug" (#440) within three weeks and has been
through four models in four weeks. Today three trust stores coexist (sqlite
watermark, git-HEAD default, committed ledger) plus a representation marker;
the baseline defaults *diverge within the canonical loop* (`report`/`task`/
`accept` default git-HEAD; `apply` defaults watermark-ON; MCP `sync_report`
uses the watermark whenever the DB file exists); `provider_available` is
hardcoded differently per verb over the same plan function, so a cold pair is a
"task candidate" in `report` and "refuse" residue in `apply`; and
`report --ledger` sees fewer items than `task` will frame for the same deck,
because `task`/`accept` build their plans without the ledger. Path-keying alone
produced three distinct bugs (#374, #435, #477). The ledger (#448) is the
watermark done right — content-keyed, committed, fail-safe when stale — but was
deployed as a *fourth overlapping mechanism* instead of the replacement for the
other three.

The deepest dogfooding finding redefined the problem: across repeated reconcile
rounds, the baseline must resolve **per slide** ("X synced 3 days ago, Y two
weeks ago, Z never"), and no single point in history is trustworthy. A per-deck
baseline — *any* per-deck baseline — produces the 56-conflicts/3-real noise as
a structural artifact. Per-member recorded trust is the fix, and it must be the
*only* store.

### 3.3 The engine models 2 files while the product is 4

Companion blindness was a deliberate, recorded scope decision
(`split-voiceover-hardening.md` §8, 2026-06-02) that rotted into a data-loss
class (#501, #360, #443), aggravated by validator suggestion text and course
docs that *incorrectly told users sync propagates companions*. The #501 fix —
in-memory inline projection — is the right idea (the four files are renditions
of one logical document) executed at the wrong layer: a non-involutive **text**
transform with a compensation stack (representation markers, re-extract both
halves, watermark recorded over inlined space, `occ`-ordinal round-tripping),
rather than a parsed document model in which "stored in companion" is a layout
attribute and serialization is a lens. The projection also forces every one of
the five baseline sources to be projected identically — a new cross-cutting
invariant maintained by discipline.

---

## 4. Why agents give up (and manual wins)

Manual Claude-Code sync works because the agent's native state model is *pure
functions over git plus explicit per-cell decisions*: `git diff` shows both
sides; the agent judges direction and translation equivalence — a judgment the
engine already concedes belongs to it (`report` hardcodes
`provider_available = True` with the comment "the agent is the verifier");
it writes the twins and commits. Every piece of state is visible, diffable,
recoverable; failure is local. The tool loses on four verified fronts:

1. **Invisible state** — three trust stores the agent cannot `cat` or `diff`;
   staleness indistinguishable from drift (`--explain` re-runs the whole
   classifier against git HEAD just to make staleness *visible*).
2. **Semantics that shift mid-loop** — per-verb baseline defaults and
   `provider_available` flips; the same flag with different polarity per verb;
   exit code 2 meaning four different things.
3. **Fragile handles** — report item ids re-minted per invocation from
   positional 6-tuples; `accept` re-plans from scratch and rejects on ≠1 match
   with "re-run report"; positions shift after every write, so a multi-item
   session is O(N) full re-plans with designed-in stale-handle rejections.
4. **All-or-nothing writes** — the flush gate skips the *entire* 2-file write
   on any error, and "errors" include inexpressible moves; one cell the engine
   cannot express converts to "the whole apply did nothing". This is the single
   strongest driver of fall-back-to-manual, because manual editing never loses
   completed work.

The division of labor is inverted: autopilot embeds four model clients to do
the judging the agent does better, while the mechanical layer the tool should
own perfectly (byte-exact 4-file consistency, id bookkeeping, atomic writes,
structural checks) refuses whole passes and re-litigates decided cells.

---

## 5. What is sound (the keep list)

Verified directly, and worth preserving through any restructuring:

- **`split.py` / `unify`** — the one component with a declared algebraic law
  (round-trip identity), property-pinned. The pattern the whole document model
  should generalize.
- **The write boundary** — `atomic_write_all`, the buffered temp-swap, the
  error-gated flush ("no LLM failure mode reaches disk" was traced path-by-path
  in assessment 1 and has held).
- **Resolve-then-apply purity + dry-run/apply parity** (#216 lesson), pinned by
  a dedicated suite.
- **`sync_verify`** — the structural gate held in dogfooding (52/52 at the
  end); id symmetry, duplicate ids, shared-cell byte parity.
- **The validated LLM tiers** — body-free recovery with six hard validation
  gates and safe-abort; the accept-path guards (multi-cell smuggling rejection
  via the parser's own boundary predicate, `strict_single` translation).
- **The prompt builders and wire codecs** (task framing).
- **The ledger design** (#448) — content-keyed, committed, fail-safe-stale;
  wrongly deployed as an overlay, but the right storage model.
- **The behavioral oracles** — corpus no-op backstop, corpus mutation oracle
  (it caught #443), `_falsely_consistent`, dry-run parity. These are the
  harness that makes replacement *safe*; they must be ported first and put in
  CI.

---

## 6. Recommendation

Replace the identity + baseline + channel core within the existing perimeter,
per **`docs/claude/design/sync-total-identity-document-model.md`**: one
canonical bilingual document; the four files as lens projections with declared
round-trip laws; **total identity** that is invariant under every mutable
attribute (lang-ness, tags, id-upgrades, layout — so class transitions such as
neutral↔localized are diffable state changes, not regime migrations); one
committed per-member trust store (the ledger finished, the watermark deleted);
one generic 3-way diff producing a plan derived once; value-keyed per-item
apply; a 4-verb surface with autopilot as a script over it.

Execution discipline (the honest lesson of v2's burn-in — 7 silent-drop/double
bugs in its first 9 days): port the black-box oracles first and put the corpus
runs in CI; run the new differ in **shadow mode** against the old one over the
212-pair corpus and real edit scenarios before the new apply ever writes;
retain the 189 issue-pinned regression scenarios as behavioral fixtures
(rewritten against the public surface — the current suite's private-symbol
coupling means much of it must be rewritten under any plan, including
continued patching).

What stays hard regardless, so expectations are calibrated: both-sided edits of
the same member are genuine conflicts requiring judgment (the agent's job, now
with full excerpts and a policy knob); translation equivalence is never
deterministically checkable (structural verify only); deck renames still need a
stable deck identity or rename detection. These residues land in one place
under the new model instead of ten, and each arrives as a framed decision
rather than a refusal.

---

## 7. Confidence, and what would change this verdict

**High confidence:** the defect distribution (issue-history slice, `gh`-sourced,
~35 defects classified); the identity-mechanism enumeration (verified at
file:line by two independent critics); the surface/state inventory; the
dogfooding numbers (first-party record in the handover memory); the flip-
condition analysis of #443/#501 against assessment 1's own §4.

**Moderate confidence:** the ~11% "ordinary bug" figure (classification
judgment on dual-cause defects); effort estimates in the design note; the
claim that the ledger can fully subsume the watermark's lead-HEAD property
(design note §6 addresses it; needs the Phase-2 prototype to confirm).

**What would change the verdict back toward "consolidate by patching":** field
evidence that the #501 projection + #448 ledger + #447 conflict policy, once
fully landed, drive the false-positive rate near zero and end the
fall-back-to-manual pattern over a sustained dogfooding period — i.e. evidence
that the current model's remaining gaps are closable without new identity
regimes or new state stores. Given three weeks of the opposite trend under
remediation-as-specified, that is not the way to bet, but the oracles and the
shadow-mode comparison make the replacement path cheap to abort if the new
core underperforms the old one on the corpus.
