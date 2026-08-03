# Sync v3 Q-Register — Handover

**Created**: 2026-08-02 | **Updated**: 2026-08-03 | **Status**: Q1–Q7 all closed,
**all build items done**. What remains is §4's adjacent work (M12, P5/P6, #682)
and the two issues this arc spun out: **#772** (validate rule for untranslated
text) and **#773** (`verify_translation` volume)
| **Source review**: `docs/claude/sync-v3-adversarial-review.md` (2026-07-29)

The seven maintainer questions in **§7** of the sync-v3 adversarial review are
all answered and every build item is now shipped. This document is kept as the
record of *what was decided and why* — §0's lessons and each item's landmines
are the parts still worth reading before touching this code. Findings, evidence and reproduction details
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

## 3. Q4 delegation half — **DONE, narrowed** (PR #775)

Shipped 2026-08-03. Only the **tag-parity** check delegates, to
`tag_parity_violations`. Measured: 25 findings become 20 on the 730-deck corpus;
one deck contributed 6, of which 5 were phantom (the #654 claim, verified).

**The scope was cut from three checks to one during review, and that is the
lesson worth carrying.** Delegating the other two was tried and reverted:

- **`_check_split_slide_id_parity`** — the engine's id comparison is
  deliberately *broader* than validate's. It is sensitive to the `!` preserve
  marker (a legal cross-half difference this module strips everywhere else), and
  it compares **every id'd cell** rather than slide-start cells only, which flags
  the one-sided narrative member `clm harvest` produces *by design* as a pending
  state. Both fire on a `--fail-on warning` pre-commit gate — i.e. they would
  block the commit `harvest` just told the author to make.
- **`_check_shared_cell_parity`** — `unify_texts` stops at the first error, so N
  diverging shared cells collapse to 1 finding; a count mismatch renders as
  "content diverges" naming two byte-identical cells; and preamble divergence
  escalates from silent to **error**.

The rule: **delegate what pairs positionally, keep what compares sets.** The
broader framing "one oracle for one question" was wrong — the gate and validate
ask *different* questions (may this enter the trust store? vs is this deck
well-authored?), which is why #766's containment property still has to be tested
rather than being true by construction.

**Repo-wide finding, unrelated to this change:** `tests/build/` is **never
collected by a bare `pytest`** — pytest's default `norecursedirs` includes
`build`, and CLM does not override it. The pre-push hook and all four CI jobs run
bare `pytest`, so that directory's tests have not run in CI. Filed as **#776**.

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
