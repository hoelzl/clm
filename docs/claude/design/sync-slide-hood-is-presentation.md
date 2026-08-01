# Slide-hood is presentation, not identity

**Status**: thesis SHIPPED (#761); the §3 scope mechanism **withdrawn** on
measurement — see §13 | **Created**: 2026-07-31 | **Amended**: 2026-07-31
**Parent**: `docs/claude/design/sync-total-identity-document-model.md` (§3.3, §3.4, §7.4)
**Supersedes**: Phase 3 of `docs/claude/sync-v3-adversarial-review.md` §8
**Issues**: #653 (the refusal), #652/#738 (order), #650 (owner rows), #656 (ceremony)

---

## 1. Thesis

The `slide` / `subslide` tags are a **presentation attribute**: they select the
transition that happens when the cell is shown. Authors add and remove them for
visual reasons — "this slide looks too full once the output is displayed" — with
no intent to change the document's structure or meaning.

The current model treats slide-hood as an **identity regime selector**. It
decides how a cell is paired with its twin, what its ledger key is, and what the
keys of its neighbours are. So a cosmetic edit produces a whole-deck refusal
(#653), or silently re-keys cells nobody touched.

> No identity — member key, scope token, pairing route, or trust handle — may
> depend on whether a cell carries the `slide` tag.

This is not a new principle. It is P2 of the parent design ("identity never
depends on the transitioning attribute") applied to an axis §7.4 forgot to
enumerate. The review (G2) diagnosed the same defect and proposed *adding the
role axis to the transition space*; this addendum argues the axis should not
exist, which is both more correct and less machinery.

### 1.1 Two field observations that motivate it

- **Slide-hood is fluid by design.** The slide/subslide distinction is only the
  transition on display; introducing or removing a slide break is routinely a
  function of how full a rendered slide looks. It is not a semantic boundary the
  author is asserting.
- **Narration attaches to cells, not to slides.** Authors attach a narrative
  cell to whatever content cell it accompanies. The build already agrees:
  `slide_parser.py:315-330` appends any `is_narrative` cell to the group it is
  physically inside, regardless of `for_slide`. `for_slide` is a sync-layer
  construct, and the identity token for narration
  (`anchor_primitives.narrative_anchor_token`) is *predecessor cell* + owning
  group bounds — the predecessor half is already cell-shaped.

Both say the same thing: the engine's structural backbone is finer-grained than
the slide, and pinning identity to slides makes the engine notice changes that
are not changes.

## 2. Where slide-hood currently leaks into identity

| # | Site | Mechanism | Cost of a boundary move today |
|---|---|---|---|
| L1 | `doc_lenses.py:447-448` | `pair_by_id` **excludes anchors** — they pair via group co-location instead | Same id becomes two members → `duplicate_id`, whole-deck refusal, zero items (#653) |
| L2 | `doc_lenses.py:933-953` | `MemberKey.positional(group.anchor_id, kind, ordinal)` — the scope token **is the anchor's id** | Every id-less cell in the span re-keys → cold in the ledger → `verify_cold` rows for untouched cells |
| L3 | `doc_lenses.py:749-752` | Region pairing runs **per paired group**; a one-sided group pairs nothing | Id-less members in the span frame as one-sided adds/removes on both halves |
| L4 | `doc_lenses.py:881, 918-926` | `for_slide` resolves against `by_anchor` only | A slide that stops being a slide orphans its narration → `owner_missing` → `broken_owner` |
| L5 | `doc_ledger.py:114-116` | Order scopes keyed `(lang, group, part)`; `group_order` is a sequence of **anchors** | A boundary move drops an entry from the deck-level sequence and orphans a `member_order` scope |
| L6 | `anchor_primitives.py:251-265` | `narrative_anchor_token` scopes the predecessor anchor to `owning_group` bounds | The narrative's identity token shifts — and it is **shared with the recording watermark**, where drift means silent misplacement (PR #199 invariant) |

L1–L5 are in scope for this addendum. **L6 is deliberately out of scope** — see
§8.3.

Note what is *not* in the list: `slide` ↔ `subslide` retags are already
harmless, because `is_slide_start = is_slide or is_subslide`
(`slide_parser.py:42-49`) so anchor-hood survives. Only the anchor ↔
non-anchor half of the axis is broken, which is exactly the half authors flip
for layout reasons.

## 3. The rule: scopes are delimited by id-bearing cells

> **Withdrawn — see §13.** Implementing this rule and measuring it against the
> corpus showed it makes positional keys *more* fragile overall, not less. The
> thesis (§1) and the dissolution it implies (§4.1 items 1–3) shipped in #761
> and stand; what follows in this section is kept as the record of a design
> that measurement rejected.

**Scope owner.** A deck cell carrying a bare `slide_id`, whether or not it is a
slide start. Plus the existing synthetic owners: `HEADER_GROUP` (cells before
the title macro), `PREFACE_GROUP` (no title macro), `ORPHAN_GROUP`
(unplaceable companions), and the title macro itself, which owns the reserved
id `title` (it carries no `slide_id`; §3.3 unchanged).

**Scope.** Within one part (`deck` / `companion`) on one side: the maximal run
of cells from a scope owner up to, but excluding, the next scope owner.

**Positional key.** `pos:<scope owner id>/<kind>/<ordinal>`, where `ordinal`
counts the id-less members of that kind class **within the scope** in merged
document order. Identical in shape to today (`bilingual_doc.py:75-96`); only
the token's source changes: nearest preceding *id-bearing cell* instead of
nearest preceding *anchor*.

**Scopes refine groups.** Since anchors are id-bearing (an id-less anchor is
already refused, `idless_anchor`), every new scope is contained in exactly one
old group, and the first scope of a group carries the old group's token. The
new scoping is a **subdivision** of the old, never a merge. §8 leans on this.

### 3.1 Worked example — the #653 deck

```
                      EN half                         DE half (tags=["slide"] removed)
  [md slide id=u-curve]         anchor, group U      [md slide id=u-curve]        anchor, group U
  [md            ]              pos:u-curve/md/0     [md            ]             pos:u-curve/md/0
  [md slide id=u-curve-explain] anchor, group X      [md       id=u-curve-explain]  member of group U
  [md            ]              pos:u-curve-explain/ [md            ]             pos:u-curve/md/1  ← re-keyed
                                    md/0
```

*Today*: `u-curve-explain` is an anchor on EN (excluded from `pair_by_id`,
minted as a one-sided group anchor member) and a plain member on DE (paired by
id, minted inside group U). Two members, one key →
`_check_key_uniqueness` (`doc_lenses.py:976-986`) refuses the deck. And even if
it did not, the id-less cell below it changes key on the DE side.

*Under the rule*: `u-curve-explain` is a scope owner on both sides, pairs by id
like any other cell, and the id-less cell below it is
`pos:u-curve-explain/md/0` on **both** sides. The only observable difference
between the halves is `de.tags = []` vs `en.tags = ["slide"]` — a cross-side
tag divergence, which is already #615's territory
(`sync_diff.py:1800-1863`): a mechanical `mirror_tags` whose direction comes
from the ledger's per-side `tags_fp`, or a `record_tags` transition if both
sides moved. Nothing else in the deck notices.

## 4. Component changes

### 4.1 Lens (`doc_lenses.py`)

1. **`pair_by_id`** (:435): drop the `_is_anchor` exclusion at :447-448. Every
   id-bearing cell pairs by id, across groups and across parts, as the docstring
   already promises for non-anchors.
2. **`_segment_deck`** (:202): emit **scopes** (id-delimited) alongside the
   existing group segmentation. Groups survive as a derived view — they are
   still what a slide *is* — but they stop being the keying and pairing unit.
3. **`_build_anchor`** (:848): build the group's anchor member from the by-id
   pairing result rather than from group co-location, so a pair that disagrees
   on slide-hood is **one** member. When the sides disagree, attach an
   `anchor_shape_divergence` observation naming both sides.
4. **Region pairing** (:749-752): pair **scope against scope**, matched by
   scope-owner id. Scope correspondence is by id, so it survives slide-hood
   divergence — this dissolves the review's "group tail slicing" problem
   (L3) rather than solving it. A scope owner present on one side only yields a
   one-sided scope, exactly as a one-sided group does today.
5. **`_assign_positional_ordinals`** (:933): count per scope.
6. **`_place_companions`** (:881, :918): resolve `for_slide` against **any**
   scope owner, not `by_anchor`. `member.owner` becomes the referenced cell's
   key. `owner_missing` then fires only when the referenced id genuinely does
   not exist.
7. **`_check_key_uniqueness`** (:976): unchanged, but anchor-shape divergence
   can no longer reach it. `duplicate_id` keeps its meaning — two cells really
   claiming one id — and its `rename-id` hint becomes correct for every
   surviving cause.

### 4.2 Differ and apply

Mostly free. The merged member presents divergent tag sets, which `_check_tags`
and `_tags_only_change` already frame (`sync_diff.py:131-132, 1800-1863`); the
write is a tag-line rewrite the executor already performs, and the group
boundary follows on re-parse because it is derived. What does need attention:

- **Owner rows** (`sync_diff.py:1245-1300`): with `for_slide` allowed to name
  any id-bearing cell, a boundary move produces **no owner change at all** —
  narration keeps pointing at the cell it accompanies. `broken_owner` and the
  #650 `retarget_owner` path stay as they are, for genuine removals and renames.
- **Placement** (`doc_apply.py:753`): companion placement lands inside the
  owner's *scope*; the containing slide is derived where the build needs it
  (`anchor_primitives.owning_group`), not asserted as an identity precondition.
- **`_detect_group_renames` / `group_map`**: today keyed on anchors. Extend to
  scope owners, so a renamed id'd non-anchor cell carries its scope the way a
  renamed anchor carries its group.

### 4.3 Ledger (`doc_ledger.py`)

- `member_order` keys `(lang, group, part)` → `(lang, scope, part)`: same
  shape, new token.
- `group_order` / `group_order_by_side` → the sequence of **scope owners**.
  This is a refinement of the current sequence (strictly more order evidence),
  and a slide→continuation change no longer deletes an entry from it — which
  also removes one of the ways order trust silently erodes (#652-i2 shape).
- `SCHEMA_VERSION` 2 → 3, with the migration in §8.

### 4.4 CLI and docs

- `rename-id` must cascade scope tokens for **any** id-bearing cell, not only
  group anchors (`rename_id.py`, `doc_ledger.rename_group_scopes`).
- `clm info sync-agents`: `anchor_shape_divergence` as an observation, the
  corrected `duplicate_id` hint, and the `for_slide` relaxation.
- `clm info slide-format`: `for_slide` may reference any id-bearing cell.

## 5. What deliberately does not change

- **Ordinals stay scope-local.** Global positional keys would make any insert
  re-key the rest of the deck. Locality of churn is the reason group-scoping
  existed; the rule keeps it and only changes what defines the scope.
- **Identity is never inferred.** No fingerprint matching, no content-derived
  correspondence. Scope owners are read off the file.
- **The two sanctioned key migrations** (id-stamp, `rename-id`) stay two. §8's
  migration is a one-time schema upgrade, not a third inference path, and §9.1
  describes the stamp cascade honestly rather than hiding it.
- **Groups remain real.** Slides are what the build renders and what narration
  is spoken over. They stop being the *identity* unit; they remain the
  presentation unit.

## 6. The transition space (§7.4) gains an invariant, not an axis

The parent note's property 2 enumerates `langness × id × layout × side`. The
review proposed adding a role axis. Under this addendum there is nothing to
add — instead §7.4 gains a fourth property:

> **Property 4 — presentation attributes are identity-inert.** For any deck and
> any id-bearing cell, toggling the `slide` tag (either direction, on either
> side) leaves the member key set, the pairing, and every ledger handle
> unchanged. The only diff is the tag row.

This is directly machine-checkable, and it is the acceptance test for the whole
change: a property test that walks every deck in the bundled corpus, flips the
`slide` tag on each id-bearing cell in turn, re-parses, and asserts key-set and
pair-map equality. It would have failed on #653 from day one.

## 7. Why this is smaller than the review's Phase 3

| Review Phase 3 item | Under this addendum |
|---|---|
| Merge same-id anchor↔non-anchor into one member | Falls out of removing the `pair_by_id` exclusion — nothing to merge, they were never split |
| Add the role axis to §7.4 with a `mirror_tags`-and-move-boundary row | No axis; the existing #615 tag rows already cover it |
| Extend region slicing for group tails | Dissolved: id-delimited scopes correspond across sides by construction |
| Frame the id-less fork-in-progress instead of refusing (M5) | Still needed, still separate — unrelated refusal cliff in the same file |

The cost moved from engine complexity to a **one-time ledger migration**, which
is bounded, computable, and verifiable.

## 8. Ledger migration (schema 2 → 3)

The ledger is a committed per-topic artifact,
`<topic>/.clm/sync-ledger.json` (`doc_ledger.py:76-77`). Migration must be
reliable enough that a course repo can be updated in one pass and trusted
afterwards.

### 8.1 The mapping is computable from the current parse alone

A positional key does not name a historical cell; it names *whatever occupies
that slot now* — the engine already re-resolves `pos:` keys against the current
parse on every report. So the migration needs no record of the deck's state at
record time. Parsing each deck once yields, for every id-less member, both its
old token (nearest preceding anchor) and its new token (nearest preceding
id-bearing cell), hence a deterministic `old_key → new_key` map. Because scopes
subdivide groups (§3), the map is injective and the first scope of every group
is a fixed point.

Applied to: member entries, `member_order` handles, `owner` references,
`group_order` / `group_order_by_side`. Keys that do not resolve in the current
parse are dropped — they are cold under either scheme, and dropping them is the
`prune_dangling_refs` precedent (#718).

### 8.2 Command and rollout

```
clm slides sync migrate-ledger DECK|DIR [--dry-run] [--json] [--verify]
```

- **`--dry-run`** prints the full old→new mapping, per-deck counts, and every
  unresolved key. Default for the first run in the docs.
- **Idempotent**: a schema-3 ledger is a no-op.
- **Atomic**: existing `atomic_write_all`, plus the M8 UUID-temp fix if it has
  landed by then.
- **`--verify`** is the reliability guarantee the migration is judged on: run
  `sync report` before and after, apply the key map to the "before" item set,
  and assert the two item sets and both `is_clean` verdicts are equal. A
  migration that changes any verdict is a bug, not a migration.
- **Fail-closed, never silent**: in the release that introduces schema 3, a
  schema-2 ledger loads read-only with a loud warning naming the command;
  `record` and `apply` refuse to write to it. Auto-migrating on load would
  rewrite a committed artifact in the user's repo without being asked, and a
  silent re-key is exactly the failure class this addendum exists to remove.
  One release later, schema 2 is rejected (cold start), matching the schema-4
  wire-format rollout.

### 8.3 Recovery

Ledgers are committed, so `git checkout` restores them — **but verify they are
tracked before migrating**. Some course repos gitignore `.clm/`, which hides
ledgers from git entirely (a known landmine). The command must check
`git ls-files` per ledger and, when the file is untracked, write a
`sync-ledger.json.pre-schema3` backup beside it rather than relying on git.

## 9. New costs and residues — state them, do not hide them

### 9.1 The stamp cascade

Stamping an id onto a previously id-less cell now **splits a scope**, re-keying
the id-less cells after it within the same slide. Today stamping is collateral-
free. This is the one place the addendum makes things worse, and it must be
handled by the same key-migration machinery, applied in the same operation:
`normalize --stamp-ids` and the #443 one-sided stamp transition migrate the
following keys as part of the stamp, or those cells go cold.

The trade is still clearly right: stamping is a deliberate, tool-driven
operation with an existing sanctioned migration point, whereas boundary moves
are casual hand edits made for visual reasons. The cascade moves from the
frequent, invisible case to the rare, explicit one.

### 9.2 `rename-id` grows

Renaming any id-bearing cell — not just an anchor — now cascades into scope
tokens and order scopes. `rename_group_scopes` already rewrites all four
reference classes after #718; it needs to be reachable for non-anchor renames.

### 9.3 Narration identity (L6) is out of scope

`narrative_anchor_token` still scopes to `owning_group`, so a boundary move
still perturbs a narrative's positional anchor. It is left alone deliberately:
the token is shared with the recording watermark, so changing it is a
cross-artifact migration touching recorded takes. Status quo, no regression,
own design pass. Record it in the parent note's §9 residue list.

### 9.4 Interaction with #654

Order scopes became first-class three days ago (#654 / PR #727). §4.3 changes
their key token and widens `group_order` to all scope owners. This must be
designed against the shipped code, not the design note, and re-verified with
#654's own regression tests.

### 9.5 `for_slide` is now a misnomer

It may reference a cell that is not a slide. Renaming the authoring attribute
is a breaking change to every deck in the corpus and is **not** proposed here;
the docs should describe it as "the cell this narration accompanies".

## 10. Sequencing

| Step | Content | Gate |
|---|---|---|
| 0 | **Measure** the migration: how many `pos:` keys change token across PythonCourses. Sizes §8 and confirms the subdivision property empirically | before implementation |
| 1 | **653a** — refusal degradation: `anchor_shape_divergence` code, per-side lines, correct hint, drop the circular `rename-id` hint. Independent of everything below | ship now |
| 2 | Lens: id-delimited scopes, anchors pair by id, divergence observation | behind schema 3 |
| 3 | Differ/apply/ledger: tag rows, owner relaxation, scope-token rename, `group_order` widening | with step 2 |
| 4 | `migrate-ledger` + `--verify` + rollout | with step 2 |
| 5 | Property-4 test over the bundled corpus; §7.4 amendment; info topics | with step 2 |
| — | Residues: L6 narration token; validate delegation (review Q4); M5 id-less fork refusal | later, separate |

Steps 2–5 are one PR: they change the key format together, and shipping them
apart would mean two migrations.

## 11. Open questions for the adversarial pass

1. **Is the subdivision property actually total?** It relies on every anchor
   being id-bearing. `idless_anchor` (`doc_lenses.py:425`) refuses those today — but the refusal is at
   parse time, so a deck can be *on disk* with an id-less anchor. Does the
   migration handle a deck it cannot parse? (Proposal: skip and report; the
   ledger is untouched and the deck was already refusing.)
2. **Do scopes want to nest?** A slide containing three id'd cells becomes four
   scopes; the slide is no longer recoverable from the key alone. Does anything
   need "the slide this key is in" as trust data, or is deriving it from the
   parse always enough?
3. **`group_order` widening and `common_set`**: more entries means more order
   evidence, but also more ways two halves legitimately differ mid-transition.
   Does the #654 order-parity gate need a scope-owner exemption during
   in-progress stamps?
4. **Companion pool keys** (`_companion_pool_key`, N4) include the bare
   `for_slide`. With owners allowed to be non-anchors, does the pool
   partitioning still hold?
5. **Does anything outside sync read `pos:` keys?** MCP payloads and the
   `clm export context` surface echo handles; they may need the migration note.

## 12. Amendments

Add to the parent design note: a §13 row when the engine change lands, an entry
in the satellite-doc index pointing here (review finding D2), the §7.4 property
4 text (§6 above), and the L6 residue in §9.

---

## 13. Amendment (2026-07-31): the scope mechanism is withdrawn

**What shipped.** §1's thesis and §4.1's dissolution: slide-hood is a property
of the *pair*, a boundary only one half draws opens no group on either side,
and the state frames a mechanical `mirror_tags` row instead of refusing the
deck (#761, closing #653). That part is settled and correct — it removes the
tag from the *identity-regime* decision, which was the P2 violation.

**What is withdrawn.** §3's "positional keys scope to the nearest id-bearing
cell", and with it §8's schema 2→3 ledger migration.

**Why — the measurement.** A positional key re-keys when a *change point*
above it inside its slide is added, removed or renamed. The two rules differ
only in what counts as a change point:

| Rule | Change points | Count (PythonCourses, 730 decks) |
|---|---|---|
| anchor-scoped (today) | slide anchors | 9,950 |
| id-scoped (§3) | anchors **+ every id'd cell** | 13,217 — **1.3× more** |

The corpus has 3,267 id'd non-anchor cells (overwhelmingly localized markdown
cells, which must carry ids by §3.4). Under §3 each of them becomes a scope
owner, so *inserting, deleting or renaming any of them* re-keys the un-id'd
cells below it within its slide — an edit at least as routine as toggling a
slide tag, and one the current rule handles for free.

So the rule does not remove churn; it **relocates** it, from one frequent
trigger to a more numerous class of triggers — and charges a 1,792-entry
ledger migration plus a schema bump for the move. Implementation confirmed the
shape of it before the numbers did: the change broke 18 tests, and the
representative one is exactly the trap — a shared code cell under a localized
note re-keyed `pos:intro/code/0` → `pos:intro-note/code/0`, becoming hostage
to a cell that has nothing to do with it.

§9.1 already flagged the stamp cascade as "the one place this makes things
worse". The measurement shows that was the general case, not a corner: every
id'd cell is a stamp that already happened.

**What this leaves.** After #761 a boundary move still re-keys the un-id'd
cells in the affected span, so they report `verify_cold` once and re-bank with
`record`. That is bounded, visible, and loses nothing — noise, not damage.

**The real escalation path is Q1, not this.** Positional keys are fragile
because they are positional; scoping them differently only chooses which
neighbours they are hostage to. The way to make them stable is to stop having
them: a per-deck opt-in "fully id'd" mode (stamp on first sync touch), which
the review's Q1 already names as the escalation to take *after* measuring what
the Phase-0 guards left behind. That measurement now exists — this is a data
point for Q1, and the id-delimited-scope idea should not be re-proposed
without addressing the change-point count above.

---

## 14. Amendment (2026-08-01): Q1 is withdrawn, and the residue is cheap

§13 closed by naming Q1 — a per-deck opt-in "fully id'd" mode — as the real
escalation path for positional fragility, and by pointing at the residue #761
left: a boundary move re-keys the un-id'd cells in its span, which then report
`verify_cold` once. Both of those have now been measured
(`scripts/measure_positional_composition.py`, PythonCourses, 730 decks, 0
refusals). Both conclusions change.

**Q1 rested on the wrong metric.** The escalation was argued from "82.4% of
positional members sit in a pool with siblings". That number is correct and
close to meaningless: *membership in a pool > 1* scores a slide with two code
steps exactly like the 170-cell pool that headlined the argument. The metric
that reflects cost is **blast radius** — inserting or deleting one member of a
pool of size `n` re-keys about half its siblings, so the pool costs
`n*(n-1)/2`:

| pool size | % of positional members | % of churn |
|---|---:|---:|
| 1 (behaves like an id) | 17.6% | 0.0% |
| 2–3 (a slide's code steps) | 35.5% | 4.6% |
| 4–9 | 29.1% | 10.8% |
| 10+ | 17.8% | **84.6%** |

Churn is not diffuse, it is concentrated: the top 5 decks carry 48.9% of it and
the top 20 carry 76.8%. And those files are not decks. The 170-cell pool is
`slides_np_computation_old.de.py`, which has **exactly one anchor** — the title
slide, with every code cell in the file beneath it. The seven largest pools are
all 1-anchor files and four of the top ten churn carriers are legacy `*_old*`;
restricted to live decks with ≥6 anchors, the worst pool in the corpus is 35.
These are notebooks living in the slides tree. The remedy is anchors (or moving
them out), not a second identity regime for all 730 decks.

**The residue is cheaper than stated, because of what positional members are.**
Positional identity is **92.5% shared `code` cells**; the remainder is 729
one-per-deck localized `j2` headers, 133 shared markdown cells and 5 localized
code cells. Only 133 markdown cells corpus-wide are positionally keyed — prose
is already essentially 100% id'd, which also disposes of the hypothesis that
this is an id-*assignment* problem. Under `record_neutral` (main note §6.2.1,
agreed the same day) a cold two-sided `shared` `code`/`j2` member whose halves
agree on every compared field is banked mechanically instead of framed. So the
members a re-keyed pool sends cold are, overwhelmingly, exactly the class that
now **self-clears** at the next `record`. The §13 residue costs a ledger write,
not a question.

**Status.** Q1 withdrawn, not deferred — there is no remaining trigger to wait
for. Re-open only if a framed dead end actually recurs in the field, and argue
it from blast radius rather than pool membership. The §9 residue entry in the
main note stands unchanged: shared-member reorder plus a one-sided edit within
one group is still the one place positional identity is ambiguous, ceiling one
framed decision, permanently resolvable by minting an id.
