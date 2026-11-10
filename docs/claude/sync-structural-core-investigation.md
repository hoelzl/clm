# Sync Structural Core: Can Pairing and Order Be Fixed at the Root?

**Status**: Investigation, complete. The maintainer decided D1 to D3 on
2026-11-10 (§9): pursue the proposal in §4 in the order §9 gives, with a
go/no-go before the engine is switched over. Step 1 has landed (§2.5). D4 and
D5 are open.
**Date**: 2026-11-10
**Author**: Claude (Fable 5.2), at the maintainer's request
**Measured against**: master `2e011286` (clm 1.38.0)
**Question**: The sync engine works reasonably well, but agents still hit
problems in straightforward sync operations, and recent fixes have added special
cases whose code paths handle the same situation differently. Can the design be
improved at the level of the algorithm, instead of fixing symptoms one by one?
And can the engine's operation be formalized, and properties of it proved?

Forward design for the sync engine lives in
`docs/claude/design/sync-total-identity-document-model.md`. This document does
not amend it. It is evidence and a proposal.

---

## 1. Verdict

Yes. Almost every structural failure of the engine traces to one design choice:
a cell without a `slide_id` gets its identity from its position,
`pos:<slide>/<kind>/<ordinal>`. Everything that makes the engine hard to reason
about (pools, spans, order scopes, placement rows, suspensions, freezes,
deferrals) exists to recover what that choice throws away.

A replacement for this part of the engine fits in about 450 lines of Python
(`scripts/sync_model.py`). On the repo's own sync probe (67,871 deck shapes) master converges on 50,031 cases; the
prototype converges on all of them, with a fifth of the questions (§2). Its
three basic laws hold on every small input (§5), and they do not depend on the
pairing heuristics, which is what makes a proof feasible (§6).

The evidence has limits, listed in §7. The main ones: the prototype works on an
abstract view of a deck, not on files, and it does not model partial progress.

The id-keyed half of the engine (translation rows, the wire contract, the
ledger file, the gates) is not the problem and stays.

---

## 2. Measurements

### 2.1 The probe

`scripts/sync_probe.py` drives `report -> apply` over 67,871 deck shapes until
the pair is clean, answers questions "as a careful agent would", and compares
the final halves with the intended result. `scripts/sync_model_probe.py` runs
the prototype (`scripts/sync_model.py`) over the same cases with the same answer
policies and the same oracle.

| cases | run | converged | visible stop | silently wrong |
|---|---|---:|---:|---:|
| all 67,871 | master | 50,031 | 16,632 | 1,208 |
| all 67,871 | prototype | 67,871 | 0 | 0 |
| one half untouched, 27,502 | master | 22,513 | 4,937 | 52 |
| one half untouched, 27,502 | prototype | 27,502 | 0 | 0 |

"Visible stop" is a livelock, an unanswerable question or a stall. "Silently
wrong" means the report is clean and the halves are not what was intended.
Per case, the prototype is better than master on 17,840 cases and worse on none.

### 2.2 One half untouched

The second pair of rows is the finding that matters most. In those 27,502 cases
one half is exactly what the last sync left: the author edited German only, or
English only. That is the everyday operation, and master fails 18% of it.
2,140 of the failing cases are single-round and contain no content edit at all.
Three small ones (`S` slide, `a b e` id'd voiceovers, `c k` un-id'd cells):

| recorded | the changed half | master |
|---|---|---|
| `S0 a c1 b c2 e S1` | `S0 b c2 a e c1 S1` (reordered) | livelock, the verify withholds every pass |
| `S0 a c1 b c2 S1` | `S0 b c2 a S1` (one cell removed, rest reordered) | livelock |
| `S0 a c1 b c2 S1` | `S0 k1 a b c1 c2 S1` (one cell added, two swapped) | stall on an answerless `verify_cold` |

### 2.3 Questions

On the 50,031 cases both converge on, master answers 45,142 questions, 41,204 of
them `pool_placement_divergence`. The prototype answers 9,842: 4,712 identity
questions (§4.5), 2,533 order questions, and the content and removal questions
it shares with master.

### 2.4 Without the identity question

The prototype asks one question the engine does not have: "which of these cells
is X?" Switched off (`--no-identify`), it guesses where the engine guesses, and
scores 67,593 converged, 141 visible stops, 137 silently wrong. All 278 are in
the `both_edit_both_move` family: both halves move and edit a slide's un-id'd
cells, the residual accepted with #1144. So the question is what closes that
residual, and nothing else on the probe needs it.

### 2.5 The laws on the engine (step 1, added 2026-11-10)

The laws of §5 need no oracle, so they can be checked on the engine itself.
`tests/slides/sync_laws.py` drives `report -> apply` with every question
refused over every small deck: up to 3 recorded cells between two slides (id'd
voiceovers and un-id'd code cells, contents may repeat), a changed half of up
to 4 cells; where both halves changed, up to 2 recorded cells and 3 per half.
`scripts/sync_laws.py run --model` puts the prototype in the engine's place.

| law | cases | master holds | prototype holds |
|---|---:|---:|---:|
| nothing changed: the report is clean | 63 | 63 | 63 |
| one half changed: no question, both halves end as that half | 16,744 | 1,344 | 16,744 |
| both changed: a question-free sync converges, conserves cells, and does not depend on which half is which | 11,450 | 10,906 | 11,450 |

What master does instead in the 15,400 one-sided cases:

| outcome | cases | what it is |
|---|---:|---|
| `stalls:verify_cold` | 9,200 | the half added an un-id'd cell (9,196 of them). The engine does not copy it (design note §5): the row has no answer, and the author gives the cell a `slide_id` |
| `asks:pool_placement_divergence` | 5,816 | the slide also holds an id'd cell, and the half changed the un-id'd cells around it (4,608 of these add one). The engine asks which half is right |
| `livelock_withheld` | 384 | the report never turns clean. The smallest: recorded `x x`, one half `y x` (one of two identical cells edited) |

The 1,344 that hold are removals (504), replacements in place (736) and
reorders (104). No case that adds a cell holds.

Of the 544 two-sided violations, 426 are livelocks and 118 are asymmetries: the
engine converges with the halves one way round and asks with them swapped.

Three things this adds to §2.2:

- **No violation is silent.** In all 28,257 cases the engine either satisfies
  the law or stops visibly. The probe's silent failures lie outside this
  scope: its decks are larger and its questions are answered.
- **The share is much higher than the probe's 18%, for two reasons.** The probe
  counts a correctly answered question as convergence, and the law counts any
  question as a violation, because the other half is exactly what the last
  sync left and nothing needs deciding. And 13,872 of the 16,744 cases add a
  cell on one half, and none of those holds.
- **The largest class is by design, not a defect.** A one-sided un-id'd
  insertion is refused because the positional key cannot say where its twin
  goes. It is the "give it a `slide_id`" exit of §3.5, measured.

The cases with at most 3 cells per half are a ratchet in CI
(`tests/slides/test_sync_laws.py`, 4,767 cases, 2,658 baseline entries); see
"Sync laws" in `docs/developer-guide/testing.md`. The prototype's column is
asserted on the same cases by `tests/scripts/test_sync_model_laws.py`.

---

## 3. The root cause

### 3.1 One choice

Design §3.3 gives an un-id'd cell the key `pos:<slide>/<kind>/<ordinal>` and
argues that this is sound in the steady state, because shared cells are
byte-identical on both halves. The argument holds while nothing moves. Three
consequences follow once something does.

### 3.2 The ledger has no total order

The ledger records, per slide and kind, the ordinal of each un-id'd cell, and
order only for id'd cells (`doc_identity.baseline_from_deck`: "pos handles alias
across states, never order-tracked"). It does not record where a code cell sits
relative to a markdown cell, or to cells of the next slide. That information is
reconstructed on every diff:

- spans, the nearest id'd cell above a cell (#906), later recorded per half
  (#1130);
- crossed sync points (#1052), learned spans and pool edges (#1051, #1054);
- moves between slides, proven by fingerprint uniqueness (#1142);
- slide-hood, because the slide is part of the key: promoting a cell to
  `subslide` re-keys every cell under it (#653, #1092, #1187, #1216).

### 3.3 Cells are paired half-to-half before the ledger is consulted

The lens pairs the German cells with the English cells by cursor
(`doc_lenses._Parser.pair_positionally`), without looking at the ledger. The
differ then re-pairs each half against the ledger, per pool
(`sync_diff._Differ._align_pool_sides`), and must undo the lens where the two
disagree (`DiffItem.twin`, `absorbed_pos`, `shifted_pools`). The same function
has a second algorithm, selected per pool (`legacy=True`), and the executor a
matching second placement (`DiffItem.legacy_placement`).

At least six places decide which cell corresponds to which, each by its own
rule:

| where | rule |
|---|---|
| `doc_lenses._Parser.pair_positionally` | cursor walk per kind class, inside the spans id pairs delimit |
| `sync_diff._Differ._align_pool_sides` | each half against the ledger, per `(slide, kind)` pool, in three stages, plus the legacy variant |
| `sync_diff._Differ._cross_group_pool_moves` | deck-wide, by fingerprint uniqueness |
| `split.unify_texts` (the structural verify) | its own alignment of the two texts |
| `assign_ids` (`normalize --stamp-ids`) | a text-level pairing, falling back to the lens (#892) |
| `validator` split-pair checks | their own pairing; only tag parity delegates to the engine (Q4) |

`doc_conserve.check_conservation` is deliberately pairing-free, because the
pairing it guards could not be trusted (#1141). Fixes have to be made "at both
authorities from the same rule" (#906) or "at both authorities together"
(#1052). This is the divergence between code paths.

### 3.4 The plan is rows, executed one at a time, recorded by re-reading

`apply` executes the report's rows in six phases (`doc_apply._PHASES`) as
in-place mutations, re-parses what it wrote, and records from that snapshot
(`doc_apply._record_item`, `_upsert`, `rerecord_pool`). Three failure classes
come from this:

- **Rows that are only valid together.** One move plus edit becomes a position
  row and a content row; the pre-write verify judges them one by one and
  withholds both, every pass (#1124, #1130).
- **The engine reads its own write as an author edit** (#885, #1071, #1137,
  #1179).
- **Recording banks more than was decided.** A snapshot of a member records all
  of its aspects, so a row that settles one aspect can bank another that nobody
  reviewed (#1181, #1187). The guards are suppressions and freezes
  (`_POOL_FREEZING_ACTIONS`, `defer_recording`, `_drop_unresolved_from_pools`).

Design P6 ("the plan is derived once, no post-passes") and P7 ("every item is
independently applicable") no longer describe the code: the differ carries
about twenty pieces of cross-phase state and three post-passes.

### 3.5 Growth and issue history

| | 1.20.0 (2026-07-04, cutover) | 1.38.0 (2026-11-08) |
|---|---:|---:|
| `sync_diff` + `doc_apply` + `doc_lenses` + `doc_ledger` + `sync_verify`, lines | 5,832 | 12,837 |
| registered actions | 38 | 46 |

114 commits touched the differ, executor or lens in between.

A subagent classified the 40 sync issues that are open or were closed since
2026-08-15, from the issue and PR text
(`docs/claude/analysis/sync-structural-core/issue-classification.md`; its
reading, not re-verified row by row):

- 36 describe a deck shape. 20 of them involve an un-id'd cell, including all
  11 where the engine paired wrongly.
- Of the 20 closed ones, 9 fixes changed a pairing algorithm, 4 added a
  suppression, deferral or freeze, and none was classified as a generalization.
- At least 10 were filed as a consequence of an earlier fix (#900, #1052,
  #1124, #1128, #1130, #1137, #1142, #1150, #1186, #1187).
- "Give the cell a `slide_id`" is the recurring manual exit, and the tool behind
  it (`normalize --stamp-ids`) has five issues of its own.

16 of the 36 involve no un-id'd cell. Those are not explained by §3.1 to §3.3.
About a third fall under §3.4 (#885, #992, #1179, #1181, #1184); the rest are
header attributes, the answer contract, companions and documentation (§8).

---

## 4. The proposed core

Un-id'd shared cells do not need an identity of their own. They need a recorded
order and a merge. The prototype (`scripts/sync_model.py`) has three layers.

### 4.1 What the ledger records

Per deck part and half: the sequence of cells as recorded, each entry a slide id
or none, the kind, and the content fingerprint. This replaces the `pos:`
entries, the per-half spans, `member_order`, `group_order` and
`group_order_by_side`. Id-keyed entries keep their fields.

An un-id'd cell's handle is its index in the recorded sequence. It is stable
for as long as the ledger section is unchanged, which `report_id` already pins.

### 4.2 Align each half to the record, never half to half

For one half, in this order:

1. **By id.**
2. **By exact content,** first in order (a longest common subsequence), then
   moved (the same content among what is left, in occurrence order).
   Byte-identical cells are interchangeable, so occurrence order is the only
   pairing they admit.
3. **New on both halves.** Cells unmatched on both halves with the same id or
   the same content are one new member.
4. **Replaced in place** (inference). Between two neighbours that kept their
   relative order, the unmatched recorded cells and the unmatched current cells
   of one kind pair up in order. A surplus is removed or new.
5. **The only one left** (inference). One unmatched recorded cell and one
   unmatched current cell of a kind in the whole part: moved and edited.

Which German cell corresponds to which English cell is then derived: the two
cells aligned to the same recorded cell. Slides play no part, so a cell moved to
another slide, or a cell that became a `subslide`, needs no rule.

### 4.3 Merge membership and content, per member

| recorded member | result |
|---|---|
| present on both halves | merge the content (below) |
| gone on one half, untouched on the other | removed |
| gone on one half, changed on the other | question, `remove_vs_edit` |
| localized, gone on one half | question, `remove_localized_side`, as today |
| gone on both | gone |

| new member | result |
|---|---|
| on one half, shared | copied |
| on one half, localized | question, `translate_new`, as today |
| on both halves | kept |

Shared content is a 3-way value merge: the halves agree, or one half changed it
(take the change), or both changed it differently (`conflict_shared`). Localized
content keeps today's per-id classification; the prototype does not model it.

### 4.4 Merge order, per pair of cells

For every two recorded cells present on both halves, compare "x before y" in the
record, in German and in English. If the halves agree, take that. Otherwise
exactly one half changed it: take the change.

- If the result is a consistent order, that is the merged order. Independent
  moves on the two halves merge without a rule for moves.
- If it is cyclic, the halves reordered the same cells incompatibly: one order
  question, answered `de` or `en`.
- **Policy, not mechanism:** the prototype also asks when both halves changed
  order within the same slide, even where the changes are compatible. That is
  today's doctrine ("decision when both moved") and what the probe's oracle
  expects. Slides are used only to decide how wide this question is.

A cell that only one half places (a new cell, or one kept after a removal
question) goes directly after its predecessor on that half and before its
successor. If the other half moved one of the two, it goes after the
predecessor. Two unrelated additions at the same place: German first.

### 4.5 The one new question: which cell is which

Inferences 4 and 5 are guesses. The rule for acting on a guess:

> A guess is acted on where every reading of the files gives the same result,
> or where it is the only one-to-one reading. Otherwise ask, and only when the
> other half changed one of the cells in question.

Concretely, per half and kind: take the recorded cells with no exact match on
that half, and the current cells with no exact match. The model asks, for each
such recorded cell, "which of these cells is it, or was it removed?" when

- both lists are non-empty, and
- they are not one for one (two or more on either side), and
- the other half changed at least one of those recorded cells: its content, its
  presence, or possibly its place.

"Possibly its place" is exact: some longest run of recorded cells still in
recorded order on that half does without the cell.

Two rules of the same kind cover byte-identical cells:

- When a half removed one of several identical recorded cells and the other
  half changed another copy, which copy was removed is unknowable, so the
  removal is a question.
- When each half edited one of several identical recorded cells, whether they
  edited the same cell is unknowable. If they did not, both edits stand; if
  they did, it is a conflict and the other copy survives. The model asks
  (`conflict_copies`, answered `distinct`, `de` or `en`). This is decision D3
  (§9); before it, the prototype merged both edits.

This question is meant to replace what the engine expresses today as
`pool_placement_divergence`, `pool_pairing_shifted`, `ambiguous_alignment`, the
answerless one-sided `verify_cold`, and the advice to stamp ids by hand. It can
always be answered by reading the cells shown.

### 4.6 Render and record

Both halves are rendered from the one merged sequence, so they agree by
construction, and there are no phases and no intermediate states on disk. The
structural verify becomes an assertion about the renderer rather than a judge
of the plan.

Recording is the merge's own output: the merged sequence and the fingerprints
of the members that were resolved. A member with an open question keeps its
recorded state, and both halves keep their own cell. **This part is designed and
not prototyped**: the prototype stops at the first unanswered question.

### 4.7 What it would replace

The structural rows for un-id'd cells and for order: `mirror_order`,
`record_order`, `order_decision`, `mirror_pool_move`,
`pool_placement_divergence`, `pool_pairing_shifted`, `remove_vs_split`, and the
positional forms of `propagate_shared_edit`, `copy_new_shared`, `mirror_remove`,
`stamp_vs_new`, `ambiguous_alignment` and `verify_cold`. In the code: the pool
alignment, spans, crossed sync points, the order scopes, the placement
executors, the pool freezes, and the lens's positional pairing. This list is the
design's intent, not a measured diff.

### 4.8 Tried and dropped while building the prototype

Recorded so the next attempt does not start from them again.

1. **A plain 3-way sequence merge (diff3) with the content as the token.** A
   move is then a delete plus an insert. When the other half edited the moved
   cell, its edit meets a deletion, and no answer gives "moved and edited".
   That is the probe's most common family. Hence identity first (§4.2), with
   order and content merged separately.
2. **Placing a new cell by all its pairwise relations on its own half.** Its
   relations to a cell the other half moved are stale, and the new cell landed
   on the wrong side of it (124 `split_other_only` cases). Hence neighbours
   only (§4.4).
3. **Reading an unmatched recorded cell as removed whenever its gap did not
   pair one for one.** Where the other half had moved that cell, the edit
   silently stayed behind (108 `insert_delete` cases). Matching cells new on
   both halves first, the replaced-block reading and the identity question
   closed it in steps: 108, 36, 10, 0.
4. **Pairing by body similarity.** Not used. The ledger holds fingerprints, so
   similarity is only available half to half, and a threshold tuned to a
   synthetic probe would be one more special case. The identity question covers
   what similarity would guess at.
5. **Acting on a guess among byte-identical cells.** Found by the exhaustive
   check, not by the probe (§5).

---

## 5. Laws, and what was checked

The probe needs an oracle that knows what the author intended. The laws below
need none: they hold for every input, so every small input can be tried.
`scripts/sync_model_laws.py` tries every recorded deck of up to 4 cells against
every half of up to 5 cells, over two id'd localized cells and un-id'd contents
`x y z` (contents repeat, so identical cells are covered), with no question
answered.

| law | cases | violations |
|---|---:|---:|
| Nothing changed: no question, nothing changes | 191 | 0 |
| Only one half changed: no question, and the result is exactly that half | 212,612 | 0 |
| Swapping the halves gives the same content | 492,567 | 0 |
| No merge that asked nothing holds more copies of a cell than a half holds, or fewer than both hold | 492,567 | 0 |
| No merge that asked nothing drops an addition | 492,567 | 0 |

One qualification:

- **Order under swap.** In 128,444 of the 492,567 merges the swapped result has
  the same cells in a different order. The model breaks ties between unrelated
  additions German first, which explains such differences; they were not
  classified one by one.

The numbers are those after decision D3 (§9). Before it, 500,395 merges asked
nothing, and in 7,828 of them the result held fewer copies of a content than
each half: the recorded deck held two byte-identical cells, each half had
edited a different one, and the model merged both edits. The model now asks in
exactly those 7,828 (`conflict_copies`, §4.5), as the engine's conservation
gate (#1141) withholds that shape. The probe never produces it: the model's
score and its question counts on the probe are unchanged.

The exhaustive check found two defects in the prototype that the probe had not:
the identical-cells removal case of §4.5, and the case above. That is the
argument for laws over a hand-built probe.

The second law is the one the engine lacks today (§2.2). It holds in the model
for a structural reason: when one half equals the record, every merge rule
takes the other half's value, whatever the alignment guessed.

---

## 6. Formalization

Proving properties in Lean is feasible for this core, and not for the current
engine. No proof assistant was available in this session, so nothing here is
machine-proved; §5 is exhaustive testing.

**What makes it feasible.** The laws do not depend on the heuristics. Alignment
steps 1 to 3 are exact, and steps 4 and 5 only decide how many questions are
asked. So the theorems can quantify over every alignment that respects ids and
exact content, and the two proof-heavy functions (the longest common
subsequence and the longest increasing run) need not be verified at all.

**What to state.** A model of cells and sequences, the merge of §4.3 and §4.4,
and:

1. the three laws of §5 (unchanged, one-sided, symmetric in content);
2. conservation for merges that ask nothing;
3. stability: merging the result with itself asks nothing;
4. for order: the merged relation is a total order exactly when it has no
   cycle. It relates every pair of cells, so this is the elementary fact that
   an acyclic tournament is a linear order.

**Cost.** A few hundred lines of definitions and an estimated one to two
thousand lines of proof.

**What it buys, and what it does not.** A proof certifies the model, not the
Python. It would not catch a regression in the implementation. The regression
guard is the laws as tests in CI, which cost almost nothing (§9, step 1). Lean
is worth doing once the core is the production path, as a one-time certificate
of the design. It says nothing about the text layer (parsing and rendering,
which the lens round-trip tests cover) or about translation quality.

**Closest prior work**, for whoever takes this up: Khanna, Kunal and Pierce, "A
Formal Investigation of Diff3" (2007), on which properties a 3-way sequence
merge has; Pierce and Vouillon's specification of the Unison file synchronizer
and the Harmony work by Foster et al., on synchronizers with a recorded common
state; "Matching Lenses" (Barbosa et al., 2010), on alignment by position
against alignment by key; and Gomes et al., "Verifying Strong Eventual
Consistency in Distributed Systems" (2017), for machine-checked convergence of
list edits.

---

## 7. Limits of the evidence

- **The prototype is abstract.** A cell is an id or none, a kind and a content.
  There is no text layer, no tags, no `lang`, no companion files, no id
  stamping, no fork or unify. The probe exercises none of these either: it
  reorders, inserts, deletes and edits.
- **The identity question is answered by a perfect reader.** The harness knows
  the right cell from the names the case was built from. The probe's other
  policies have the same character, but an agent can misread.
- **Partial progress is not modelled.** The prototype stops at the first
  unanswered question (§4.6).
- **The probe was built around the current engine's failures.** A new core will
  have blind spots of its own and needs its own adversarial review. §5 is a
  start: it found two cases the probe could not.
- **The ledger changes.** Recording the cell sequence is a schema change with a
  migration (§9, D4).
- **Small scope.** §5 covers decks of up to 4 recorded cells. The laws are
  structural and unlikely to fail only on large inputs, but that is an argument,
  not a measurement.

---

## 8. Open issues

**Measured** (probe families the prototype converges on and master does not, or
not always): one-sided reorders, inserts and deletes; cells moved between slides
(the #1142 class); byte-identical cells (the #1051 and #1054 class); a slide
inserted on one half beside moved cells (the `split_*` families; #1179's own
shape, two id'd voiceovers swapped and a new slide inserted after them, was
checked directly and merges with the one `translate_new`); both halves moving
and editing un-id'd cells (#1144's residual, now a question).

**Expected by construction, not measured** (the prototype has no tags):

- #1092, #1187, #1216: a slide-hood change re-keys the cells under it. No key
  embeds the slide, so an un-id'd cell that becomes a `subslide` is a content
  change of one cell.
- #1185: a new un-id'd cell under a new slide is a plain addition.
- The frequency of "give it a `slide_id`" as the manual exit, since an un-id'd
  cell no longer needs an id to be addressed.

**Addressed by §4.6 if built as designed, not prototyped:** the engine reading
its own write (#885, #1071, #1137, #1179), and recording that banks more than
was decided (#1181, #1187).

**Not addressed:** #1180 (`vo_anchor` is not a synced aspect), #1182, #1188,
#1189, #1171, #1191, #1192 (answer contract and report), #1183 and #1190
(verify and `for_slide`), #1186 (stamping across a fork in flight), #1102
(fingerprint definition), #1117 (cross-deck move). #1118 (companion order) is
one more sequence to merge, not modelled. #1180 points at a second, smaller
design gap: header attributes are merged by specific rows (tags, owner, `lang`,
id) and anything else falls through, where design §6.3 intended one record
compared field by field.

---

## 9. The path, and decisions

Each step is useful on its own and can be stopped after. The order is the one
the maintainer agreed to on 2026-11-10; it differs from the first draft of this
document by step 2, which was not in it.

1. **Laws as tests against the current engine.** Done (§2.5):
   `tests/slides/test_sync_laws.py` is a ratchet in CI, and
   `scripts/sync_laws.py` runs the wider scope and the prototype.
2. **Extend the prototype before touching the engine**: partial progress
   (§4.6), and tags with slide-hood. These carry the claims §8 marks "by
   construction" or "if built as designed", and testing them costs no engine
   risk.
3. **Record the cell sequence in the ledger,** additively, beside the current
   entries.
4. **Run the new core in shadow** beside the differ: on the probe, on the law
   cases, and on real history through `scripts/scan_sync_history.py`. The probe
   and the law cases are generated shapes; real history is what measures the
   benefit to an author.
5. **Switch the structural rows over** and delete what §4.7 lists. The
   maintainer decides go or no-go before this step, on the results of 2 and 4.

Decisions:

- **D1. Pursue this, or keep fixing the current structure.** Decided
  2026-11-10: pursue it, in the order above, with the go/no-go before step 5.
- **D2. How wide an order question is.** Decided 2026-11-10: as today, whenever
  both halves reordered within one slide (what the prototype does). The
  switch-over then changes the mechanism and not the policy, and the probe's
  oracle stays valid. Narrowing it to incompatible reorders can be measured
  later; it would save at most the 2,533 order questions of §2.3.
- **D3. Identical recorded cells where each half changed a different copy.**
  Decided 2026-11-10: ask. The prototype does (§4.5, `conflict_copies`), and
  the conservation law of §5 now holds without an exception.
- **D4. The ledger migration**: re-record from a clean report, and what
  happens to a deck with pending items. Open. It is not needed before step 3
  is designed, and it needs a count of decks with pending items in the course
  repositories first.
- **D5. Whether to prove the laws in Lean** once the core is in production
  (§6). Open, and no decision is needed before then.

---

## 10. Reproducing

```
python scripts/sync_probe.py run ENGINE.json --workers 12      # master, 67,871 cases
python scripts/sync_model_probe.py run MODEL.json              # the prototype
python scripts/sync_model_probe.py run NOID.json --no-identify # without the identity question
python scripts/sync_model_probe.py compare ENGINE.json MODEL.json
python scripts/sync_model_laws.py                              # the laws, about 4 minutes
python scripts/sync_laws.py run LAWS.json --scope full          # the laws on the engine, 28,257 cases
python scripts/sync_laws.py run LAWS_MODEL.json --scope full --model   # and on the prototype
```

The engine run took 252 seconds with 20 workers on the development machine; the
prototype run takes about half a minute. Point `TMP` at a fast drive for the engine
run. The result files are not committed.

| file | what |
|---|---|
| `scripts/sync_model.py` | the prototype core |
| `scripts/sync_model_probe.py` | scores it on the probe and compares two runs |
| `scripts/sync_model_laws.py` | the exhaustive law check |
| `tests/slides/sync_laws.py`, `scripts/sync_laws.py` | the laws on the engine (§2.5), with `tests/slides/test_sync_laws.py` as the ratchet |
| `docs/claude/analysis/sync-structural-core/issue-classification.md` | the issue classification behind §3.5 |
