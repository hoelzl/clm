<!-- HANDOVER-ARCHIVE — fully retired on 2026-07-11 -->

# Handover Archive: Sync Resolve-then-Apply Redesign (#216)

> ⚠️ **FULLY RETIRED HANDOVER — NOT ACTIVE**
>
> This document archives a handover whose work is fully complete or has
> been abandoned. **There is no active handover document.** It must
> **not** be used with `/resume-feature`, `/implement-next-phase`, or
> similar commands that expect an active work plan.
>
> If you need to resume related work, start a fresh handover.

> **Retirement note (2026-07-11):** All three phases shipped on the v2 engine. The remaining Phase-2 deferred follow-ups (id-migration recoverer, `sync_code` structural translate) are **moot**: the entire v2 sync core (`sync_plan.py`, `sync_apply.py`, `sync_code`, …) was deleted by the Sync v3 core replacement (#520, cutover 2026-07-04). The resolve-then-apply *principles* live on in the v3 design; the code this handover describes no longer exists.

---
# Handover: Sync Resolve-then-Apply redesign (#216)

**Branch:** `claude/sync-plan-resolve-apply-redesign` (off `master` @ `1a105f6`)
**Design note:** `docs/claude/design/sync-plan-resolve-apply.md` (the canonical spec)
**Base design:** `docs/claude/design/single-language-authoring-sync.md` (the #166 engine)
**Memory:** `project-sync-coldstart-idless-bug` (corrected framing + verified findings)
**Status:** design accepted; **Phase 1 DONE**; **Phase 2 DONE (scoped: edit + add materialized)**; **Phase 3.1 (id-less cold-pair minting) DONE**; **Phase 3.2 (half-id'd adopt) DONE** — the half-id'd half of the #158 unblock, via an explicit `adopt` path (`unify`/`assign_ids` cannot pair an id-less cell with an id'd one). The whole #216 cold-start cluster is resolved end-to-end. **#225 follow-up DONE** — the cold-start bootstrap now also serves **committed** un-bootstrapped pairs: `_pair_is_unbootstrapped` (`build_sync_plan`) demotes a git-HEAD baseline to `source="none"` when the two halves share **no** `slide_id` (disjoint id sets), so a committed id-less pair mints, a committed half-id'd pair adopts, and a committed mismatched-id pair refuses — instead of the keyed baseline path that refused the id-less corpus and **silently doubled** the half-id'd / mismatched ones. The only remaining items are the Phase-2 deferred follow-ups (id-migration recoverer + `sync_code` structural translate).

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

### Phase 2 — Split apply into materialize (2b) + execute (3)  **[DONE — scoped to edit + add]**
- **Delivered** (commits `6e34368` edit / `879b61b` add): the model calls for the
  edit and add paths moved into materialize passes; the execute walks write
  mechanically.
  - **Edit** (2.1): `_materialize_edits` resolves every edit + won conflict into an
    `_EditOutcome` (`update` / `in_sync` / `blocked`); `_apply_edit` writes from it
    with no judge/translator (`_apply_code_edit` folded into `_resolve_edit`).
  - **Add** (2.2): `_materialize_idcarrying` / `_materialize_idless` pre-translate
    every add/rename source cell into a `_TransOutcome` cache keyed by source-cell
    `id`; `_add_one_direction` / `_add_idcarrying_one_direction` keep their exact
    structure but read the cache via `_translate` (which has a never-fires model
    fallback — the materialize walks enumerate a **superset** of what execute
    translates). Materialize calls sit right before their walks to preserve the
    state-mutation ordering and copy-id detection.
  - **Boundary tests** (`test_sync_apply.py`): judge called once per edit,
    translator once per add cell — both in materialize, never re-called in execute.
- **Behavior-preserving:** `tests/slides` + CLI sync = 1106 passed; ruff + mypy clean.
- **Deferred follow-ups (still call models inline):** the id-migration recoverer
  (`--llm-recover`, `_migrate_drifted_ids` subsystem) and the `sync_code` structural
  translate (`apply_code_structure`, id-less localized code). A single bundled
  `MaterializedPlan` object was judged not worth the churn (the two materialize
  seams suffice; the add seam is ordering-sensitive, so it can't simply hoist to
  the top of `apply_plan`).

### Phase 3 — Cold-start minting + correspondence gate  **[3.1 (id-less) DONE; 3.2 (half-id'd) DONE]**

**3.1a/3.1b SHIPPED** (commits `5bdf4d4` verifier tier + the id-less wiring commit):
`clm slides sync` now bootstraps a never-id'd **both-id-less** cold pair end-to-end
— `build_sync_plan(provider_available=...)` emits a `pending` `mint` candidate for a
unifiable pair; `apply_plan` verifies via `CorrespondenceVerifier` (in
`sync_recover.py`, mirroring `AlignmentRecoverer`; `SyncCorrespondenceCache` in
`cache.py`) and mints via `assign_ids_in_split_pair`; `--verify-cold-pairs`
(default-on when `has_openrouter_api_key()`) wires it through single/batch/walker.
`mint` kind + `pending` disposition + `applied_mint` added. **17 verifier unit tests
+ 7 engine mint tests + 1 CLI mint test; 1131 sync tests green.**

**Resolved open checks (record these):**
- **Half-id'd is NOT handled by `assign_ids_in_split_pair`.** `unify`'s
  `_slide_ids_pair` returns `de_id == en_id`, so a half-id'd pair (`None` vs an id)
  does **not** pair under unify → the minter would mint *separate* ids, not adopt.
  So **3.2 needs an explicit `adopt` path** (positional-stream pairing + stamp the
  id'd side's id onto the id-less twin). Both-id-less *does* pair (`None==None`).
- `provider_available` helper = `has_openrouter_api_key()` (`openrouter_client.py`).
- Verifier cache = a new `SyncCorrespondenceCache` sibling table (`sync_correspondences`).

**3.2 — half-id'd adopt (SHIPPED):** `build_sync_plan` now runs `_maybe_emit_cold_adopt`
right after `_maybe_emit_cold_mint` (same `source=="none" and provider_available` gate,
mutually exclusive — mint empties `plan.refusals` when it fires). `_cold_adopt_authority`
(`sync_plan.py`) walks the full positional localized stream and returns the fully-id'd
side only for a clean half-id'd shape (every pair role/cell-type-matched; sync pairs XOR
with a consistent authority; non-sync pairs id-less both sides), emitting a single
`kind="adopt"`, `disposition="pending"`, `direction="{authority}->{other}"`. Apply has a
second short-circuit beside the mint: `_apply_cold_adopt` verifies the slide pairs (reusing
`_build_slide_pairs` / `_resolve_correspondence`), then `_adopt_ids_in_split_pair` loads
**both** halves via `FileState.load`, walks them positionally, and stamps each authority
slide_id onto its id-less twin (`_stamp_slide_id`), flushing only the id-less half;
per-cell guards make a post-plan drift return `0` → deferral. `adopt` kind +
`applied_adopt` counter wired through `ApplyResult`, the walker, and the CLI
(`_counts_str`/`_plan_dict`/`_apply_dict`/`_outcome_line`). Mismatched-id and
mixed-authority stay `refuse`. `TestColdStartMint::test_half_idd_pair_keeps_refuse_in_phase_3_1`
became `test_half_idd_pair_is_adopt_not_mint`; new `TestColdStartAdopt` (10 cases) +
CLI `test_half_idd_pair_adopts_when_verified`. **1109 slides + 213 sync/CLI tests green.**

- **Goal (overall):** `clm slides sync` becomes the proper cold-start id minter — the
  actual #158 unblock. **The authoritative, decision-baked plan is design note §12.**
- **Decisions (settled 2026-06-04 with the maintainer):**
  - **Scope:** mint **id-less** both-directions cold pairs (the #158 case) **and**
    **half-id'd** pairs (id-less half adopts the id'd half's ids); **mismatched-id**
    (both id'd, different ids) stays `refuse`.
  - **Verifier inputs:** per aligned slide, **heading + a short body snippet**
    (~first 1–2 lines) + role; **Haiku-class** model; one cached call per deck.
    **Not body-free** (cross-language correspondence needs the heading text).
  - **Gate:** the verifier is a **required gate, default-on when a provider is
    configured** (`--verify-cold-pairs`); no provider → `refuse`.
- **Architecture (keeps the purity boundary; see §12 for detail):**
  - `classify_changes` unchanged (still `refuse`). `build_sync_plan` (with files +
    a new `provider_available: bool`) converts a both-directions cold refusal into a
    `pending` mint candidate when `provider_available` **and** the pair is
    **unifiable** (read-only `unify→split` round-trip); else keeps `refuse`.
  - `apply_plan` (2b) verifies the candidate's heading/snippet pairs with a new
    `CorrespondenceVerifier` (mirrors `AlignmentRecoverer`: Protocol + Static +
    OpenRouter, fingerprint-cached, validated, safe-abort). All "yes" → mint; any
    "no"/abort/no-verifier → downgrade to `refuse` (the disclosed Q-A divergence).
  - execute delegates to `assign_ids_in_split_pair` (`assign_ids.py`) — byte-faithful
    EN-authority minting; its own "not unifiable → None" is a second safety net. A
    cold pair carries no other apply ops, so the file-level write doesn't fight the
    FileState buffer.
  - CLI: `--verify-cold-pairs` (default on when provider set); pass `provider_available`
    into `build_sync_plan`.
- **Acceptance:** the `TestColdStartRefusalParity` cases become mint-under-a-confirming
  -`StaticCorrespondenceVerifier` / refuse-under-a-denying-one / refuse-with-no-provider;
  + a half-id'd adopt case, a not-unifiable→refuse case, and verifier caching. Unblocks
  the 1.8 PythonCourses gate (`#158`).
- **Open implementation checks:** confirm `assign_ids_in_split_pair`/`unify` propagates
  an existing id onto the id-less twin for **half-id'd** (else add an explicit `adopt_id`
  stamp); pick the verifier cache (extend `SyncAlignmentCache` vs sibling table);
  `provider_available` env helper.

---

## 4. Current Status

### Done (committed on the branch)
- **Phase 2 implementation** (commits `6e34368` edit-path / `879b61b` add-path):
  edit + add paths are materialize-then-execute (see Phase 2 above). Touches only
  `src/clm/slides/sync_apply.py` + `tests/slides/test_sync_apply.py` (2 boundary
  tests). `tests/slides` + CLI sync = 1106 passed; ruff + mypy clean.
- **Phase 1 implementation** (commit `de038a7`, docs `033db03`): the `refuse`
  disposition + the both-directions guard relocated to the resolver. Touches
  `src/clm/slides/sync_plan.py`, `sync_apply.py`, `sync_plan_walker.py`,
  `src/clm/cli/commands/slides_sync.py`, `src/clm/cli/info_topics/migration.md`,
  and the four test files (markers removed, assertions rewritten to the refusal
  outcome, `TestBothDirectionsRefusal` added).
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
- After Phase 2: `tests/slides` + `tests/cli/test_slides_sync.py` = **1106 passed,
  2 skipped**; ruff + mypy clean. Phase 1's 9 parity tests + `TestBothDirectionsRefusal`
  (6 cases) all pass; Phase 2 added 2 boundary tests (judge-once / translator-once).
  All pre-commit hooks green on both Phase 2 commits.

---

## 5. Next Steps

**Phases 1 and 2 are DONE; Phase 3's design is finalized.** Implement **Phase 3 —
cold-start minting + correspondence gate** per the **decision-baked plan in design
note §12** (mirrored in the Phase 3 breakdown above). It lets a confirmed cold-start
pair *mint* a shared id instead of refusing — **the actual #158 unblock**. Build
order:

1. **Data model** (`sync_plan.py`): add the `pending` disposition value and a `mint`
   proposal kind (carries the aligned heading/snippet pairs). Render/JSON/exit-code
   like `refuse` (a pending mint is exit 1, "changes pending").
2. **2a candidacy** (`build_sync_plan`, +`provider_available: bool`): when the cold
   refusals are convertible and the pair is **unifiable** (read-only `unify→split`
   round-trip) and a provider is set → emit the `pending` mint candidate; else keep
   `refuse`. Keep `classify_changes` pure.
3. **Verifier** (`sync_recover.py`): `CorrespondenceVerifier` Protocol + `SlidePair`
   + `correspondence_fingerprint` + `validate_correspondence` + `Static…` / `OpenRouter…`
   (Haiku), mirroring `AlignmentRecoverer`. **Heading + short body snippet** inputs
   (NOT body-free). Cache + validate + safe-abort.
4. **2b verify + 3 mint** (`sync_apply.py`): for a `pending` mint, verify; all-yes →
   `assign_ids_in_split_pair` (handles id-less + half-id'd); any-no/abort/no-verifier →
   downgrade to `refuse`.
5. **CLI** (`slides_sync.py`): `--verify-cold-pairs` (default on when provider set);
   pass `provider_available` into `build_sync_plan`.
6. **Tests:** rewrite `TestColdStartRefusalParity` (mint / refuse-on-no / no-provider),
   add half-id'd adopt + not-unifiable→refuse + verifier-cache cases.

**Watch (open checks, §12):** confirm `assign_ids_in_split_pair`/`unify` propagates an
existing id onto the id-less twin for **half-id'd** (else add an explicit `adopt_id`);
choose the verifier cache; `provider_available` env helper.

**Also remaining (lower priority, Phase 2 follow-ups):** materialize the id-migration
recoverer and the `sync_code` structural translate so execute is *fully* model-free
(today only the edit + add paths are). Not required for Phase 3.

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
