# Sync v3: Total Identity over One Document — Target-Model Design Note

**Status**: Design agreed with the maintainer — §12 decisions settled 2026-07-02;
phases 0–4 shipped (v3 is the only engine since 1.20.0); amended 2026-07-10
with the post-cutover refinements (§13), and 2026-08-01 with `record_neutral`
(§6.2.1) — agreed, not yet implemented
**Author**: Claude (Fable 5), with the maintainer
**Date**: 2026-07-01 (decisions recorded 2026-07-02)
**Issue**: [#520](https://github.com/hoelzl/clm/issues/520) (umbrella)
**Motivating assessment**: `docs/claude/sync-engine-architecture-assessment-2.md`
(evidence: `docs/claude/analysis/sync-engine-assessment-2/`)
**Supersedes (as the forward design)**: the per-channel classifier model of
`single-language-authoring-sync.md` + `sync-content-anchor-identity.md`, the
watermark storage model of `sync-git-as-baseline.md`, and the text-level
projection of `sync-separated-voiceover-companions.md` §5 — while deliberately
**preserving** their verified components (write boundary, verify gate, task
validators, ledger storage, split/unify lens) and their behavioral oracles.
**Relation to #448**: this note *finishes* the consistency-ledger direction —
the ledger becomes the only trust store instead of the fourth.

**Satellite design docs** (forward design that lives outside this note — keep
this index current; a satellite with no entry here is invisible, the review's
D2 finding):

| Doc | Scope |
|---|---|
| `sync-tag-parity-conflicts.md` | #615 — tags as a pair invariant; the `mirror_tags` / `record_tags` / `conflict_tags` rows |
| `sync-slide-hood-is-presentation.md` | #653 — slide-hood is a presentation attribute, not an identity regime; id-delimited scopes; ledger schema 3 |

---

## 1. Problem and goals

Assessment 2 identified three generative defects in the current engine:
identity is optional and heterogeneous (≥8 coexisting schemes selected by
incidental metadata); the baseline is out-of-band mutable state (three
overlapping trust stores); and the engine models 2 files while the product is 4
(companions handled by a text-level projection). ~80% of the ~35-defect history
traces to these three; the observable results are a ~96% false-positive rate on
the one real production reconcile, agents abandoning the tools mid-task, and a
maintainer who bypasses the tool entirely.

Goals, in priority order:

1. **A noise-free `report`**: a member flagged for work is genuinely out of
   sync, with the bytes attached. (The dogfooding bar: the W10 scenario must
   report ~3 items, not 73.)
2. **A mechanical, partial-progress `apply`** an agent can trust: per-item,
   value-keyed, never loses completed work, atomic per deck (≤4 files).
3. **One identity model and one trust store**, closed under authoring
   evolution: new cell properties and class transitions (neutral↔localized,
   id-less→id'd, inline↔companion) must be expressible as *state changes* in
   the existing model, never as new identity mechanisms. No further patch
   cascades by construction.
4. **Judgment stays with the judge**: the agent (or a model the human's
   `autopilot` chooses) decides direction, translations, and conflicts; the
   tool owns only the mechanics it can do perfectly.

Non-goals: changing the authoring format beyond a one-time id normalization
(§3.4); supporting mixed inline+companion voiceover in one deck (stays refused,
per the #501 invariant); syncing pre-split bilingual files (`suggest-sync` is
untouched).

---

## 2. Design principles (the closure rules)

These are the load-bearing rules. Every later section is an application of
them, and every future change must be checked against them — they are what
prevents the "endless series of patches" failure mode.

- **P1 — Identity is total.** Every logical cell has exactly one identity,
  computed once at parse time by one rule (§3.3), carried unchanged through
  report → decisions → apply → ledger.
- **P2 — Identity is invariant under every mutable attribute.** Lang-ness,
  tags, content, layout (inline/companion), and id-*upgrades* are member
  **state**, never identity-regime selectors. A cell that changes class keeps
  its key; the change itself becomes a diffable transition (§7). *(This is the
  direct answer to the maintainer's neutral↔localized observation.)*
- **P3 — Ids are monotone.** Ids are only ever added (minted at authoring
  time, at normalization, or by a fork event), never removed or required to be
  removed by a class transition. Any cell may carry an id regardless of class.
- **P4 — One document, N projections.** The four files (and the legacy inline
  layout) are lens projections of one parsed document, with declared,
  property-tested round-trip laws — the `split`/`unify` discipline
  generalized. Projection happens on the *parsed model*, never as text
  transforms.
- **P5 — One committed trust store.** All persistent sync state lives in the
  committed per-topic ledger, keyed by member identity and content
  fingerprints. No sqlite watermark, no path-keyed state, no representation
  markers. Anything derivable is a rebuildable cache at most.
- **P6 — The plan is derived once.** One generic 3-way diff over the unified
  member stream produces the full plan. No post-passes that retract or rewrite
  emitted proposals; no per-channel detector code — comparable aspects are
  **fields of one record** (§6.4), so a new aspect is a new field, included in
  the generic diff automatically.
- **P7 — Per-item application.** Every plan item is independently applicable
  and independently recordable. A member the engine cannot resolve becomes a
  framed decision for the judge; it never blocks unrelated items. Completed
  work is never lost.
- **P8 — Closure rule (meta).** A change request that cannot be expressed as
  (a) a new member-state field, (b) a new transition row in §7's finite table,
  or (c) a new framed-decision kind, is a signal the model is being violated —
  stop and redesign rather than special-case. This rule goes in the code as a
  review checklist and in the test suite as the transition-matrix enumeration
  test (§7.4).

---

## 3. The canonical document model

### 3.1 Structure

```
BilingualDeck
  header:   per-language j2/header members (fixed roles, singleton identity)
  groups:   ordered list of SlideGroup
SlideGroup
  anchor:   the id'd title/slide member that opens the group
  members:  ordered list of Member (everything until the next anchor)
Member
  key:      MemberKey                          # §3.3 — the ONE identity
  kind:     markdown | code
  role:     slide | notes | voiceover | aux | header | ...
  langness: shared | localized                 # STATE, not identity (P2)
  layout:   inline | companion                 # STATE, voiceover roles only
  owner:    MemberKey of the owning slide      # reference, serialized as
                                               # for_slide in companions
  tags:     per-language tag sets (equal for shared members)
  content:  shared    -> one body
            localized -> {de: body?, en: body?}   # a missing side = pending
```

One `Member` is the *logical* cell. A shared member appears byte-identically in
both language files; a localized member appears as its `lang="de"` variant in
the DE file and its `lang="en"` variant in the EN file; a `layout=companion`
voiceover member appears in the `voiceover_*` files instead of the decks. There
is no separate model for companions — #501's insight ("the four files are
renditions of one document") lifted from the text layer to the parsed layer,
where it needs no representation markers and no re-extraction compensation.

### 3.2 Parsing reads all four files into one document

`parse_bundle(de_deck, en_deck, de_comp?, en_comp?) -> BilingualDeck` pairs the
projections member-by-member (§3.3) and *records* mismatches as first-class
observations on the document (a member present on one side only, lang attrs
disagreeing, a companion cell whose owner is missing) rather than erroring.
Malformed-beyond-pairing input is a framed "normalize first" refusal for that
deck — never a degraded heuristic path.

### 3.3 The identity rule (closed, total)

```
MemberKey(member):
    1. id:<slide_id>            if the cell carries a slide_id
    2. pos:<group>/<kind>/<i>   otherwise: owning group's anchor id +
                                kind-class + ordinal among the group's
                                id-LESS members of that kind-class
```

with these supporting rules:

- **Ids are unique per deck across all roles.** A narrative/voiceover member
  carries its *own* id (the direction #501 already fixed as canonical:
  "narrative WITH slide_id"); its relationship to the slide is the `owner`
  reference, serialized as `for_slide`. Uniqueness is validator-enforced; a
  duplicate id is a normalize-first refusal.
- **Rule 2 is only ever reached by shared members and headers** after
  normalization (§3.4), because every localized and narrative member is id'd.
  For shared members, positional identity within the group is *sound in the
  steady state*: their content is byte-identical across projections by
  invariant, so cross-language pairing is trivial, and within-group sequence
  alignment (over content fingerprints) localizes any reorder/insert ambiguity
  to one group. The one genuinely ambiguous residue — reordering byte-identical
  boilerplate cells within one group *while* editing one of them on one side —
  is a framed decision, and minting an id on the affected cell (P3) resolves it
  permanently.
- **Cross-half id disagreement is a transition, not a regime split.** A cell
  id'd on one half and id-less on the other (the #443 shape) parses as one
  member with an `id-stamp pending twin` transition (§7.3) — a mechanical item
  that stamps the twin once the pairing is ledger-known, framed until then —
  because the id'd side's key wins and the twin is
  located by rule 2 within the same group. Under the current engine this
  configuration selects *two different classifiers*; here it is one row in the
  transition table.
- **Group identity** is the anchor's id. An id-less anchor (a title without an
  id) is a normalize-first condition. **Deck identity** stays path-derived with
  the existing rename recovery for now; a content-based stable deck id is an
  open item (§12) and orthogonal to this design.

### 3.4 One-time normalization (precondition, not heuristic)

Before a deck enters the v3 engine: every localized cell and every narrative
carries a `slide_id` (`clm slides assign-ids --accept-code-derived` already
mints deterministic ids for code; narratives get content-slug ids via the same
machinery). Corpus impact: ~1.7k id-less localized cells + the legacy id-less
narrative population — a one-time, reviewable normalize commit per course repo,
in a format agents already edit routinely. Shared cells are *not* stamped
(nothing forces ~13k-id churn; the old objection conflated naming with
alignment — shared cells need byte-parity, not names). The engine checks the
precondition per deck and emits a single framed "run normalize" item when it
fails; it never falls back to id-less-localized heuristics. This deletes the
cold-start mint/adopt/reconcile/refuse matrix from the engine (cold pairing
becomes part of `normalize`, run once, reviewed as a diff).

---

## 4. Projections (the lenses)

```
project(deck, lang=de, part=deck)      -> slides.de.py     text
project(deck, lang=de, part=companion) -> voiceover_*.de.py text   (if any
                                          member has layout=companion)
parse ∘ project == identity            (per part, per lang)
project ∘ parse == byte-identity       on normalized input
```

- Laws are property-tested (golden suites + Hypothesis over generated decks),
  extending the discipline `split.py` already proves. `merge_voiceover_text` /
  `_plan_extraction`'s pure cores are refactored *into* the lens rather than
  called around it; `vo_anchor` and `for_slide` become serialization details
  derived from the model (owner reference + position), not identity mechanisms.
- Because parsing consumes all four files into one document (§3.2), **read
  purity and write atomicity come for free**: report never mutates anything;
  apply renders the ≤4 projections and commits them through the existing
  `atomic_write_all`. No inlined intermediate state, no representation marker,
  no baseline-source projection discipline — there is nothing to project
  *onto*, because the model *is* the joint state.
- The wholly-inline-or-wholly-companion invariant survives as a per-deck
  validity check on `layout` values (all voiceover members agree per deck), and
  a violation is a framed normalize item.

---

## 5. Sync state: the ledger, finished

Per-topic committed file (as #448 established, e.g.
`<topic>/.clm/sync-ledger.json`), now the **only** trust store:

```
entry := {
  member:       MemberKey,
  langness:     shared | localized,          # recorded class (drives §7)
  layout:       inline | companion,
  fingerprints: {shared: h} | {de: h_de, en: h_en},
  tags_fp:      h_tags,                      # tag-set fingerprint per side
  state:        verified,
  provenance:   apply | accept | record | agent | semantic,
  hash_version: N
}
```

- **Content-keyed, per-member, committed.** This preserves both watermark
  properties the git-as-baseline note identified as essential: it can *lag*
  HEAD (a committed-but-unsynced edit still differs from the recorded
  fingerprint) and *lead* HEAD (sync-without-commit updates fingerprints that
  travel with the next commit). It also resolves the dogfooding's deepest
  finding — the baseline is naturally **per member**, so "X synced 3 days ago,
  Y never" needs no ladder of `--baseline`/`--since` mechanics to trust.
- **A never-recorded member is cold**: report shows it as `unverified` with a
  framed verification task (structural pairing evidence attached; the #448
  trust rungs — assume / structural / agent / semantic — survive as *task
  framing and provenance labels*, not as engine tiers).
  *Refinement (#566, 2026-07-05):* the cold rule applies to **two-sided**
  members — both bodies exist, their relationship is unknown, and `confirm`
  can record trust over them. A never-recorded **one-sided** member is an
  *add* (§6.2's add row), not cold: there is no twin whose trust could be
  asserted, and framing it cold is a dead end (`confirm` is rejected on a
  one-sided member). It falls through to `translate_new` (localized/header —
  framed) or `copy_new_shared` (shared — mechanical, safe because it can only
  fill an empty slot, never overwrite). Exception: a one-sided **positional**
  (un-id'd) member stays cold, because ordinal aliasing pairs it with a
  *different* twin cell at the same slot and the executor cannot locate the
  empty target — minting a `slide_id` routes it through the id-keyed add path.
  Where §5's cold rule and §6.2's add row overlap ("one-sided and
  un-ledgered"), sidedness decides: two-sided → cold, one-sided → add.
  *Refinement (#764, 2026-08-01):* cold means *the relationship between the
  halves is unknown*. For a **language-neutral** member that relationship is
  not unknown — it is observable. A two-sided un-ledgered member whose two
  sides agree on every field the differ compares, whose recorded langness is
  `shared`, and whose kind carries no natural-language content (`code` / `j2`)
  is resolved by the mechanical `record_neutral` row (§6.2) instead of framed
  as a question. Trust banked this way takes provenance `structural` — the
  #448 rung, now an actual provenance value: the engine observed the halves
  agree, no judge attested anything. **Prose is deliberately excluded**, see
  §6.2's predicate and §9's residue entry.
- **Stale = fingerprint mismatch**, which is fail-safe: it produces a re-check
  item, never silent trust. `hash_version` migrates entries lazily (re-verify
  on version bump), the #458 lesson encoded.
- The sqlite watermark, the `baseline` verb group, the six repair mechanisms,
  the `seed` bridge, and the representation marker are **deleted**. If a
  performance cache is ever needed, it is derived from the ledger and
  disposable. Git refs remain available to `report --since/--baseline` as
  *forensic views* (what changed in this window?), never as trust.
- Ledger merge conflicts (two branches synced the same member) are true
  positives; resolution = take the union where fingerprints agree, drop to
  `unverified` where they disagree (re-check item). Append-only history is not
  required — git history of the file is the audit trail.

### 5.1 Order scopes (specified with #654; shipped earlier unspecified — D3)

Alongside its member entries, each deck section records **order scopes**,
the trust context for §6.2's `order` outcome:

```
deck := {
  members:             {key: entry},
  group_order:         [anchor_id],                 # merged view (rename detection)
  group_order_by_side: {de: [anchor_id], en: [anchor_id]},
  member_order:        {(lang, group, part): [id-keyed member handle]},
  preamble_fps:        {(lang, part): h}
}
```

- **Id-keyed handles only.** Positional handles alias across states
  (ordinals renumber on any insert/remove), so the pool alignment owns
  their order; `member_order` never lists them.
- **Seeding.** A full `record`, `split`, or `translate-bootstrap` snapshots
  every scope; a landed order item records its own scope; and — issue #654,
  closing the C3 bootstrap circularity — an `apply` pass that ends with
  **zero unresolved items** seeds every scope whose two sides currently
  agree, compared over the handles both sides carry. A scope whose sides
  disagree is never seeded from agreement, and the structural write gate
  (#719) still arbitrates whether the save happens at all.
- **Absence frames, never trusts** (issue #654). Order is a *pair*
  invariant — the #615 lesson applied to sequences: the differ checks the
  sides' **current** orders against each other unconditionally (over their
  common two-sided handles, minus members carrying their own cross-group
  move item) and frames an `order_decision` on divergence, cold decks
  included. Recorded order trust *refines* that verdict into the directed
  mechanical rows (`mirror_order` / `record_order`); it is not a
  precondition for seeing the divergence. A parse-observed
  `group_order_divergence` also suppresses `report`'s `is_clean` — the
  observation's coverage is wider than the differ's item coverage.
- **Cross-bracket placement with no evidence frames on the member, and
  banks nothing** (#654 adversarial rounds 1–2). A member whose sides sit
  under different *physical* group brackets, with no recorded placement,
  frames a member-keyed `order_decision` ("adopt that side's placement";
  the executor re-homes the twin cell) — never a scope reorder, because
  the merged owner token cannot express which side is displaced and a
  scope answer would permute cells across brackets. Its same-key
  `verify_cold` row is suppressed for the pass (the `conflict_tags`
  precedent), and the landed placement row is **recording-deferred** like
  an answered `conflict_tags`: nothing on the member was reviewed, so the
  member re-frames cold and banks on the next pass. Apply additionally
  keeps `member_order` scope lists filtered to recorded members, so a
  landed order item beside pending members cannot commit unbacked
  handles.

---

## 6. The diff: one 3-way, derived once

### 6.1 Inputs

Per member: `base` = ledger entry (or *cold*), `current` = the member as parsed
from the working tree (both languages jointly, §3.2). There is no second
baseline source and no per-verb baseline divergence: every verb sees the same
comparison.

### 6.2 Per-member outcomes (the whole vocabulary)

| Outcome | Meaning | Handling |
|---|---|---|
| `in-sync` | fingerprints match recorded | nothing |
| `mechanical` | resolution is deterministic — normally because one side moved off base, but also where the halves' agreement is directly observable (§6.2.1) | apply does it: shared verbatim copy, tag mirror, id-stamp twin, order mirror, companion-layout mirror, remove mirror, owner record/retarget, `record_neutral` |
| `edit` | one side moved off base, other side needs judgment (localized twin) | framed task: translate/adapt, with both bodies + base attached; answers: twin `body`, or `keep_twin` when the twin is still a faithful rendering — a pure ledger record (#566) |
| `add` / `remove` | member present/absent vs base on one side | verbatim (shared) or framed translate (localized) / mirrored remove; removals of verified content always surfaced, never silent |
| `conflict` | both sides moved off base and differ | framed decision (de-wins / en-wins / merged body / "it's a fork" §7), full excerpts by construction |
| `transition` | class change (§7): fork, unify, id-stamp, relayout | mechanical when complete, framed when transitional |
| `unverified` | no ledger entry (cold), **two-sided** (§5 — a one-sided un-ledgered member frames as `add`), and **not language-neutral-decidable** (§6.2.1) | framed verification task; answers: `confirm` (banks both sides as-is, §9), plus `body`+`side` naming the stale twin on an id-keyed member (#572) |
| `order` | group-level member-sequence divergence | sequence diff over MemberKeys; mechanical when one side moved off recorded order, decision when both — or when no recorded order covers the divergence (the §5.1 pair-parity check, #654) |

Direction is decided **per member** by which side's fingerprint moved off base
— no deck-level direction inference, no mtime tiebreaks, no "established
direction" threading between passes.

### 6.2.1 `record_neutral`: the engine does not ask what it can observe

*(#764, 2026-08-01. Amends the `unverified` and `mechanical` rows above and
§5's cold rule.)*

**The rule.** A cold member is framed as a question only when the relationship
between its halves is genuinely unknown. It is resolved by the mechanical
`record_neutral` row — which writes **no file bytes**, only the ledger entry,
like `record_order` and `record_owner` — when all of the following hold:

1. it has **no ledger entry** (this is the branch being replaced);
2. it is **two-sided** (both `de` and `en` present);
3. its recorded langness is **`shared`** — the author declared the cell
   language-neutral;
4. its kind is **`code` or `j2`** — kinds that carry no natural-language
   content; and
5. **every per-side field the differ compares is equal across the halves** —
   body, tags, layout, ownership. Not a hand-enumerated list: the predicate is
   defined over the same generic record-diff §6.3 already runs, so a field
   added later tightens it automatically (P6).

Trust banked this way carries provenance `structural`. Any member failing any
clause keeps today's `verify_cold` framing unchanged. The existing suppression
doctrine is untouched: a member carrying any framed row is not a
`record_neutral` candidate, because clause 5 cannot hold.

**Why this is not the "auto-confirm" mistake.** The rejected proposal (Q6b, an
`apply --mechanical` that auto-answers `translate_edit → keep_twin`) banks the
claim *"my edit did not change what the twin should say"* — a semantic claim
about two **different** texts that the tool cannot verify, and banking it
unverified is precisely the silent-divergence class this programme exists to
remove. `record_neutral` banks a different claim: *"these two halves are the
same bytes"*, which the engine directly observed.

The governing principle is the same one, and it is the general answer to Q6b:
**auto-resolve only what the engine can observe, never what it must assume.**
Applied to a cold member that yields clause 3–5 above; applied to an *edited*
member it yields a different but equally observable test — the source-side
change is normalizer-equivalent, so the twin is provably unaffected. Both are
mechanical rows under §6.2, so an `apply --mechanical` flag would be a caller
of existing rows rather than a new contract with its own auto-answer policy.
Q6b needs no separate mechanism; it needs this principle applied twice.

**Why prose is excluded (clause 4 is load-bearing).** For `markdown`, `shared`
+ byte-identical has two readings the engine cannot tell apart: a genuinely
language-neutral cell (a fenced code block, a shell snippet, an `<img>` tag),
or German prose duplicated verbatim onto the EN side and mis-declared shared.
Auto-blessing the second would bank an untranslated cell as in-sync — the
exact failure being designed out. `wrong_language_cell` does not catch it
either: a shared cell carries no `lang=` attribute to contradict. Code and j2
carry no natural language, so clause 3's declaration is self-evident rather
than trusted, and the reading is unambiguous. That claim is measured *and*
enforced, not assumed: German was found in ~0.6% of neutral `code` members
(#771 — comments and string literals), so `clm validate` polices the boundary
with an **error**-severity detector (#772, promoted from warning once the
corpus cleanup reached 0 findings, #782), with the per-cell
`allow-untranslated` tag as the explicit escape hatch. Banking itself is not
blocked — `record` gates on the structural verify only, which deliberately
never sees this heuristic — so a German shared cell can still be banked
before validation runs. What the detector guarantees is that unmarked German
cannot *survive* a `clm validate` gate (pre-commit/CI, where a repo wires
one) unnoticed: banked-but-flagged trust gets fixed or declared instead of
accumulating silently.

**Measured** (`scripts/measure_positional_composition.py`, PythonCourses, 730
decks, 0 refusals, 2026-08-01). A fully cold corpus emits 28,791 verification
questions:

| class | count | share |
|---|---:|---:|
| localized — real question | 15,439 | 53.6% |
| **shared + identical, `code` — `record_neutral`** | **13,043** | **45.3%** |
| **shared + identical, `j2` — `record_neutral`** | **17** | **0.1%** |
| shared + identical, `markdown` — excluded by clause 4 | 282 | 1.0% |
| one-sided / shared-but-diverged — real questions | 10 | 0.0% |

So the rule removes **45.4%** of cold-start verification volume and leaves 282
prose questions standing as the deliberate price.

**Why this also closes the positional-churn question.** Positional identity is
**92.5% shared `code` cells** (10,922 of 11,806; the rest is 729 one-per-deck
localized `j2` headers, 133 shared markdown, 5 localized code). Only 133
markdown cells in the whole corpus are positionally keyed — prose is already
essentially 100% id'd, so id *assignment* was never the problem. The members
whose keys churn when a pool re-numbers are therefore overwhelmingly the same
class this rule decides, which means a re-keyed pool **self-clears** at the
next `record` instead of generating work. That is what makes the residue the
#653 withdrawal left ("a boundary move re-keys the span once, those members
report `verify_cold`") close to free, and it is why the review's Q1 escalation
is withdrawn rather than deferred — see §13.

### 6.3 Where the current engine's channels go

Body, tags, ownership, position-in-group, langness, layout are **fields of the
member record**, compared by one generic record-diff. The hand-enumerated
channel detectors, the `TagHold` dual-identity type, the recorded-but-unread
watermark partitions, and the channel-coverage meta-test are all replaced by a
single structural guarantee, testable by construction: *every serialized field
of the member record is either compared by the differ or explicitly annotated
cosmetic* — one test over one type, not a hand-maintained registry of private
function names.

### 6.4 Report

`report` renders the member table: key, outcome, direction, trust state, and
the actual bytes (base/de/en) for every non-`in-sync` member — full excerpts
are structurally free because the member *is* the bytes. Item handles are
MemberKeys: value-keyed, stable across re-plans, meaningful in a decision
document (§8), and diffable by a human. The 14-kind × 3-tier × frameable-subset
vocabulary collapses into the table above.

---

## 7. Class transitions (the maintainer's scenario, first-class)

> Maintainer observation (2026-07-01): *"it happens (rarely, but with non-zero
> probability) that previously language-neutral cells become language-specific
> or vice versa. The new model should handle these situations without once
> again introducing endless series of patches."*

This is exactly the failure shape that killed the current engine — there, a
cell whose metadata changes **migrates between identity regimes** (shared
partition ↔ keyed classifier ↔ id-less localized machinery), and every
regime boundary needed bespoke guards (#443 is the id-ness version of this,
#501 the layout version). Under P2 the situation cannot arise: the member keeps
its key; what changes is recorded state, and the change itself is diffed.

### 7.1 Detection

The differ compares the *recorded class* (ledger `langness`/`layout`/id-state)
against the *observed class* (lang attributes, layout, id presence, in both
projections jointly). Because both projections are parsed into one member, a
half-completed transition (author touched only one file) is directly
observable as an asymmetric class state — a `transition (in progress)` item,
never a misclassification.

### 7.2 The langness rows

Base class **shared** (one body, byte-identical projections):

| Observed | Interpretation | Action |
|---|---|---|
| both attr-less, hash == base | in-sync | — |
| both attr-less, equal, hash ≠ base | already-propagated symmetric edit | mechanical: record new fingerprint |
| both attr-less, one side ≠ base | shared edit | mechanical: verbatim copy to twin, record |
| both attr-less, both ≠ base, differ | conflict | framed decision — options include "propagate DE", "propagate EN", "merge", **"this is a fork"** |
| one/both sides carry `lang=` | **fork** | §7.3 |

Base class **localized** (two bodies):

| Observed | Interpretation | Action |
|---|---|---|
| attrs removed both sides, bodies byte-equal | **unify** complete | mechanical: entry drops to one fingerprint, same key |
| attrs removed both sides, bodies differ | unify intent, content unresolved | framed decision: "choose/author the shared body" |
| attr removed one side only | unify in progress | framed task: complete on twin or revert |

### 7.3 Fork and unify mechanics

- **Fork (shared → localized).** Signal: `lang=` attributes (the author's
  explicit intent channel — already the authoring convention). Complete fork
  (both sides marked, both bodies present): mechanical — the ledger entry
  upgrades in place from `{shared: h}` to `{de: h_de, en: h_en}` under the
  *same key*; if the member had no id, one is **minted at fork time** through
  the existing symmetric twin chokepoint (localized members must be id'd,
  §3.4), which permanently removes the member from positional identity.
  Fork with a missing twin body: framed translate task. Fork combined with a
  simultaneous edit or group reorder: the minted id anchors all subsequent
  diffs, so the worst case is one framed decision on the fork member — it
  cannot destabilize its neighbors' identities.
- **Unify (localized → shared).** The id **stays** (P3 — shared cells may
  carry ids; 548 already do). The entry drops to one fingerprint under the
  same key. The only judgment is which body becomes the shared content when
  the variants differ — a framed decision, since it is a genuine authoring
  choice.
- **Id-stamp (id-less → id'd, incl. one-half-only).** Observed id on one half,
  none on the twin: mechanical `stamp twin` item (the #443 shape, reduced to a
  one-row transition) — **but only when the pairing is ledger-known** (Y5,
  adversarial review 2026-07-24): the ledger entry the member resolved to
  (its own id entry, or a recorded positional entry matched at migration)
  must agree with the stamped side's twin — by content fingerprint, or, for
  the fork shape, by *pre-fork* fingerprint (content modulo exactly the
  `lang` attribute a fork adds; tags, owner, vo_anchor, body and separators
  must still match). A purely positional, never-recorded adoption frames a
  single `verify_translation` row and suppresses every other row for the
  member this pass — the stamp fixes identity (P2), so no mechanical row
  (not `record_fork`, not `mirror_tags`, not an order mirror) may execute or
  bank against a pool-order guess. `confirm` banks the pairing and the next
  pass stamps mechanically; the suppressed aspects re-derive then.
  Observed id on a previously positional member: the key
  migrates `pos:… → id:…` in the ledger entry *at record time*, an explicit,
  logged rename of the key — one of exactly **two** places key migration is
  allowed.
- **Id-rename (id'd → differently id'd) — the second sanctioned migration
  (#572, 2026-07-08).** A deliberate `slide_id` rename goes through
  `clm slides rename-id DECK OLD NEW`, which rewrites the id (and every
  `for_slide` owner reference) on **both** halves and migrates the ledger key
  in the same step — carrying the recorded fingerprints, never re-hashing —
  so a rename done alongside an edit still frames `translate_edit` against
  the carried baseline, never a stale cold-`confirm`. Renaming a group anchor
  cascades into the group's `pos:` keys and order scopes
  (`doc_ledger.rename_group_scopes`). Key migration is never **inferred**:
  teaching the differ to recover hand `id:→id:` renames by content
  fingerprint was adversarially reviewed and **rejected** (#572) — it
  re-enters inference into identity (P1/P2) and mis-migrates on the
  boilerplate/blank-cell fingerprint collisions that are the norm in decks,
  creating a new silent-failure class. Do not re-propose it. A hand rename
  that bypasses the command drops the member toward the cold/framed path —
  exactly *how* it frames depends on the content: an untouched rename is
  caught by the content-matched rival check (`ambiguous_alignment`), while a
  rename *combined with an edit* defeats that check and is caught instead by
  the Y7 suspicion guards (2026-08-14): the removal side frames
  `remove_vs_edit` when the gone side holds an unpaired cell that could
  be the renamed/stripped member, and the copy side frames `stamp_vs_new`
  because `_id_half_gap` finds the id-keyed entry's unaccounted half
  (falling back to a group-unscoped scan while a one-sided anchor change
  is in flight).
  Recovery stays §5's cold path (`confirm`, or `body`+`side` for a stale
  twin), or `treat_as_new` + `remove` when the rename was deliberate.
- **Relayout (inline ↔ companion).** A voiceover member's `layout` flips;
  content identity is untouched. Mechanical mirror to the twin (both languages
  keep the per-deck invariant), entry updates `layout`.

### 7.4 Why this cannot regress into patch cascades

Three structural properties, each testable:

1. **Identity never depends on the transitioning attribute** (P2), so no
   transition can strand ledger entries, split a member across classifiers, or
   create the cross-half regime disagreement that produced #443. There is
   nothing to patch *around* — the member is the same member before, during,
   and after.
2. **The transition space is finite and enumerated once**: langness {shared,
   localized} × id {present, absent} × layout {inline, companion} × side
   {complete, in-progress-de, in-progress-en}. A single parametrized test walks
   the full matrix (a few dozen cases) and asserts every combination maps to
   exactly one row of §7.2/§7.3 — the analogue of the channel meta-test, but
   over a closed product of enum states instead of a hand-maintained registry
   of function names.
3. **The escape hatch is uniform** (P8): any observation that fits no row is a
   framed decision carrying the member's full state — never a refusal of the
   deck, never a silent default, never a new mechanism. If a future authoring
   practice adds a new class axis, it enters as a new enum field with its own
   transition rows, and property 2's test forces the enumeration to be
   completed at the moment the field is added.

---

## 8. The surface

```
clm slides sync report  DECK|DIR [--json] [--since REF]   # read-only, ledger-trusted
clm slides sync apply   DECK [--decisions FILE|-]          # mechanical + validated decisions, per-item
clm slides sync verify  DECK|DIR                            # structural gate (unchanged)
clm slides sync record  DECK [--member KEY]... [--provenance ...]  # bless/accept collapsed
clm slides sync autopilot DECK|DIR [--model ...]            # a SCRIPT over report→judge→apply
```

- **Decision documents** replace `task`/`accept` round-trips and `id(proposal)`
  keying: `report --json` emits framed items keyed by MemberKey with prompts,
  inputs, and answer schemas attached (the existing builders/validators,
  re-homed); the agent answers any subset in one JSON document; `apply
  --decisions` validates each answer through the existing accept-gates
  (multi-cell smuggling rejection, `strict_single`, structural checks) and
  applies **per item** — invalid answers are rejected individually with
  reasons, valid ones land, nothing already applied is lost, and the ledger
  records each landed item. Handles survive replanning because they are
  values, not positions.
- **Answer vocabularies are per-item and shape-aware**
  (`doc_apply.item_answers`): `translate_edit` takes a twin `body` or
  `keep_twin` (#566); `verify_cold` takes `confirm`, plus `body` with a
  `side` naming the stale twin — id-keyed two-sided members only (#572); a
  positional cold member takes only `confirm` (ordinal aliasing, §5). The
  report advertises exactly what the executor will accept — advertising an
  answer the executor then rejects is a defect. *Watch-item:* `verify_cold`'s
  answer set already varies by member shape in three ways, all derivable from
  one principle ("what can be addressed, and what can be trusted"); a
  proposal for a **fourth** shape-conditional answer set on any single action
  is the P8 alarm — redesign the action instead of conditioning it further.
  `stamp_vs_new` (#600) is that rule applied: the one *resolvable*
  `ambiguous_alignment` shape (a new id'd cell while a positional cell of the
  same pool is unaccounted on that side — both the id-view and the pos-view
  row) became its own framed action with a uniform `treat_as_new` answer
  (grow the twin verbatim / mirror the removal), instead of conditioning
  `ambiguous_alignment`'s answers by shape. The pos-view row is only emitted
  while the survivor sits on base: an *edited* survivor would
  deterministically reject the mirrored removal, so that shape frames
  `remove_vs_edit` (whose answers land) with the suspicion in the detail —
  the report never advertises an answer apply is guaranteed to refuse.
  `remove_vs_split` (#610/#630) is the same route again: the suspected
  group-split reframe became its own framed action with a uniform `remove`
  answer for the coincidental-duplicate case (the split case stays manual:
  mirror the inserted slide, then re-report). `ambiguous_alignment` itself
  stays answerless — its remaining shapes (rival id stamps, both-sides-added
  pool collisions, multi-candidate pending twins) are genuinely manual.
- **One baseline rule everywhere.** Every verb trusts the ledger; `--since REF`
  is a forensic *view* on `report` (show me git-window changes annotated with
  trust state), not a trust change. `provider_available`, `--use-watermark`,
  and the per-verb divergences disappear with the mechanisms that required
  them.
- **The human path** is `autopilot`: a loop of report → (configured model
  judges each framed item) → apply → verify, over the same verbs an agent
  drives — one code path, one behavior, a handful of options (model, conflict
  policy, yes/dry-run). It is no longer a second engine with 26 flags. A human
  who prefers an agent just tells the agent, which uses the same four verbs.
- **Exit codes**: `report` 0 clean / 1 work pending / 2 error (kept); `verify`
  0/2 (kept); `apply` 0 all-applied / 1 residue / 2 error.
- MCP: `sync_report` reads the same member table; `sync_apply_decisions`
  completes the non-shell path (the current MCP/CLI baseline divergence
  disappears by construction).

---

## 9. What stays hard (honest residue)

- **Both-sided semantic conflicts** on one member are genuine: someone must
  choose. The model guarantees full excerpts, a stable handle, per-item
  application, and a conflict-policy knob — it cannot remove the judgment.
- **Translation equivalence** is not deterministically checkable; `verify`
  stays structural, and "is the EN actually a good rendering of the DE?" stays
  with the judge (recorded as `semantic` provenance when a model attests it).
- **Deck renames**: path-derived deck identity with rename recovery persists
  until a stable deck id is designed (§12).
- **Shared-member reorder + one-sided edit within one group** remains the one
  place positional identity can be ambiguous (§3.3); ceiling = one framed
  decision, permanently resolvable by minting an id.
- **Cold `confirm` carries no freshness guarantee.** Confirming a
  never-recorded member banks both sides as-is — cold means there is no
  baseline to check freshness against, so no engine check is possible; the
  judgment that both sides are faithful is the agent's. `rename-id` (§7.3)
  removes the most common way a warm member fell cold; a known-stale twin on
  a cold id-keyed member is recovered in one pass with the `body`+`side`
  answer (#572). `record_neutral` (§6.2.1) removes the 45% of cold questions
  where no freshness judgment is needed, but it deliberately does not reach
  the ones where it is.
- **Shared byte-identical *prose* stays a cold question** (§6.2.1 clause 4,
  #764): 282 members corpus-wide. The engine cannot distinguish a genuinely
  language-neutral markdown cell from German prose duplicated onto the EN
  side and mis-declared `shared`, and `wrong_language_cell` cannot see it
  either (a shared cell has no `lang=` to contradict). This is a chosen price,
  not an oversight — auto-resolving it would trade a real silent-divergence
  risk for 1.0% of cold volume. A validator rule that flags natural-language
  content in a cell declared `shared` would shrink it; that is separate work
  and belongs to `validate`, not the differ.
- **A scope with fewer than two two-sided id'd members is
  order-untrackable** (#654 residue, review M4): one handle has no relative
  order, so a lone id'd cell among positional siblings can sit at different
  positions per side with no item and no observation. Minting ids makes
  order trackable; the structural gate's `(slide_id, role)` order-parity
  check (#719) is the deck-part backstop.
- **Mid-move members contaminate same-pass order evidence** (review N11,
  bounded): a member being moved cross-group contributes its merged-token
  position to the destination scope's sequences. The pair-parity check
  excludes members carrying their own cross-group move item; residual
  contamination is limited to same-pass verdicts and re-frames next pass.

---

## 10. Component inventory (keep / rebuild / delete)

| Component | Fate | Notes |
|---|---|---|
| `raw_cells`, slide parser, `content_lang`, `tags` | **keep** | feed `parse_bundle` |
| `split.py` / `unify` | **keep** | the lens pattern; its law suite becomes the template |
| `voiceover_tools` pure cores (`merge_voiceover_text`, `_plan_extraction`) | **refactor into the lens** | companion projection moves from text layer to model layer |
| `path_utils.atomic_write_all`, buffered temp-swap | **keep** | the write boundary |
| `sync_verify` | **keep** | unchanged gate |
| `sync_recover` validators + prompt builders, wire codecs | **keep** | become decision-document validators/framers |
| `sync_translate` prompts, glossary plumbing | **keep** | framed-task payloads; the client lives only in `autopilot` |
| `sync_ledger` storage core | **keep, promote** | schema per §5; becomes the only store |
| `sync_accept` guards (smuggling, `strict_single`) | **keep** | re-homed into `apply --decisions` validation |
| Behavioral oracles (`test_sync_corpus_noop/mutation`, `_falsely_consistent`, dry-run parity) | **keep, port to CI** | the acceptance gate for the whole migration |
| Issue-pinned regression scenarios (189 refs) | **keep as behavioral fixtures** | rewritten against the public surface (required under any plan — they import ≥15 private symbols today) |
| **new** `bilingual_doc.py` (model), `doc_lenses.py` (projections), `sync_diff.py` (generic 3-way + transitions), `sync_apply` v3 (per-item executor), `sync_report` v3 | **build** | estimated ~2.5–4k lines total — totality removes the channel matrix, the cold-start matrix, and the plan-patching passes that make the current core 9.2k |
| `sync_plan.py` channels/post-passes, `sync_apply.py` tiers, `sync_code.py` structural signatures | **delete after shadow parity** | |
| Watermark store, `baseline` verb group, six repair mechanisms, `seed`, representation marker | **delete** | |
| `sync_companion` projection compensations, `reconcile_vo_ids` | **delete** | subsumed by the model + §7 transitions |
| `sync_diagnose` 9-label catalog | **delete** | report states + `--since` views subsume it |
| `autopilot` engine body (26 options) | **replace** | becomes the §8 script |

---

## 11. Migration plan

Each phase is independently shippable and abortable; the oracles gate every
step. The old engine remains the default until Phase 4's evidence gate.

- **Phase 0 — Oracles + normalize (no behavior change).** Port the corpus
  no-op and mutation oracles into CI on the bundled corpus (today they
  effectively run only locally); add DE-side and both-sided mutations. Ship
  `normalize --stamp-ids` (localized + narrative id stamping over
  `assign-ids`) and run it on PythonCourses as a reviewed one-time commit.
  Exit: oracles red/green in CI; corpus normalized.
- **Phase 1 — Model + lenses.** `parse_bundle` / `project` with the round-trip
  law suite (golden + Hypothesis), covering companions, subdir/sibling
  layouts, and the legacy inline layout. Exit: `project ∘ parse` byte-identity
  over the full corpus.
- **Phase 2 — Differ + transitions, shadow mode.** `sync_diff` + the §7
  transition table + the §7.4 matrix test. `report v3 --shadow` runs both
  engines over the corpus and scripted mutation scenarios and diffs the
  verdicts; replay the W10 dogfood scenario. Exit: shadow disagreements
  triaged to zero-or-explained; **W10 replay reports ~3 items, not 73**.
- **Phase 3 — Apply v3 + ledger promotion.** Per-item `apply --decisions`;
  `record`; ledger seeded from a verified pass (the #448 seed logic, one-time);
  v3 engine behind `CLM_SYNC_ENGINE=v3` per the §12.5 dispatch design. Exit:
  mutation oracle green through the v3 write path; a real dogfood week on
  PythonCourses using only v3 verbs, with the fall-back-to-manual count as the
  metric.
- **Phase 4 — Cutover + deletion.** Flip the default, delete the watermark and
  the old core, prune the surface, update `info_topics/{commands,sync-agents,
  migration}.md` (Info Topics Maintenance Rule), the `deck-sync` /
  `reconcile-deck-edits` skills, and PythonCourses guidance; changelog
  fragment; breaking-release notes (milestone-#158 style).
  **DONE 2026-07-04**: the v3 verbs are unconditional (`CLM_SYNC_ENGINE`
  removed), the v2 core + watermark store + judge stack deleted, `--since`
  re-implemented as the bundle-at-ref forensic view, split/translate/Studio/
  MCP ported to the ledger engine, v1 ledger sections dropped on save.
- **Phase 5 — Optional.** MCP `sync_apply_decisions`; stable deck id; ledger
  analytics (staleness dashboards).

Rough effort: Phases 0–2 ≈ 2–3 focused weeks; 3–4 ≈ 2–3 more. Calibration: the
current trajectory spent ~5 weeks on ~35 fixes with the noise problem still
open, and the highest-risk artifacts here (oracles, lenses, validators) already
exist and are being reused, not rebuilt.

## 11a. Risk register

| Risk | Mitigation |
|---|---|
| v3 burn-in repeats v2's (7 silent drops in 9 days) | oracles-first + shadow mode: v3 must match-or-beat v2 on the corpus and the scripted mutation suite *before* it may write; the mutation oracle already proved it catches real drops (#443) |
| Normalize commit churns course history | one reviewed commit per repo, ids only on localized+narratives (~10% of cells), agents already author ids routinely |
| Ledger file merge conflicts | per-topic granularity + union-else-unverified rule (§5); conflicts are true positives |
| Lens not byte-stable on legacy formatting | Phase 1 law suite over the full corpus is the gate; non-conforming decks get framed normalize items, never silent rewrites |
| Downstream breakage (skills, course repos) | Phase 4 lockstep migration map, as the 1.16 re-cut successfully did |
| The one-shot human path regresses while v3 lands | old `autopilot` stays until Phase 4; the agent path (the maintainer's actual workflow) is served from Phase 3 |

---

## 12. Decisions (settled with the maintainer, 2026-07-02)

1. **Narrative id policy — SETTLED: every narrative gets its own unique id**
   at normalize (aligned with #501's canonical form). This is what makes §7
   fully closed; `(owner, occ)` identity for narratives is off the table.
2. **Ledger granularity — SETTLED: per-topic file** (follows #448).
3. **`--since` — SETTLED: kept**, as a forensic report view (§8): "show me the
   git-window changes annotated with trust state". Never a trust source.
4. **Stable deck id — SETTLED: deferred.** Path-derived deck identity with the
   existing rename recovery persists; a content-based deck id remains a
   Phase-5 option.
5. **Cutover style — SETTLED: env-flag switch, engineered for clean v2
   removal** (the maintainer's stated priority). Concretely:
   - One dispatch point, at the **verb layer**: each verb resolves
     `CLM_SYNC_ENGINE` (`v2` default through Phase 3, `v3` opt-in; flipped in
     Phase 4) and calls one engine facade. No v2/v3 branching below that
     point.
   - The v2 core stays an **isolated, deletable module tree** — no v3 code
     imports from `sync_plan.py`/`sync_apply.py`/`sync_code.py`, enforced by
     an import-cleanliness test (the same mechanism that made "no model on the
     agent path" structural in 1.16). Removal in Phase 4 is: delete the
     modules, delete the flag check, done.
   - The JSON envelope is **self-describing** (`schema: 3` plus the stable
     top-level booleans `is_clean` / `needs_model` / `needs_agent` in both
     shapes), so agents branching on the booleans survive the flip and
     consumers can detect which report shape they hold.
   - Downstream invocations never change names: the same verbs mean the same
     thing before and after cutover — the env flag only exists during the
     transition window and is removed with v2.

---

## 13. Post-cutover amendments (audited 2026-07-10)

Every post-cutover change to the engine was audited against P1–P8 on
2026-07-10; all conform, and the refinements below are now part of the design.
Per P8, keep this note current: a change that lands in the engine without a
row here (or an edit to the section it refines) has skipped the checklist.

| Change | Sections amended | Nature |
|---|---|---|
| #555 — git-idempotent `record` (`confirmed_commit` preserved on unchanged members; byte-identical ledger writes skipped) | none (P5 untouched; `confirmed_commit` = "commit at which this state was last actually established") | pure idempotence fix |
| #566 — one-sided un-ledgered members frame `add`, not cold; positional exception | §5, §6.2 | design clarification (resolved the §5/§6.2 overlap by sidedness) |
| #566 — `keep_twin` answer on `translate_edit` | §6.2, §8 | P8(c) extension: new answer in an existing framed kind |
| #570 — `DiffItem.side` means the *present/source* side on every `translate_new` emitter; executor derives the mint target from the member | none (implementation consistency; one field, one meaning — P6 spirit) | inconsistency removal |
| #572 — `clm slides rename-id` = the second sanctioned key migration; fingerprint-inferred `id:→id:` migration **rejected** | §7.3 | design extension (explicit, never inferred) |
| #572 — `body`+`side` recovery on cold id-keyed two-sided members; cold-`confirm` caveat documented | §6.2, §8, §9 | P8(c) extension + honest-residue entry |
| #600 — `stamp_vs_new` framed action: the "new id'd cell while a positional pool cell is unaccounted" shapes (id-view + pos-view rows) split out of `ambiguous_alignment`, with a `treat_as_new` answer (grow the twin / mirror the removal) | §8 | new framed kind via the §8 watch-item's "redesign the action" route — `ambiguous_alignment` stays answerless |
| #600 follow-up (adversarial review of #602) — pos-view `stamp_vs_new` only emitted while the survivor sits on base (edited survivor frames `remove_vs_edit` with the suspicion in the detail); pools carrying an unresolved `stamp_vs_new`/`remove_vs_edit` item are frozen during apply's ledger re-record so a partial answer cannot erase a sibling's removal evidence | §8 | defect fixes: "never advertise an answer apply must refuse" + one-sided evidence has no cold state to fall back to |
| #609 — target-aware `body` writer/validation for single-line j2 macro cells (`id:title` etc.): the j2 line is the cell's boundary AND content, so the generic delimiter guard rejected every valid answer and the generic writer would append instead of replace; a `body` now replaces the j2 line in place (full-line form, or bare text spliced into the macro's quoted argument — bare form disabled on the mint-a-new-cell paths, which cannot derive the right macro name) | §8 | defect fix: "never advertise an answer apply must refuse" (the report advertised `body` on `translate_edit id:title`) |
| #610 — group-split guard: a pos-keyed `mirror_remove` whose gone-side base fp matches a one-sided cold cell of another group on that side is reframed (post-pass) as answerless `ambiguous_alignment` with a `suspected_group_split` observation — an id-keyed slide inserted before a run of un-id'd cells moves them into its group on one half, and mechanically mirroring the "removals" would delete the twin's untouched cells; `ambiguous_alignment` joins the #600 pool-freezing set (its reframed rows carry two-sided base evidence whose gone side survives only as the base fp) | §6.2, §8 | defect fix: a mechanical row that silently defaults to data loss; resolution = mirror the inserted slide on the twin, then re-report |
| #630 — `remove_vs_split` framed action: the #610 group-split reframe split out of `ambiguous_alignment` (the #600 route again) with a `remove` answer — the fingerprint-only heuristic also blocks a genuine deletion that coincides with a byte-identical one-sided cold cell elsewhere (repeated boilerplate), and the answerless reframe left that case with no in-tool resolution; the pool freeze is gated to `remove_vs_split` (keying it on `ambiguous_alignment` had re-gated five unrelated pre-existing emitters), the detail/observation name every rival group, the rival match also keys on the gone side's recorded *body* fp (an attrs/tags-only change on moved cells must not dodge the guard), and a warn-only similar-bodies observation flags the residual move+edit shape the byte guard cannot see (twin body as proxy — valid only for non-diverged shared bases, so diverged slots are skipped; memoized + budgeted, and emitted even alongside an exact rival so a coincidental duplicate cannot hide the real split target) | §6.2, §8 | new framed kind via the §8 "redesign the action" route — `ambiguous_alignment` stays answerless |
| #716 — lens rule-2 adoption cardinality guard: adopting an id'd-on-one-half cell now additionally requires the id'd side's pool residue not exceed the id-less side's — a genuine #443 stamp preserves pool cardinality, while an *inserted* id'd cell's surplus previously married the id-less cursor cell to the insertion (byte-equality or lang-attr evidence alone), orphaning its true twin into a mechanical `mirror_remove` (silent data loss) or handing a new localized cell another slide's translation; under a surplus the id'd cell skips as a one-sided member (frame, never guess) | §3.3 | defect fix: byte-identity-as-identity-evidence inference removed from the lens — the #572/#644 rule applied one layer up (adversarial-review finding C1/C2) |
| #717 — `copy_new` lang swap: the executor primitive shared by `copy_new_shared` / `treat_as_new` / `keep` mints the TARGET half's lang variant for a lang-attr'd source cell (the `translate_new` swap applied at the chokepoint) instead of copying the source `lang=` verbatim into the twin file — which the re-parse gate would refuse as `wrong_language_cell`, aborting the whole pass (P7 broken at the gate). Defense-in-depth: e2e probes route the suspect shapes to `fork_pending_twin` before they reach `copy_new` | §8 | defect fix at the primitive level (adversarial-review finding M9, severity corrected on the issue) |
| #718 — group-rename ledger integrity: `rename_group_scopes` now rewrites ALL FOUR reference classes (pos: keys, order-scope keys, **owner refs** — every group member carries `owner = the anchor's key` — and **`id:` handle values inside member-order lists**), so the apply executor's `record_group_rename` no longer commits dangling `id:<old>` references into the sole trust store; `save` additionally sweeps every deck section for dangling handles/owners (`prune_dangling_refs`) — stale handles are dropped (the differ silently intersects them away anyway, eroding order trust with no signal), a dangling owner degrades to `None` (self-heals via a mechanical `record_owner`), and field-damaged ledgers heal on their next save | §5, §7.3 | defect fix: the trust store must not carry claims it cannot back (adversarial-review finding C6; the #656 field report) |
| #719 — order parity in the structural gate: `structural_violations` gains an `order-parity` check (the halves' common `(slide_id, role)` keys in first-occurrence document order must match — one-sided ids are excluded, they are the transition machinery's concern) — a *warning* in `sync verify` (pre-existing committed divergences must not hard-fail CI; the #615 tag-parity precedent) that the **whole-deck `structural_gate` promotes to blocking**: the trust store must never record a pair whose halves disagree about member order (`unify_texts` interleaves language-tagged cells permissively, so a localized-only group swap previously reached the ledger as verified — #652 instance 2). The scoped per-slide gate keeps its doctrine (a whole-pair order divergence does not block recording one reconciled slide) | §8 (verify), §6.2 `order` row's trust boundary | defect fix: verify ⊉ validate on exactly the property the field failures hit (adversarial-review finding C4); framing order divergence as an actionable item stays Phase 1 |
| #720 — anchor-adjacency guard on `insert_mirrored`: minting an **anchor** (slide/subslide) twin refuses when the mirrored-predecessor slot would separate it from its group's existing target-side cells (the group's cells are identified on the SOURCE side — the members following the anchor up to the next anchor — so a renamed anchor id cannot dodge the check); the violation fails the one item with an actionable reason, others proceed. For a non-anchor member with a two-sided anchor the predecessor rule is span-correct by construction (the predecessor is the anchor or a later same-group member), so no guard is needed there — the reachable corruption class was exactly the anchor mint under divergent group order, which previously wrote the twin at file end and re-parented cells on re-parse (#652 instance 1) | §8 (apply), §6.2 `order` row's write path | defect fix: the executor presumed order parity instead of checking it (adversarial-review finding C5); P8 — frame, never guess |
| #654 — order first-class (remediation Phase 1, the #615 treatment): order becomes a pair invariant checked from **current** cross-side evidence unconditionally — the differ compares the sides' current sequences over their common two-sided handles (minus members carrying their own cross-group move item) for every scope, base-covered or not, cold decks included, and frames `order_decision` on divergence; recorded order trust only *refines* the verdict into the directed `mirror_order`/`record_order` rows, and `record_order` additionally requires pair parity on the wider current evidence (a convergence on base-covered handles must not bank a divergence on uncovered ones). The C3 bootstrap circularity closes from the other side too: an `apply` pass ending with zero unresolved items seeds every order scope whose sides currently agree (`seed_order_scopes`), so confirm-seeded ledgers acquire order trust through the verb loop and later one-sided moves frame *directed*. A parse-observed `group_order_divergence` suppresses `is_clean` (observation coverage > item coverage), and the text report prints that observation. §5.1 specifies the order-scope schema the code had shipped unspecified (D3). Pre-merge adversarial rounds added: a no-evidence **cross-bracket placement** frames a member-keyed `order_decision` (executor re-homes the twin cell; the scope pair check excludes such members — a scope answer would permute cells across brackets), its same-key `verify_cold` row is suppressed AND the landed row is recording-deferred like an answered `conflict_tags` (the placement answer banks nothing; the member re-frames cold next pass), and apply filters `member_order` scope lists to recorded members so a landed order item beside pending members cannot commit unbacked handles | §5.1 (new), §6.2 `order` row, §9 (M4 + N11 residue entries) | defect fix: the #652/#654 order-blindness class (adversarial-review C3, M1, M3; D3/D6 doc debt) — frame from evidence, direct from trust |
| #655 — decision-body normalization at the write boundary (remediation Phase 2, decision-free part): `_replace_body` normalizes a markdown body to the canonical cell shape (leading blank comment line inserted; a bare blank first line promoted — the normalizer's `markdown-blank-lead` rule applied where the engine writes), so validate never warns on engine output and the out-of-band-fix → full `keep_twin` ceremony round dies; code cells and the #609 j2 macro path untouched; the `choice`/`body` exclusivity rejection now teaches the body-selects-body-answer inference; the decision-body contract documented in the info topics | §8 (apply; goal 4 "the tool owns the mechanics") | defect fix: engine output must satisfy the repo's own quality gate (adversarial-review finding M10; field report #655) |
| #650 — `broken_owner` gains a `remove` answer: a companion cell whose `for_slide` matches no slide anchor (its owning slide removed or renamed) was framed and gate-blocked but **answerless** — the only remedy was the both-halves hand-edit the doctrine forbids, and the failure otherwise surfaced at build time. `remove` prunes the orphaned narration from every present half (no gone side exists — unlike the survivor-removal answers); retargeting `for_slide` / restoring the slide stay hand-edits (they need a target no answer shape carries). The detail names the answer and the alternatives. Pre-merge adversarial round added: a framed `broken_owner` **suppresses the member's other rows** for the pass (the conflict_tags doctrine — the one-sided/edited orphan otherwise co-framed `translate_new`/`translate_edit` under the same key and the advertised answer rejected on the sibling); and a same-pass slide *rename* the differ can prove (`group_map`) never frames the removal decision — the new mechanical **`retarget_owner`** rewrites the companion's `for_slide` on every present half to follow the rename, so live narration is never steered into a prune | §8 (new answer in an existing framed kind — the P8(c) route — plus one new mechanical row) | defect fix: adversarial-review finding M15 scoped down — the report/gate coverage claimed missing was already present post-#719; the actual gap was the missing decision vocabulary |
| #649 / Q2 — **wire schema 4, part 1: report identity** (remediation Phase 2). Every report pair payload carries `report_id` (sha256 over the bundle's projected bytes plus this deck's ledger section), `deck_key` and `ledger`. A decision document echoes `report_id`; a mismatch refuses the WHOLE document (exit 2, nothing written) — wholesale because a moved deck makes every answer in the document suspect, not just the mismatching one. Token-less documents accepted for one release with a warning (`sync_wire.REQUIRE_REPORT_ID` flips it). `already_applied` split out of `rejected`: an answer whose member frames nothing asks for a state that already holds, and no longer blocks exit 0 — the verdict half of #649, where apply reported "rejected — stale handle" for decisions whose writes had landed. The companion→deck redirect now announces itself. | §8 (report/apply surface), §5 (the ledger section is half the token) | defect fix + contract extension (adversarial-review C7/M14, Q2) |
| #656 / Q3 — **wire schema 4, part 2: the item and answer shape**. Items carry `resolution` (`mechanical` / `decision` / `manual`), derived from the sets `apply_deck` itself branches on, so `answers: []` stops meaning two opposite things (M6); items carry `de_body`/`en_body`, the cell bytes without the `# %%` delimiter, so report output is valid decision input (M10). Decision rows may name the `action` they answer: two framed rows on one member can both be answered, and an answer aimed at an unframed row is reported instead of silently executing another. A cold member present on ONE half only no longer advertises `confirm` (the executor always refused it, and for a positional member the rejection then blocked its whole pool) — it is answerless, `manual`, with the repair in its detail. | §6.4 (item rows), §8 (decision document) | contract revision — additive fields, schema 3 documents still accepted |
| #656 / Q6a — **the two hand-edit flows sanctioned**. `verify_translation` accepts `body` + `side` (symmetric with `verify_cold`'s #572 recovery; the info topic had documented it for longer than the engine accepted it — M7). `fork_pending_twin` gains `mark_twin`, which writes the twin's `lang=` attribute and nothing else: the body adaptation is the next pass's `translate_edit`, because a one-pass stamp+lang+rewrite defeats the fork identity-carry and records as a fresh pair (F3). The two-pass fork recipe is documented in `clm info sync-agents`; it existed nowhere. Doc drift fixed alongside: `--dry-run` does not run the verify gate, and `pos:→id:` is not the only key migration. | §7.2 (fork transition), §8 (new answers) | P8(c) extensions: new answers in existing framed kinds |
| #653 — **anchor-hood is a pair property** (the first half of `sync-slide-hood-is-presentation.md`): the `slide`/`subslide` tags select a display transition, so they may not select an identity regime (P2). When the halves disagree about an id'd cell's slide-hood, the boundary opens no group on EITHER side — the cell stays an ordinary member and pairs by id, which is what lets the deck parse while a retag is half-done. The state reports an `anchor_shape_divergence` observation plus the mechanical `mirror_tags` row that copies the shape onto the twin; it used to refuse the whole deck with `duplicate_id` and frame nothing. The 1.23.x `anchor_shape_divergence` REFUSAL code is retired. Residue: positional keys inside the affected span are still anchor-scoped, so they re-key and go cold — id-delimited scopes plus a ledger migration are the design's second half. | §3.3 (anchor-hood), §7.4 (the role axis is closed by removal, not enumeration) | defect fix + model correction (adversarial-review G2/M2) |
| #764 — **`record_neutral`: the engine does not ask what it can observe** (§6.2.1, new). A cold two-sided member that is `shared`, of kind `code`/`j2`, and whose halves agree on every field the generic record-diff compares, is resolved by a new mechanical row that writes only the ledger entry (provenance `structural` — the #448 rung becomes an actual provenance value), instead of being framed `verify_cold`. Measured on PythonCourses (730 decks): **45.4% of the 28,791 cold-start verification questions are this class** — both halves are the same bytes, so there is no translation divergence to verify and the question is ceremony. **Prose is excluded by construction** (clause 4): for `markdown`, `shared` + byte-identical cannot be told apart from German prose duplicated onto the EN side and mis-declared shared, so 282 members stay real questions — the chosen price. This also settles the positional-churn question: positional identity is 92.5% shared `code` cells, so a re-keyed pool now self-clears at the next `record` rather than generating work, which is what makes the #653 withdrawal's residue cheap. `scripts/measure_positional_composition.py` re-runs the measurement. | §5 (cold rule), §6.2 (`unverified` + `mechanical` rows), §6.2.1 (new), §9 (two residue entries) | P8(b)/(c) extension — a new mechanical row in an existing outcome, no new axis; supersedes review Q1 (withdrawn, see below) and subsumes Q6b |
| Q1 — **per-deck fully-id'd opt-in mode: WITHDRAWN** (2026-08-01), not deferred. It was proposed on the reading that "82.4% of positional members sit in an ambiguous pool". The arithmetic was right and the metric was wrong: *membership in a pool > 1* scores a slide with two code steps identically to a 170-cell anchor-less deck. The honest metric is **blast radius** (`n*(n-1)/2` per pool — inserting or deleting one member re-keys about half its siblings): pools of size 1–3 hold 53.1% of positional members and carry **4.6%** of the churn, while pools of 10+ hold 17.8% and carry **84.6%**, concentrated so hard that the top 5 decks account for 48.9% and the top 20 for 76.8%. Those decks are not decks — `slides_np_computation_old` (the 170-cell pool) has **exactly one anchor**, the title slide, and the seven largest pools are all 1-anchor files; restricted to live decks with ≥6 anchors the worst pool in the corpus is 35. So the fragility is a property of a few dozen notebook-shaped files in the slides tree, addressable by adding anchors, and not a property of the keying rule. With `record_neutral` removing the cold cost of re-keying the class that actually churns, the escalation has no remaining trigger. | §9 (the positional-ambiguity residue entry stands as written — one framed decision, resolvable by minting an id) | escalation withdrawn on measurement; re-open only if a framed dead end recurs in the field |
| Q4 — **gate ⊇ validate containment, pinned** (2026-08-02). CLM judges split-pair health from two independently grown places: `clm validate`'s split-pair family (the authoring-time detective on the pre-commit gate) and `gate_projected_pair` (what may enter the trust store). Two oracles for one question is the setup for "validate calls it corrupt, the gate banks it as verified" — and *no test related them at all*. The relation is now a property: **a validate *error* implies a non-empty gate**, pinned over twelve corruption shapes with each shape's validate severity declared, not discovered (a check downgraded to `warning` would otherwise shrink the claim's reach silently). Measured over the 730-pair corpus: 0 gaps — containment already held, it was merely unguarded. The permitted asymmetry is pinned too (the gate is *stricter* on the id family, and catches shared-companion body drift validate cannot see), as is the deliberate exemption (tag parity is warning-only on both sides by design). One real defect found and fixed: the whole-deck gate returned its promoted `order-parity` violation still labelled `severity="warning"` — harmless while callers treat non-empty as a refusal, but a trap for any caller that re-filters on `severity == "error"`, which would silently reopen #652. Promotion now relabels; `verify`'s warning severity is untouched (it reads `verify_pair`). One live corpus instance (`slides_lucky7`). | §8 (verify/gate), §6.2 `order` row | test-only + severity correction — no model change (adversarial-review Q4, containment half; the delegation half — validate's family becoming an adapter over `parse_bundle` + `structural_violations` — stays open) |
| Q5 — **`uniform_drift_side` observation** (2026-08-02). The review-after-translate flow regenerates or hand-reviews one half; every drifted member then frames `translate_edit`; each row names its own side but says nothing about the others, so an agent reading row by row works through N members when one `keep_twin` sweep resolves them. The field report cost ~30 pointless decision items to that blindness. The differ now emits one deck-level observation when three or more `translate_edit` rows exist and **every** one drifts on the same side (the floor is a judgment: two rows collapse no ceremony and land one-sided about half the time by chance), naming the side, `keep_twin`, and the opposite reading; `verify_translation` rows are counted in the detail — as "needing two-sided verification", NOT as "moved on both sides": one of that action's three emit sites fires when a side with no recorded fingerprint merely landed, where the other half never moved — so a blanket sweep cannot pick up rows that do not even accept `keep_twin`. The per-item detail also names `keep_twin`, and the human report prints the observation after the items. **Explicitly not** the review's `drift: source|twin|both` field: `side`/`direction` already ship per item, so that field would add no information — it would add an *inference*, requiring the engine to name one half authoritative, which it cannot observe (the review's own P8 "frame, never guess"). Informational only: it never affects `is_clean` and changes no classification. | §3.2 (observations — this kind is emitted by the DIFFER, not the lens; the `suspected_group_split` precedent) | additive observation kind — no `WIRE_SCHEMA` change, no new framed action, no model change, `is_clean` untouched (adversarial-review Q5) |
| Q7 — **ledger hardening: concurrency, churn, and one honest definition** (2026-08-02). (a) **M8 lost update fixed.** A topic ledger is one file of independent per-deck sections and every verb did whole-file load-mutate-save, so two runs on *different* decks of one topic — the normal parallel-sweep shape — silently reverted each other. `save` now three-way merges: `TopicLedger.load_snapshot` records each section as read, so "did this run change it?" is answered by comparison rather than by bookkeeping every verb would have to remember; ours wins where we changed it, disk wins where we did not, and a section changed on both sides takes the later writer **with a warning**. Merging is explicitly not locking — it shrinks the window from the whole verb to the re-read→`os.replace` gap, and is portable, which file locking on Windows is not. Pruning (#718) moved after the merge so healing covers every section written. (b) **M13 churn fixed.** `preserve_unchanged_member` compared `provenance`, and the verbs alternate `record`/`apply`, so unchanged members rewrote every pass (883-line diffs for 60 cells). A stamp the verb applies on its own no longer counts as a change; one the user **typed** always does. Intent is threaded from the CLI (`ParameterSource`), NOT inferred from the value — an enumeration of "automatic" strings was drafted and rejected in review, because `record` is both `--provenance`'s default and a value a human types to reset a stale `semantic:<model>`, so the enumeration silently swallowed that reset while still reporting the member recorded. An automatic pass also no longer demotes a deliberate stamp. (c) **`confirmed_commit` redefined to what the code always did**: the repo HEAD when the entry was last written with a real change. It does NOT contain the recorded state (`record` precedes the commit), a no-op re-record leaves it alone (#555), and no verdict reads it (though `deck_section_fingerprint` digests it, so churning it would churn `report_id` freshness) — and the `git cat-file` re-derivation sketched in `sync-consistency-ledger.md` §11.3 cannot be built on it, since that needs a commit *containing* the state. The field is KEPT: #555 already removed the churn that motivated dropping it. UUID temp names were already in `atomic_write_bytes`; asserted rather than assumed. | §5 (trust store: write path + entry semantics) | defect fixes (adversarial-review M8/M13) + a documentation correction — no schema bump, no `hash_version` bump, no data reinterpretation (the writer already stamped HEAD-at-record-time) |
| Q4 (delegation half) — **`clm validate`'s tag-parity check delegates to the engine** (2026-08-03). The check that paired the halves *positionally* over the whole non-j2 stream now calls `tag_parity_violations`, which pairs id'd cells by `(slide_id, role)` and falls back to positional matching only *within* a slide — so a one-sided insert can no longer cascade into a run of phantom mismatches. Measured on the 730-deck corpus: 25 findings become 20; one deck contributed 6, of which **5 were phantom and 1 real** (#654). `VerifyViolation` gains an optional `line` so the finding keeps naming the offending DE cell. **Scope was narrowed during review from three checks to one.** Delegating `_check_split_slide_id_parity` was tried and reverted: the engine's id comparison is deliberately broader than validate's — sensitive to the `!` preserve marker (a legal cross-half difference this module strips everywhere else) and covering every id'd cell rather than slide-start cells only, which flags the one-sided narrative member `clm harvest` produces *by design* as a pending state. Both would have fired on a `--fail-on warning` pre-commit gate in downstream course repos. Delegating `_check_shared_cell_parity` was also reverted: `unify_texts` stops at the first error (N diverging cells collapse to 1 finding), renders a count mismatch as "content diverges", and adds a silent→error escalation on preamble divergence. The rule that survives: **delegate what pairs positionally, keep what compares sets.** | §8 (verify/gate — the tag oracle is now shared with validate) | consolidation, scoped to the measured defect (adversarial-review Q4, delegation half; containment half was #766) |
| #782 — **the shared-cell German-text check is an `error`, and the first declared containment exemption** (2026-08-04). The #772 detector was born a `warning` because the corpus carried pre-existing German shared cells; the cleanup finished at **0 findings across all 659 split pairs**, so the §6.2.1 clause-4 boundary is categorical again and the finding is promoted — new German in a shared code cell fails `clm validate` instead of advising, with `allow-untranslated` as the per-cell escape hatch. The #771 base-rate caveat on `NEUTRAL_KINDS` is retired: unmarked German cannot *survive* a `clm validate` gate unnoticed (banking itself stays ungated — `record` runs only the structural verify, which never sees this heuristic — so the guarantee is that flagged trust gets fixed or declared, not that it is never banked). The Q4 containment property gains its one declared exemption (`CONTAINMENT_EXEMPT`): this is now a validate **error** the write gate never sees — deliberately, because the halves agree byte-for-byte (structurally valid trust) and a content-language heuristic belongs to validate, the advisory oracle, never to the gate, the trust oracle. Every other validate error still implies a non-empty gate. | §6.2.1 (the boundary's enforcement), §8 (Q4 containment scope) | severity promotion + caveat retirement — no engine change, no schema change; the markdown exclusion (clause 4's 282-member price) stands unchanged |
| #773 phase 1 — **base-diff recovery on translation rows (wire schema 5)** (2026-08-04). `verify_translation` — measured at **68.4% of all framed rows** over 200 reference-repo commits (`scripts/measure_sync_ceremony.py`, the Q6b close-out) — asks a semantic question the engine cannot answer, so the fix attacks the *reading* cost: recovered rows (and `translate_edit`, which rides the same walk at near-zero marginal cost) carry `base_ref` plus per-side `de_diff`/`en_diff`, unified hunks against the newest commit whose bytes match the ledger's recorded per-side fingerprints. Recovery is a capped (30) newest-first walk over the bundle's change-commits (`git_text.recent_change_refs`), parsing each candidate once for all rows; the match is exact on both sides **and key-aware** (fingerprints are modulo `slide_id`, so an id-keyed row only matches the member carrying its own id and a pos-keyed row only matches id-less members — a boilerplate fingerprint lookalike can neither steal the match at a newer ref nor leak its id into the hunks; adversarial-review F1), making a recovered base the recorded state modulo the id attribute, never a nearest guess, and everything else — base never committed (`record` runs pre-commit), rewritten history, renamed deck, no git — **degrades to absence** (`sync-verify-translation-ceremony.md`, direction A). The ledger stays hash-only (§11.3's no-full-row decision untouched); git is the byte source, in the §12.3 view posture `--since` established — and `--since REF` passes its named ref as the sole candidate instead of walking. A `verify_translation_batch` observation (the `uniform_drift_side` shape) fires when ≥3 `verify_translation` rows share one recovered base — one editing session — and is suppressed when any row failed to recover ("all N" must cover N). The note's §4 pin holds: no auto-resolution at any threshold; the observation names no answer and the answer surface is untouched. `WIRE_SCHEMA` 4→5; decision documents are byte-identical to schema 4 (report-side additive), accepted set now {3, 4, 5}, the schema-3/`REQUIRE_REPORT_ID` sunset unchanged. The text report renders the hunks inline under each item (§7 rendering decision: reading is the measured cost, so no flag). Phase 2 (shape annotation) stays behind a re-measurement; the predicate batch-answer (direction D) stays deliberately unbuilt. | §6.4 (item rows), §3.2 (observations — differ-adjacent view layer), §8 (report surface) | additive view-layer extension — no trust change, no ledger schema change, no new framed kind; `is_clean` untouched |
| Q2 completion — **`report_id` mandatory, wire schema 3 retired** (2026-08-04, the release after 1.24.0, executing the 2026-07-31 decision as scheduled). `REQUIRE_REPORT_ID` flipped to `True` and `ACCEPTED_DECISION_SCHEMAS` narrowed to {4, 5}: a decision document that omits the freshness token, or announces `"schema": 3`, is refused wholesale — exit 2, nothing written, message naming the field and the accepted set. The one-release grace (1.24.0 accepted token-less documents with a warning) is over; drivers copy `report_id` out of the report envelope, and a fresh token is needed per apply pass (your own apply expires the previous report). No report-side change; schema-4 documents remain accepted indefinitely (byte-identical to 5 on the decision side). | §8 (decision-document contract) | contract tightening, pre-announced in the schema-4 row — no new fields, no model change |
| Y6 — **preamble propagation gains the carried-divergence guard** (adversarial review 2026-07-24, remediation Phase 3 item 6). The one-side-moved preamble branch emitted a mechanical `propagate_preamble` unconditionally, so on a baseline whose recorded preamble fingerprints already differed a trivial one-sided preamble edit (the review's example: DE kernel metadata) replaced the *entire* twin preamble verbatim. The branch now frames `pending_divergence` exactly as the shared-cell path does — no side of a diverged base is a safe verbatim source — including the shape where the twin preamble was empty at base (the base fingerprints disagree by construction). Two apply-side defects the frame surfaced are fixed with it: a `de`/`en` answer on the member-less `pos:~preamble/<part>/0` handle routed to the *cell* propagate and was rejected with the "carries no member" executor error (an advertised-answer dead end — it now routes to the preamble copy, like `conflict_preamble`), and the landed answer fell through to the member-table upsert instead of banking the preamble scope (preamble recording is now keyed on the handle, not the action list). | §6.2 (`mechanical` row's trust boundary), §8 (apply) | defect fix: the Y1 class one channel over — a mechanical propagate against a base that cannot back it, plus two dead-end/dead-record apply routes |
| Y7 — **one-sided rename+edit no longer executes destructive mechanics** (adversarial review 2026-07-24, remediation Phase 3 item 7; PR #831, four adversarial rounds). An id-keyed shared cell renamed AND edited on one half defeated both rival checks — the content-matched `_find_rival_stamp` (edit breaks the match) and the pos-only `_pool_side_deficit` (id-keyed entries never counted) — and emitted mechanical `copy_new_shared` + `mirror_remove`: a decision-free apply deleted the twin's untouched cell and the *next diff was clean*, banking the loss invisibly. The removal side frames `remove_vs_edit` (reusing the Y1-hardened `remove`/`keep` answers — extending `stamp_vs_new` to id-keyed removal rows was considered and rejected: it would have touched the executor's view dispatch, `_item_phase`, and the record path for zero vocabulary gain) when the gone side holds an unpaired cell that could be the renamed/stripped member, and the copy side frames `stamp_vs_new` via `_id_half_gap`, a per-half existence check for id-keyed entries (group via the recorded owner anchor; anchor/header entries never count). The pos→id migration precondition stays pos-only (round 1, Critical: an id-keyed gap satisfying it let a new id'd cell byte-identical to a present pool cell steal that cell's base entry — the same loss signature), the suspicion scans fall back to group-unscoped while a one-sided anchor change is in flight (round 1, Important — the over-frame on a plain one-sided slide add is deliberate and pinned: an anchor renamed AND edited is fingerprint-indistinguishable from remove+add), and the estranged-cell scan must not skip absorb-claimed cells (round 2, Critical: a mid-transition fork classified earlier in deck order could claim the estranged cell and hide it — the row depended on cell ORDER). §7.3's "a hand rename drops to cold" claim was wrong as written — true only for untouched renames — and is corrected inline. | §7.3 (hand-rename paragraph rewritten), §6.2 (`mechanical` row's trust boundary) | defect fix: mechanical remove/copy against a renamed-away cell — P8 frame, never guess; no new framed action (P8(c) reuse) |
| Y8 — **the pool's lone-candidate landed-twin claim requires content affinity** (adversarial review 2026-07-24, remediation Phase 3 item 8). `_align_pool` claimed ANY lone unmatched new cell on a pending-twin slot's missing side as the landed twin; the downstream `pending_divergence` frame's `de`/`en` answer then overwrote a genuinely new, unrelated cell verbatim — framed, so misleading rather than silent. The claim now requires body similarity to the recorded cell (the budgeted `_BodySimilarity` oracle the #630 split scan uses). The no-affinity candidate is a genuine add, not the twin — but it cannot simply keep its add row: the positional pairing marries it to the slot's member (the twin's verbatim copy refuses an occupied target), and its cold row shares the slot row's rendered handle (pool ordinals alias), which would defer the cold row's recording against an answerless sibling forever. The slot therefore frames `ambiguous_alignment` with the honest reading (not the twin — mint a `slide_id` or remove the cell, re-report) and suppresses the candidate's news row for the pass (the #654 placement-suppression precedent); the documented reconciliation converges mechanically on the next pass. | §6.2 (`conflict` row), §8 (report surface) | defect fix: never advertise an answer whose execution destroys unreviewed content; P8 — frame, never guess |
