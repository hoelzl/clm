# Sync v3: Total Identity over One Document — Target-Model Design Note

**Status**: Design agreed with the maintainer — §12 decisions settled 2026-07-02;
phases 0–4 shipped (v3 is the only engine since 1.20.0); amended 2026-07-10
with the post-cutover refinements (§13)
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
  that stamps the twin — because the id'd side's key wins and the twin is
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
| `mechanical` | one side moved off base, resolution is deterministic | apply does it: shared verbatim copy, tag mirror, id-stamp twin, order mirror, companion-layout mirror, remove mirror |
| `edit` | one side moved off base, other side needs judgment (localized twin) | framed task: translate/adapt, with both bodies + base attached; answers: twin `body`, or `keep_twin` when the twin is still a faithful rendering — a pure ledger record (#566) |
| `add` / `remove` | member present/absent vs base on one side | verbatim (shared) or framed translate (localized) / mirrored remove; removals of verified content always surfaced, never silent |
| `conflict` | both sides moved off base and differ | framed decision (de-wins / en-wins / merged body / "it's a fork" §7), full excerpts by construction |
| `transition` | class change (§7): fork, unify, id-stamp, relayout | mechanical when complete, framed when transitional |
| `unverified` | no ledger entry (cold), **two-sided** (§5 — a one-sided un-ledgered member frames as `add`) | framed verification task; answers: `confirm` (banks both sides as-is, §9), plus `body`+`side` naming the stale twin on an id-keyed member (#572) |
| `order` | group-level member-sequence divergence | sequence diff over MemberKeys; mechanical when one side moved, decision when both |

Direction is decided **per member** by which side's fingerprint moved off base
— no deck-level direction inference, no mtime tiebreaks, no "established
direction" threading between passes.

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
  one-row transition). Observed id on a previously positional member: the key
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
  that bypasses the command drops the member to cold; recovery is §5's cold
  path (`confirm`, or `body`+`side` for a stale twin).
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
  (grow the twin verbatim / mirror the removal; an edited survivor rejects),
  instead of conditioning `ambiguous_alignment`'s answers by shape.
  `ambiguous_alignment` itself stays answerless — its remaining shapes
  (rival id stamps, both-sides-added pool collisions, multi-candidate
  pending twins) are genuinely manual.
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
  answer (#572).

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
