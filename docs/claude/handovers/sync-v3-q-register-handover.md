# Sync v3 Q-Register — Handover

**Created**: 2026-08-02 | **Updated**: 2026-08-03 | **Status**: Q1–Q7 all closed;
**one build item remains** (Q4's delegation half)
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

## 1. `record_neutral` (#764) — **DONE** (PR #771)

Shipped 2026-08-03. 45.4% of cold-start items (13,059 of 28,791) now resolve
mechanically. Landmines for anyone touching it:

- **A `pos:` record must re-record its whole pool.** Ordinals renumber together,
  so patching one slot never converges. The first draft did that.
- **Therefore the `structural` stamp is applied ONCE, after the whole landing
  loop.** Per-item stamping was clobbered by the next sibling's `rerecord_pool`
  — measured at 65% of positional neutral members. Every test fixture used a
  single-member pool, so it shipped green through one review round.
- **The entry's owner is dropped when the anchor is still cold**, or #718's
  dangling-reference detector fires on every cold apply and stops meaning
  anything.
- **Clause 4's premise is false and was shipped knowingly.** "Code carries no
  natural language" is wrong for ~0.9% of shared code cells (120 cells, 43
  decks) that carry German in comments or string literals. The maintainer
  shipped on the base rate (vs ~100% for markdown); the docs now say "compared,
  not read" rather than claiming a guarantee. The detector that would make the
  boundary categorical is **#772**.

## 2. Q6b mechanical sweeps — **CLOSED, measured out** (2026-08-03)

Do not build this. `scripts/measure_sync_ceremony.py` replays real commits and
classifies every changed cell by the row it frames. Over 200 commits of the
reference course repo:

| | rows | share of framed |
|---|---:|---:|
| `verify_translation` (both halves moved) | **1053** | **68.4%** |
| `translate_edit` (one half moved) | 487 | 31.6% |
| shared member moved — *already mechanical* | 346 | — |

**The normalizer-equivalence row — Q6b's whole remaining substance after design
§6.2.1 rejected the auto-answer sweep — would remove 1 of 487 rows (0.2%).** The
mechanism is real (v3 fingerprints are raw bytes, so unlike v1/v2 a soft re-wrap
does read as drift, #429) but the population is not.

Two things worth carrying forward:

- **Beware the naive count.** A first pass said 5.1%. It counted all changed
  cells; cosmetic edits land overwhelmingly on *shared* cells
  (`propagate_shared_edit`) or identically on both halves
  (`record_symmetric_edit`), neither of which is ceremony. Filtering to the only
  shape a normalizer row could help collapses it 25x.
- **`uniform_drift_side` (#767) already covers the shape.** Of the 57
  (commit, deck) pairs framing ≥3 one-sided edits, **55 have every edit on one
  side**. Median one-sided edits per deck is 1.

**What the measurement surfaced instead**: `verify_translation` is 68% of framed
rows, up to 32 in one deck — and it fires exactly when both halves moved apart,
the one shape where the engine has *observed* a divergence it cannot resolve.
Q6b proposed auto-`confirm`ing it; that would bank 1053 unread divergences. The
volume is real ceremony and needs a different answer. Tracked as **#773**.

## 3. Q4 delegation half — validate as an adapter — **THE ONLY BUILD ITEM LEFT**

**Why it was scheduled last.** It is the biggest refactor of the three, and it touches
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
