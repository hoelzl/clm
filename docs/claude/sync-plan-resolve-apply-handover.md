# Handover: Sync Resolve-then-Apply redesign (#216)

**Branch:** `claude/sync-plan-resolve-apply-redesign` (off `master` @ `1a105f6`)
**Design note:** `docs/claude/design/sync-plan-resolve-apply.md` (the canonical spec)
**Base design:** `docs/claude/design/single-language-authoring-sync.md` (the #166 engine)
**Memory:** `project-sync-coldstart-idless-bug` (corrected framing + verified findings)
**Status:** design accepted, both forks settled; **Phase 1 DONE** (all 5 xfails flipped to passing); Phase 2 is next.

---

## 1. Feature Overview

`clm slides sync` reconciles a split `.de.py`/`.en.py` deck pair. Today it runs in
two stages — `build_sync_plan` (pure classifier → `SyncPlan`) then `apply_plan`
(writes). The bug this redesign fixes: **`apply_plan` is a second decision-maker,
not an executor.** It re-runs structural logic the plan never captured (deferrals,
guards, refusals), so the `SyncPlan` is *not* a faithful description of what will
happen. Every symptom found this session is one cause.

The redesign re-architects the plan/apply boundary into **decide once at plan
time, then mechanically replay**: a pure structural *resolve* stage assigns every
item a final *disposition* (apply / refuse / defer / pair / conflict / pending),
an LLM *materialize* stage fills in generated content, and a decision-free
*apply* stage executes. Because `--dry-run` becomes the pure resolve stage shared
byte-for-byte with the writing run, the two can no longer diverge.

**Why it matters:** the divergence misleads authors (a dry-run promising changes
the writing run refuses), and a sibling of the same root cause **silently doubles
decks** (data corruption). It also blocks the **CLM 1.8 PythonCourses gate**
(`#158`): ~200 id-less split halves need `clm slides sync` to be a correct
cold-start id minter, which it currently is not.

**Issues:** `#216` (cold-start id-less; the entry point), `#158` (1.8 gate, blocked).

---

## 2. Design Decisions

### The core reframing (user's, confirmed)
Apply does much more than apply. The decisions hiding in `apply_plan` must move to
plan creation; applying must be mechanical. Refined into **three stages with a
hard purity boundary**:

```
1. Classify   [pure]            structural diff vs. baseline → raw observations
2. Resolve
   2a. structural  [pure, no LLM, may read files]   decide EVERY disposition
   2b. materialize [LLM/IO]                          translate / rewrite / verify
3. Apply      [mechanical]      replay; cannot defer or refuse
```

`--dry-run` = stages **1 + 2a** (pure, shared with the writing run) → structural
parity is guaranteed by construction.

### Two forks settled by the user (do NOT re-litigate)
- **Dry-run faithfulness → "structural-faithful, no LLM."** Dry-run runs 1+2a,
  shows every structural disposition, marks generated content `pending` (no model
  call). The only permitted apply-time divergence is a `pending` item the model
  can't materialize/verify → downgrades to `blocked`/`refused`, disclosed because
  the preview already said `pending`. (A future `--dry-run --with-content` may opt
  into running 2b — deferred.)
- **Cold-start pairing → "mint when correspondence confirmed, else refuse."** A
  never-id'd aligned pair is a *candidate*; with a provider, a cheap cached LLM
  gate confirms the halves correspond, then mints shared ids; with no provider or
  a "no" verdict, it **refuses** with a clear message. **Provider availability is a
  plan-time (structural) input**, so dry-run and apply agree even here.

### Why these over the alternatives
- **Mint on structural alignment alone** (the issue's literal proposal) was
  **rejected**: two coincidentally same-length/same-role halves (one rewritten or
  reordered) pass alignment yet aren't translations → a silently-wrong shared id,
  *worse* than today's visible refuse. The base design's **§3.2 "no
  similarity-guess"** principle forbids it. The chosen policy supplies the missing
  cross-language signal via an explicit *verification*, not a blind guess — a
  principled evolution, not a violation.
- **Fully-faithful dry-run (runs the LLM)** was rejected as the default: it makes
  every preview cost money/latency. Structural-faithful covers the reported pain.
- **Build all-new minting machinery vs. delegate**: the resolver's
  `mint_shared_id`/`adopt_id` ops should **delegate byte-level work to the
  existing, proven `assign_ids_in_split_pair`** (`assign_ids.py:823`,
  round-trip-verified EN-authority paired minting) rather than re-implement slug
  derivation. The maintainer's "no unify→assign→split *workaround*" means don't
  make the *author* run it manually — `sync` calling it internally is fine.

### Constraint: the classifier stays pure
`sync_plan.py` is documented "pure analysis — no LLM, no writes." The LLM
correspondence gate lives in **stage 2b (apply-side tier)**, mirroring the
existing opt-in `--llm-recover` / `AlignmentRecoverer` pattern in `sync_recover.py`
(opt-in flag, cached, validated, safe-abort). 2a only needs to know *whether* a
provider is configured.

---

## 3. Phase Breakdown

### Phase 1 — `Refuse` as a first-class disposition; move structural guards to 2a  **[DONE]**

*Implemented.* `Proposal` gained a `disposition` field (`"apply"` | `"refuse"`)
and a `refuse` kind (`SyncPlan.refusals`, `_KIND_ORDER`, the `summary()` headline,
`count("refuse")`). The both-directions refusal moved into `classify_changes`:
`_refuse_cold_both_directions` (cold path — refuses when the combined idd+idless
adds span both directions: parallel id-less, mismatched-id, half-id'd) and
`_refuse_idless_both_directions` (baseline path — refuses only id-less
both-direction adds; id-carrying both-direction adds stay adds, since against a
real baseline they are distinct new slides). `apply_plan` executes a `refuse` as a
deferred no-op (`result.deferred += 1`, watermark holds, **no error → exit 1**);
the old `_apply_adds:710-718` guard and the `process_idless` parameter/branches in
`_add_one_direction` are deleted. The CLI (`_plan_dict` counts + `disposition`),
the interactive walker (`REFUSE` action, shown-not-prompted, `refused` summary),
and the `migration.md` info topic were all updated. **All 5 xfails flipped to
passing** (the main-path refusal also fixed the watermark both-sides case, so 5,
not the 4 first estimated); a new `TestBothDirectionsRefusal` pins the classifier
behavior directly. `tests/slides` green (1048 passed), ruff + mypy clean.

The original TODO recipe (kept for reference):
- **Goal:** relocate every *structural* decision from `apply_plan` into the
  resolver so the plan is faithful and apply stops re-deciding.
- **Work:**
  - Add a `disposition` concept to the plan (a field on `Proposal`, or a new
    plan-item type) with values `apply` / `refuse(reason)` / `conflict(reason)`
    (and later `pending`). `refuse` must render in `render_plan` / `--dry-run`
    output and JSON, and drive `_plan_exit_code`.
  - In `classify_changes` (cold path, `sync_plan.py:879-883` and
    `_append_idless_adds` ~1104): when id-less adds would span **both directions**
    (the parallel cold-start case), emit a single `refuse` item instead of N
    bidirectional adds. This is the both-directions guard, **moved from apply**.
  - Cover the **id-carrying** sibling too: a cold-start pair emitting *id-carrying*
    adds in both directions (mismatched-id / half-id'd) must also `refuse` — this
    is the currently-**unguarded** doubling path.
  - In `apply_plan` (`sync_apply.py`): delete the both-directions guard at
    `_apply_adds:710-718` and the `process_idless` branching; apply now just
    executes whatever disposition the plan carries (refuse → no-op + hold
    watermark).
  - Make `_plan_exit_code` / `_apply_exit_code` (`slides_sync.py:913/922`) derive
    from dispositions so they converge.
- **Files:** `src/clm/slides/sync_plan.py`, `src/clm/slides/sync_apply.py`,
  `src/clm/cli/commands/slides_sync.py`.
- **Acceptance:** flips **4 of 5** xfails — both dry-run parity xfails
  (`test_sync_dry_run_parity.py`, `TestDryRunApplyParity`) and both id-carrying
  doubling xfails (`test_sync_apply.py`). Remove their `xfail(strict=True)`
  markers (strict will force this — they become XPASS failures otherwise).
- **Risk:** low. No LLM, no new minting; pure relocation + a new disposition.

### Phase 2 — Split apply into materialize (2b) + execute (3)  **[TODO]**
- **Goal:** make apply decision-free. Formalize a `MaterializedPlan`; fold
  translation (`_translate`, `_add_one_direction`) and the edit judge into a 2b
  pass that attaches content or marks an item `blocked`; the execute pass does
  only mechanical writes (insert / replace-body / stamp-id / delete / move).
- **Files:** `src/clm/slides/sync_apply.py` (the bulk), possibly a new module for
  the execute primitives.
- **Acceptance:** behavior-preserving refactor; full sync suite stays green; the
  boundary is enforced (execute makes no decisions, calls no model).

### Phase 3 — Cold-start minting + correspondence gate  **[TODO]**
- **Goal:** `clm slides sync` becomes the proper cold-start id minter.
- **Work:**
  - 2a (provider-aware): for a structurally-aligned cold-start pair, emit
    `mint_shared_id(de_cell, en_cell)` (both id-less) or `adopt_id(src_id →
    twin)` (half-id'd) **candidates** when a provider is configured (else
    `refuse`). Alignment predicate adapted from the idea behind
    `_streams_aligned` (`sync_plan.py:1267`) — note it is **not** a drop-in (see
    Gotchas).
  - 2b: a `CorrespondenceVerifier` beside `AlignmentRecoverer` in
    `sync_recover.py` — cheap model (Haiku-class), body-free inputs, cached by
    pair fingerprint + prompt version, validated, safe-abort. "yes" → materialize
    the mint/adopt; "no" → downgrade to `refuse`.
  - Delegate the byte-level stamping to `assign_ids_in_split_pair`
    (`assign_ids.py:823`).
  - CLI: `--verify-cold-pairs` (default on when a provider is set).
- **Acceptance:** flips the remaining #216 bootstrap xfails (the two
  `test_sync_dry_run_parity.py::TestDryRunMatchesApplyKnownBugs` cases, once
  Phase 1 has them refusing they'll need updating to assert *mint* under a stub
  verifier). Unblocks the 1.8 PythonCourses gate.

---

## 4. Current Status

### Done (committed on the branch)
- **Phase 1 implementation** (this session): the `refuse` disposition + the
  both-directions guard relocated to the resolver. Touches
  `src/clm/slides/sync_plan.py`, `sync_apply.py`, `sync_plan_walker.py`,
  `src/clm/cli/commands/slides_sync.py`, `src/clm/cli/info_topics/migration.md`,
  and the four test files (markers removed, assertions rewritten to the refusal
  outcome, `TestBothDirectionsRefusal` added). `tests/slides` = 1048 passed,
  2 skipped; ruff + mypy clean.
- `93d5e59` — **test spec** (originally 4 pass / 5 xfail; the 5 xfails are now
  passing after Phase 1):
  - `tests/slides/test_sync_dry_run_parity.py` (NEW): parity helper
    `_assert_dry_run_predicts_apply` + `_dry_then_apply`; `TestDryRunMatchesApply`
    (3 pass: noop / single-side id-less add / one-side edit);
    `TestDryRunMatchesApplyKnownBugs` (2 xfail: cold-start parallel id-less;
    watermark both-sides id-less).
  - `tests/cli/test_slides_sync.py`: `TestDryRunApplyParity` (1 pass + 1 xfail) +
    helpers `_stub_translator`, `_idless_slide`.
  - `tests/slides/test_sync_apply.py`: `import pytest` added; 2 xfail
    (`test_cold_start_mismatched_ids_must_not_double`,
    `test_cold_start_half_idd_must_not_double`) for the id-carrying doubling.
- `cff8e85` — **design note** `docs/claude/design/sync-plan-resolve-apply.md`.

### Verified findings (the truth table — reproduced empirically + a 7-agent verification workflow)
| Cold-start scenario | Behavior today | Disposition |
|---|---|---|
| **A** symmetric id-less (issue's literal repro) | apply **defers** all, errors, writes nothing | SAFE (guarded). Only the **dry-run lies** (`N add`/exit 1 vs apply exit 2) |
| **B** asymmetric (id-less on one side only) | apply translates + inserts | correct (legit "new on one side") |
| **C** half-id'd (one half id-less, other id'd) | **both decks DOUBLE, errors=[]** | **silent corruption** (unguarded idd both-directions) |
| **D** mismatched-id (both id'd, different ids) | **both decks DOUBLE, errors=[]** | **silent corruption** (same root cause as C) |
| **E** id-less code + id-less narrative | narrative adds **deferred** | SAFE (code rides structural pass) |

> **Phase 1 resolved A / C / D** (and the watermark both-sides id-less case): all
> now emit `refuse` proposals at plan time — dry-run and apply agree (exit 1,
> nothing written, watermark held). The "Behavior today" column above is the
> *pre-Phase-1* state, kept for the record.

**Key correction:** the issue's "silent doubling of the id-less case" is **wrong** —
that case is *refused* (guard at `sync_apply.py:710-718`, added 2026-05-31,
predates the issue). The real doubling is the **id-carrying sibling** (C/D), which
has **no guard** (`sync_apply.py:695-705`).

### Blockers / open questions
- None blocking Phase 1. Open design refinements live in the design note §11
  (`--dry-run --with-content`; serializable resolved plan; reconcile-vs-refuse for
  mismatched-id pairs with a provider — start with refuse).

### Test state
- After Phase 1: the 9 parity tests all pass (the 5 xfail markers removed),
  `TestBothDirectionsRefusal` added (6 cases). Full `tests/slides` green
  (1048 passed, 2 skipped); `tests/cli/test_slides_sync.py` green. ruff + mypy
  clean on the changed modules.

---

## 5. Next Steps

**Phase 1 is DONE.** Start **Phase 2 — split apply into materialize (2b) +
execute (3)** (design note §10). The goal is to make `apply_plan` decision-free:

1. Formalize a `MaterializedPlan` (or a per-proposal `materialized_*` payload):
   a pass that, for each `add` / `rename` / `edit` / `retag`, attaches the
   already-generated content (the translated body / the judge's rewrite) or marks
   the item `blocked` when the model is unavailable/fails — instead of today's
   inline `_translate` (`sync_apply.py` ~870) and `judge.propose` (~590) calls
   that decide *and* write in one step.
2. Make the **execute** pass mechanical: insert / replace-body / stamp-id / delete
   / move only, no `translator`/`judge` reference, no `result.deferred`/`errors`
   decisions of its own (those become `blocked` dispositions decided in 2b).
3. Keep it behavior-preserving — the full sync suite must stay green; the boundary
   is the deliverable (assert execute calls no model). No new user-visible
   behavior; this just locks in what Phase 1 started.

Phase 3 (cold-start minting + `CorrespondenceVerifier`) remains the larger payoff
— see §3 of this handover and design §7. It is what finally lets a confirmed
cold-start pair *mint* instead of refuse, unblocking the 1.8 PythonCourses gate
(`#158`). Until then the Phase 1 refusal is the safe, honest default.

**Prereqs / setup (IMPORTANT — see Gotchas):**
- This worktree's venv was incomplete. Run **`uv sync --extra all`** before
  testing (vcrpy / `[replay]` was missing → `import vcr` ModuleNotFoundError broke
  the pre-commit suite).
- When committing, export **`PYTEST_XDIST_AUTO_NUM_WORKERS=4`** (the pre-commit
  wrapper `scripts/run_pytest_hook.py` `setdefault`s it; 8 flakes the
  mitm/worker-lifecycle contention tests on this 64-thread box).

---

## 6. Key Files & Architecture

### Source (to change)
- `src/clm/slides/sync_plan.py` — the classifier; becomes Classify + Resolve-2a.
  - `classify_changes` ~841; cold path `879-883`; `_classify_cold` ~1074 (pairs by
    shared id only, `in_sync_count += 1` — **does not mint**); `_append_idless_adds`
    ~1104 (the unconditional both-directions emission — the bug source);
    `_streams_aligned` ~1267; `build_sync_plan` ~1448 (has the raw `de_cells`/
    `en_cells`; baseline = watermark → git-head → none); `ordered_sync_cells` ~304;
    `Proposal` ~130; `SyncPlan` ~205.
- `src/clm/slides/sync_apply.py` — becomes Materialize-2b + Apply-3.
  - `apply_plan` ~147 (persists both decks once, **only on an error-free pass**);
    `_apply_adds` ~649 (**guard 710-718 = idless only**; **idd both-directions
    695-705 = unguarded**); `_add_idcarrying_one_direction` ~741; `_add_one_direction`
    ~820 (the minter — translate+insert); `_place_new_cell` ~930.
- `src/clm/slides/sync_writeback.py` — `role_of` ~66 (the predicate that decides
  what's sync-relevant — see Gotchas).
- `src/clm/slides/assign_ids.py` — `assign_ids_in_split_pair` ~823 (reuse for
  Phase 3 stamping).
- `src/clm/slides/sync_recover.py` — `AlignmentRecoverer` / `--llm-recover`
  pattern to mirror for the Phase 3 `CorrespondenceVerifier`.
- `src/clm/cli/commands/slides_sync.py` — `_plan_exit_code` ~913, `_apply_exit_code`
  ~922, dry-run/apply branching ~493/512, `_print_human` ~956, `_plan_dict` ~1008.

### Tests (the executable spec — already committed)
- `tests/slides/test_sync_dry_run_parity.py` — parity invariant (NEW).
- `tests/cli/test_slides_sync.py` — `TestDryRunApplyParity` + `_stub_translator`.
- `tests/slides/test_sync_apply.py` — id-carrying doubling regressions.

### Docs
- `docs/claude/design/sync-plan-resolve-apply.md` — the design (authoritative).
- `docs/claude/design/single-language-authoring-sync.md` — base #166 design
  (§3.2 no-similarity-guess, §3.4 isolate-and-refuse).

### Pattern to continue
- **Dispositions are first-class and shown in the plan.** Any decision that
  affects whether/what apply writes belongs in 2a (pure) and must render in
  `--dry-run`. Never let apply make a structural decision the plan didn't record.
- The parity assertion (`_assert_dry_run_predicts_apply`) is written to survive
  *either* fix shape ("a writing-run error must have been foreseen by the
  dry-run"), so it needs no edits — only the markers come off.

---

## 7. Testing Approach

- **Unit (fast):** `tests/slides/test_sync_plan.py`, `test_sync_apply.py`,
  `test_sync_dry_run_parity.py`; **CLI:** `tests/cli/test_slides_sync.py`.
- **The invariant under test:** a `--dry-run` and a writing run agree on every
  structural disposition (add/pair/refuse/defer/conflict + exit class); the only
  permitted apply-time divergence is a `pending` item the model couldn't
  materialize/verify. Asserted by `_assert_dry_run_predicts_apply` using the real
  CLI exit-code helpers and a non-failing static translator/judge.
- **Run the relevant tests:**
  ```
  uv run python -m pytest tests/slides/test_sync_dry_run_parity.py \
    tests/slides/test_sync_apply.py tests/slides/test_sync_plan.py \
    tests/cli/test_slides_sync.py -o addopts="-n auto" -q
  ```
  (single test serially: add `-o addopts="-n0"`).
- **Still needs tests (Phase 3):** the *mint* path under a stub
  `CorrespondenceVerifier` (yes → mints shared id; no → refuses); the two
  `TestDryRunMatchesApplyKnownBugs` cases will change from "must not lie" to "mints
  under a confirming verifier" once Phase 3 lands.

---

## 8. Session Notes

- **`role_of` subtlety (load-bearing):** `sync_writeback.py:66`. A markdown cell
  with a narrative tag (slide/subslide/voiceover/notes) gets a role **regardless of
  slide_id** → id-less *narrative markdown* IS sync-relevant (these are the
  `de_idless`/`en_idless` cells). A localized code cell needs **both** lang and
  slide_id → id-less code returns `None` and rides the structural `sync_code` pass.
  So the cold-start adds are narrative-markdown only.
- **`_streams_aligned` is NOT a drop-in for the cold path.** It consumes raw
  `Cell`s (incl. code) from `_localized_lang_cells`; `classify_changes` only holds
  role-filtered `CurrentCell`s (narrative markdown) and **is not passed the raw
  cells** (`build_sync_plan` has them). Phase 3's alignment predicate must be
  adapted, or build_sync_plan must forward raw cells.
- **The issue's premise was partly wrong** — verified empirically: the id-less
  symmetric case is *refused*, not doubled; the doubling is the id-carrying
  sibling. The corrected story is in memory `project-sync-coldstart-idless-bug`.
- **Env gotchas that cost time this session** (so the next session doesn't repeat):
  `uv sync --extra all` first; `PYTEST_XDIST_AUTO_NUM_WORKERS=4` when committing.
  A backgrounded `git commit` reporting "exit 0" was **misleading** — always
  confirm with `git log` (a pre-commit-rejected commit can mislead the wrapper).
- **User preferences observed:** wants the comprehensive clean design (not a quick
  patch); approved the plan/apply separation as the organizing principle; expects
  dry-run to be honest; standing permission to commit at sensible checkpoints
  (push still needs a request). Work stays on this branch.
