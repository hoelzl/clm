# `clm slides sync` as an Agent Toolkit — Redesign Note

**Status**: Design — decisions **(A) breaking verb group, (B) drop embedded
models, (C) demote the watermark** are **settled** by the user (2026-06-22). This
note is the implementation-facing successor to the strategy note
`sync-baseline-storage-and-agent-direction.md` (#366): that note *argued* the
agent-first pivot and recorded the §11 decisions; this note *executes* it —
re-cutting the command surface, removing the embedded models from the engine
path, and demoting the watermark.
**Author**: Claude (Opus 4.8)
**Date**: 2026-06-22
**Issue**: [#366](https://github.com/hoelzl/clm/issues/366) (umbrella). Companion
to the `area:sync` cluster #438 / #435 / #430 / #429 / #365 / #364 / #236, which
this note re-frames (see §8).

> Read `sync-baseline-storage-and-agent-direction.md` first for the *why*
> (the §1 "the watermark exists only because a deterministic differ cannot tell
> 'edited' from 'always looked like this'" framing, and the §8 agent pivot). This
> note does not re-argue that. It takes the pivot as decided and specifies the
> surface, the model removal, and the storage demotion concretely enough to build.

---

## 1. The problem this note fixes: the pivot was articulated, not executed

The §11 decisions settled the agent-first direction and shipped the *reporting*
surface (the tiered `ReconciliationReport`, `--verify`, the `realign` residue item,
`clm info sync-agents`, the MCP `slides_sync_report` tool). But that surface was
**added on top of the old autonomous solver — the solver was never removed.** The
engine still tries to do everything itself:

- **`clm slides sync DECK` writes by default** (`sync.py:264-267`), and when an API
  key is present it invokes **four** embedded model clients on the main path: the
  edit-reconciliation judge (`--provider` / `--llm-model`, `sync.py:306-344`), the
  new-slide translator (`--translation-model` / `--glossary-*`, `sync.py:345-372`),
  the cold-pair correspondence verifier (`--verify-cold-pairs`, which **defaults on**
  when a key is set, `sync.py:393-405`), and the bounded-LLM id recoverer
  (`--llm-recover`, `sync.py:374-385`). The `sync-agents` topic itself tells the
  agent that step 4 "calls a model." So the engine still *reaches out to models* as
  its normal behaviour — the exact thing the agent pivot was supposed to stop.
- **One leaf command, ~20 flags, doing 7 jobs** (analyze / apply / interactive /
  verify / rebaseline / recover / translate). The actual toolkit — the tiered report
  — is buried behind `--dry-run --json`, *beside* a "legacy flat plan block."
- **The active bug cluster is almost entirely watermark bugs** (#435, #430, #364,
  #366, #429): the load-bearing local state the agent path does not need.
- **#438 is the diagnostic tell:** a clean, committed, in-sync deck reports
  `needs_agent=true` purely because an id-less slide can't be cold-paired — the
  *analyzer* is still entangled with the *solver's* pairing assumptions instead of
  answering the only question that matters for a read: "are these two halves
  consistent right now?"

The toolkit the redesign wants is **latent in the code**. Crucially, the recovery
machinery is already factored as **prompt-builder → model adapter → decoder → pure
validator** (`sync_recover.py`: `build_recovery_user_prompt` :368, `encode_mapping`
:156 / `decode_mapping` :165, `validate_alignment` :225; the only embedded shell is
`OpenRouterAlignmentRecoverer` :381). The pivot keeps the pure pieces and lifts the
adapter off the path. The same shape holds for correspondence (`validate_correspondence`
:570, `build_correspondence_user_prompt` :644, `OpenRouterCorrespondenceVerifier` :650).

---

## 2. Settled decisions

| | Decision | Consequence |
|---|---|---|
| **(A)** | **Breaking re-cut into a verb group** (`clm slides sync <verb>`), plus an authoritative agent guide so downstream course-repo agents can revise their own guidelines without guessing. | §4 (surface) + §9 (the guide). Downstream invocations change; §7 is the migration map. |
| **(B)** | **Drop the embedded models from the engine path.** The engine emits *framed tasks* and *verdicts of necessity*; it never invokes a model. The agent (or a model it chooses) does the work; the engine *validates* the result deterministically. | §5. The four OpenRouter clients survive only behind one explicit `autopilot` verb for the agent-less human. Makes "no live LLM in CI" structural. |
| **(C)** | **Demote the watermark.** Git `HEAD` is the default baseline for the read surface (`report` / `verify`); the watermark becomes an opt-in accelerator used only by `apply` / `autopilot`. | §6. Defuses the #435 / #430 / #364 / #366 bug cluster by construction. |

These three are mutually reinforcing. (B) is what lets the engine be a deterministic
core; (C) is what removes the state that the deterministic core kept tripping on;
(A) is the surface that exposes the core as composable verbs an agent drives.

---

## 3. Principles (the contract the surface must honour)

1. **Read by default; every write is an explicit verb.** Bare `clm slides sync DECK`
   *returns the report* — it never mutates files. This single inversion is the pivot
   in one line: the tool's default becomes "tell me what is necessary."
2. **Emit, don't invoke.** When work needs a model, the engine emits the exact prompt
   + context + the validator that will check the answer. It does not call a model.
   "Return the fact that reconciliation is necessary" is the *primary* output, not a
   `--dry-run` afterthought.
3. **Three jobs, three guarantees.**
   - *Classify / report* — pure, deterministic, no model, no writes, no watermark.
   - *Apply* — deterministic tier-1 only, no model; writes the bytes the engine knows
     exactly.
   - *Verify* — pure structural safety (no corruption), explicitly **not** semantic
     correctness (a bad translation passes verify; only a model/human catches that).
4. **The engine never fabricates a tier-3 resolution.** An ambiguity item states
   *what* is unresolved and hands over the cell bytes; it never guesses an answer.
5. **Nothing the agent path depends on is hidden, local, or stateful.** The agent
   reasons from the two files + `git diff`; the watermark is an optimisation it may
   ignore.

---

## 4. The new command surface

`clm slides sync` becomes a Click **group**. Today it is a leaf command
(`slides/__init__.py:44`); it becomes `slides_sync_group` with the verbs below.

```
clm slides sync DECK                      # default → `report` (read-only)
clm slides sync report   DECK [opts]      # the tiered ReconciliationReport
clm slides sync verify   DECK [opts]      # structural integrity check
clm slides sync apply    DECK [opts]      # deterministic tier-1 apply (no model)
clm slides sync task     DECK --item ID   # emit a framed model task (no model)
clm slides sync accept   DECK --item ID --answer -   # validate + write a result
clm slides sync baseline {show,bless,clear,prune}    # watermark state (rename)
clm slides sync autopilot DECK [opts]     # legacy all-in-one WITH embedded models
```

`DECK` is, everywhere, either half (`deck.de.py`), the shared stem, or a directory
(a batch sweep). The verbs:

### `report` — the primary verb (read-only, no model, git baseline)

The promoted, de-legacied `--dry-run --json`. Emits the `ReconciliationReport`
(`sync_report.py`) — the three tiers (`mechanical` / `assisted` / `ambiguity`) with
`is_clean` / `needs_model` / `needs_agent`, and under `--json` the per-item cell-byte
enrichment (`source_excerpt` / `target_excerpt` / lines). Default baseline is git
`HEAD` (§6); `--baseline REF` and `--baseline watermark` opt into other baselines.
Never writes, never calls a model, needs no API key. The **flat `plan` block is
removed** (it was kept only for back-compat; the report is the contract).

- Human form (no `--json`): a compact tier summary.
- `--json`: the `ReconciliationReport` model as the `report` block of the envelope (the
  flat `plan` / `apply` / `walker` keys are kept for back-compat consumers; `report` is
  the blessed contract — the versioned schema).
- Exit code: **`0` clean / `1` work pending (any tier) / `2` classifier error** —
  *settled at SHIPMENT (O1 below): `report` doubles as a drift gate so a CI sweep can
  branch on exit `0` without parsing JSON, and a human running bare `sync DECK` sees `1`
  = "something is pending". `verify` remains the separate **structural** gate (0/2). An
  agent still branches on the JSON booleans, not the exit code.*

### `verify` — the structural gate (read-only, no model, no watermark)

Exactly today's `--verify` (`sync_verify.py`: `verify_pair` :208, `structural_violations`
:97, `dropped_id_violations` :182). Confirms the pair is a structurally valid split:
byte-identical shared cells, header parity, clean alignment (reuses `unify`),
`de_id == en_id` set-symmetry, no duplicate ids; **warns** (does not fail) on an id'd
cell dropped vs git `HEAD`. Exit `0` = sound (warnings allowed), `2` = corrupt. The
no-drop check reads the **git pre-edit version**, so verify needs no watermark.
Answers "did this edit corrupt the pair?" — not "is it in sync?" and not "is the
translation good?".

### `apply` — deterministic apply (writes, no model)

Applies **tier-1 mechanical** only — `move` / `remove` / `retag` and the
language-neutral verbatim propagation (`plan.anchor_direction`). It **never calls a
model.** If tier-2/3 items exist it applies what it can, then reports the residue and
exits non-zero, pointing the agent at `report` / `task`. (Contrast today's default,
which silently calls the judge/translator for tier 2.) Uses the watermark as a
baseline accelerator when present (and advances it on success); falls back to git
`HEAD`. `--no-cache` ignores the watermark entirely.

### `task` — emit a framed model task (read-only, no model) — NEW

For a tier-2 (`assisted`) or tier-3 (`ambiguity`/`realign`) item, emit everything a
model needs to do the job and nothing more: the source/target cell bytes, the
direction, the role, the glossary context (for a translation), and the **expected
answer format** the validator will enforce. This is a thin wrapper over the existing
builders (`build_recovery_user_prompt` :368, `build_correspondence_user_prompt` :644,
and a new translation-prompt builder factored out of the current translator). Output
is JSON (a `SyncTask` model: `item`, `prompt`, `inputs`, `answer_schema`,
`validator`). The agent runs the prompt through *any* model it likes.

`--item ID` selects one item from the `report` (items get stable ids); omitting it
emits tasks for every tier-2/3 item.

### `accept` — validate + write a result (writes, no model) — NEW

Take the agent's answer (from `--answer FILE` or stdin) for a given `--item ID`, run
it through the **pure validator** the task named (`validate_alignment` :225 for a
realign, `validate_correspondence` :570 for a cold pair, a structural cell-shape
check for a translation/edit), and write it back to **both halves** iff it passes —
maintaining `de_id == en_id` and byte-identity for neutral cells. On failure it
rejects with the precise reason and writes nothing. This is the verified write-back
that closes the loop without the engine ever invoking a model.

### `baseline` — watermark state (rename of `watermark`)

Rename `clm slides watermark {list,clear,prune}` → `clm slides sync baseline
{show,bless,clear,prune}`, co-located with the command it serves and renamed to
reflect its demoted role (it is *a* baseline, not *the* state):

- `show` (was `list`) — watermarked pairs, row counts, last sync, on-disk status.
- `bless` — record the current working-tree state as the baseline. Replaces the
  `--rebaseline` flag; gated on git divergence, **not** on a no-op HEAD, so it no
  longer needs a throwaway commit (#430). Diffs against the recorded `synced_commit`
  (#375) when present.
- `clear` (was `clear`) — drop a pair's watermark.
- `prune` (was `prune`) — drop orphan rows whose files no longer exist.

### `autopilot` — the agent-less escape hatch (writes, **embedded models**)

The *only* place the four OpenRouter clients live. Reproduces today's full
`clm slides sync` behaviour (classify → tier-1 apply → judge tier-2 edits → translate
tier-2 adds → cold-pair verify → `--llm-recover` tier-3) for a human running CLM with
no agent. All the model flags (`--provider`, `--llm-model`, `--translation-model`,
`--glossary-*`, `--recovery-model`, `--verify-cold-pairs`, `--ollama-url`,
`--llm-timeout`) move here. Clearly documented as *not* the agent path. CI never runs
it.

> **Why keep `autopilot` at all?** Two reasons: (1) the embedded models are real,
> tested capability we are not deleting (decision B is *relocate*, not *destroy*);
> (2) a human at a terminal without a coding agent still wants a one-shot. Keeping it
> isolated behind one verb is what makes the *agent* path cleanly model-free.

### What is removed / hidden

- The `--interactive` walk: dropped (the agent loop replaces it; a human uses
  `autopilot` or edits + `verify`). *(O2: keep `--interactive` on `autopilot`? Low
  cost; recommendation: keep there only.)*
- `--explain`: folded into `report --explain` (the anchor-diff diagnostic stays a
  read-only superset of `report`).
- `suggest-sync` (the hidden bilingual single-file tool, `suggest_sync.py`): kept
  hidden, unchanged, but documented as the **bilingual** (pre-split) analogue —
  explicitly *not* part of the split-pair toolkit. Its MCP twin `slides_suggest_sync`
  likewise stays for non-split files only.

---

## 5. Dropping the embedded models (decision B)

The engine path stops invoking models. Each of the four clients has a clean fate:

| Embedded client (today) | Used for | Under the redesign |
|---|---|---|
| Edit judge (`SyncJudge` / OpenRouter) | tier-2 `edit` reconciliation on id'd localized cells | `report` frames it (cell bytes + direction); `task` emits the prompt; agent runs it; `accept` validates the cell shape and writes. Judge client → `autopilot` only. |
| Translator (`SlideTranslator`) | tier-2 `add` of a brand-new slide | Same: `task` emits the translation prompt (with glossary context, factored out of today's auto-discovery); `accept` validates structurally. Translator client → `autopilot` only. |
| Cold-pair verifier (`OpenRouterCorrespondenceVerifier` :650) | confirm a never-id'd pair corresponds before minting ids (#216) | `report` surfaces the cold pair as a tier-2 `assisted` item; `task` emits `build_correspondence_user_prompt`; agent judges; `accept` runs `validate_correspondence` then mints. Client → `autopilot` only. |
| Id recoverer (`OpenRouterAlignmentRecoverer` :381) | tier-3 drifted-id `realign` (#190 §10) | Already emitted as the `realign` ambiguity item; `task` emits `build_recovery_user_prompt`; agent re-identifies; `accept` runs `validate_alignment`. Client → `autopilot` only (`--llm-recover`). |

**Preserved verbatim (the valuable, model-free code):**

- The deterministic classifier and tier-1 apply (`sync_plan.py`, `sync_apply.py`),
  including the #190 content anchors — the fast path that nails ~90 % for free.
- Every **prompt builder** (`build_recovery_user_prompt`, `build_correspondence_user_prompt`,
  plus a new `build_translation_user_prompt` lifted from the translator) — re-surfaced
  as `task` output.
- Every **validator** (`validate_alignment`, `validate_correspondence`, the `unify`
  structural checks, `sync_verify`) — re-surfaced as `accept` / `verify` gates.
- The **wire codecs** (`encode_mapping` / `decode_mapping`, `encode_verdicts` /
  `decode_verdicts`) — the `task` answer format.
- The **static** stand-ins (`StaticAlignmentRecoverer` :301, `StaticCorrespondenceVerifier`
  :587) — they become the *default* for deterministic tests, with no OpenRouter import
  reachable from the agent path.

**Relocated (not deleted):** the four `OpenRouter*` adapters and all model flags →
`autopilot`. **Net:** the agent path (`report` / `verify` / `apply` / `task` /
`accept`) has no reachable `openai` / OpenRouter import, so "no live LLM in CI" stops
being a discipline and becomes a structural property.

---

## 6. Demoting the watermark (decision C)

The agent path asks "are these two halves consistent **now**, and which side did git
show was edited?" — answerable from the two files + `git diff`, with no stored past.
So:

- **`report` and `verify` default to git `HEAD`** as the baseline. The watermark is
  consulted **only** when `--baseline watermark` is passed, or implicitly by `apply`
  / `autopilot` (which want the extra signal to choose a direction for uncommitted
  both-sided edits).
- The watermark becomes a **rebuildable accelerator**, never the authority. When it
  is stale, missing, or under a worktree path it cannot be found, the read path simply
  uses git — no error, no cold-start surprise.
- `baseline bless` (was `--rebaseline`) records from the working tree, gated on git
  divergence rather than a no-op HEAD (#430), so it never needs a throwaway commit.

This is decision-note §9 made concrete: *"the watermark stops being load-bearing for
correctness; it survives only as a cache that keeps the deterministic fast-path
cheap."* The storage A-vs-B-vs-C debate stays parked (§11 Q2/Q4 of the prior note);
this is the demotion, not a new store.

> **The worktree key bug (#435) under demotion.** The read path no longer reads the
> watermark, so #435 cannot silently mislead an agent working in a worktree (the
> normal case). The key-canonicalization fix is still wanted for `apply`/`bless`
> correctness, but it is no longer a silent-correctness landmine on the path agents
> use 99 % of the time. Demotion converts #435 from "silently wrong" to "an
> optimisation occasionally misses, harmlessly."

---

## 7. Migration map (old → new)

The breaking surface. Downstream course-repo guidelines, skills, and scripts update
per this table (and the §9 guide spells out the new workflow).

| Today | Redesign |
|---|---|
| `clm slides sync DECK` (writes, may call models) | `clm slides sync apply DECK` (deterministic) **or** `clm slides sync autopilot DECK` (with models). Bare `sync DECK` now = `report`. |
| `clm slides sync DECK --dry-run --json` | `clm slides sync report DECK --json` |
| `clm slides sync DECK --dry-run` | `clm slides sync report DECK` |
| `clm slides sync DECK --explain` | `clm slides sync report DECK --explain` |
| `clm slides sync DECK --verify [--json]` | `clm slides sync verify DECK [--json]` |
| `clm slides sync DECK --rebaseline` | `clm slides sync baseline bless DECK` |
| `clm slides sync DECK --baseline REF` | `clm slides sync report DECK --baseline REF` (or on `apply`) |
| `clm slides sync DECK --no-cache` | default for `report`/`verify` (git baseline); `apply --no-cache` to force-ignore the watermark |
| `clm slides sync DECK --interactive` | `clm slides sync autopilot DECK --interactive` (human only) |
| `clm slides sync DECK --llm-recover` | tier-3 `realign` item → `task`/`accept`; flag survives on `autopilot` |
| `--provider/--llm-model/--translation-model/--glossary-*/--recovery-model/--verify-cold-pairs/--ollama-url/--llm-timeout` | all move to `autopilot` |
| `clm slides watermark {list,clear,prune}` | `clm slides sync baseline {show,clear,prune}` |
| MCP `slides_sync_report(file)` | unchanged (already the structured `report`) |
| MCP `slides_suggest_sync(file)` | unchanged (bilingual single-file only) |

---

## 8. How the active issues collapse under this frame

| Issue | Today | Under the redesign |
|---|---|---|
| **#438** false `needs_agent` on a clean deck | cold-pair refusal fires every run for a byte-stable id-less slide | `report` is a git-HEAD consistency check: an id-less slide equal on both halves vs HEAD is *consistent now* → no tier-3. Refusal only for a genuinely new/changed id-less slide. **Largely dissolves.** |
| **#435** worktree watermark key miss | read path silently cold-starts off HEAD from a worktree | read path *intends* git HEAD; the watermark miss is a harmless missed optimisation, not silent wrong drift. **Defused** (key fix still wanted for `apply`). |
| **#430** rebaseline needs a throwaway commit | `--rebaseline` gates on no-op HEAD | `baseline bless` gates on git divergence vs `synced_commit`. **Fixed.** |
| **#364 / #366** stale watermark / commit coupling | stale watermark → hard error; nothing keeps it in lockstep | read path is git-baselined + stateless → no stale-error; coupling removed by demotion. **Mostly dissolves.** |
| **#429** reflow read as an edit | content hash is `strip()` only | still worth doing (reflow-insensitive hash), but it stops being the difference between "works" and "errors" once the agent judges tier-2 edits. **De-risked.** |
| **#365** id-less localized both-sided drift | hard error; proposal was "assign ids to all localized cells" | becomes a tier-3 `ambiguity` item carrying both cells' bytes → `task`/`accept`. Matches the note's preference for "positional conflict + agent judges" over universal ids. **Re-homed.** |
| **#236** assisted interleave for diverged DE/EN | refuses to auto-reorder, leaves the author stuck | the `task`/`accept` pattern: emit the pairing worklist as a task, accept the agent's interleaving, `verify`. The non-interactive first cut the issue itself asks for. **Subsumed.** |

§2 of the refiling (issue work) records these mappings on each issue and in an
umbrella epic.

---

## 9. Agent Usage Guide — driving the toolkit (authoritative)

> This section is written to be **lifted into `src/clm/cli/info_topics/sync-agents.md`
> when the verbs ship** (per the Info Topics Maintenance Rule). Until they ship, the
> live info topic still describes the *current* `--dry-run`/`--verify` surface — do not
> overwrite it with this and mislead downstream agents about commands that do not exist
> yet. This is the draft of the post-ship contract, and the reference a *course-repo*
> agent uses to **revise its own repository guidelines** (CLAUDE.md / AGENTS.md /
> skills / scripts) to the new surface without guessing.

### 9.1 The mental model

A bilingual topic is split into two language halves (`deck.de.py` + `deck.en.py`).
They must stay aligned: same slides, same order, language-neutral code byte-identical,
each `slide_id` carried by both halves (`de_id == en_id`). Editing one half drifts the
other. The toolkit lets **you** do the judgement and the **engine** do the mechanical
work and the verification — you spend attention only on the residue. **The engine
never calls a model. You do (or you decide not to).**

### 9.2 The canonical loop

```
1. clm slides sync report DECK --json     # what is necessary? (read-only, no key)
2. branch on report.is_clean / needs_model / needs_agent
3. clm slides sync apply DECK             # do the mechanical tier-1 (no model)
4. for each assisted/ambiguity item:
     clm slides sync task DECK --item ID  # get the framed prompt + inputs
     <run it through whatever model you choose, or do it yourself>
     clm slides sync accept DECK --item ID --answer -   # validate + write back
5. clm slides sync verify DECK            # structural gate (no model), exit 0 = sound
```

Steps 1, 2, 4-emit, and 5 are **read-only and need no API key**. Steps 3 and
4-accept write, deterministically, still with **no model**. The only place a model
runs is *your* call between `task` and `accept` — and you pick it.

### 9.3 Reading `report`

`clm slides sync report DECK --json` emits a `ReconciliationReport`:

- `is_clean` — no work in any tier; the pair is in sync. **Stop.**
- `needs_model` — a tier-2 (`assisted`) or tier-3 (`ambiguity`) item exists.
- `needs_agent` — a tier-3 item needs *your* judgement.
- `baseline_source` — `git-head` (default) / `git:<ref>` / `watermark` / `none`.

Each item carries a stable `item` id, plus `kind` / `role` / `direction` /
`slide_id` / `reason`, 0-based `source_position` / `target_position`, and (under
`--json`) the cell bytes: `source_excerpt` / `source_line` (the side to reconcile
*from*) and `target_*` (the existing counterpart). Excerpts are
report-time-only and **fail-closed**: an absent excerpt means "I could not locate
this with certainty" — trust that over a guess, and read the source you hold.

**Tier 1 — `mechanical`** (`move` / `remove` / `retag` / `neutral-propagate`):
trust and ignore; `apply` does them for free, no model.

**Tier 2 — `assisted`** (`add` / `edit` / `rename` / `mint` / `adopt` /
`reconcile`): a *framed* task — the engine knows which cell, which direction, what is
needed. You: run `task`, do it (any model, or by hand), `accept`.

**Tier 3 — `ambiguity`** (`conflict` / `issue` / `realign`): the engine refuses to
guess. It states *what* is ambiguous and hands you the bytes. You decide, then `accept`
(or edit the deck and re-`report`).

### 9.4 `task` and `accept` (the model handoff you own)

`clm slides sync task DECK --item ID` returns a `SyncTask`: `prompt` (ready to send
to a model), `inputs` (the cell bytes / glossary / direction), `answer_schema` (the
exact shape `accept` will enforce), and `validator` (which deterministic check runs).
Run the prompt through any model; capture the answer in the `answer_schema` shape;
pipe it to `clm slides sync accept DECK --item ID --answer -`. `accept` validates and
writes **both halves**, or rejects with the reason and writes nothing. A rejected
answer is a signal to retry with a better model/prompt — the deck is never corrupted.

For a **`realign`** item (a `slide_id` drifted off its construct while a cell split):
`task` gives you the region and the candidate continuation; you move the `slide_id`
onto its true continuation on **both** halves (neutral code is byte-identical), then
`accept` runs `validate_alignment`. This is the agent-first replacement for the old
`--llm-recover`.

### 9.5 `verify` (the structural gate)

`clm slides sync verify DECK` confirms an edit did not *corrupt* the pair —
byte-identical shared cells, header parity, clean alignment, `de_id == en_id`
symmetry, no duplicate ids — and **warns** (never fails) if an id'd cell was dropped
vs git `HEAD`. **No model, no watermark, writes nothing.** Exit `0` = sound (warnings
allowed), `2` = corrupt. It answers "did this edit break the pair?" — **not** "is it
in sync?" (`report`) and **not** "is the translation good?" (only a model/human knows).
Run it after every hand edit and after every `accept`. It is CI-safe.

### 9.6 Baselines (you rarely touch these)

`report`/`verify` default to git `HEAD`; you do not need the watermark for the normal
loop. If you committed single-language edits *before* syncing, point the report at the
pre-edit commit: `clm slides sync report DECK --baseline HEAD~1`. To bless the current
consistent state as the baseline (e.g. after reconciling): `clm slides sync baseline
bless DECK` — no throwaway commit needed. Inspect with `baseline show`; drop orphans
with `baseline prune`.

### 9.7 Quick reference

| You see… | Do… |
|---|---|
| `report.is_clean == true` | Nothing — the pair is in sync. |
| only `mechanical` items | `clm slides sync apply DECK` (no model). |
| `assisted` items | `task` → run a model (or do it) → `accept`. Or edit + re-`report`. |
| `conflict` / `issue` (ambiguity) | Read `reason`, edit the deck to resolve, re-`report`. |
| `realign` (ambiguity) | `task` → move the `slide_id` onto its true continuation on **both** halves → `accept`. |
| after any hand edit or `accept` | `clm slides sync verify DECK` (0 = sound, 2 = corrupt). |
| committed edits before syncing | `clm slides sync report DECK --baseline HEAD~1`. |
| want a one-shot, no agent | `clm slides sync autopilot DECK` (uses models; needs a key). |

### 9.8 Revising your repository guidelines (for a course-repo agent)

When updating a course repo to this surface, change exactly these:

1. **Any `clm slides sync DECK` that expected a write** → decide intent: a CI/drift
   check is `clm slides sync report DECK --json` (read); an automated mechanical
   reconcile is `clm slides sync apply DECK`; a human one-shot is `autopilot`.
2. **`--dry-run` / `--verify` / `--rebaseline` flags** → the verbs `report` / `verify`
   / `baseline bless` (see §7).
3. **`clm slides watermark …`** → `clm slides sync baseline …`.
4. **Skills / scripts that set `$OPENROUTER_API_KEY` for sync** → only `autopilot`
   needs it now; `report`/`verify`/`apply`/`task`/`accept` are key-free. Document the
   model handoff (§9.4) where a skill previously relied on the engine translating.
5. **CI** → replace any `clm slides sync` (which could call a model) with
   `clm slides sync verify DECK` (a deterministic gate) and/or `report --json`
   parsing; never run `autopilot` in CI.

---

## 10. Sequencing

Low-regret order (mirrors the prior note's §10 4(i)-(iv), now concrete):

1. **Surface cut + default flip** *(the prototype — issue work item 3)*. Make `sync`
   a group; add `report` (= old `--dry-run --json`, git-baselined, flat-plan removed),
   `verify` (= old `--verify`), `apply` (deterministic tier-1, no model); bare `sync`
   → `report`. Route to existing internals; old embedded-model behaviour temporarily
   lives under `autopilot` (initially just the old command body). Ship behind the
   existing tests adapted to the verbs.
2. **Watermark demotion**. Flip `report`/`verify` to git-HEAD default; `baseline
   bless` replaces `--rebaseline` (gate on `synced_commit`). Closes #430; defuses
   #435/#364/#366 on the read path.
3. **`task` + `accept`; lift models off the engine**. Factor the translation prompt
   out of the translator; wire `task`/`accept` over the existing builders + validators;
   move the four OpenRouter clients behind `autopilot`. The agent path becomes
   import-clean of OpenRouter. Re-home #365 and #236 as `task`/`accept` flows.
4. **Consolidate + document**. Promote §9 into `info_topics/sync-agents.md`; update
   `clm info commands`; add a `migration.md` entry; update the `deck-sync` skill and
   course-release skill; changelog fragment.

Each step is shippable on its own; 1 is the prototype the user asked to feel.

---

## 11. Open questions / risks

- **O1 — `report` exit code.** *SETTLED at shipment: `report` exits `0` clean / `1` work
  pending / `2` classifier error* (not the originally-recommended `0`-always + opt-in
  `--exit-code`). It doubles as a drift gate (gate CI on exit `0`), while `verify` is the
  structural gate (`0`/`2`); an agent branches on the JSON booleans regardless. No
  `--exit-code` flag was added — the default IS the gate.
- **O2 — `--interactive`.** Keep only on `autopilot` (human path). *Recommendation:
  yes.*
- **O3 — batch (`DIR`) semantics across verbs.** `report`/`verify` over a directory
  are read-only sweeps (cheap, no gate). `apply` over a directory keeps the `--yes`
  confirm gate. `task`/`accept` are single-item, so single-pair only.
- **O4 — `accept` answer transport.** Stdin/file with the `answer_schema` shape; the
  validator is named in the `SyncTask` so `accept` cannot run the wrong check.
- **R1 — test churn.** Breaking the surface invalidates a large sync test suite. The
  prototype adapts the *surface* tests; the engine tests (sync_plan/sync_apply) are
  untouched. Budget for it; do not let it block the cut.
- **R2 — downstream breakage.** PythonCourses and the personal `deck-sync` /
  `course-release` skills call the old surface. §9.8 is the migration script; update
  them in lockstep with the ship (step 4), not before.
- **R3 — `autopilot` rot.** Isolating the models behind one verb risks it bit-rotting
  (no CI model runs). Mitigate: keep the static recoverer/verifier as the test default
  so `autopilot`'s *plumbing* is covered with replayed fixtures, exactly as today.

---

## 12. Appendix — code keep / relocate / delete inventory

| Module | Keep (agent path) | Relocate (`autopilot`) | Notes |
|---|---|---|---|
| `slides/sync_plan.py`, `sync_plan_walker.py` | ✅ classifier, proposals, issues | — | the deterministic spine |
| `slides/sync_apply.py` | ✅ tier-1 apply, `detect_idmigration_residue` | judge/translator call sites | split apply (deterministic) from the model-driven tiers |
| `slides/sync_report.py` | ✅ entire report + enrichment | — | the contract; `task`/`accept` schemas join it |
| `slides/sync_verify.py` | ✅ `verify_pair`, `structural_violations`, `dropped_id_violations` | — | `verify` verb |
| `slides/sync_recover.py` | ✅ `validate_alignment`, `build_recovery_user_prompt`, `encode/decode_mapping`, `StaticAlignmentRecoverer` | `OpenRouterAlignmentRecoverer` | textbook prompt/validate split |
| `slides/sync_recover.py` (corr.) | ✅ `validate_correspondence`, `build_correspondence_user_prompt`, `StaticCorrespondenceVerifier` | `OpenRouterCorrespondenceVerifier` | cold-pair via `task`/`accept` |
| `slides/sync_translate.py` | ✅ a new `build_translation_user_prompt` + structural result check | the OpenRouter translator client | factor the prompt out of the client |
| `slides/sync_writeback.py`, `sync_code.py` | ✅ | — | byte-level write/compare; #429 lands here |
| `cli/commands/slides/sync.py` | rewritten as the verb group | the old command body → `autopilot` | the surface cut |
| `cli/commands/slides/watermark.py` | renamed → `baseline` verbs | — | + `bless` (was `--rebaseline`) |
| `cli/commands/slides/suggest_sync.py` | ✅ unchanged, hidden | — | bilingual single-file only |
| `mcp` `slides_sync_report` / `slides_suggest_sync` | ✅ unchanged | — | already the structured `report` / bilingual twin |
