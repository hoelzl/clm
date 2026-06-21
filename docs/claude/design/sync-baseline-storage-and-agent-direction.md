# Sync Baseline Storage & the Agent-First Question — Design Note

**Status**: Design exploration / strategy (no code change)
**Author**: Claude (Opus 4.8)
**Date**: 2026-06-21
**Issue**: [#366](https://github.com/hoelzl/clm/issues/366). Companion to
[#419](https://github.com/hoelzl/clm/pull/419) (the abstract git-as-baseline
note), and to the #363–#365 batch (watermark CLI / stale-watermark auto-heal /
id-less-localized positional conflicts).

> This note does three things #419 did not:
> 1. Makes the **per-pair sync-state sidecar** concrete (the realization of
>    #419's mechanism (c) at per-pair granularity).
> 2. Honestly compares it against **leaving the sqlite DB as-is** and against the
>    **id-based-correspondence** direction (#366 option C / #190's road not taken)
>    — day-to-day workflow, good points, pain points, and *which one repeatedly
>    drags us into trouble that is hard to get out of*.
> 3. Confronts the strategic question the user actually raised: should
>    `clm slides sync` be repositioned **from a stand-alone solver to an
>    agent-assist tool**, and is that the more honest path forward given how much
>    we have already spent fighting the same class of bug?
>
> It is opinionated on purpose. Where it disagrees with the per-pair-sidecar idea
> that prompted it, it says so.

---

## 1. The one sentence everything else hangs on

**The watermark exists only because a *deterministic* differ cannot tell
"edited" from "always looked like this" without a stored memory of the past.**

That is the whole job of the baseline. Everything painful about sync — the stale
watermark (#363–#366), the id-less localized drift (#364/#365), the
occurrence-anchor instability (#403 Phase B), the five separate times an
anchor-uniqueness bug bit during #190 — is downstream of one demand: *reconstruct
"what changed, and in which direction" deterministically, from content that does
not carry stable identity.* For ~90 % of cells (language-neutral, structurally
stable) this is cheap and the engine nails it. For the id-less **localized**
residue it is genuinely ambiguous, and we have been buying each additional
percent of determinism with an additional fragile rule.

Keep that sentence in mind. It is the lens that makes the three storage options —
and the agent pivot — fall into place. Two of the options move *where the memory
lives*; the third tries to *put identity in the file so no memory is needed*; the
agent pivot asks *whether we need the deterministic reconstruction at all.*

---

## 2. The three storage options, precisely

All three answer the same narrow question: **where do we keep the last-reconciled
state so the next sync can tell what drifted?**

- **(B) Status quo — shared sqlite watermark.** `SyncWatermarkCache` in
  `clm-llm.sqlite` stores, per `(de_path, en_path, lang)`, an ordered list of
  `(position, slide_id, role, content_hash, construct, tags, anchor)` rows, plus a
  `sync_watermark_meta` row per pair (`synced_commit`, `synced_at`). Local to the
  machine, never pushed, advances only on a successful apply.
- **(A) Per-pair in-repo sidecar.** The same per-pair `BaselineBundle`, serialized
  to a tracked file next to the deck (`slides_foo.sync.json`, or a `sync/`
  companion under the existing sidecar-layout machinery). The user's proposal, and
  the concrete form of #419's mechanism (c). Travels with the repo.
- **(C) Id-based correspondence in the file.** Put a **stable id on every cell that
  matters**, so the de↔en correspondence is explicit *in the source* and no stored
  baseline is needed at all: the file *is* its own identity record, and git diff is
  the change signal. This is #366's option (C) and the "assign an id to everything"
  road #190 deliberately did **not** take (it measured ~13 149 new ids = a one-line
  diff on two-thirds of every deck, and chose DB-side content anchors instead).

A & B are the *same data in a different drawer*. C is a *different data model*.
This asymmetry is the heart of the comparison: **A vs B is a logistics question;
C vs (A/B) is an architecture question.**

---

## 3. Option B — leave the DB as it is

### Day-to-day
You edit one half, run `clm slides sync`, it reads the local watermark, classifies
drift, applies, advances the watermark. Invisible and fast. Nothing in the repo
changes except the source files.

### Good points
- **Zero ceremony, zero churn.** No tracked file, no merge conflicts, no PR noise.
- **Cheap and correct for the common case.** The #190 content-anchor work makes the
  neutral 90 % reuse verbatim with no LLM and no direction guess.
- **Already shipped, already hardened.** Five rounds of adversarial review live
  behind it.

### Pain points
- **It is invisible out-of-band state, and it rots silently.** Edit + commit both
  halves *without* syncing → the watermark falls behind HEAD and a later run errors
  or conflicts against a stale baseline even though the halves are fine. This single
  failure spawned the entire #363–#366 batch.
- **Orphan rows.** A topic renumber leaves a `de_path` that no longer exists; the
  store accumulates dead rows with no in-repo signal.
- **Not shareable.** Two clones / two authors / CI each have a *different* baseline,
  or none. The watermark is a property of *one machine's history*, not of the work.

### The shape of its trouble
**Frequent, hidden, but recoverable.** The stale-watermark desync recurs (it is
structural — nothing forces sync-and-commit to stay in lockstep), it is hard to
*diagnose* because the state is hidden, but it never corrupts the source: worst
case is a loud error and `--rebaseline`. We have already paid down the *diagnosis*
cost (#364's localized error + stale hint). The residual cost is that it keeps
happening and an agent or newcomer cannot see *why*.

---

## 4. Option A — the per-pair sidecar

### Day-to-day
`clm slides sync` reads `slides_foo.sync.json` as the baseline, classifies, applies,
**rewrites the sidecar**, and you commit it alongside the source edit. On another
clone, `git pull` brings the new baseline with it. A merge that touched the same
deck on both branches produces a *sidecar conflict* — which is a true-positive "both
sides re-synced" signal, not noise.

### Good points (real, and better than #419's monolithic-file sketch)
- **Travels, is reviewable, pushed by default.** The baseline becomes a repo fact,
  not a machine fact. CI, every clone, and every agent see the same thing.
- **Self-pruning.** Renumber/move/delete a topic and `git mv` (via `clm slides
  tidy`) carries or removes its sidecar. The orphan-row class disappears by
  construction — the one unambiguous win over B.
- **Merge-local.** Per-pair (vs #419's one course-wide file) means a conflict is
  about *that one deck*, touches only people who edited it, and is the exact
  divergence signal #419 §6-Q5 wanted. A monolithic file would serialize every
  concurrent sync into one blob and conflict constantly — the same lesson as this
  repo's `changelog.d/` fragments.
- **Reuses mature infra.** Sidecar dual-probe discovery, `subdir`/`sibling` layout,
  `<sidecar-layout>` + `CLM_SIDECAR_LAYOUT`, `clm slides tidy`, and the `.clm-*`
  release-skip convention (students never receive it) all already exist. This is a
  new sidecar *type*, not new machinery.
- **Answers #419's open Q1 correctly.** Storing the **full serialized bundle**
  (whole-doc hashes + per-cell rows + `synced_at`) is self-contained: it preserves
  *both* properties a bare commit-ref marker loses — the baseline may legitimately
  **lag** HEAD (the original bug) and **lead** it (sync-without-commit).

### Pain points (be honest)
- **It relocates state; it does not eliminate it.** This is the central
  over-sell risk. You still have a baseline store; it is now in two places during
  migration (the sidecar *and* the sqlite tables sync still uses for snapshots /
  alignments / correspondences). The clean end-state (sqlite demoted to a
  rebuildable cache) is real work, not a side effect.
- **Churn, and it is opaque churn.** Every sync rewrites a file full of content
  hashes. Per-pair keeps each diff small, but they are frequent and unreviewable by
  eye (a reviewer cannot tell a correct hash from a wrong one). The "reviewable
  in PRs" benefit is partly notional: you can see *that* a deck re-synced, rarely
  *whether it did so correctly*.
- **"Cell matches" buy less than they look like.** The de↔en correspondence the user
  wants to persist is *already* what the watermark rows encode (slide_id / construct
  / anchor / content_hash). Persisting it adds value only if keyed the way the
  watermark already keys it. If it is persisted as **positions**, it re-introduces
  exactly the instability #403 Phase B fought ("identity = occurrence-under-slide,
  NOT predecessor anchor — token unstable under sibling insert"). Taken to its useful
  conclusion, "store the cell matches" converges back onto "store the watermark rows,
  in a file." The sidecar buys *location*, not *matching power*.
- **A pushed baseline changes semantics.** Today my watermark is *my* last sync. A
  pushed sidecar means a sync someone else did and committed becomes *my* baseline on
  pull — desirable (shared reconciled state) but a behavior change worth stating, not
  a free lunch.

### The shape of its trouble
**Visible, conflict-shaped, recoverable.** It moves the failure out of a hidden
store and into the merge/PR workflow, where it is at least legible. It kills the
orphan-rot recurrence. Its new hard case — a sidecar merge conflict where both
branches genuinely re-synced the same deck — requires reasoning about two baselines,
but it is *surfaced* rather than silent. Net: trades B's invisible recurring rot for
visible occasional merge friction. That is a good trade *if* an in-repo, shared,
reviewable baseline is something we independently want.

---

## 5. Option C — id-based correspondence in the file

### Day-to-day
Every meaningful cell carries a stable `slide_id` present in *both* halves. Sync (or
an agent) matches de↔en by id directly — no baseline lookup, no drift reconstruction.
git diff is the change signal: a changed cell body under a stable id *is* the edit, and
its twin is found by id in O(1). New cells get an id at authoring time; a split keeps
`de_id == en_id`; a rename keeps the id (only the body changes).

### Good points
- **It dissolves the watermark.** No stored "last-synced state" is needed, because
  identity is intrinsic to the content. The entire #363–#366 stale-baseline class
  *cannot occur* — there is nothing to fall behind. This is the only option that
  attacks the root cause from §1 rather than relocating the memory.
- **Maximally legible.** A human or an agent opening the two files sees the
  correspondence directly. No anchors, no hashes, no occurrence counting.
- **git-native.** The change signal is the diff; no second store to keep in lockstep
  with commits — which is the precise coupling #366 exists to remove.

### Pain points (and they are not small)
- **Front-loaded churn.** #190 measured it: ~13 149 new ids, a one-line diff across
  two-thirds of every deck. The author explicitly dreaded this, which is *why* #190
  chose DB content anchors and got ~90 % of C's benefit without the churn.
- **Authoring discipline forever.** Every new cell needs an id; every split must keep
  `de_id == en_id`; copy-paste must not duplicate an id. This is a permanent tax on
  authoring, and humans are bad at it (which is partly why we are here).
- **Id migration is its own hard problem.** #190 Phase 4/5 already wrestled with the
  "def-my-fun" case (an id worn by the wrong cell after an edit), and it needed
  uniqueness guards, new-cell evidence, symmetric both-deck writes, and a bounded-Opus
  recoverer that must *never drop a worn id*. C does not remove this; it makes id
  hygiene load-bearing for *everything*.
- **It does not actually remove the LLM for translation.** Localized cells still need
  translating; ids tell you *which* cell, not *what it should say* in the other
  language. C fixes correspondence, not content.

### The shape of its trouble
**Front-loaded and disciplinary, then low-recurrence — but with a sharp,
hard-to-resolve tail.** Once ids are universal, day-to-day desync largely vanishes.
But the tail cases (id duplicated by copy-paste, id stranded by a rename, a split that
breaks `de_id == en_id`) are *corrupting* in a way B's and A's are not: a wrong id
silently mis-pairs cells across languages, and #190 showed how subtle the guards must
be. C trades many small recoverable annoyances for few large ones that need real care.

---

## 6. Which option repeatedly drags us into hard-to-resolve trouble?

Directly, because the user asked directly:

| | Recurrence | Visibility | Worst case | Recoverability |
|---|---|---|---|---|
| **B (DB)** | High (structural) | Hidden | Loud error vs stale baseline | Easy (`--rebaseline`) |
| **A (sidecar)** | Medium (merge-time) | Visible in PR | Two-baseline merge conflict | Moderate (reason about both) |
| **C (ids)** | Low after rollout | Maximally visible | Silent cross-language mis-pair | Hard (subtle id-migration) |

The honest reading:

- **B keeps us in *frequent, shallow* trouble.** It recurs forever and is annoying to
  diagnose, but it never destroys work. We have already paid down most of its
  diagnosis cost.
- **C buys us out of recurring trouble at the price of a *rare, deep* trouble** plus a
  large up-front cost and a permanent discipline tax. Its bad days are worse than B's,
  but there are far fewer of them.
- **A sits in between and, crucially, makes the trouble *visible*.** It does not
  reduce the amount of state; it relocates it somewhere a human or agent can see and a
  merge can flag.

**None of the three is a clean win**, and that is the uncomfortable truth the §1
sentence predicts: as long as we demand a *deterministic* reconstruction of an
*irreducibly semantic* correspondence, we are choosing *which* shape of trouble to
own, not whether to own one.

---

## 7. Agent-friendliness — which surface does an agent actually want?

The user's emerging real workflow is the most important data point in this note:
*"I find myself more and more just kicking the sync job over to Claude Code."* That is
not a hypothetical; it is the tool being bypassed in practice for exactly the cases it
was built to solve. Take it seriously as evidence.

What an agent wants from sync, concretely:

1. **A precise, machine-readable statement of what changed and what is ambiguous** —
   not a hidden baseline it cannot inspect. `clm slides sync --explain` and
   `--dry-run --json` already exist and are the right shape; the MCP
   `slides_suggest_sync` tool already returns a structured verdict.
2. **A way to apply a *decision* and have it *verified*** — "I judge DE cell 7 ↔ EN
   cell 9 a pair; propagate DE→EN" → CLM checks the structural invariants (unify
   byte-identity for neutral cells, `de_id == en_id` symmetry, no dropped cells, header
   parity) and rejects a structurally unsound edit.
3. **Legible identity.** An agent reasons far better over `slide_id`s and source text
   than over occurrence ordinals and sqlite content hashes.

Against that:

- **B is the *least* agent-friendly.** The baseline is invisible, local, and
  un-inspectable; the failure mode is `stale watermark, run --rebaseline`, which an
  agent cannot reason about because it cannot see the watermark.
- **A is more agent-friendly than B** — the agent can read, diff, and reason about the
  sidecar as a file — **but it is full of opaque hashes**, so the agent reasons over a
  derived encoding, not the thing itself.
- **C is the *most* agent-friendly for the matching step** — correspondence is explicit
  in the source the agent is already reading — at the cost of the agent having to
  *maintain* id discipline (which, notably, an agent is far *better* at than a human:
  stamping a fresh id on every new cell is exactly the kind of mechanical invariant an
  agent keeps without resentment).

There is a deeper point. **An agent does not actually need the watermark's core
function at all.** The watermark answers "what changed since last time?" An agent
answers a *different and sufficient* question: "do these two files currently say the
same thing modulo translation, and if not, which side does git show was edited?" The
first question needs a stored past. The second needs only the two files plus
`git diff`. **The baseline is a crutch for determinism; an agent judging present
consistency does not need the crutch** — it needs the source, the diff, and a
verifier.

---

## 8. The pivot: `clm slides sync` as an agent-assist tool

This is the honest path the user is pointing at, and I think it is right — with
guardrails. The reframing is not "throw away the engine." It is a **re-prioritization
of the tiers the engine already has.**

Today the engine is a deterministic solver with a *narrow opt-in* LLM-recovery tier
(`--llm-recover`, default off, used only when every deterministic tier is stuck).
The pivot promotes that ordering's *intent*:

- **The deterministic engine becomes the fast-path and the verifier**, not the
  authority of last resort. It still does the unambiguous 90 % for free (no LLM, no
  non-determinism, no cost) — *that part is genuinely good and must not be thrown
  away*. And it becomes the thing that *checks* an agent's edit: invariants, parity,
  no-drop, id symmetry.
- **The agent becomes the solver for the ambiguous residue** — the id-less localized
  drift, the simultaneous rename, the N:1 merge/split — the exact cases where we have
  been bolting on fragile deterministic rules and *still* "keep running into trouble."
  Here a semantic judge is not a worse approximation of a deterministic rule; it is the
  *correct* tool for an irreducibly semantic problem.
- **CLM's job shifts from "decide" to "frame and check":** emit the precise
  reconciliation report (which cells are keyed / drifted / ambiguous, with source text
  and ids), accept the agent's decisions, and *verify* them hard before writing.

Pitched in one line: **`clm slides sync` stops trying to be the thing that solves
de/en correspondence and becomes the thing that lets an agent solve it safely and
verifiably.**

### Why this is the more *honest* framing
We have spent the #166 → #190 → #269 → #364/#365/#403 arc approximating semantic
judgment with escalating deterministic heuristics, and the residue has *not*
converged — each fix narrows the failure and exposes the next edge. That is the
signature of approximating an underspecified problem. Putting the semantic judgment
where it belongs (an agent) and keeping determinism where *it* belongs (the cheap
common case + verification) stops fighting the problem's nature.

### Where this must be criticized, hard
- **Non-determinism / no reproducibility.** A pipeline that *depends* on an agent to
  sync cannot be re-run for the same answer twice. This is unacceptable for the easy
  90 %, which is precisely why the deterministic fast-path stays primary and the agent
  is invoked only for flagged ambiguity. Sync-in-CI must remain deterministic or fail
  loud, never silently call a model.
- **Cost and latency.** Opus-per-sync is far more expensive and slower than verbatim
  reuse. Hybrid is not a nicety; it is the only economically sane shape.
- **Trust.** An agent can mistranslate or drop a cell with total confidence. The
  deterministic verifier is therefore *non-optional* — the engine we already built
  becomes the safety net, not the solver. We do **not** get to delete it; we
  re-purpose its best part.
- **It does not free us from a baseline for the *fast path*.** The deterministic 90 %
  still wants *a* baseline to know what drifted. So the agent pivot **does not, by
  itself, decide A vs B vs C** — it changes how much that decision *matters*.

---

## 9. How the storage choice and the agent pivot interact

This is the synthesis, and it changes the recommendation.

If sync becomes agent-first for the hard cases, then **the baseline's only
irreplaceable job — detecting the lag where both halves were edited+committed without
syncing — can be done by the agent from the files + git, without a stored
last-synced memory at all.** The agent does not ask "what changed since the
watermark"; it reads both halves and `git log`/`git diff` and reasons about present
consistency and edit direction directly.

That has a sharp consequence for the storage question:

- It **lowers the value of A**. The main thing a per-pair sidecar buys over B —
  a shared, reviewable, travelling baseline — is most valuable to *humans and agents
  reasoning about the baseline*. But an agent reasoning *semantically from source +
  git* does not need the stored baseline to be shared, reviewable, or travelling,
  because it is not leaning on it. So building A is real work whose main beneficiary
  the agent pivot partly removes.
- It **raises the relative appeal of "B + agent recovery."** Keep the cheap local
  watermark for the deterministic fast-path; when it is stale, missing, or the case is
  ambiguous, the agent recovers from source + git rather than from a perfected store.
  The watermark is allowed to be imperfect because it is no longer the authority of
  last resort — the agent is.
- It **does not rescue C from its up-front cost**, but it *changes who pays it*: an
  agent maintaining universal id discipline is far cheaper and more reliable than a
  human doing it. If we ever wanted C, the agent pivot is what makes it affordable —
  the agent stamps and migrates ids as a mechanical invariant. That makes C a
  *long-term* option that only becomes sane *after* the agent workflow is real, not
  before.

---

## 10. Recommendation (honest, and partly against the idea that prompted it)

1. **Do not build C as a forcing function now.** The churn is measured and was already
   rejected; content anchors captured most of its benefit. Revisit C only *after* an
   agent workflow exists to pay its discipline tax — then it becomes the genuine
   long-term escape from the watermark.
2. **Treat the per-pair sidecar (A) as worthwhile *only if* an in-repo, shared,
   reviewable baseline is independently wanted** (e.g. multi-author course repos, CI
   that must see the same baseline, or a desire to kill orphan-rot for its own sake).
   It is a sound, CLM-native design — store the **full bundle**, key cell-matches by
   anchor/construct/hash **never position**, reuse the sidecar-layout + `tidy` +
   release-skip infra — but be clear-eyed that it is **relocation, not elimination**,
   and that the agent pivot removes part of its reason to exist. If we go agent-first,
   A is plausibly *unnecessary complexity*.
3. **Keep B as the cheap deterministic fast-path baseline.** It is good at what it is
   good at; its failure mode is now backstopped by the agent rather than by an
   ever-growing pile of deterministic rules.
4. **Invest the next increment in the agent pivot, not in storage.** Concretely and in
   low-regret order:
   - **(i)** Make `--dry-run --json` / `--explain` / MCP `slides_suggest_sync` the
     *blessed* agent interface: a complete, stable, machine-readable reconciliation
     report (keyed / drifted / ambiguous cells, with source text, ids, and a
     direction hypothesis). Most of this exists; harden and document it as the
     contract.
   - **(ii)** Add a **verify-only** mode: given the two halves *after* an agent has
     edited them, run the deterministic invariants (unify byte-identity, `de_id ==
     en_id`, no-drop, header/anchor parity) and report pass/fail with precise
     locations. This is the safety net that makes agent-driven sync trustworthy, and
     it is *pure determinism reused* — the highest-leverage, lowest-risk piece.
   - **(iii)** Reposition `--llm-recover` from a narrow last-resort tier to the
     documented path for the ambiguous residue, with the verifier from (ii) gating
     every write.
   - **(iv)** *Then* revisit whether a shared baseline (A) is still wanted. By that
     point we will know from real use whether the agent needs it.

The honest bottom line: **the storage debate (A vs B vs C) is a second-order question
once you accept that the first-order problem is partly semantic and we have been
paying determinism to pretend otherwise.** The user's agent instinct is the more
honest path — not because the engine is bad (its cheap common-case handling is
genuinely valuable and must survive as the fast-path *and* the verifier), but because
the residue we keep tripping on is exactly the part a deterministic store can never
make clean. Spend the next effort making sync *frame and verify* for an agent, keep
the DB as the cheap fast-path, and let the per-pair sidecar wait until we know whether
an agent workflow still wants it.

---

## 11. Open questions to decide before any build

1. **Verify-only mode first?** Is item 4(ii) — the deterministic verifier as a
   stand-alone `clm slides sync --verify` — the right first increment? (My strong
   lean: yes; it is low-risk, pure-reuse, and unblocks trusting an agent.)
2. **Do we still want a *shared* baseline at all** once the agent recovers from source
   + git? If no, A is dropped and B stays as the local fast-path. If yes (multi-author
   / CI parity), A is the right shape — but build it after (ii)/(iii).
3. **CI contract.** Sync-in-CI must stay deterministic or fail loud. Do we forbid the
   LLM tier in CI entirely, or allow it behind an explicit, logged flag?
4. **Is C ever on the table?** Only worth reopening once the agent maintains id
   discipline mechanically. Park it as "the long-term stateless option, affordable
   only after the agent workflow exists."
5. **MCP vs CLI as the primary agent contract.** `slides_suggest_sync` (MCP) and
   `--dry-run --json` (CLI) overlap. Which is the blessed surface, and do they share
   one report schema?
