# Sync: the Sequence Core — Switch-Over Design Note

**Status**: ACCEPTED 2026-11-13. The decision to switch over was taken on
2026-11-13 (the go of `docs/claude/sync-structural-core-investigation.md`
§9); this note is the design of step 5. The maintainer answered the
decisions of §11 the same day, each as recommended. Phase A is next; nothing
in this note is implemented yet.
**Author**: Claude (Opus 5.5), at the maintainer's request
**Builds on**: `docs/claude/sync-structural-core-investigation.md` (the
finding, the prototype, its laws and the shadow on real history),
`scripts/sync_model.py` (the prototype core), `src/clm/slides/doc_sequence.py`
(the recorded sequence, step 4)
**Amends**: `docs/claude/design/sync-total-identity-document-model.md` (the
v3 design): P1, P6 and P7 of its §2, §5 (positional entries), §6 (the
diff) and the §6.2 vocabulary, once the switch-over lands (§3 below)
**Discussion**: `docs/claude/discussions/sync-structural-core/`

---

## 1. The question

The investigation found that nearly every structural failure of `clm slides
sync` traces to one choice: un-id'd cells get a positional identity
(`pos:<slide>/<kind>/<n>`), so the engine has to decide which cell is which
before it knows what changed, in at least six places, each by its own rule
(investigation §3.3). A prototype core that aligns each half to the
**recorded sequence** instead, and merges membership, content, tags, file and
order 3-way, satisfies the sync laws on every small input. It also ended as the
author did in every one of 6,583 one-sided replays of real course history. The
engine did so in 5,856 (investigation §2.6).

Step 4 made every deck in sync record that sequence in its ledger. This note
answers what step 5 has to build: how the prototype becomes the engine's
structural core, what of today's engine stays, how a pass turns the core's
result into files, what agents in course repositories see change, and in which
order the old structure is replaced.

What this note does not reopen: the decisions D1 to D4 of the investigation
(pursue, the width of the order question, asking on identical copies, no
ledger migration step), and the v3 design's one-document model (P4: four
files, one parsed document, lenses with round-trip laws), which stays.

---

## 2. What changes and what stays

| | today (v3, positional) | after the switch-over |
|---|---|---|
| identity of an id'd cell | its id (`id:<sid>`) | the same |
| identity of an un-id'd cell | `pos:<slide>/<kind>/<n>`, fixed at parse time by the lens's half-to-half cursor pairing | none of its own; within a pass it is matched to an entry of the recorded sequence (by id, by content, or by the two inferences of investigation §4.2) |
| the base | member entries, order scopes, spans, pools | the recorded sequence (structure) plus the member entries of id'd cells (trust: was this translation verified) |
| the plan | rows, executed one at a time in six phases, recorded by re-reading what was written | one merge result, rendered into both halves at once, recorded from the result |
| translation work | `translate_new`, `translate_edit`, `verify_translation`, fork / unify rows | the same rows, from a text layer that runs on the merge result (§5) |
| the agent contract | report rows keyed by member handles, answers per row, `report_id` freshness | the same contract. Some row kinds go away, two are new, and un-id'd cells get a new handle (§7) |
| the pre-write verify (#1051 B2) and content conservation (#1141) | the judges of a plan built from rows | assertions about the renderer: a violation is a bug, and the pass writes nothing (§6.4) |

The things that stay unchanged are the parser (`parse_bundle`, as the reader of
each file and of ids, tags, `lang`, owners and companions), the lenses and
their round-trip laws, the ledger file and its merge rules, the member entries
of id'd cells, the decision-document format and `apply --decisions`, the CLI
verbs, and every check that does not depend on pairing (validator, coverage,
rename verbs, harvest).

---

## 3. Principles, amended

The v3 design's §2 holds except for three principles, which the switch-over
replaces:

- **P1 — Identity is total** becomes **P1′ — Identity is given or recorded.**
  An id'd cell's identity is its id, as before. An un-id'd cell has no identity
  of its own. A pass aligns it to the recorded sequence, by content where the
  content is unchanged, and by the two inferences where it is not. Where every
  reading of the files gives the same result, the pass acts; where they differ
  and the other half changed one of the cells, it asks (`identify`). No key
  ever encodes a position. *(This is the investigation's root cause, removed.)*
- **P6 — The plan is derived once** becomes **P6′ — The result is derived
  once.** One merge produces the whole result of a pass: one sequence from
  which both halves are rendered. There is no plan of rows to execute, so
  there is nothing to reorder, defer, freeze or re-read.
- **P7 — Per-item application** becomes **P7′ — Progress around open
  questions, by rule.** A question about an id'd cell holds that cell only;
  any other open question holds the part it is in (investigation §4.6, checked
  across passes on every small input). Completed work is never lost: what a
  pass writes, it records. What it holds, it leaves exactly as it was, on both
  halves and in the record.

P2 to P5 and P8 stand. P8's closure rule gets a sharper test: a new behaviour
is a new aspect merged 3-way (like tags, file and localized-ness), a new
question kind, or a new text-layer rule. It is never a new pairing rule.

---

## 4. A pass

```
read     each file of each half on its own  ─┐
         the ledger: sequence + id'd entries ─┤
align    each half to the recorded sequence   │  structural core
merge    membership, content, tags, file,     │  (§4.2, a port of
         localized-ness, order — 3-way        ─┘   scripts/sync_model.py)
text     translation, owners, preambles, trust   text layer (§5)
render   one merged sequence → both halves'      renderer (§6)
         files, byte by byte
gate     verify + conservation as assertions
write    the changed files
record   member entries and the sequence, from the result
```

`report` runs read → text and prints the questions and the changes. `apply`
runs the whole pass with the answers it was given. Both run the same function
up to the text layer, so the two ends cannot disagree.

### 4.1 Reading

Each file of each half is read on its own: the cells of the German deck, of
the German companion, and likewise in English, in file order
(`doc_lenses._read_source` today). The lens's half-to-half pairing
(`_Parser.pair_positionally`) is not consulted for structure. The parse is
still needed: it reads ids, tags, `lang`, owners and companion placement, and
it refuses decks that break the normalization precondition (an un-id'd
localized cell, a duplicate id). Those refusals stay as they are.

The base is the ledger section's `sequence` (step 4). A deck whose section has
none is handled by §8.2.

### 4.2 The structural core

A port of `scripts/sync_model.py` into `src/clm/slides/` (proposed name
`doc_core.py`), pure, with the shadow's adapter (`scripts/sync_shadow.py`:
real cells as the core's cells) moved alongside. What it decides:

- **which cell is which**: id, then exact content, then "replaced in place"
  and "the only one left"; `identify` where readings differ and it matters;
- **membership**: added, removed, kept, per member, 3-way; `remove_vs_edit`
  where one half removed what the other edited;
- **shared content**: 3-way; `conflict_shared` when both changed it
  differently; `conflict_copies` when each half edited a different one of
  several byte-identical recorded cells (D3);
- **tags**: 3-way; `conflict_tags`;
- **the file** a cell sits in (deck or companion): 3-way, like tags;
- **localized-ness**: a fork or unify on one half is a question (`fork`,
  `unify`); on both halves it needs none;
- **an id gained on one half**: the other half gains it;
- **order**: pairwise precedence, 3-way, within a file, with the width of D2;
  `order` when both halves reordered within one slide.

The port changes nothing about these rules. Every change to them goes
through the prototype's four checks first (probe, laws, laws with tags,
passes) and the shadow. Those checks move into the test suite: the laws on
the `ci` scope as fast tests, the rest in the slow tier.

### 4.3 What the core does not decide

The text of a localized cell, the header attributes of a localized cell other
than tags (`for_slide`, `vo_anchor`), file preambles, and whether a recorded
entry is trusted. Those are the text layer's (§5). The core treats a localized
cell's content as opaque per half: it keeps each half's own text and only
places, adds, removes, retags or moves the cell.

---

## 5. The text layer

The text layer runs on the merge result. It compares each member's per-half
state with its recorded entry, the comparisons today's differ makes for id'd
cells. They do not depend on pairing, because every member is already
settled. Its rows are today's, with today's answers:

| aspect | rows (unchanged names and answers) |
|---|---|
| text of a localized cell new on one half | `translate_new` (`body`) |
| text of a localized cell edited on one half | `translate_edit` (`body`, `keep_twin`) |
| text edited on both halves | `verify_translation` (`confirm`, `body`) |
| a fork or unify on one half | asked by the core, not here: `fork` carries the twin's text, `unify` chooses the content |
| `for_slide` / `vo_anchor` of a localized cell | merged 3-way like tags: one half changed it, take it; both differently, `conflict_owner`; an owner that names no anchor, `broken_owner` |
| file preambles | `propagate_preamble`, `record_preamble`, `conflict_preamble` |
| a localized member with no trusted entry | `verify_cold` (`confirm`, and `body` with `side`) |

Two things change here. The owner and anchor attributes become merged aspects
instead of rows that compare one attribute at a time. That closes the design
gap #1180 points at ("anything else falls through"): the core already treats
the header as content plus aspects, and the text layer does the same for
localized cells. And `verify_cold` is only asked about localized cells. An
un-id'd shared cell is always in the recorded sequence or new, and the
positional cold row (the "give it a `slide_id`" exit, 9,200 of the 16,744
one-sided small cases of investigation §2.5) has no reason to exist.

The fork and unify rows of the v3 design (§7.2 there: `fork_pending_twin`,
`unify_pending_twin`, `unify_choose_body`, `record_fork`, `record_unify`)
become the core's `fork` / `unify` questions. A `fork` answer is the twin's
text, so a fork takes one answer where it takes two rows today (mark the twin,
then translate it).

---

## 6. Rendering

### 6.1 Bytes come from cells, never from text transforms

Every cell a half holds after the pass is one of these:

1. **its own cell**, byte for byte, when nothing about it changed;
2. **its own cell, adjusted**: the `slide_id` added (an id gained on the other
   half), the tags set (`sync_writeback.set_header_tags`), an owner or anchor
   attribute set;
3. **the other half's cell**, byte for byte, for a shared cell that half added
   or changed (a shared cell is byte-identical on both halves, modulo the id);
4. **a twin built from the other half's cell** with the translator's body
   (`sync_writeback.build_twin_cell`, which swaps the `lang` attribute), for a
   localized cell new on one half, and for the twin of a cell one half forked.

That is the executor's existing set of primitives, applied to a sequence
instead of a row. No new way of producing bytes is needed.

### 6.2 Separators and file structure

The cells of each file are emitted in the merged order through the existing
emitter (`doc_write.DeckEmitter`). It writes the file's preamble and then each
cell's lines verbatim, so a cell's trailing blank lines (its separator) are
part of the cell. A cell that keeps its place keeps its separator. A cell that
is new on a half or moves there needs the separator its new position calls
for, as the executor's insert path produces today (`build_cell` /
`build_twin_cell` take the body bare and leave the separator to the insert).
Phase B has to state that rule in one place and test it. The identity law and
the byte-level oracle of §6.3 are the tests. A file the pass creates (a
companion that only the other half had) goes where `expected_companion`
says.

### 6.3 The laws of the renderer

- **Identity:** rendering an unchanged deck from its own merge gives the same
  bytes (the v3 `project ∘ parse` law, extended). Tested over the bundled
  corpus and, in the shadow, over every in-sync state of both course
  repositories.
- **Agreement:** after a pass without held cells, both halves parse to the
  same sequence (`deck_sequence` succeeds). This is the "both halves are
  rendered from one sequence" claim, made executable.
- **The oracle on history:** the shadow (§9, phase B) compares the rendered
  files with the next in-sync commit byte by byte, the translator's text taken
  from that commit. Today it compares cells, not bytes.

### 6.4 The gate becomes an assertion

The structural verify and content conservation stay, and run on the rendered
files before anything is written. With the core, a violation they find is not
a plan to recompute (#1051 B2's withhold-and-retry): it is a defect in the
core or the renderer. The pass then writes nothing, records nothing, and
reports an internal error naming the check, the slide and the deck. Course
repositories never get a partial write. `apply` exits 2, and the error asks for
an issue. During phases C and D (§9), the old engine is still selectable for
that deck.

---

## 7. The agent contract

### 7.1 Handles

A row is keyed by a handle the agent echoes. An id'd cell keeps `id:<sid>`. An
un-id'd cell gets one of two handles:

- `seq:<n>` — the n-th entry of the recorded sequence (0-based), for a
  recorded cell;
- `new:<lang>:<file>:<n>` — the n-th cell of that file on that half, for a
  cell the record does not know.

Both are only meaningful against the files and the ledger section the report
was made from, and `report_id` already pins exactly those two inputs. A
decision document whose `report_id` is stale is refused today, so a handle can
never land on the wrong cell. The `pos:` handle disappears.

### 7.2 Rows that go, rows that come

Rows that go: the positional and order machinery of investigation §4.7.
- `pool_placement_divergence`, `pool_pairing_shifted`, `ambiguous_alignment`,
  `remove_vs_split` and `stamp_vs_new`: the core decides these without asking,
  or asks `identify`.
- `mirror_pool_move`, `mirror_order`, `record_order`, `record_key_migration`
  and `record_group_rename`: mechanical rows that become part of the merge
  result.
- The positional `verify_cold` (§5).
- `fork_pending_twin`, `unify_pending_twin`, `record_fork`, `record_unify`,
  replaced by `fork` / `unify`.

Rows that come:

- `identify` (answer: one of the candidate handles, or `removed`): which cell
  on this half is the recorded cell X. It is asked only where the readings
  differ and the other half changed one of the candidates. The prototype asks
  it 6,328 times on the probe's 67,871 cases, where the probe's reader knows
  the answer. The shadow on real history never asked it, as expected for
  one-sided edits.
- `conflict_copies` (answers `de`, `en`): D3.
- `fork` (answer `body`: the twin's text, as `translate_new`) and `unify`
  (answers `de`, `en`, `body`, as `unify_choose_body` today).

`order_decision` keeps its name and answers (`de`, `en`) with the width of
D2. The report keeps listing the mechanical changes of a pass, so an agent can
see what `apply` will do. These are now derived from the merge result
(propagate, copy, mirror tags, move, remove, and so on), not executed one at a
time.

### 7.3 Versioning

The report's `schema` goes from 5 to 6 at the switch-over (phase D), because
handles and row kinds change meaning. `apply` accepts decision documents of
schema 6 only from the new core, and of schema 4 and 5 only from the old one
while it can still be selected. The info topics `commands.md`, `sync-agents.md`
and `migration.md` describe the change in the same PR (the Info Topics
Maintenance Rule).

---

## 8. The ledger

### 8.1 What is recorded

After a pass, the record is the merge result: the sequence of every member the
pass settled, and the recorded entry of every member it held. `deck_sequence`
on the written files must equal that sequence when nothing was held; a
mismatch is the renderer assertion of §6.4. Member entries of id'd cells are
written from the result, as today's `_upsert` does, but without re-reading the
files. Positional member entries (`pos:…`), the per-half spans, the order scopes
(`member_order`) and `group_order_by_side` are no longer written or read. The
first pass of the new core on a deck drops them. That is a one-time ledger
diff, and much of it is deleted lines.

### 8.2 A deck without a sequence

By D4, every deck should have one before the default flips (phase D), and the
pending decks have to be finished first. For a deck that has none anyway (a
fresh checkout of an old branch, a deck recorded only by an old clm), the new
core takes the current halves as the base when they are in sync
(`deck_sequence` of the files). Otherwise it refuses, the same way it refuses
a deck it cannot parse: `sync report` names the deck and says to finish the
pending items with the old engine (phases C and D) or to `record` once the
halves agree.

---

## 9. Order of work

Each phase ships on its own, keeps the old engine the default until phase D,
and has an exit gate. The v3 cutover followed the same pattern (v3 design §11).

- **Phase A — the core as a library.** Port `scripts/sync_model.py` and the
  shadow's adapter into `src/clm/slides/doc_core.py`; move the laws (fast on
  the `ci` scope, slow over the full scopes and across passes) into the test
  suite; `scripts/sync_shadow.py` and the probe scorer import the library
  instead of the prototype. Exit: the probe, all law checks and the shadow
  give the prototype's numbers exactly.
- **Phase B — text layer and renderer, in shadow.** §5 and §6, run beside the
  engine by `scripts/sync_shadow.py`. The shadow's oracle becomes byte-level
  (§6.3), and the renderer laws run over every in-sync state of both course
  repositories. Exit: every one-sided replay ends in the next in-sync commit's
  bytes, up to the translator's text. Each difference is fixed or explained in
  the investigation doc.
- **Phase C — report and apply behind a switch.** `CLM_SYNC_CORE=sequence`
  selects the new core for `report`, `apply` and `record`; the default stays
  positional. The new handles and rows (§7) exist only under the switch. The
  probe, the laws ratchet (`tests/slides/test_sync_laws.py`) and the sweep run
  against both engines in CI. Exit: no case worse than the old engine in any of
  them, and the maintainer's own course work done with the switch for two
  weeks or 50 sync passes, whichever comes later (decision O4), counting
  manual fall-backs as the v3 dogfood week did.
- **Phase D — the default flips.** Finish the decks still pending (D4); wire
  schema 6; info topics, changelog, the `migration.md` entry. The old engine
  stays selectable with `CLM_SYNC_CORE=positional` for one release.
- **Phase E — deletion.** Remove the old structural machinery
  (investigation §4.7: the pool alignment, spans, crossed sync points, order
  scopes, placement executors, pool freezes, the lens's positional pairing),
  the six-phase executor, and the switch. The v3 design gains a §13 row, and
  its §5 and §6 are rewritten to this note.

Phases A and B change nothing a course repository sees. Phase C changes
nothing unless the switch is set.

---

## 10. Risks

| risk | mitigation |
|---|---|
| The core was checked on generated shapes and one-sided real edits only; two-sided real edits are rare in history and untested there | the laws cover two-sided cases exhaustively on small decks; phase C's dogfood period is the field test; the old engine stays selectable through phase D |
| The renderer gets bytes wrong in ways the cell-level shadow could not see (separators, preambles, CRLF) | phase B's byte-level oracle over every in-sync state of two repositories, and the identity law; the gate turns a renderer slip into a refused pass, never a written one |
| A whole deck held by one open question costs more progress than today's per-row apply | measured in phase C (how often a pass with an open question wrote nothing although the old engine would have written something); narrowing the part to slide groups is decision O2 |
| Agents built for schema 5 rows (`pos:` handles, `pool_*` rows) break | schema bump, info topics in the same PR, the old engine selectable for a release; course-repo skills are updated with phase D |
| The prototype's behaviour drifts while being ported | phase A's exit gate is exact equality with the prototype's numbers; the prototype is deleted only in phase E |
| Content questions the text layer must ask that the core hid | the text layer's rows are today's id'd-cell rows, already in use; the shadow counts its answered questions against the engine's (investigation §2.6) |

---

## 11. Decisions for the maintainer

All five were decided by the maintainer on 2026-11-13, each as recommended:
O1 index handles; O2 the whole deck; O3 ask `identify`; O4 two weeks or 50
sync passes, whichever comes later; O5 keep the positional entries through
phases C and D and drop them in phase E. The options and reasons are kept
below.

- **O1. Handles for un-id'd cells** (§7.1): `seq:<n>` / `new:<lang>:<file>:<n>`,
  pinned by `report_id`. Alternative: content-derived handles
  (`code:<fingerprint prefix>`), which survive unrelated edits but are
  ambiguous for identical cells. *Recommendation: the index handles.*
  Identical cells are exactly where an ambiguous handle would hurt, and
  `report_id` already refuses stale documents.
- **O2. How much an open question holds** (§3, P7′): start with the whole deck
  (the rule checked across passes), or build slide groups as parts before the
  switch-over. *Recommendation: whole deck first, measured in phase C.* Slide
  groups need the check across passes extended to multi-group decks before they
  can be trusted. Doing that first would delay the switch-over for a cost not
  yet measured.
- **O3. `identify` in the contract** (§7.2): ask it, or refuse such a pass and
  tell the author to give the cells ids (today's positional exit).
  *Recommendation: ask it.* It needs both halves to have changed the same
  slide's un-id'd cells. The history scan behind #1130 step 3 (v3 design §13)
  found that in 5 committed PythonCourses states, and the hardest form of it
  (both halves moved and edited) in none. Commits only, so that is a lower
  bound. The shadow cannot say more, because it replays one-sided edits, where
  the question is never asked. Refusing would bring back the manual exit
  the switch-over removes; how often it is asked is measured in phase C.
- **O4. The dogfood period of phase C**, and who runs it: the maintainer's
  course work with `CLM_SYNC_CORE=sequence` set, for a period the maintainer
  chooses. *Recommendation: two weeks of normal course work on PythonCourses
  and CppCourses, or 50 sync passes, whichever comes later.*
- **O5. Ledger clean-up at the switch-over** (§8.1): drop positional entries,
  spans and order scopes on the new core's first pass, or keep writing them
  while the old engine is selectable. *Recommendation: keep them during phases
  C and D* (the old engine needs them, and a deck must be able to go back),
  *and drop them in phase E.*

---

## 12. Effort

A rough estimate, from the prototype's size (about 960 lines) and the
executor's (about 4,000):
- Phase A: a few days.
- Phase B: the largest piece, one to two weeks.
- Phase C: about a week of code, then the dogfood period.
- Phases D and E: a few days each.

Phase E removes more code than the switch-over adds. The differ (about 5,600
lines) loses its positional half, and the executor its six phases.
