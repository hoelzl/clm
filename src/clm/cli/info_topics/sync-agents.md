# Driving `clm slides sync` as an Agent (CLM {version})

`clm slides sync` is an **agent toolkit, not an autonomous solver**. The
engine owns the *mechanics* — parsing the pair's ≤4 files into one bilingual
document, diffing members against the committed ledger, executing the
mechanical rows, atomic writes, structural verification, ledger bookkeeping —
and **never calls a model**. **You** own the *judgment*: translations,
conflict resolution, and confirming cold members. Every model-shaped task is
framed as a decision item you answer in one JSON document. For the exhaustive
field-by-field reference see `clm info commands` (the `clm slides sync`
section); this topic is the *how*.

## The mental model

- The pair's files (both deck halves plus any separated `voiceover_*`
  companions) parse into **one canonical bilingual deck**. Each cell pair is
  a **member** with a stable handle: `id:<slide-id>` for id'd cells,
  `pos:<group>/<kind>/<n>` for id-less shared cells. Handles survive
  replanning — they are values, not positions.
- The **committed per-topic ledger** (`<topic>/.clm/sync-ledger.json`) is the
  only trust store. A member with **no entry is cold** — reported as a framed
  `verify_cold` item, never silently trusted. Commit the ledger with the
  content; a merge conflict in it is a true positive.
- The diff is 3-way per member: each side's current fingerprint vs its own
  recorded base. One side moved → propagate (shared) or translate
  (localized); both moved to the same bytes → record; both moved apart →
  conflict, framed.

## The canonical loop

```bash
clm slides sync report DECK --json      # 1. what is necessary? (read-only)
# 2. build decisions.json answering the framed items (see below)
clm slides sync apply DECK --decisions decisions.json --json   # 3. write, per item
clm slides sync verify DECK             # 4. structural gate (also gates the ledger)
git diff                                # 5. review what landed
```

Exit codes are load-bearing: `report` 0 clean / 1 work pending / 2 error;
`apply` 0 all-applied / 1 residue / 2 error; `verify` 0 sound / 2 corrupt.
A bare `clm slides sync DECK` (no verb) is `report` — it **reads, never
writes**. The default is "tell me what is necessary", not "do it".

## Reading the report

`report --json` emits a schema-5 envelope (`"schema": 5, "engine": "v3"`).
Branch on the stable booleans rather than scanning the lists:

- `is_clean` — nothing to do; **stop**. One non-item state suppresses it:
  a parse-observed `group_order_divergence` (the halves order their common
  groups differently). If `is_clean` is false with zero `items`, read
  `observations` — the text report prints the same line — and reorder one
  half, or answer the order item when one is framed.
- `needs_model` — framed **translation** work exists (`translate_edit` /
  `translate_new`).
- `needs_agent` — judgment beyond translation (conflicts, cold members, a
  normalize refusal).

**Before answering a wall of `translate_edit` rows, check `observations` for
`uniform_drift_side`.** It fires when three or more `translate_edit` rows exist
and *every* one drifted on the **same** language half. Each row names its own
side and offers `keep_twin`, but says nothing about the others, so a report full
of them still reads as N separate members to work through; the observation is
the one line telling you they are N views of a single event. Branch on it in
code — it carries `kind` and `side`, so you need not parse any prose.

**It does not tell you what to answer**, and it is not a default. The engine
sees which side *moved*, never which side is *authoritative*, so both readings
stay open and only you know which applies:

- you edited or reviewed that side and the twin still renders it faithfully →
  answer those `translate_edit` rows `keep_twin`; it records the new baseline
  without re-supplying a body. Note this **banks** the pair: the member reports
  in sync from then on, so an unfaithful twin waved through here is not raised
  again;
- that side is your source of truth and the twin must follow → supply adapted
  bodies for the twin.

`keep_twin` is in the `translate_edit` vocabulary **only** — `verify_cold`,
`verify_translation`, `conflict_*` and `order_decision` all reject it. Members
needing two-sided verification frame `verify_translation` and are counted
separately in the observation's detail, precisely so a blanket sweep does not
pick them up.

### Base diffs on translation rows (`base_ref` / `de_diff` / `en_diff`)

A `verify_translation` row asserts "both halves moved off the recorded base" —
a judgement you can only make by seeing *what* moved. Since schema 5 the
engine recovers that base for you when it can: it walks the deck's recent
git history (capped), finds the newest commit whose bytes match the ledger's
recorded fingerprints, and puts per-side unified diffs on the row:

- **`base_ref`** — the full sha of the commit holding the recovered base
  (`git show <base_ref>` works verbatim);
- **`de_diff` / `en_diff`** — unified hunks from the base cell to the current
  cell. **Read these instead of comparing the full `de`/`en` cells by eye.**
  An empty string means that side is byte-identical to its base (the unmoved
  side of a `translate_edit`, which carries the same fields).

The fields are **optional**: a base that was never committed (`record` runs
pre-commit), a history rewritten away, or a repo without git yields rows
without them — then fall back to the full cells as before. The recovery is
exact up to the `slide_id` attribute — the equivalence the ledger's own
fingerprints define: both sides must match, and the match is key-aware (an
id-keyed row only matches the member carrying its own id), so a present
`base_ref` is the recorded state, never a nearest guess or another member's
lookalike bytes. The text report prints the same hunks under each item.

**Before working a wall of `verify_translation` rows, check `observations`
for `verify_translation_batch`.** It fires when three or more such rows all
diverge from the *same* recovered base — one editing session, most likely. If
the hunks repeat one pattern (a rename, renumbering, a formatting sweep),
judge the pattern once. It never changes what you answer: every row still
takes its own explicit answer (`confirm`, or `body` + `side`); there is no
batch answer, at any count.

Each item row carries `key` (the member handle), `outcome`, `action`,
`direction` (`de_to_en` / `en_to_de` / `both` / `none`), `detail`, the full
current cell bytes for both sides under **`de` and `en`** (those exact key
names — so you never re-read files to act), and an **`answers` list naming
exactly the decision shapes `apply --decisions` accepts** for that item.
`answers` is present on every item. Branch on **`resolution`**, not on the
emptiness of that list:

| `resolution` | meaning | what you do |
|---|---|---|
| `mechanical` | `apply` executes it; `answers` is `[]` | nothing — review with `git diff` |
| `decision` | answerable; `answers` names the shapes | put one in the decision document |
| `manual` | framed but **unanswerable**; `answers` is `[]` | read `detail`, repair the files, re-report |

An empty `answers` used to mean both the first and the third case, and this
topic documented only the first — its own example filter script therefore
misclassified every blocked item. `resolution` is the schema-4 discriminator.

The `de`/`en` excerpts are the full cell bytes **including** the `# %%` header
line; **`de_body`/`en_body`** are the same cells without it — which is exactly
what a `body` answer must contain, so you can feed an excerpt straight back
(trailing blank lines are ignored at the write boundary). A report whose
**questions** are all `verify_cold` also carries a top-level `hint` — that is
the seeding case; use `record`, not a confirm-all document (see "Cold
members"). Mechanical `record_neutral` rows sit beside them and do not suppress
it, so test the items that have `answers`, not every item.

### Naming the row you are answering (`action`)

A decision row may carry the item's **`action`**:

```json
{"key": "id:intro", "action": "translate_edit", "body": "…"}
```

Normally you can omit it — one member frames one row. Two things it buys:

- **A member that frames two rows** can have both answered in one document;
  without the discriminator the second row is a `duplicate key` error.
- **Self-checking answers.** A row naming an action the member does not
  currently frame is *reported* (`rejected`, with both actions named) instead
  of silently landing on whatever row the member does frame.

### The freshness token (`report_id`) — copy it into your answers

Every pair payload carries three identity fields:

- **`report_id`** — a token over the bundle bytes **plus** this deck's ledger
  section. Put it at the top level of your decision document
  (`{"schema": 5, "report_id": "…", "decisions": [...]}`). `apply` recomputes
  it and, on a mismatch, refuses the **whole document**: exit 2, nothing
  written, and a message naming both values. That is deliberate — if the deck
  moved since the report, every answer in the document is suspect, not just
  the one that no longer matches.
- **`deck_key`** / **`ledger`** — the deck's trust identity. Two CLI spellings
  (`slides_x.de.py` and its `voiceover_x.de.py` companion) are **one** deck
  with **one** ledger section; the companion form now says so on stderr. Two
  passes over "different" paths are two passes over the same deck.

A document with no `report_id` is still accepted, with a warning naming the
field — schema 3 predates it. That grace ends in a future release; emit the
token now.

**Your own `apply` invalidates the token** — it records into the ledger, which
is half of the token's input. That is deliberate: one report, one apply, then
re-report. If you are applying a report in stages (`--member` at a time), take
a fresh report between passes rather than reusing the document; the second
pass is answering a deck that has already moved.

**Mechanical actions** (no decision needed — `apply` executes them):
`propagate_shared_edit`, `copy_new_shared`, `mirror_remove`, `mirror_tags`,
`mirror_order`, `mirror_layout`, the `record_*` acknowledgements, and the
fork/unify/id-stamp transitions. Trust them; review with `git diff`.

`record_neutral` is the one you will see most on a **never-recorded deck**, and
it writes no file bytes at all — only the ledger entry. It fires for a member
with no ledger entry whose two halves the engine can compare *directly*: both
sides present, declared language-neutral (no `lang=`), of kind `code` or `j2`,
and agreeing on every field the differ compares. There is no translation
divergence to verify there, so it is recorded instead of asked about — roughly
**45% of a cold deck's items**. Prose (`markdown`) is deliberately excluded even
when byte-identical: the engine cannot tell a genuinely neutral cell from German
prose duplicated onto the EN side, so those stay `verify_cold` for you to judge.

**Framed actions** (answer them): `translate_edit` / `translate_new` (produce
the target-language body — or answer `translate_edit` with `keep_twin` when
your edit did not change what the twin should say), `verify_translation` (both
sides moved off base — `confirm` banks them as they are, or supply a `body`
plus the `side` it replaces when your review found one of them wrong; the
side is required here because *both* moved, so the engine cannot infer which
one you corrected), `conflict_shared` / `remove_vs_edit`
/ `unify_choose_body` / `order_decision` / `conflict_preamble` (choose a side),
`conflict_tags` (the twins' tag sets diverged with no attributable direction —
answer `de` or `en`; mirrors **only the chosen side's tag set** onto the twin,
bodies untouched — see "Tag parity" below),
`verify_cold` (confirm the member is in sync — or, on an **id-keyed** member,
supply a `body` + `side` to overwrite a stale twin in the same pass; a cold
member present on **one half only** carries no answers at all — `confirm`
asserts both halves agree — so it comes back `resolution: manual` with the
repair in its `detail`),
`stamp_vs_new` (a new id'd cell appeared while a positional cell of the same
pool vanished on that side — answer `treat_as_new` when the id'd cell really
is new; see "Replacing a positional cell" below), `remove_vs_split` (a
removal whose vanished cell is byte-identical — or body-identical, when
only header attrs/tags changed — to an un-ledgered one-sided
cell of another group — either a suspected **group split**: an id-keyed
slide inserted before a run of un-id'd positional cells moved them into its
group on one half, so the "removals" the other half would mirror are really
the same untouched cells under a new anchor — mirror the inserted slide on
the twin (e.g. answer its `translate_new`), then re-report; or a genuine
deletion that merely coincides with a duplicate cell elsewhere — answer
`remove` to execute it), `broken_owner` (a voiceover/notes companion cell
whose `for_slide` matches no slide anchor — its owning slide was removed;
answer `remove` to prune the orphaned narration from every present half,
or hand-fix the `for_slide` / restore the slide and re-report. A framed
`broken_owner` suppresses the member's other rows for the pass, and until
it is resolved the write gate refuses to record the pair. A slide *rename*
the differ can see in the same pass never frames this: it emits the
mechanical `retarget_owner`, which rewrites the companion's `for_slide` to
follow the rename — narration is never a removal decision when its slide
still exists, #650), `ambiguous_alignment` (genuinely ambiguous residue
— rival id stamps, both sides adding different content into one pool;
carries **no** answers: reconcile by editing, minting ids, then re-report),
`fork_pending_twin` (a shared cell is becoming a localized pair: one side
carries a `lang=` attribute and its twin does not — answer `mark_twin` and the
engine writes the twin's attribute; see "Forking a shared cell" below), and
the normalize-refusal deck item (run `clm slides normalize`, then re-report).

### Parse refusals: read the code, not the header

A refusal blocks the **whole deck** and frames zero items, so its code is the
only routing information you get. Only the id-less codes (`idless_anchor`,
`idless_localized`, `idless_narrative`) are repaired by
`clm slides normalize --stamp-ids` — and the refusal header names that command
only when at least one such reason is present. The others carry their own
`hint:` line:

| Code | What it means | Fix |
|---|---|---|
| `duplicate_id` | one `slide_id` names two cells on one side | `clm slides rename-id DECK OLD NEW` |
| `legacy_title_companion` | pre-#242 `slide_id="title"` with no `for_slide` | give the cell `for_slide="title"` and its own `slide_id` |

**A one-sided `slide` tag is not a refusal** (CLM {version}, #653). Slide-hood
is presentation, so anchor-hood is a property of the *pair*: when the halves
disagree, the boundary opens no group on either side, the cell pairs by id
like any other, and you get an `anchor_shape_divergence` **observation** plus
the mechanical `mirror_tags` row that copies the shape onto the twin. Nothing
to answer — `apply` executes it.

### Forking a shared cell (two passes, both in-engine)

A shared cell becomes a localized pair in **two** steps, and doing both in one
edit silently drops the member's ledger history (the fork identity-carry needs
one side still at its recorded baseline):

1. Add `lang="<your side>"` to the cell on the half you are editing. Report
   frames `fork_pending_twin`; answer **`mark_twin`**. The engine writes the
   twin's `lang=` attribute — that attribute only. (Marking the twin by hand is
   what "never hand-edit the other language" forbids, and it used to be the
   only route.)
2. Re-report. The pair is now localized and its bodies are identical, so the
   member frames `translate_edit`; answer it with the adapted body.

Do **not** stamp the id, add `lang=`, and rewrite the body in one pass: the
differ can no longer prove the two cells are the same member, and the fork
records as a fresh pair.

## Tag parity (tags are language-independent)

Cell tags mirror across the twins — a tag set is never per-language. The
differ checks this as its own aspect, orthogonal to the body rows (the same
way layout and owner changes get their own rows):

- **One side's tags moved off base** → mechanical `mirror_tags`, even when
  the bodies drifted too: a one-sided tag edit that coincides with body drift
  co-frames a `mirror_tags` row *next to* the framed body row on the same
  key. `apply` mirrors the tag set; you answer the body row as usual.
- **Both sides' tags moved apart, or the ledger itself carries a cross-side
  tag divergence** (e.g. banked by a pre-fix confirm) → framed
  `conflict_tags`. Answer `de` or `en`; the chosen side's **tag set only** is
  mirrored onto the twin — bodies are untouched.
- **Sequencing**: a framed `conflict_tags` suppresses **every other row** on
  that member for the pass — the framed body row *and* the layout/owner rows
  alike (two framed rows on one key cannot both be answered, and a framed
  `conflict_owner` shares `conflict_tags`' exact `de`/`en` vocabulary — one
  decision would silently execute both mirrors). The report shows the tag
  conflict first. Answer it, then **re-run `report`** — the suppressed rows
  re-frame once the tags are reconciled. Convergence is two passes, and the
  report is never silent in between.
- **Recording deferral**: a landed `conflict_tags` records **nothing** by
  design (its resolution mutates one tag line; the member re-frames and
  records on the next pass) — and it also defers the ledger recording of
  every *other* row that landed on the same key in that pass, so the
  suppressed body drift is never banked alongside it. A deferred
  **record-only** row reports status `deferred` (nothing was banked; it
  re-frames next report); a deferred **file-mutating** row keeps status
  `applied` with the reason suffix
  `(recording deferred: unresolved sibling item on this member)` — the file
  write stays, the ledger baseline does not move. Consequence: answering a
  divergent-tags *fork*'s `conflict_tags` lands the tag mirror immediately,
  but the co-emitted `record_fork` reports `deferred` and the fork banks on
  the **next** pass.
- **Confirm guard**: `confirm` — on `verify_translation` *and* `verify_cold`
  — and `keep_twin` on `translate_edit` are **rejected while the member's
  current DE/EN tag sets differ**; the rejection reason names both tag sets.
  Answer the tag item (or align the tag lines by hand), then re-report. A
  `confirm`/`keep_twin` answered in the same document as a co-framed
  mechanical `mirror_tags` still lands in one pass — the mirror executes
  first and the guard sees the reconciled state.

## Order parity (order is language-independent, like tags)

The twins must order their id'd cells and their slide groups identically;
the differ checks this **cross-side on the current state**, whether or not
the ledger carries recorded order trust — a cold or confirm-seeded deck is
not order-blind:

- **Recorded order trust exists and one side moved** → mechanical
  `mirror_order` (directed). Trust it.
- **No recorded order covers the divergence** (a cold deck, a
  confirm-seeded ledger, a same-pass rename+edit of a slide) → framed
  `order_decision` naming both sequences. Answer `de` or `en` to adopt that
  side's order, or reorder one half by hand and re-report.
- **A single cell sitting under different slides per half** with no
  recorded placement (typically a cold member) → framed `order_decision`
  keyed on the **member** (`id:<slide-id>`), not on a scope. Answer
  `de`/`en` to adopt that side's placement — `apply` re-homes the twin
  cell under the chosen side's group. The member's `verify_cold` row is
  suppressed for that pass (two framed rows on one key cannot both be
  answered — the `conflict_tags` precedent), and the placement answer
  **banks nothing**: the member re-frames for verification on the next
  report. On an order-blind ledger this member-keyed row can co-frame with
  a body row on the same key; answer one — the other re-frames next pass.
- **Order trust seeds itself through the loop**: any `apply` pass that
  resolves every item banks order trust for the scopes whose sides agree —
  after your first fully-clean apply, later one-sided moves frame as
  directed mechanical mirrors instead of decisions. (A full `sync record`
  seeds order trust wholesale, as before.)

## The decision document

One JSON document answers any subset of framed items:

```json
{
  "decisions": [
    {"key": "id:intro-motivation", "body": "# The translated EN body…"},
    {"key": "id:setup-venv", "choice": "confirm"},
    {"key": "pos:main/code/3", "choice": "de"}
  ]
}
```

- `body` — the produced text for translate/verify items. **Format:** the cell
  body *without* its `# %%` delimiter line, but *with* the jupytext `# `
  comment prefixes on each line (a markdown cell is prefixed comment lines; a
  code cell is bare source). A body carrying a delimiter line is rejected. For
  a localized markdown slide whose source (DE) reads

  ```
  # %% [markdown] lang="de" slide_id="intro-motivation"
  #
  # # Motivation
  ```

  the `translate_new` answer that mints the EN twin is
  `{"key": "id:intro-motivation", "body": "#\n# # Motivation (EN)"}` — note the
  leading `#\n`, matching the source's comment lines, and no `# %%` line. Bodies
  are validated through the accept-gates: a body smuggling a cell delimiter,
  touching the wrong cell kind, or answering a stale handle is **rejected
  individually with a reason** while every valid answer still lands. Nothing
  already applied is lost.

  A markdown body that *omits* the leading blank `#` line is accepted and
  **normalized at write** (CLM {version}): apply inserts the canonical
  blank comment line the validator expects, so a body opening directly
  with `# ## Title` no longer produces `markdown cell body does not start
  with a blank comment line` warnings right after a clean apply. The
  delimiter rule is unchanged — bodies must still exclude the `# %%` line
  (the report's `de`/`en` excerpts *include* it, so strip the first line
  when reusing them).

  `choice` and `body` are **mutually exclusive** per decision: a `body`
  alone already selects the body answer — never add `choice: "body"`
  alongside it (rejected with `give exactly one of 'choice' or 'body'`).

  **Exception — single-line j2 macro members** (e.g. `id:title`, the deck's
  header macro): the cell is one j2 line, so the `body` answer is either the
  full replacement line (`# {{ header_de("Neuer Titel") }}`) or the bare
  replacement text (`Neuer Titel`), which is spliced into the existing
  macro's quoted argument. The line is replaced in place; multi-line bodies
  and `# %%` lines are rejected.
- `choice` — one of the item's `answers` (e.g. `confirm`, `de`, `en`,
  `keep_twin`). For a `translate_edit` whose edit left the twin a faithful
  rendering, `{"key": …, "choice": "keep_twin"}` records the new baseline and
  keeps the existing twin verbatim — no need to re-supply an unchanged body.
  On a `conflict_tags` item, `de`/`en` names the side whose **tag set** wins —
  only the tag line is mirrored; bodies stay untouched.
- `side` — `"de"` or `"en"`, **only** alongside a `body` on a two-sided
  `verify_cold` item: it names the stale twin to overwrite. `{"key":
  "id:intro", "body": "# frische Übersetzung", "side": "de"}` replaces the DE
  cell and records the fixed pair — cold recovery in one pass. Every other
  action derives its target side itself, so a `side` there is rejected.

Feed it to `apply` (`-` reads stdin):

```bash
clm slides sync apply DECK --decisions decisions.json --json
clm slides sync apply DECK --decisions - --json < decisions.json
```

`--member KEY` restricts a pass to named handles; `--dry-run` validates every
answer and writes nothing. Note what it does **not** cover: a dry run stops
before the write, so it never runs the structural verify gate that a real pass
runs afterwards — a document that dry-runs clean can still end in
`verify_violations` (writes landed, nothing recorded). Landed items are recorded into the ledger
**on fully resolved members only**, and the recording is **gated on the
structural verify** — file writes from a pass that ends structurally corrupt
stay on disk for review, but nothing is recorded as trusted. A landed row on
a member that still carries an unresolved sibling item (pending, rejected,
failed — or an answered `conflict_tags`, which re-frames by design) keeps its
file mutation, but its ledger recording is **deferred**: the entry stays at
its old baseline and the member re-frames on the next report (see "Tag
parity" for the statuses this produces).

### Reading the apply result (`--json`)

The envelope (keys verbatim — do not guess `applied`/`results`/`outcome`,
they do not exist):

```json
{
  "schema": 5, "engine": "v3",
  "dry_run": false,
  "error": null,
  "wrote": true, "written": ["…/slides_x.en.py"],
  "counts": {"applied": 4, "recorded": 2, "deferred": 0, "pending": 1,
             "already_applied": 0, "rejected": 1, "failed": 0, "skipped": 0},
  "items": [
    {"key": "id:intro", "action": "translate_edit",
     "status": "applied", "reason": ""},
    {"key": "id:setup", "action": "verify_cold",
     "status": "rejected", "reason": "…why…"}
  ],
  "exit_code": 1,
  "deck_key": "slides_x", "ledger": "…/.clm/sync-ledger.json",
  "ledger_recorded": true,
  "verify_violations": []
}
```

**Always check `counts.rejected` (and each rejected item's `reason`) before
moving on** — rejections are also echoed to stderr (in `--json` mode they are
printed *before* the payload, so a merged stream still ends in valid JSON).
`already_applied` is **not** a rejection: the member frames nothing now, so
the state your answer asks for already holds — a sibling pass, an earlier
apply, or the other CLI spelling of this deck got there first. It does not
block exit 0. Only a handle naming **no member of this deck** is `rejected`
as stale. `pending` = framed items
you did not answer (exit 1, not an error). `deferred` = a record-only row
whose ledger write was deferred because the member still carries an
unresolved sibling item — nothing was banked; it re-frames on the next
report. (A *file-mutating* row in the same situation stays `applied`, with
the reason suffix `(recording deferred: unresolved sibling item on this
member)`.) `ledger_recorded: false` with `verify_violations` means writes
landed but nothing was trusted — fix the pair, then `record`. One violation
kind worth knowing by name: `order-parity` (the halves order their common
id'd cells differently — a group swap or a one-sided cell move). It is only
a warning in `sync verify` output, but it **blocks** `record` and `apply`'s
ledger save: reorder one half so the twins mirror (`clm validate` names the
diverging sequences too), then re-run.

The same divergence has a write-path guard: minting a slide's missing twin
(`copy_new_shared` / `translate_new` on a slide or subslide cell) **fails
that one item** when the computed insert position would separate the slide
from its group's existing cells on the other half — the reason reads
"cannot mint the … twin … the halves' group order diverges". This is not a
whole-pass abort: all other items still land. The remedy is the same as for
`order-parity` — reorder one half so the twins mirror, then re-apply.

## Cold members and `record`

A brand-new checkout, a never-synced deck, or a deck whose ledger entries
predate a fingerprint-function bump reports **two-sided** members (both halves
present) as `verify_cold` — the engine will not silently trust a pair it has
never recorded.

The exception is the member it does not have to trust: two-sided, declared
language-neutral, of kind `code` or `j2`, and the same bytes on both halves.
That is `record_neutral` — mechanical, answerless, and roughly **45% of a cold
deck**. It writes no file bytes, only the ledger entry. Prose stays a question
even when byte-identical, because a neutral cell and untranslated German look
the same to the tool. Two ways to converge on what remains:

- **Per item**: answer `{"key": …, "choice": "confirm"}` in a decision
  document after you have checked the pair is genuinely in sync. `confirm`
  banks **both sides as-is** — it makes no freshness guarantee, so read both
  bodies first — and it is **rejected while the twins' tag sets diverge**
  (tags are language-independent): align the tag lines, or answer the framed
  tag item, then re-report. If the twin is **stale** (e.g. the source was
  edited while the ledger was cold), do not `confirm`: on an **id-keyed**
  member, answer with a `body` + `side` naming the stale twin
  (`{"key": "id:x", "body": "…", "side": "de"}`) to overwrite it in the same
  pass. A *positional* cold member has no
  addressable id and takes only `confirm` (mint a `slide_id` first if its twin
  is stale). Pool-scoped coherence applies to `pos:` handles: confirm the whole
  `(group, kind)` pool's cold items in one document (a lone positional confirm
  is rejected).
- **Wholesale**: `clm slides sync record DECK|DIR` after a verified pass —
  bless/accept collapsed into one verb, gated on the structural verify, with
  `--provenance agent` (or `semantic:<model>` when a model attested the
  translation quality).

**`record`'s gate reads the voiceover companions (CLM {version}).** It runs the
structural checks over the same companion-inlined projection `verify` uses, so a
divergence that lives only in a separated `voiceover_*` companion — a
byte-diverged **shared** narration cell, a one-sided id'd narrative member, a
duplicated companion id, or a companion cell whose `for_slide` matches no slide
— **refuses the record** (exit 1, `refused` in the JSON, nothing written)
instead of banking it as verified. Before this, `verify` failed on such a pair
while `record` blessed it, and the banked "verified" divergence is what let a
later mirror or propagation overwrite content that existed on one side only.

What to do when `record` refuses: read the reason, fix the narration (usually
`report` already frames the same member as translation work — answer it and
`apply`), then record. `--allow-diverged-companion` is the escape hatch for a
divergence you have judged to be a deliberate pending state; it drops **only**
the companion-introduced violations (a corrupt deck half still refuses) and logs
each one at WARNING. Do not reach for it to silence a message you have not read.
`clm slides sync apply` takes the same flag for its post-write ledger save.

(`clm harvest accept --record` is deliberately **not** subject to this gate: a
harvest write lands narration on one language side, and recording that one-sided
member is what frames the twin as `translate_new` — see `clm info
harvest-agents`.)

**Rule of thumb: when every question in a report is `verify_cold` (the report
says so in a `hint`), use `record`, not a confirm-all decision document.**
Mechanical rows alongside them do not change that — `apply` executes those and
`record` supersedes them. They assert
the same trust; `record` is one command instead of a scripted
report→build-JSON→apply pipeline. Reserve per-item `confirm` for the mixed
case where cold items sit next to real work.

`clm slides split` and `clm slides translate` record freshly-created pairs
automatically, so a normal authoring flow starts warm.

**Renaming a `slide_id` is a common way to fall cold — do not do it by hand.**
The ledger keys trust by `id:<slide_id>`. The engine performs exactly two key
migrations, and neither is ever *inferred*: `pos: → id:` (an id-less cell
gaining an id, at record time) and `id: → id:` (only through
`clm slides rename-id`, which also cascades a renamed group anchor into its
members' `pos:` keys and order scopes). A hand `id: → id:`
rename therefore reads as a cold add on the new id (and a `record_remove` on the
old one), so a cell you *renamed and edited* in one go reports `verify_cold` —
whose `confirm` would bank the existing, now-stale twin. Use
`clm slides rename-id DECK OLD NEW`: it rewrites the id (and every `for_slide`
owner reference) on both halves and **migrates** the ledger baseline key
(carrying the recorded fingerprints, never re-hashing). A pure rename then
reports clean; a rename you did alongside an edit reports `translate_edit`
against the carried baseline — so the stale twin is never silently confirmed.

## Adding a slide in one language (the twin does not exist yet)

Author a new cell on one half only — a new markdown slide (with a fresh
`slide_id`) or a new **id-keyed** shared code cell — and `report` frames it so
the engine grows the missing twin; you never hand-author both halves:

- A new **localized** cell (or a per-language header) → `translate_new`. Answer
  with the target-language `body`; `apply` inserts the twin and mints the
  shared `slide_id` on it.
- A new **shared** id-keyed cell → `copy_new_shared` (mechanical). `apply`
  copies it verbatim to the twin — no answer needed.

This works because the `slide_id` lets `apply` place the twin unambiguously. A
new **un-id'd positional** cell (a `# %%` code cell with no `slide_id`) inserted
among existing cells is instead reported `verify_cold`: its ordinal aliases a
*different* cell on the other half, so the engine cannot mirror it mechanically.
**Mint a `slide_id`** on the new cell (e.g. `clm slides assign-ids`, or add one
by hand) and re-`report` — it then frames `translate_new` / `copy_new_shared`
and the twin is created for you.

## Replacing a positional cell with id-keyed cells — `stamp_vs_new`

Replacing an un-id'd positional cell with one or more new `slide_id`-keyed
cells on ONE half (e.g. a display-only `df.drop_duplicates()` cell replaced by
an assign-back + check pair) frames every affected row `stamp_vs_new`: the
engine cannot tell whether the positional cell was *removed* (and the id'd
cells are genuinely new) or *stamped with an id and edited* — mechanically
copying could duplicate it, mechanically removing could delete real content.
The answer vocabulary is `treat_as_new`:

- On the new id'd cell's row (`id:…`), `{"choice": "treat_as_new"}` copies it
  verbatim to the twin — the normal `copy_new_shared` path it would have taken
  without the suspicion.
- On the vanished positional cell's row (`pos:…`), `{"choice": "treat_as_new"}`
  mirrors the removal onto the surviving half. This row only appears while the
  survivor is untouched; if it was *also* edited (removal would lose the
  edit), the row frames `remove_vs_edit` instead — answer `remove` (delete the
  edited survivor) or `keep` (re-add it on the other half), with the stamp
  suspicion repeated in the row's detail.

Answer all the affected rows in one document and the whole replacement lands in
one `apply` pass. Partial answers are safe: while any row of a pool is still
unanswered, the pool's ledger entries stay frozen, so the remaining rows keep
their framing on the next `report` (already-landed slots re-frame as
mechanical records). If the cell really was stamped-and-edited (the same cell,
now carrying an id), do NOT answer `treat_as_new` — stamp the twin cell with
the same `slide_id` by hand (the halves then pair id-keyed) and re-`report`.

## The forensic window — `report --since`

`clm slides sync report DECK --since "2 days ago"` (or `--since REF`) diffs
against the bundle **at that git ref** instead of the ledger — "what changed
in this window", reported with the same actions. It is a *view*: the ledger
is neither consulted nor written, and `apply` always uses the ledger. Use it
to scope a review; use the normal loop to reconcile.

## Verifying — `clm slides sync verify DECK|DIR`

The deterministic structural gate (no model, no ledger): the pair unifies
back into one bilingual source, `de_id == en_id` symmetry holds, no
`(slide_id, role)` key is duplicated; warns (never fails) on an id'd cell
dropped vs git `HEAD` and on a cross-side **tag-parity** mismatch (twin cells
whose tag sets differ — the state `report` frames as a tag row). Run it after
every write batch and freely in CI. A green verify means the edit did not
*corrupt* the deck — translation quality stays your judgment.

## Asymmetric voiceover/notes companions are alerted, not guessed

A separated companion edited on one language only surfaces as a framed
translate item; a deck mixing inline and separated narration (or
inconsistently across languages) is **refused** with a normalize hint
(`clm voiceover inline` / `extract`). An orphaned companion cell is refused
rather than dropped.

A **one-sided (DE-only) separated voiceover companion** — the state left by
`clm harvest accept` when the EN twin is deferred, i.e. the deck twin
(`slides_x.en.py`) already exists but `voiceover/voiceover_x.en.py` does not —
is framed `translate_new` (`direction: de_to_en`, answer with the EN `body`).
Answering `apply` with that `body` **creates the missing EN companion file**
and writes the cell (minting the shared `slide_id`/`for_slide`, same as harvest
mints cells); the EN deck stays untouched — narration remains in the companion.
No hand-authoring of `voiceover_x.en.py` is needed: the documented harvest →
sync handoff closes through the ordinary loop.

## Non-shell agents — the MCP tool

`slides_sync_report` (MCP) returns the same schema-5 pair payload as
`report --json`, including the `answers` vocabulary per framed item and the
`base_ref`/`de_diff`/`en_diff` fields on recovered translation rows.
Writing decisions currently requires the CLI `apply --decisions`.

## Working patterns for agents

Patterns proven in real sessions (the sessions that used them had zero
rejected decisions; the sessions that improvised did not):

- **Generate the decision document with a script, from the report JSON** —
  never by hand-escaping JSON in a shell string. The report items carry
  everything you need:

  ```python
  import json, subprocess
  rep = json.loads(subprocess.run(
      ["clm", "slides", "sync", "report", DECK, "--json"],
      capture_output=True, text=True).stdout)
  decisions = []
  for it in rep["items"]:
      if not it["answers"]:            # [] = mechanical, apply handles it
          continue
      # your judgment per item: a translated body, confirm, keep_twin, de/en …
      decisions.append({"key": it["key"], "choice": "confirm"})
  print(json.dumps({"decisions": decisions}))
  ```

- **Feed decisions via stdin** (`apply DECK --decisions - --json`) — it
  sidesteps every temp-file/path/quoting problem (Windows `/tmp`, unset
  env vars, MSYS path mangling all produced real failures).
- **Answer by `answers`, never blanket-confirm**: a `translate_edit` offers
  `body`/`keep_twin` — a `confirm` on it is rejected. Branch on each item's
  `answers` list.
- **Always `--dry-run` first** on a nontrivial decision document; it
  validates every answer without writing. It does not run the structural
  verify gate (that happens after the write), so a clean dry run is not a
  promise that the pass will record.
- **Many `translate_new` bodies at once** (e.g. a whole deck authored in one
  language): answering each in JSON works but is heavy. The sanctioned bulk
  alternative is `clm slides translate DECK.en.py` to bootstrap the missing
  half wholesale (it records the ledger), then review and reconcile the
  drifts through the normal loop (`keep_twin` for cells your review left
  unchanged).
- **Parallel sweeps**: `report --json` writes to stdout — in a fan-out,
  capture each deck's output under a deck-derived filename (generic names
  like `report1.json` collided and mixed decks up in real runs), and verify
  `de_path` in the payload matches the deck you asked about.
- **Exit codes are states, not failures**: `report` exits 1 whenever work is
  pending — a read-only command doing its job. Treat only 2 as an error.

## Quick reference

```bash
clm slides sync DECK                          # = report (read-only)
clm slides sync report DIR --json             # sweep a course tree
clm slides sync report DECK --since HEAD~5    # forensic window view
clm slides sync apply DECK --decisions - --json
clm slides sync apply DECK --member id:intro --dry-run
clm slides sync verify DIR
clm slides sync record DECK --provenance agent
```

## Revising your repository guidelines (for a course-repo agent)

If your course repository's agent instructions still reference the pre-cutover
toolkit — `task` / `accept` verbs, `--baseline` / `--use-watermark` /
`--cache-dir` / `--ledger` flags, `sync autopilot`, `sync diagnose`,
`sync baseline bless`, or `clm slides watermark` — update them: the verbs are
now exactly `report` / `apply` / `verify` / `record`, decisions travel in one
JSON document, and the committed ledger replaced every baseline mechanism.
See `clm info migration` for the mapping.

## Principles

1. **Read before you write.** `report --json` first; bare `sync DECK` is
   read-only by design.
2. **Answer items, don't edit around the engine.** A decision document keeps
   identity, validation, atomic writes, and ledger bookkeeping on the engine.
3. **Never bypass a refusal.** A normalize refusal or verify failure names
   the real problem; renaming ids or hand-patching to silence it buries a
   divergence.
4. **Record only verified states.** `record` and confirmed decisions are
   trust assertions — run `verify` (and your own reading) first.
5. **Commit the ledger with the content.** It is the baseline; losing it
   costs a re-confirmation sweep, not correctness.
