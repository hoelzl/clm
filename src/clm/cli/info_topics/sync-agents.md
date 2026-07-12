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

`report --json` emits a schema-3 envelope (`"schema": 3, "engine": "v3"`).
Branch on the stable booleans rather than scanning the lists:

- `is_clean` — nothing to do; **stop**.
- `needs_model` — framed **translation** work exists (`translate_edit` /
  `translate_new`).
- `needs_agent` — judgment beyond translation (conflicts, cold members, a
  normalize refusal).

Each item row carries `key` (the member handle), `outcome`, `action`,
`direction` (`de_to_en` / `en_to_de` / `both` / `none`), `detail`, the full
current cell bytes for both sides under **`de` and `en`** (those exact key
names — so you never re-read files to act), and an **`answers` list naming
exactly the decision shapes `apply --decisions` accepts** for that item.
`answers` is present on every item; `[]` means mechanical (nothing to
answer — `apply` executes it). Note the `de`/`en` excerpts **include** the
`# %%` header line; a `body` answer must **not** (see below). A report whose
items are *all* `verify_cold` also carries a top-level `hint` — that is the
seeding case; use `record`, not a confirm-all document (see "Cold members").

**Mechanical actions** (no decision needed — `apply` executes them):
`propagate_shared_edit`, `copy_new_shared`, `mirror_remove`, `mirror_tags`,
`mirror_order`, `mirror_layout`, the `record_*` acknowledgements, and the
fork/unify/id-stamp transitions. Trust them; review with `git diff`.

**Framed actions** (answer them): `translate_edit` / `translate_new` (produce
the target-language body — or answer `translate_edit` with `keep_twin` when
your edit did not change what the twin should say), `verify_translation` (both
sides moved — confirm or supply a body), `conflict_shared` / `remove_vs_edit`
/ `unify_choose_body` / `order_decision` / `conflict_preamble` (choose a side),
`conflict_tags` (the twins' tag sets diverged with no attributable direction —
answer `de` or `en`; mirrors **only the chosen side's tag set** onto the twin,
bodies untouched — see "Tag parity" below),
`verify_cold` (confirm the member is in sync — or, on an **id-keyed** member,
supply a `body` + `side` to overwrite a stale twin in the same pass),
`stamp_vs_new` (a new id'd cell appeared while a positional cell of the same
pool vanished on that side — answer `treat_as_new` when the id'd cell really
is new; see "Replacing a positional cell" below), `remove_vs_split` (a
removal whose vanished cell is byte-identical to an un-ledgered one-sided
cell of another group — either a suspected **group split**: an id-keyed
slide inserted before a run of un-id'd positional cells moved them into its
group on one half, so the "removals" the other half would mirror are really
the same untouched cells under a new anchor — mirror the inserted slide on
the twin (e.g. answer its `translate_new`), then re-report; or a genuine
deletion that merely coincides with a duplicate cell elsewhere — answer
`remove` to execute it), `ambiguous_alignment` (genuinely ambiguous residue
— rival id stamps, both sides adding different content into one pool;
carries **no** answers: reconcile by editing, minting ids, then re-report),
and the normalize-refusal deck item (run `clm slides normalize`, then
re-report).

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

`--member KEY` restricts a pass to named handles; `--dry-run` validates
everything and writes nothing. Landed items are recorded into the ledger
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
  "schema": 3, "engine": "v3",
  "dry_run": false,
  "error": null,
  "wrote": true, "written": ["…/slides_x.en.py"],
  "counts": {"applied": 4, "recorded": 2, "deferred": 0, "pending": 1,
             "rejected": 1, "failed": 0, "skipped": 0},
  "items": [
    {"key": "id:intro", "action": "translate_edit",
     "status": "applied", "reason": ""},
    {"key": "id:setup", "action": "verify_cold",
     "status": "rejected", "reason": "…why…"}
  ],
  "ledger_recorded": true,
  "verify_violations": []
}
```

**Always check `counts.rejected` (and each rejected item's `reason`) before
moving on** — rejections are also echoed to stderr. `pending` = framed items
you did not answer (exit 1, not an error). `deferred` = a record-only row
whose ledger write was deferred because the member still carries an
unresolved sibling item — nothing was banked; it re-frames on the next
report. (A *file-mutating* row in the same situation stays `applied`, with
the reason suffix `(recording deferred: unresolved sibling item on this
member)`.) `ledger_recorded: false` with `verify_violations` means writes
landed but nothing was trusted — fix the pair, then `record`.

## Cold members and `record`

A brand-new checkout, a never-synced deck, or a deck whose ledger entries
predate a fingerprint-function bump reports **two-sided** members (both halves
present) as `verify_cold` — the engine will not silently trust a pair it has
never recorded. Two ways to converge:

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

**Rule of thumb: when a report is *all* `verify_cold` (the report says so in
a `hint`), use `record`, not a confirm-all decision document.** They assert
the same trust; `record` is one command instead of a scripted
report→build-JSON→apply pipeline. Reserve per-item `confirm` for the mixed
case where cold items sit next to real work.

`clm slides split` and `clm slides translate` record freshly-created pairs
automatically, so a normal authoring flow starts warm.

**Renaming a `slide_id` is a common way to fall cold — do not do it by hand.**
The ledger keys trust by `id:<slide_id>`, and the only key migration the engine
recovers is `pos: → id:` (an id-less cell gaining an id). A hand `id: → id:`
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

`slides_sync_report` (MCP) returns the same schema-3 pair payload as
`report --json`, including the `answers` vocabulary per framed item.
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
  validates every answer without writing.
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
