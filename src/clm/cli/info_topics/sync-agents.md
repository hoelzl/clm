# Driving `clm slides sync` as an Agent (CLM {version})

This is the workflow guide for a **coding agent** that keeps a split bilingual
deck pair (`deck.de.py` + `deck.en.py`) in sync. `clm slides sync` is built so
the agent does the *judgement* and the engine does the *mechanical* work — you
spend attention only on the residue, never on re-deriving the whole deck.

For the exhaustive field-by-field reference (every flag, every JSON key) see
`clm info commands` (the `clm slides sync` section). This topic is the *how*, not
the *what*: the loop you run, and what to do with each thing the engine hands you.

## The mental model

A topic authored bilingually is split into two language halves. The two halves
must stay aligned — same slides, same order, language-neutral code byte-identical,
each `slide_id` carried by both halves (`de_id == en_id`). When you edit **one**
half, the other drifts. `clm slides sync` reconciles them against a baseline (a
structural watermark, else git `HEAD`).

The engine resolves everything it can **deterministically, with no model**. What
it cannot resolve, it does not guess — it hands you a *characterised* description
of the residue. That hand-off is the **tiered report**. Your job is to act on the
two tiers the engine deliberately leaves for a model / for you, then let the
engine verify the result.

## The core loop

```
1. clm slides sync DECK --dry-run --json     # read the report; write nothing
2. inspect report.is_clean / needs_model / needs_agent
3. act on each tier (see below)
4. clm slides sync DECK                       # apply (or re-run after you edit)
5. clm slides sync DECK --verify              # confirm structural integrity (no model)
```

`DECK` may be either half (`deck.de.py`), the shared stem, or a directory (a
batch sweep over every pair under it). Steps 1 and 5 are **read-only** and need
no API key; only step 4's `assisted` tier (translation / edit reconciliation)
calls a model.

## Reading the report

`clm slides sync DECK --dry-run --json` emits a `report` block (alongside the
legacy flat `plan` block, kept for back-compat). The report partitions the
engine's work into **three tiers** and exposes three booleans so you can branch
without scanning the lists:

- `is_clean` — no work in any tier; the pair is already in sync. **Stop.**
- `needs_model` — a tier-2 (`assisted`) or tier-3 (`ambiguity`) item exists.
- `needs_agent` — a tier-3 (`ambiguity`) item needs *your* judgement.

Each item carries `kind`, `role`, `direction`, `slide_id`, `reason`, and 0-based
`source_position` / `target_position`. Under `--dry-run` an `assisted` or
`ambiguity` item is additionally enriched with the **cell bytes** the work
concerns — `source_excerpt` / `source_line` (the side to reconcile *from*) and
the matching `target_*` (the existing counterpart) — so you can act without a
separate read. (Excerpts are dry-run-only: after an apply the file positions no
longer match.)

### Tier 1 — `mechanical` (trust and ignore)

`move` / `remove` / `retag` / `neutral-propagate`. The engine applies these
deterministically with **no model** on the writing run. You do **not** need to
read or touch them — a plain `clm slides sync DECK` applies them for free. They
are in the report only so it names *every* change the engine would make.

### Tier 2 — `assisted` (a scoped model task)

`add` / `edit` / `rename` / `mint` / `adopt` / `reconcile` — a **framed** task:
the engine already knows *which* cell, *which* direction, and *what* is needed
(translate a new slide, reconcile an edit on an id'd localized cell, confirm a
cold-pair correspondence). You have three choices:

1. **Let the engine do it** — run `clm slides sync DECK` with an API key set; its
   own model (Sonnet by default) performs the task.
2. **Delegate to a cheap model yourself** — use `source_excerpt` as the input,
   write the result back into the cell, then re-run sync.
3. **Do it yourself** — for a small edit you can just make it.

The point of the tier is that this is *bounded* work on a *named* cell, not a
free-form "translate the deck".

### Tier 3 — `ambiguity` (your judgement)

The engine **refuses to guess**. The item states *what* is ambiguous, never a
fabricated fix. Three shapes:

- A **`conflict`** — the same cell drifted on both halves in opposite directions.
  Decide the winner (edit the deck so both halves agree), then re-sync.
- An **`issue`** (carries `severity`) — a structural situation the classifier
  will not turn into a proposal (a one-sided header edit, a both-sided incompatible
  shared-cell change). Read `reason`, fix the deck, re-sync.
- A **`realign`** — see the next section; this is the one the agent-first design
  added specifically for you.

## The `realign` item (drifted-id recovery)

When a `slide_id` drifts off its construct **while** a cell splits — e.g. a
function is renamed in the same edit that pulled an `import` into its own cell, or
two cells now share a construct so the move is ambiguous — the deterministic
id-migration cannot safely decide which cell should carry the id. Rather than
guess (a wrong id silently breaks cross-references and voiceover links) or spend
the engine's embedded recovery model, the report surfaces a tier-3 item:

```json
{
  "tier": "ambiguity",
  "kind": "realign",
  "role": "neutral-code",
  "slide_id": "def-my-fun",
  "reason": "slide_id 'def-my-fun' drifted off its baseline construct 'function-my-fun' — the cell now wearing it is 'import-time'. ... Re-identify the cells, move the slide_id onto its true continuation, then run `clm slides sync --verify` to confirm.",
  "source_excerpt": "import time",
  "source_line": 12,
  "target_excerpt": null
}
```

`source_excerpt` is the cell **currently wearing** the id (the one that drifted);
`target_excerpt` is the **likely continuation** when there is an unambiguous one
(it is `null` when a rename broke the construct link — then you decide). To fix it:

1. Read the region (you hold the deck source; the excerpt + `slide_id` locate it).
2. Move the `slide_id` header attribute onto the cell that is the *true*
   continuation of that id (usually the renamed/split-out construct), and let the
   orphaned cell take a fresh id or none.
3. Apply the **same** change to **both** halves (neutral code is byte-identical
   across halves — `de_id == en_id` must hold).
4. `clm slides sync DECK --verify` to confirm the pair is structurally sound.

This is the agent-first replacement for `--llm-recover`: you re-identify the
cells (you have the full context), and the deterministic verifier guards the
result. `--llm-recover` still exists for a **standalone, agent-less** run — it
asks Opus for a validated body-free alignment — but you do not need it.

## Verifying — `clm slides sync DECK --verify`

A **structural** safety check: it confirms an edit did not *corrupt* the pair —
byte-identical shared cells, header parity, clean alignment, `de_id == en_id`
set-symmetry, no duplicate ids — and **warns** (never fails) if an id'd cell was
dropped vs git `HEAD`. **No model, no watermark, writes nothing.**

It answers *"did this edit break the pair?"* — **not** *"is it in sync?"*
(`--dry-run`) and **not** *"is the translation good?"* (a semantic call it never
makes). Exit `0` = structurally valid (warnings allowed), `2` = corruption. Run
it as the deterministic gate after you reconcile a deck — it is CI-safe because it
needs no model. It pairs with `--json` and works on a single pair or a directory.

## Non-shell agents — the MCP tool

If you drive CLM over MCP rather than the shell, `slides_sync_report(file)`
returns the **same** tiered `ReconciliationReport` (with dry-run excerpts) for a
split-pair half or stem — the structured analogue of step 1. It is read-only (no
writes, no model; reads the watermark only if one already exists, else falls back
to git `HEAD`). Use it to get the report, act on the tiers, then apply via the
shell or your normal edit path. (For a *single bilingual file* — both languages in
one file — use `slides_suggest_sync` instead; that is a different, advisory tool.)

## Quick reference

| You see… | Do… |
|---|---|
| `report.is_clean == true` | Nothing — the pair is in sync. |
| only `mechanical` items | `clm slides sync DECK` (applies them, no model). |
| `assisted` items | Apply with a key (engine translates), or use `source_excerpt` to do/delegate it, then re-sync. |
| `conflict` / `issue` (ambiguity) | Read `reason`, edit the deck to resolve, re-sync. |
| `realign` (ambiguity) | Move the `slide_id` onto its true continuation on **both** halves, then `--verify`. |
| after any hand edit | `clm slides sync DECK --verify` (exit 0 = sound, 2 = corrupt). |
| stale-watermark error | `clm slides sync DECK --rebaseline` (only when halves agree vs git `HEAD`). |

## Principles

- **The report shrinks your attention surface.** Mechanical work is the engine's;
  you spend tokens only on tiers 2–3.
- **The engine never fabricates a tier-3 resolution.** An `ambiguity` item tells
  you *what* is unresolved, not a guessed answer — that is the contract.
- **`--verify` is structural, not semantic.** It catches a *corrupt* pair, not a
  *bad translation*. Use a model (or your own judgement) for meaning; use
  `--verify` for integrity.
- **Excerpts are dry-run-only and fail-closed.** A position the engine cannot
  resolve with certainty yields no excerpt rather than a wrong one — trust an
  absent excerpt over a mislocated one, and read the source you hold.
