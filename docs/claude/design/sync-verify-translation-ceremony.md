# verify_translation ceremony — diff-first framing (design note for #773)

**Status: proposed** (no engine change yet). Written 2026-08-04 against the
v3 engine at `sync_diff.py` / `doc_report.py` / `doc_ledger.py` as of CLM
1.23.1. Companion measurement: `scripts/measure_sync_ceremony.py`.

## 1. The problem, measured

Over 200 commits of the reference course repo, `verify_translation` — both
halves of a localized member moved off base — is **68.4% of all framed rows**
(1053 of 1540), more than twice `translate_edit` (487), with a median of 1 per
deck but a **max of 32** in a single deck. Every ceremony reduction so far
addressed something else:

- **Q5 / `uniform_drift_side`** (#767) targets `translate_edit` only, and its
  observation *explicitly excludes* `verify_translation` rows
  (`sync_diff.py`, `_uniform_drift_observation`).
- **Q6b** (auto-answer `confirm`) was **rejected**, rightly: the row fires
  precisely when the engine has positively observed a divergence it cannot
  resolve. Auto-confirming banks 1053 unread divergences — the
  silent-divergence class the whole programme exists to remove.
- **`record_neutral`** (#764) is cold-start only.

`confirm` on `verify_translation` asserts *"these two texts, which both
changed, still say the same thing"* — a semantic judgement with nothing the
engine can check. So the goal is **not** auto-resolution at any threshold.
The goal is making the judgement *cheap to make*: today the reader gets two
full cells (`de`/`en` + `de_body`/`en_body` in the item payload) and must
re-derive *what changed on each side* by eyeball. Reading two full cells to
spot a one-word change is most of the cost, times 32 in the worst deck.

## 2. What exists today (the parts a fix can stand on)

| Piece | Where | What it gives |
|---|---|---|
| Item payload with full current bytes, both sides | `DiffItem.payload()` (`sync_diff.py`) | excerpts are already "structurally free" — but only for the *current* state |
| Ledger base **fingerprints** per side (`de_fp`/`en_fp`) | `MemberBaseline` / `doc_ledger.py` | can *recognize* the base state anywhere; cannot *reproduce* it |
| `confirmed_commit` per member | `LedgerMember` (`doc_ledger.py`) | repo `HEAD` at record time. **Does not contain the recorded state** (record runs pre-commit; its docstring warns exactly this) — but the state usually lands in a *descendant* commit shortly after |
| Read-only deck bytes at any git ref, rename-following, degrades to `None` | `git_text.bundle_texts_at_ref` | the byte source a diff needs |
| A whole forensic diff-vs-ref view | `doc_report.diff_bundle_at_ref` (`--since REF`) | precedent that git-based views are sanctioned: *"a view, never a trust source (design §12.3)"* |
| Deck-level observation precedent | `uniform_drift_side` (#767) | the shape for "tell the reader what the rows have in common" |
| Decision-document freshness binding | schema-4 `report_id` (`doc_apply.py`) | the hook any batch-answer guard would attach to |

One prior decision constrains the solution space: the ledger deliberately
stores **`hash + confirmed_commit`, no full row** — settled in
`sync-consistency-ledger.md` §11.3 after weighing exactly this trade. A
"stored excerpt" variant would reverse that.

## 3. The four directions from #773, analyzed

### A. Show the diff, not the cells — **recommended, phase 1**

For each `verify_translation` row, recover the base bytes and put **per-side
unified diffs** in the item payload next to the full cells:

```
"de_diff": "@@ -3,1 +3,1 @@\n-# Der alte Satz.\n+# Der neue Satz.",
"en_diff": "@@ -3,1 +3,1 @@\n-# The old sentence.\n+# The new sentence.",
"base_ref": "abc1234"
```

**Base recovery** is a fingerprint-matching walk, per deck (not per row):

1. `git log --format=%H -n CAP -- <de> <en>` (rename-following via the
   existing `git_text` helpers; `CAP` ≈ 30).
2. Newest-first, read the bundle at the ref (`bundle_texts_at_ref`), parse
   once per ref (one parse serves every row of the deck), fingerprint the
   member, stop at the first ref where both sides match the ledger's
   `de_fp`/`en_fp`.
3. The last sync is usually a few commits back, so the typical walk is 1–5
   refs. `confirmed_commit` is a hint for where to stop expecting matches,
   never the answer itself (see its docstring — it predates the commit that
   contains the state).
4. **No match inside the cap → degrade to today's behavior** (full cells,
   no `*_diff` fields). Base-never-committed, rebased-away history, and
   hash-version drift (`LedgerMember.hash_version` — recompute with the
   recorded version, degrade if that version is gone) all land here. The
   fields are additive and optional; absence is honest.

Properties that make this the right first move:

- **View-layer only.** No ledger schema change, no trust semantics, read-only
  git — the exact §12.3 posture `--since REF` already holds. The answer
  surface (`confirm` / `body`+`side`) is untouched.
- **Attacks the measured cost** — reading — for the 68% class *and* incidentally
  for `translate_edit` (the same recovery serves its rows when present).
- Wire note: the payload gains optional fields, so `WIRE_SCHEMA` gets its
  additive bump and the report contract doc a row (§13 amendments log —
  a row is required on any engine change, including this one).

**Rejected variant — store base excerpts in the ledger.** Reverses the §11.3
"no full-row" decision; multiplies a *committed* file's size by the corpus's
body mass; churns merges; and turns the trust store into a content cache —
a character change the v3 design has consistently resisted.

### B. Group by cause — **fold into A as an observation, not a mechanism**

The 32-row deck is one editing session, and the reader should learn that
once, not 32 times. With A in place, two cheap deck-level signals exist:

- all rows recovering the **same `base_ref`** (they diverged from one sync
  point), and
- the per-side diffs visibly repeating a pattern (mechanical rename, list
  renumbering) — visible to the reader without any engine judgement.

Emit one `verify_translation_batch` observation (the `uniform_drift_side`
shape): *"N verify_translation rows, all diverging from base `abc1234` —
read the diffs; if they show one repeated pattern, judge the pattern once."*
No git-blame archaeology, no commit clustering: the same-base grouping falls
out of A for free, and anything finer buys little (the measurement's median
deck has 1 row).

### C. Separate "both moved, still parallel" from "both moved apart" —
**phase 2, annotation only**

With per-side diffs available, the engine can observe *structural*
parallelism without judging semantics: both diffs confined to the same
relative region, same added/removed line counts, same tag-line vs body-line
split. Annotate the row (`"shape": "parallel-region"` vs
`"shape": "disjoint-regions"`) so the reader triages the cheap ones first.

Strictly an annotation on a row that still requires an answer — never a
resolution path. The precedent for one-sided honesty about heuristics is
`content_lang` (abstains rather than guesses); this annotation must abstain
(`"shape": null`) whenever the diffs are non-trivial. Deferred behind A
because its value depends on A's diffs existing, and its thresholds deserve
their own measurement pass.

### D. Batch answering by predicate — **deferred, with a stated hazard**

A decision-document form answering by predicate
(`{"action": "verify_translation", "choice": "confirm", "expected_count": N}`)
cuts *writing* cost only — and writing is not the measured cost; reading is.
It is also one keystroke away from the rejected Q6b sweep: a predicate
`confirm` invites confirming rows never read. If it is ever built, the
guards are (1) an exact `expected_count` that must match the live row count,
(2) the existing `report_id` freshness binding, (3) per-deck scope. But the
recommendation is to **not build it now**: land A, re-run
`measure_sync_ceremony.py`, and only revisit if the residual cost is in
answer-writing rather than reading — which the current measurement says it
is not.

## 4. What must not happen

Restated so a future session cannot drift into it: **no auto-resolution of
`verify_translation` at any threshold, count, or similarity score.** 1053
rows is the scale of what would be banked unread. Diffs, groupings, and
shape annotations inform a human/agent judgement; only an explicit per-row
(or explicitly-guarded, count-bound) answer resolves one.

## 5. Costs and risks

- **Walk cost**: one `git log` + typically 1–5 `git show`+parse per deck
  *with* `verify_translation` rows (median deck: 1 row; decks without such
  rows skip recovery entirely). Cap + degrade keeps the worst case bounded.
  Parse results are cached per ref within a report run.
- **Wrong-base risk**: fingerprint match is exact (both sides), so a
  recovered base is *the* recorded state, not a guess. Ambiguity (the same
  fp at several refs) is harmless — any matching ref reproduces identical
  bytes for that member.
- **Report size**: diffs are almost always smaller than the full cells
  already shipped; net growth is the two hunk headers.
- **Dirty-ledger repos** (the `.clm/` gitignore landmine): a repo that never
  commits its ledger still has committed *decks*; recovery only needs the
  deck bytes, so it works — but repos whose decks are also uncommitted
  degrade, correctly.

## 6. Recommendation

1. **Phase 1 — A + B**: per-side base-diff recovery (git fingerprint walk,
   capped, degrading) rendered as `de_diff`/`en_diff`/`base_ref` payload
   fields, plus the `verify_translation_batch` observation. `WIRE_SCHEMA`
   bump, contract-doc row, §13 amendments-log row.
2. **Re-measure** with `measure_sync_ceremony.py` extended to report how many
   `verify_translation` rows would have recovered a base (expected: the
   large majority — decks sync and commit routinely).
3. **Phase 2 — C** (shape annotation) only if phase 1's diffs prove reliable
   in practice.
4. **D stays unbuilt** unless post-phase-1 measurement shows answer-writing
   as the residual cost.

## 7. Open questions for the maintainer

- Is `-n 30` an acceptable walk cap, or should it be time-bound
  (`--since`-style) to match course-repo commit cadence?
- Should `translate_edit` rows get the same `*_diff` fields in phase 1
  (same recovery, near-zero marginal cost), or stay minimal until the
  verify_translation value is proven?
- Where should diffs render in the human (non-`--json`) report — inline
  hunks, or a `--diffs` flag?
