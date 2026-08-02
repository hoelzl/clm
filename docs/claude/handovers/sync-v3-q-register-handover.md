# Sync v3 Q-Register — Handover

**Created**: 2026-08-02 | **Status**: Q1–Q5, Q6a, Q7 closed; three items remain
| **Source review**: `docs/claude/sync-v3-adversarial-review.md` (2026-07-29)

The seven maintainer questions in **§7** of the sync-v3 adversarial review are
now all answered. This document covers only what is **left to build**, in the
order that is cheapest to build it. Findings, evidence and reproduction details
stay in the review; the design of record is
`docs/claude/design/sync-total-identity-document-model.md` (the note), whose
**§13 amendments table** carries one row per landed change and must gain one for
each item below.

---

## 0. Read this first

Two things about the register that cost time in the 2026-08-01/02 sessions and
will cost it again:

**The register is a year of accumulated notes. Verify before you build.** Three
Q-items turned out to be already done or unbuildable as written:

| Item | Reality |
|---|---|
| Q4 "id-sequence order parity joins `structural_violations`" | already landed in #719 |
| Q7 "UUID temp names" | already in `atomic_write_bytes` (`path_utils.py`) |
| Q7 "automatic provenance `structural`" | asserted in four docs; `record_neutral` has never existed in `src/` |

The Q4 *containment* half was likewise not a bug at all — it held on all 730
corpus pairs and was simply untested. Budget a verification pass before a build
pass on every remaining item.

**Measure before you accept a framing.** Q1 was withdrawn outright when the
"82.4% of positional members are ambiguous" figure was re-derived: the
arithmetic was right and the *metric* was wrong. `scripts/measure_positional_composition.py`
and `scripts/measure_sync_change_points.py` re-run the corpus measurements
against any slides root. Use them.

---

## 1. `record_neutral` — issue #764 *(do this first)*

**Why first.** The design is complete and merged (note §6.2.1, PR #765); the
issue carries a scope checklist; the measurement is committed and re-runnable;
nothing else depends on it and it depends on nothing. It is also the largest
single payoff in the register: **45.4% of the 28,791 cold-start verification
questions** on the PythonCourses corpus are this class.

**What it is.** A cold member is framed as a question only when the relationship
between its halves is genuinely unknown. A new **mechanical** row,
`record_neutral`, resolves it instead — writing **no file bytes, only the ledger
entry**, like the existing `record_order` / `record_owner` rows — when all five
clauses of §6.2.1 hold: no ledger entry; two-sided; langness `shared`; kind
`code` or `j2`; and every per-side field the differ compares is equal across the
halves.

**Clause 5 is defined over the generic record-diff (§6.3), not a hand-written
field list.** That is deliberate (P6): a field added to the comparison later
tightens the predicate automatically. Do not re-enumerate the fields.

**Clause 4 is load-bearing and was the maintainer's explicit decision.** For
`markdown`, `shared` + byte-identical has two readings the engine cannot
distinguish — a genuinely neutral cell (fenced code, a shell snippet, an
`<img>`), or German prose duplicated onto the EN side and mis-declared shared.
Auto-blessing the second banks an untranslated cell as in-sync. `wrong_language_cell`
cannot catch it either: a shared cell carries no `lang=` to contradict. The
price is 282 corpus members that stay real questions. **Do not widen clause 4
to `markdown` without a new maintainer decision.**

### The one thing that changed under it since the design landed

The design says trust banked this way carries provenance `structural`. PR #768
(Q7) **removed the string-enumeration approach to provenance entirely** — there
is no `_AUTOMATIC_PROVENANCE` set any more. `preserve_unchanged_member` now
takes `deliberate_provenance`, defaulting to `False`, and intent is threaded
from the caller.

That is *good news* for this item: writing `provenance="structural"` needs no
registration anywhere. It will be treated as automatic by default, which is
correct — a structural observation must not overwrite a human's `agent` or
`semantic:<model>` attestation on a member whose content is unchanged. **Do not
add `structural` to any list.** If you find yourself wanting to, re-read
`preserve_unchanged_member`'s docstring; that mistake is recorded there.

### Where the code goes

`_diff_unmatched_current` in `src/clm/slides/sync_diff.py` is the branch being
replaced — it currently gates the cold decision on *sidedness*, never on
*langness*:

```python
if not self.base.complete and not member.is_one_sided:
    self.emit(handle, "unverified", "verify_cold", "none", ...)
```

The executor side needs a ledger-only write; `record_order` / `record_owner` are
the shape to copy in `doc_apply.py`.

### Acceptance

- `python scripts/measure_positional_composition.py <slides-root>` section 3
  should show the decidable class resolving, and the residual cold count
  dropping by ~45%.
- The suppression doctrine must be untouched: a member carrying any framed row
  is not a candidate, because clause 5 cannot hold. Pin that.
- Info topics: `sync-agents.md` (the agent sees a new mechanical action) and
  `commands.md`. Per the CLAUDE.md rule these are version-accurate and
  downstream course-repo agents read them.
- §13 amendments row.

---

## 2. Q6b — mechanical sweeps *(second; the principle is shared with #1)*

**Why second.** The design note's §6.2.1 already contains the answer, and it is
freshest immediately after building #764. **Q6b as the review states it was
superseded by that design** — read §6.2.1's "Why this is not the auto-confirm
mistake" before you read the review's Q6b, or you will build the rejected thing.

### What the review proposed, and why it is wrong

> `apply --mechanical` (auto-answer exactly `translate_edit→keep_twin`,
> `verify_translation→confirm`, hard-fail on anything else, never default-on)

Auto-answering `translate_edit → keep_twin` banks the claim *"my edit did not
change what the twin should say"* — a **semantic** claim about two *different*
texts that the tool cannot verify. Banking it unverified is exactly the
silent-divergence class the programme exists to remove.

This is not theoretical. PR #767 established (and `clm info sync-agents` now
documents) that **`keep_twin` banks the pair**: the member reports in sync from
then on, so an unfaithful twin waved through is never raised again. A sweep that
applies it across a deck permanently blesses every twin it touches.

### What to build instead

The governing principle from §6.2.1: **auto-resolve only what the engine can
observe, never what it must assume.**

Applied to a *cold* member that yields `record_neutral` (item 1). Applied to an
*edited* member it yields a different but equally observable test: **the
source-side change is normalizer-equivalent, so the twin is provably
unaffected**. A whitespace-only or normalizer-canonical edit to one half cannot
change what the other half should say — and that is checkable, not assumable.

Both are **mechanical rows under §6.2**. So `apply --mechanical` becomes *a
caller of existing mechanical rows*, not a new contract with its own auto-answer
policy. That is the whole difference: no new trust semantics, no new vocabulary,
nothing to get wrong at the contract layer.

### Sequencing note

`uniform_drift_side` (PR #767) is the report-level signal that tells an agent
when a bulk `keep_twin` is *appropriate* — it fires when every `translate_edit`
drifted on one side, the review-after-translate shape. That is the honest
version of the ceremony fix: **surface the pattern, let the human who knows
which half they reviewed decide**. Check whether that already removes enough
ceremony before building an auto-answer at all. If it does, Q6b may reduce to
the normalizer-equivalence row and no flag.

**Q6a is DONE** — all four hand-edit flows have in-engine answers now: fork
twin-marking (`mark_twin`, #656), order repair (first-class order items, #654),
anchor-shape framing (#653), and `verify_translation` with a stale twin
(`body`+`side`, #656). No issue exists for Q6b; open one.

---

## 3. Q4 delegation half — validate as an adapter *(third; largest blast radius)*

**Why last.** It is the biggest refactor of the three, and it touches
`clm validate`, which runs on the **pre-commit gate in course repositories**. A
regression here blocks every commit in every downstream repo, not just CLM's.
Do it when the two cheaper items are behind you.

**Why it is now safe to attempt.** PR #766 pinned the relation the refactor must
preserve: `tests/slides/test_gate_validate_containment.py` states as a property
that *a `clm validate` split-pair **error** implies a non-empty
`gate_projected_pair`*, over twelve corruption shapes, with each shape's validate
severity **declared** rather than discovered. Run it continuously during the
refactor; it is the specification.

**The goal.** Validate's split-pair family — `_check_shared_cell_parity`,
`_check_split_tag_parity`, `_check_split_slide_id_parity`,
`_check_split_companion_for_slide_parity` — becomes an adapter over
`parse_bundle` + `structural_violations`. This kills the positional-artifact
diagnostics (#654's phantom tag mismatches) **by construction** rather than by
another special case.

### The migration hazard, with a number

The gate is currently **strictly stronger** than validate. Measured across 730
corpus pairs during #766:

| | count |
|---|---|
| validate split-pair *errors* | **0** |
| pairs the gate blocks where validate has no error | **1** (`slides_lucky7`, order-parity) |
| containment gaps | **0** |

So a naive delegation makes validate newly report on **one** corpus pair. That
is small, but it is not zero, and the gate sees things validate structurally
cannot — notably **shared-companion body drift**, because
`_check_split_companion_for_slide_parity` compares which *slides* the companions
narrate and never the narration bytes.

Before landing: re-run the sweep, decide per newly-surfaced class whether it is
`error` or `warning` at the validate surface, and remember validate's severity
convention differs from the gate's on purpose — validate must not hard-fail CI
on pre-existing committed divergences (the tag-parity precedent). The
containment test's `VALIDATE_SEVERITY` map is where those decisions get written
down.

**Do not** simply raise validate to the gate's severities. `TestDeliberateNonContainment`
pins the exemption that must survive: tag parity is warning-only on *both* sides
by design, because an error there would make the write gate refuse a pair the
apply pass is mid-reconcile on.

No issue exists; open one.

---

## 4. Adjacent open work (not in scope above, but in the same phases)

The review's §8 program put these in the same phases as the items above. They
are **not** closed, and a session picking up "Phase 4" or "Phase 5" should know:

- **M12 — surface honesty** (Phase 4). Three parts, all still open: `record`/`apply`
  success does not imply next-report-clean (tag advisories re-frame,
  undocumented); `sync verify` treats an unresolvable companion as
  warning/exit-0 while the write gate errors on the same state, and "run verify
  in CI" is advertised; a write `OSError` leaves per-item results saying
  `applied`. PR #766 aligned the *order-parity* severity only — that is adjacent
  to M12's second part, not the same thing.
- **`record`/`apply` honesty about advisory residue** (Phase 4).
- **Slide-scoped item grouping in the report** (P6, Phase 5).
- **Partial-body answers / engine-side draft mechanism** (P5, Phase 5) — the
  review calls this the largest open UX question and explicitly fine to defer.
- **#682 corpus-gate split** (D7, Phase 5) — the gates are pinned to a moving
  private repo.

---

## 5. Landmines carried forward

- **`REQUIRE_REPORT_ID = False`** in `src/clm/slides/sync_wire.py`. The
  maintainer's Q2 decision is to flip it — and drop schema 3 from
  `ACCEPTED_DECISION_SCHEMAS` — in the release *after* the one that ships schema
  4. Nothing else reminds you.
- **`report_id` covers the ledger section**, so your own `apply` invalidates it.
  Staged `--member` runs must re-report between passes.
- **The save snapshot must be based on `ledger`, not `merged`** (`doc_ledger.py`).
  `_merge_with_disk` returns a new object holding the *disk* copy for sections
  this run did not change; basing on it writes a stale copy over a sibling's
  work on the next save, silently, with the both-changed warning suppressed.
  This was introduced and caught inside PR #768.
- **`.clm/` is gitignored in some course repos**, which hides ledgers. Check
  before concluding a deck is cold.
- **A doc row claiming "amended §X" is often wrong.** Two review rounds in PR
  #767 caught two separate false claims of that shape (a §6.4 edit never made, a
  "promotes out of the bulk-translate bullet" that was additions-only). Verify
  the diff before writing the row.

---

## 6. What closed, for orientation

| Q | Outcome | PR |
|---|---|---|
| Q1 | **Withdrawn** on measurement — blast radius, not pool membership, is the honest metric | — |
| Q2 | Schema 4: `report_id` + `already_applied` + `deck_key`/`ledger` | #754 |
| Q3 | Schema 4: `resolution`, `action`, body symmetry, `mark_twin` | #755–#759 |
| Q4 | Containment half: property pinned, 0 gaps / 730 pairs; gate severity fix | #766 |
| Q5 | `uniform_drift_side` observation — **not** the proposed `drift:` field | #767 |
| Q6a | Four hand-edit flows given in-engine answers | #653, #654, #656 |
| Q7 | Merge-on-save (M8), provenance intent from the caller (M13), `confirmed_commit` corrected | #768 |

Related: `project_sync_v3_adversarial_review`, `project_sync_positional_composition`,
`project_sync_slide_hood_presentation` (memory topics).
