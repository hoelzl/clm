# Driving `clm slides sync` as an Agent (CLM {version})

This is the workflow guide for a **coding agent** that keeps a split bilingual
deck pair (`deck.de.py` + `deck.en.py`) in sync. `clm slides sync` is an **agent
toolkit**, not an autonomous solver: the engine does the *mechanical* work and
the *verification*, and **never calls a model**; you do the *judgement* (or pick
a model for it). You spend attention only on the residue, never on re-deriving
the whole deck.

`clm slides sync` is a **verb group**. The verbs are read-only or
deterministic-write; the only place a model ever runs is *your* call between
`task` and `accept`, and you choose it. For the exhaustive field-by-field
reference (every verb, every flag, every JSON key) see `clm info commands` (the
`clm slides sync` section). This topic is the *how* — the loop you run and what
to do with each thing the engine hands you.

## The mental model

A bilingual topic is split into two language halves. They must stay aligned:
same slides, same order, language-neutral code byte-identical, each `slide_id`
carried by both halves (`de_id == en_id`). Editing **one** half drifts the
other. The toolkit reconciles them against a baseline — **git `HEAD` by
default** (the watermark is a demoted, opt-in accelerator).

The engine resolves everything it can **deterministically, with no model**. What
it cannot resolve it does **not guess** — it hands you a *characterised*
description of the residue (the **tiered report**) and, for a model task, the
*framed prompt*. Your job is to act on the two tiers the engine deliberately
leaves for a model / for you, then let the engine verify the result.

## The canonical loop

```
1. clm slides sync report DECK --json     # what is necessary? (read-only, no key)
2. branch on report.is_clean / needs_model / needs_agent
3. clm slides sync apply DECK             # do the mechanical tier-1 (writes, no model)
4. for each assisted / ambiguity item:
     clm slides sync task DECK --item ID  # the framed prompt + inputs (read-only)
     <run it through whatever model you choose, or do it yourself>
     clm slides sync accept DECK --item ID --answer -   # validate + write both halves
5. clm slides sync verify DECK            # structural gate (no model); 0 = sound
```

`DECK` is, everywhere, either half (`deck.de.py`), the shared stem, or a
directory (a batch sweep). **Steps 1, 2, 4-`task`, and 5 are read-only and need
no API key.** Steps 3 and 4-`accept` write — deterministically, still with **no
model**. The only model run is *yours*, between `task` and `accept`.

A bare `clm slides sync DECK` (no verb) is an alias for `clm slides sync report
DECK` — it **reads, never writes**. This is the one inversion to internalise: the
default is "tell me what is necessary", not "do it".

## Reading the report

`clm slides sync report DECK --json` emits a `ReconciliationReport`. Branch on
the three booleans rather than scanning the lists:

- `is_clean` — no work in any tier; the pair is in sync. **Stop.**
- `needs_model` — a tier-2 (`assisted`) or tier-3 (`ambiguity`) item exists.
- `needs_agent` — a tier-3 (`ambiguity`) item needs *your* judgement.
- `baseline_source` — `git-head` (default) / `git:<ref>` / `watermark` / `none`.

Each item carries a stable `item` id (the handle you pass to `task` / `accept`),
plus `kind` / `role` / `direction` / `slide_id` / `reason`, 0-based
`source_position` / `target_position`, and (under `--json`) the cell bytes:
`source_excerpt` / `source_line` (the side to reconcile *from*) and the matching
`target_*` (the existing counterpart). Excerpts are **report-time-only** and
**fail-closed**: an absent excerpt means "I could not locate this with
certainty" — trust that over a guess, and read the source you hold.

### Tier 1 — `mechanical` (trust and ignore)

`move` / `remove` / `retag` / `neutral-propagate`. `clm slides sync apply DECK`
applies these deterministically with **no model**. You do not read or touch
them — they are in the report only so it names *every* change the engine makes.

### Tier 2 — `assisted` (a framed model task)

`add` / `edit` / `rename` / `mint` / `adopt` / `reconcile` — the engine already
knows *which* cell, *which* direction, and *what* is needed (translate a new
slide, reconcile an edit on an id'd localized cell, confirm a cold-pair
correspondence). You: run `task`, do the work (any model, or by hand), `accept`.
The point of the tier is that this is **bounded work on a named cell**, not a
free-form "translate the deck".

### Tier 3 — `ambiguity` (your judgement)

The engine **refuses to guess**. The item states *what* is ambiguous, never a
fabricated fix. Three shapes:

- A **`conflict`** — the same cell drifted on both halves in opposite directions.
  Decide the winner (edit the deck so both halves agree), then re-`report`. For a
  whole-deck "one side is authoritative" reconcile (e.g. you edited German all week
  and want English overwritten to match), `clm slides sync autopilot DECK --conflict
  de-wins --yes` resolves **every** both-edited conflict non-interactively (#447):
  it re-translates the German cell over the English one. This **discards the English
  edits** (irreversible — review the `git diff`), so it is opt-in and `--yes`-gated;
  `--dry-run` previews it. `--conflict de-wins-safe` is the cautious tier — it asks the
  model whether the English half carries content the German lacks and **escalates**
  (leaves as a conflict) those, resolving only the rest. id-less and remove-vs-edit
  conflicts are never auto-resolved. `--conflict` is autopilot-only (resolving a
  conflict re-translates, which needs the embedded model).
- An **`issue`** (carries `severity`) — a structural situation the classifier
  will not turn into a proposal (a one-sided header edit, a both-sided
  incompatible shared-cell change). Read `reason`, fix the deck, re-`report`.
- A **`realign`** — see below; the item the agent-first design added for you.

## `task` and `accept` — the model handoff you own

`clm slides sync task DECK --item ID` returns a `SyncTask`: the report-item handle
(`item`, `kind`, `tier`, `slide_id`, `direction`, `role`) plus `instructions` (the
system prompt), `prompt` (ready to send to a model), `inputs` (the cell bytes /
glossary / direction), `answer_schema` (the exact shape `accept` will enforce),
and `validator` (which deterministic check `accept` runs). Omit `--item` to emit a
task for **every** frameable tier-2/3 item.

Run the prompt through any model you like; capture the answer in the
`answer_schema` shape; pipe it to `clm slides sync accept DECK --item ID --answer
-` (or `--answer FILE`). `accept` runs the named `validator` and writes **both
halves** iff it passes (maintaining `de_id == en_id` and neutral byte-identity),
or **rejects with the reason and writes nothing**. A rejected answer is a signal
to retry with a better model/prompt — the deck is never corrupted by a bad
answer.

The engine **never calls a model** at any step here. `task` frames; *you* run the
model; `accept` validates. No verb in this loop needs an API key.

## The `realign` item (drifted-id recovery)

When a `slide_id` drifts off its construct **while** a cell splits — e.g. a
function is renamed in the same edit that pulled an `import` into its own cell, or
two cells now share a construct so the move is ambiguous — the deterministic
id-migration cannot safely decide which cell should carry the id. Rather than
guess (a wrong id silently breaks cross-references and voiceover links), the
report surfaces a tier-3 `realign` item whose `task` gives you the region and the
candidate continuation:

```json
{
  "tier": "ambiguity",
  "kind": "realign",
  "role": "neutral-code",
  "slide_id": "def-my-fun",
  "reason": "slide_id 'def-my-fun' drifted off its baseline construct 'function-my-fun' — the cell now wearing it is 'import-time'. Re-identify the cells, move the slide_id onto its true continuation, then run `clm slides sync verify` to confirm.",
  "source_excerpt": "import time",
  "source_line": 12,
  "target_excerpt": null
}
```

`source_excerpt` is the cell **currently wearing** the id (the one that drifted);
`target_excerpt` is the **likely continuation** when there is an unambiguous one
(it is `null` when a rename broke the construct link — then you decide). To fix
it: move the `slide_id` header attribute onto the cell that is the *true*
continuation of that id (usually the renamed/split-out construct), apply the
**same** change to **both** halves (neutral code is byte-identical across halves),
then `accept` runs `validate_alignment` on your answer. This is the agent-first
replacement for the old `--llm-recover` flag (which now survives only on
`autopilot`): you re-identify the cells — you have the full context — and the
deterministic validator guards the result.

## Verifying — `clm slides sync verify DECK`

A **structural** safety check: it confirms an edit did not *corrupt* the pair —
byte-identical shared cells, header parity, clean alignment, `de_id == en_id`
set-symmetry, no duplicate ids — and **warns** (never fails) if an id'd cell was
dropped vs git `HEAD`. **No model, no watermark, writes nothing.**

It answers *"did this edit corrupt the pair?"* — **not** *"is it in sync?"*
(`report`) and **not** *"is the translation good?"* (a semantic call it never
makes). Exit `0` = structurally valid (warnings allowed), `2` = corruption. Run
it after every hand edit and after every `accept`. It is CI-safe because it
needs no model, and works on a single pair or a directory.

## Diagnosing a `verify` failure — `clm slides sync diagnose DECK`

A `verify` failure is a **symptom, not a diagnosis**: the same code —
`id-asymmetry`, `duplicate-id` — is produced by several *unrelated* root causes,
each needing a *different* fix. **The anti-pattern is to rename / re-slug ids until
`verify` passes** — that hides the real defect (it can point an id at the wrong
content while the truly-missing slide stays absent — the `array-limitations`
trap). So: **diagnose each failing pair before touching it.**

`clm slides sync diagnose DECK` does that mechanically. It is a **read-only
superset of `verify` and `reconcile-vo-ids`** — it runs the same structural checks
*and* the occurrence-pairing detection that catches the most common case `verify`
cannot see (a narration cell id'd on one half, id-less on the other: no
`id-asymmetry` fires, because the slide's id is still on the slide cell in both
halves). For each finding it emits a **root cause**, the **evidence** (the cell's
*content language* vs its `lang=` tag, who carries the id, whether a twin exists),
the **prescribed fix**, and whether that fix is **mechanical** or **authoring**:

| Root cause | Meaning | Fix |
|---|---|---|
| `DUPLICATE-NARRATION-OVERSTAMP` | several `voiceover`/`notes` cells under one slide all id'd (`assign-ids` over-stamp) | **mechanical** — strip to id-less (`--apply`) |
| `NARRATIVE-ID-DISAGREEMENT` | a narration cell id'd on one half, id-less on its twin | **mechanical** — strip to id-less (`--apply`) |
| `MIS-TAG` | a cell's content language ≠ its `lang=` tag → routed into the wrong half | authoring — move it to the right half with the twin's id |
| `ID-LESS-TWIN` | the twin exists but was never tagged | authoring — add `tags=[…]` + `slide_id` (no translation) |
| `CONTENT-GAP` | the slide exists in only one language | authoring — translate the missing twin (same id) |
| `WHOLE-DECK-GAP` | one half is empty | authoring — a translation project; track it |
| `DUPLICATE-ID-NON-NARRATIVE`, `UNIFY-ALIGNMENT`, `DROPPED-ID` | a real structural problem | authoring — resolve by hand |

`diagnose --apply` performs **only** the two mechanical narrative fixes (re-gated
by structure so a write never introduces a new error); everything else is a
worklist entry it **never** auto-rewrites — and it never renames an id to silence a
symptom. Read-only by default, `--json` for the structured form. Use it as step 1
when `verify` (or a sweep) reports `id-asymmetry` / `duplicate-id`, then act on the
labelled findings instead of guessing.

## Baselines (you rarely touch these)

`report` / `verify` default to git `HEAD`; you do not need the watermark for the
normal loop. A few cases:

- **You committed single-language edits *before* syncing** — they already match
  `HEAD`, so the default report reads clean. Point it at the pre-edit commit:
  `clm slides sync report DECK --baseline HEAD~1`.
- **A whole week/module of committed single-language edits** (you edited and
  committed many German halves over days without syncing English) — pin a baseline
  from before the editing and sweep the directory: `clm slides sync report
  slides/module_410 --baseline <ref-before-the-edits>`. `--baseline REF` works over a
  directory (each pair diffed against REF), so one report tells you, per deck, exactly
  what English is missing — instead of the default git-HEAD batch reading the lot as
  "clean". Then reconcile each drifted deck (`apply`, then `task` → model → `accept`,
  then `verify`). Pick the ref from the commit log just before the editing session —
  **or skip the manual ref-picking with `--since`** (#446): `clm slides sync report
  slides/module_410 --since "2 days ago"` resolves the timeframe to the last commit
  at/before then and uses it as the baseline (a git ref works too: `--since HEAD~5`,
  an alias for `--baseline`). `--since` is available on `report` / `apply` / `task` /
  `accept` / `autopilot`.
- **Bless the current consistent state** as the baseline (e.g. after reconciling
  by hand): `clm slides sync baseline bless DECK` — no throwaway commit needed
  (it replaces the old `--rebaseline`). It is gated on `verify`, so a structurally
  corrupt pair is refused rather than blessed.
- **Stale watermark? Just `apply`.** If both halves were edited + committed without
  an intervening sync, the watermark falls behind and a watermarked run would error
  on a *false* stale-baseline conflict ("id-less localized cells edited on both
  decks", or a keyed conflict). A writing `clm slides sync apply DECK` (or
  `autopilot`) now **auto-re-baselines** that stale-but-consistent watermark by
  default (#364) — safe by construction (only when git `HEAD` shows the halves
  consistent), so the reconcile just proceeds. Pass `--no-auto-heal` to surface the
  conflict instead.
- **Inspect / maintain**: `clm slides sync baseline show`; drop orphans with
  `clm slides sync baseline prune`; `clm slides sync baseline clear DECK` re-derives
  off git `HEAD` next time.

## The consistency ledger — don't re-litigate a slide you already synced (`--ledger`)

A baseline is *one* answer for the whole deck. But after a few rounds a deck is
never uniformly drifted: slide X was reconciled three days ago, slide Y two weeks
ago, slide Z never. Pointing `--baseline <2-weeks-ago>` at the deck re-proposes an
`edit` for slide X too — re-litigating a sync you already did. The **per-slide
consistency ledger** (`<topic>/.clm/sync-ledger.json`, committed) fixes that.

- **Record trust** when you bless a reconciled deck: `clm slides sync baseline
  bless DECK --ledger`. For each localized slide it records the fingerprint of
  *both* halves at this commit (gated on `verify` — a corrupt pair is never
  recorded). A slide is trusted-in-sync **only from its first recorded
  confirmation forward**: there is no commit in history we assume was in sync, so
  trust is recorded, never guessed. `apply --ledger` (and the legacy `autopilot
  --ledger`) also record automatically — but **only on a fully-clean pass** (no
  deferred residue), so a deck you fully reconciled (or one already in sync) banks
  its trust without a separate `bless` (`confirmed_by=apply` / `=autopilot`); a deck
  still carrying residue records nothing until you finish it.
- **Bank trust per item, in the loop** — after you `accept` a single drifted edit,
  add `--record`: `clm slides sync accept DECK --item ID --answer - --record`. It
  banks **just that one reconciled cell** (`confirmed_by=accept`,
  `confirmed_oracle=agent`), gated on a *per-slide* structural verify, so the next
  `report --ledger` skips it without waiting for a whole-deck `bless`. This is the
  incremental confirm path: each `task → model → accept --record` step both fixes a
  slide and records its trust. (Only `edit` is per-item recordable; a structural
  `add` / `mint` / `adopt` / `reconcile` / `realign` is banked deck-wide by `apply
  --ledger` / `baseline bless --ledger` once the deck is coherent.)
- **Consult trust** when you reconcile: `clm slides sync report DECK --ledger`
  (and `apply --ledger`) **skips** any slide whose two current halves are
  byte-identical to a recorded confirmation — even against an old `--baseline`. So
  the timeframe reconcile above surfaces only the genuinely-drifted slides; the
  ones you synced last round stay quiet. A slide that *did* change since its
  confirmation does not match and surfaces normally; a slide with no entry is
  checked as usual (the cold path).
- **Bootstrap a legacy deck** that already has a watermark but no ledger:
  `clm slides sync baseline seed DECK` (a directory works too). Each localized
  slide inherits the watermark's recorded hashes, stamped `confirmed_oracle=assume`
  (inherited trust, not a fresh check) — so the deck does not cold-start every
  slide on its first `--ledger` run. Stale-safe (a slide drifted since the
  watermark re-checks) and fill-gaps only (a real confirmation is never downgraded).
- **Establish trust with a real check** (the `semantic` rung, the strongest):
  `clm slides sync baseline establish DECK` LLM-judges whether each localized
  `(de, en)` cell is a faithful translation and banks the faithful ones as
  `confirmed_oracle=semantic:<model>` — so a slide judged once is paid for once and
  becomes a free ledger hit. A pair judged **not** faithful is reported (a real
  divergence — reconcile it via `report`/`task`/`accept`) and **not** banked. This is
  the one-time "establish the ledger on a legacy deck" pass: it judges only slides not
  already trusted (the cold and the `seed`-inherited `assume` ones) and skips the
  already-confirmed (never re-paid — §9.4 cost discipline). It needs an OpenRouter key
  (the only ledger verb that calls a model); `--semantic-model` overrides the cheap
  default (`anthropic/claude-haiku-4-5`). Trust rungs, cheapest → strongest:
  `assume` (`seed`, no check) < `structural` (`bless`/`apply`, structure only) <
  `agent` (`accept --record`, an agent asserted + structural) < `semantic` (`establish`,
  an LLM judged the translation). `confirmed_oracle` records which, so a later run can
  distrust a specific rung without nuking the ledger.
- It is a **trust overlay**, not a new baseline: the classification is unchanged,
  the ledger only removes proposals the recorded trust makes redundant. Opt-in
  (default off = today's behavior exactly); **works over a directory** too — point
  `report`/`apply --ledger` at a whole module and each pair uses its own topic
  ledger (the batch reports the aggregate skipped/recorded). And `git log
  -S<slide_id> -- '**/sync-ledger.json'` answers "when was slide X last synced?"
  exactly — from the record, not inferred.
- **Coverage**: id'd slides (`(slide_id, role)`) *and* id-less localized
  **narratives** — voiceover / notes with no `slide_id` — keyed by
  `(owning_slide_id, role, occ)` (the n-th such narrative under its owning slide),
  so the voiceover-heavy decks the bare-id key used to miss are covered. Id-less
  localized *code* is still governed by the structural pass, not the ledger.
- **Survives an id rename.** When a `slide_id` is rewritten — a `realign` you
  `accept`, a `reconcile`, or a deterministic id-migration during `apply --ledger` —
  the ledger entry **follows the slide to its new id** automatically (#448 P3),
  keyed and provenance preserved, rather than orphaning to the cold path. A pure
  relabel (body unchanged) keeps its trust; a rename that also changed the body
  re-checks (the body hash is id-independent, so the exact-match gate decides). So a
  realign no longer silently demotes a slide you already paid to confirm.

## A clean, committed, id-less deck is *consistent* (not a cold start)

A pair whose halves share **no `slide_id`** (a fully id-less deck, or a half-id'd
one) cannot be paired by id — but if it is committed and **byte-identical to git
`HEAD` on both halves**, it has not drifted since commit, so `report` calls it
**clean** (not `needs_agent`). You only see a cold-start `mint` / `adopt` item
when such a pair is genuinely new (never committed) or has been edited since
`HEAD`. If you *want* a clean committed deck to carry ids, that is an authoring
choice — run `clm slides assign-ids` — not something sync forces every run.
(A committed *structural* problem, e.g. mismatched ids across the halves, is
surfaced by `verify`, not by the drift report.)

## Asymmetric voiceover/notes companions are alerted, not guessed

A voiceover / notes companion may be **id-less on one half** but carry its slide's
`slide_id` on the **other** (the halves disagree on whether the companion is keyed).
Such a pair re-pairs cleanly every sync while *unchanged*, but a **one-sided edit or
removal** of the id'd half cannot be propagated across the id-ness boundary without
risking a duplicate voiceover. The engine **does not guess**: it surfaces an `issue`
(`severity == "error"`, holding the watermark) whose `reason` names the slide and tells
you to run `clm slides assign-ids` on **both** halves so the companion is keyed the same
way. Re-`report` after that and the edit/removal propagates as an ordinary keyed change.

## Non-shell agents — the MCP tool

If you drive CLM over MCP rather than the shell, `slides_sync_report(file)`
returns the **same** tiered `ReconciliationReport` (with excerpts) for a
split-pair half or stem — the structured analogue of step 1. It is read-only (no
writes, no model; reads the watermark only if one already exists, else git
`HEAD`), and needs no key. Use it to get the report, act on the tiers, then apply
via the shell verbs or your normal edit path. (For a *single bilingual file* —
both languages in one file — use `slides_suggest_sync` instead; a different,
advisory tool.)

## Quick reference

| You see… | Do… |
|---|---|
| `report.is_clean == true` | Nothing — the pair is in sync. |
| only `mechanical` items | `clm slides sync apply DECK` (no model). |
| `assisted` items | `task` → run a model (or do it) → `accept`. Or edit + re-`report`. |
| `conflict` / `issue` (ambiguity) | Read `reason`, edit the deck to resolve, re-`report`. |
| `realign` (ambiguity) | `task` → move the `slide_id` onto its true continuation on **both** halves → `accept`. |
| after any hand edit or `accept` | `clm slides sync verify DECK` (0 = sound, 2 = corrupt). |
| committed edits before syncing | `clm slides sync report DECK --baseline HEAD~1`. |
| want a one-shot, no agent | `clm slides sync autopilot DECK` (uses embedded models; needs a key). |

## Revising your repository guidelines (for a course-repo agent)

When updating a course repo (CLAUDE.md / AGENTS.md / skills / scripts) to this
surface, change exactly these:

1. **Any `clm slides sync DECK` that expected a write** → decide intent: a
   CI / drift check is `clm slides sync report DECK --json` (read-only); an
   automated mechanical reconcile is `clm slides sync apply DECK`; a human
   one-shot that may call models is `clm slides sync autopilot DECK`.
2. **`--dry-run` / `--verify` / `--rebaseline` flags** → the verbs `report` /
   `verify` / `baseline bless`.
3. **`clm slides watermark …`** → `clm slides sync baseline …` (the legacy
   `watermark` group still works as an alias for the same store).
4. **Skills / scripts that set `$OPENROUTER_API_KEY` for sync** → only
   `autopilot` needs it now; `report` / `verify` / `apply` / `task` / `accept`
   are key-free. Document the model handoff (the `task` → model → `accept` step)
   where a skill previously relied on the engine translating.
5. **CI** → replace any `clm slides sync` that could call a model with `clm
   slides sync verify DECK` (a deterministic gate) and/or `report --json`
   parsing; **never run `autopilot` in CI**.

## Principles

- **Read by default; every write is an explicit verb.** Bare `clm slides sync
  DECK` returns the report and mutates nothing.
- **The engine emits, it does not invoke.** When work needs a model it frames the
  exact prompt + inputs + the validator that will check the answer — it never
  calls a model. The model run is yours.
- **The report shrinks your attention surface.** Mechanical work is the engine's;
  you spend tokens only on tiers 2–3.
- **The engine never fabricates a tier-3 resolution.** An `ambiguity` item tells
  you *what* is unresolved, not a guessed answer — that is the contract.
- **`verify` is structural, not semantic.** It catches a *corrupt* pair, not a
  *bad translation*. Use a model (or your own judgement) for meaning; use
  `verify` for integrity.
- **Excerpts are report-time-only and fail-closed.** A position the engine cannot
  resolve with certainty yields no excerpt rather than a wrong one — trust an
  absent excerpt over a mislocated one, and read the source you hold.
