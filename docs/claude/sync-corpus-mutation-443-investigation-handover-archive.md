<!-- HANDOVER-ARCHIVE — fully retired on 2026-07-11 -->

# Handover Archive: test_sync_corpus_mutation Failures (#443)

> ⚠️ **FULLY RETIRED HANDOVER — NOT ACTIVE**
>
> This document archives a handover whose work is fully complete or has
> been abandoned. **There is no active handover document.** It must
> **not** be used with `/resume-feature`, `/implement-next-phase`, or
> similar commands that expect an active work plan.
>
> If you need to resume related work, start a fresh handover.

---
# Handover — `test_sync_corpus_mutation` failures (#443) + the CI-coverage gap

> ## ✅ RESOLVED (2026-06-24)
>
> Both problems are fixed and merged. This document is kept as the record of the
> investigation; the boxed status below is the outcome, the body is the trail.
>
> - **The bug (#443) — FIXED, PR #471 (merged `a374e304`).** The §2 hypothesis
>   below (the `vo_anchor` narrative-diversion path) was **refuted**. The real
>   cause is an **asymmetric companion**: in the trigger deck the voiceover is
>   **id-less on DE but id'd on EN**. `_index_by_key` routes the id'd half to the
>   keyed diff and the id-less half to the anchor pass;
>   `_reconcile_idless_idd_narratives` (`sync_plan.py`) then pairs the EN id'd
>   `add` with the DE id-less `add` and **cancels both unconditionally** — never
>   comparing the id'd half to its baseline, so a one-sided edit/removal
>   vanishes. The fix adds `_alert_asymmetric_companion_drift`, which detects
>   baseline drift on the id'd half and **alerts** (an `error`-severity
>   `PlanIssue` that holds the watermark, pointing the author at `assign-ids`)
>   instead of dropping it. Chose *alert* over *propagate* because propagating
>   across the id-less↔id'd boundary is exactly the destructive doubling the
>   reconcile (report #10 / #199) guards against. Bundled fast regression tests:
>   `test_issue443_*` in `tests/slides/test_sync_limitations.py`.
> - **The CI-coverage gap (Phase B/C) — CLOSED, this PR.** The corpus-mutation
>   oracle and the no-op invariant now run in CI on a committed synthetic corpus
>   (`tests/data/sync_corpus/`), no longer skipped. See §4 for what shipped.
>
> Everything below is the **original brief, as written before the fix** (with
> 2026-06-24 line-anchor refreshes). Read it as the investigation trail, not as
> open work. The §2 hypothesis is preserved but **wrong** — see the box above.

---

> Status (historical): **investigation not started** — this document was the brief.
> Created 2026-06-23 while cutting the CLM 1.16.0 release; refreshed 2026-06-24
> against `master`. Author had full context; the investigator had none, so this
> was the sole source of truth.
>
> **Was re-confirmed on `master` before the fix (2026-06-24).** A run on PR #468
> independently hit the *same* two failures against the live PythonCourses
> corpus and verified (via `git stash`) that they failed **identically on clean
> `master`** — i.e. after the entire #448 consistency-ledger program (P1/P2,
> PRs #463–#468) landed on top of 1.16.0. The ledger work touched
> `sync_plan.py`/`sync_verify.py`/`sync_apply.py` but did **not** address this
> bug. The line references in §2 are as of `master` on 2026-06-24.

---

## 1. Overview

There are **two intertwined problems**, and they should be fixed separately:

1. **A real sync bug (issue #443).** `clm slides sync` can violate the #269
   *propagate-or-alert* invariant: a **one-sided edit or removal of a keyed
   voiceover companion that shares its slide's `slide_id`** is **silently
   dropped** — the run reports the decks "already consistent" (edit case) or
   plans an unrelated `add` (remove case), instead of propagating the change to
   the other half or flagging it. Concretely, on the real deck
   `slides_pe_03a_chain_of_thought` (PythonCourses), the only `(slide_id,
   voiceover)`-keyed cell is a voiceover companion whose `slide_id` equals its
   slide's id (see §2). The engine loses a one-sided change to it.

2. **A process / CI-coverage gap.** The two tests that catch this
   (`tests/slides/test_sync_corpus_mutation.py::test_keyed_companion_remove_propagates`
   and `::test_keyed_companion_edit_is_judge_reconciled`) are `slow`+`integration`
   and **`skipif` the corpus is absent** — they need a local `PythonCourses`
   checkout, which **CI and fresh clones do not have**. So they are **silently
   skipped in CI**, never gate a PR, and only surface when someone runs the full
   `pytest -m "not docker"` locally with a course corpus present (i.e. during a
   release). That is exactly how this became a release-gate detour: the 1.16.0
   pre-release suite showed `2 failed, 8748 passed`, and ruling out
   "is this a regression?" cost a controlled v1.15.0 re-run.

**Why it matters.** (1) is a genuine data-loss path on a real authoring
pattern (every voiceover deck has slide-keyed companions). (2) means our most
realistic sync oracle — mutation testing over *real* decks — contributes
**zero** signal in CI, so regressions in this exact area can land green.

**Not a regression — pre-existing across three engine generations.** Both
tests fail **identically** on (a) the released `v1.15.0` engine, (b) the 1.16.0
release (post epic #440/#442 verb-group re-cut), and (c) current `master` after
the #448 consistency-ledger program (P1/P2) — same deck, byte-identical plan
output. This is **pre-existing**, predating both the sync verb-group re-cut and
the ledger. It was shipped this way at least as far back as 1.15.0. The
`sync_plan.py` changes since 1.16.0 are the ledger MVP (#448) and
reflow-insensitive markdown hashing (#429); **none touch the keyed-companion
edit-detection path** that this bug lives in.

Links: issue **#443** (filed 2026-06-23). Related: #269 (propagate-or-alert
origin), #199 (`vo_anchor` positional anchors for voiceover narratives), #403
(occurrence-not-position narrative identity), #366/#440 (the agent-first sync
redesign that shipped in 1.16.0), #448 (the consistency-ledger trust overlay
that landed on top of 1.16.0 and re-confirmed — but did not fix — this bug).

---

## 2. Key finding: where the bug almost certainly lives

The `--verify` check used to false-fail on companion decks because it keyed on
the **bare `slide_id`**; commit `7a73434f` fixed it to key on **`(slide_id,
role)`** (see `src/clm/slides/sync_verify.py`, `_slide_id_role_list` /
`_duplicate_id_violations`). Its commit message states the governing rule:

> the engine reconciles cells **per `(slide_id, role)`** (`role_of`) … a slide
> and its `voiceover`/`notes` companion legitimately **share** a `slide_id`
> under different roles by design.

So `(slide_id, role)` is the *intended* key. **But the keyed edit/remove
diff is not the only path.** In `src/clm/slides/sync_plan.py`, voiceover/notes
narratives are **diverted out of the coarse `(slide_id, role)` keyed
edit-detection** and handled by **positional `vo_anchor` anchors** (Issue #199 /
#403 Phase B). See the module's own comments around (line numbers as of
2026-06-24 `master` — they drift; grep the quoted phrases if they've moved):

- **lines ~83–98** — "the per-cell engine does not own (`role_of` is `None`) …
  each positioned by a `vo_anchor` rather than keyed by the … edit detection;
  they are diverted out of the coarse `(slide_id, role)` keyed …"
- **lines ~445–521** — `_anchor`-mapping (`Map each narrative cell's raw index
  to (owning_slide_id, anchor_token)`) → the classifier keys it by
  `(owning_slide_id, role, anchor)`; the membership-widened watermark row gives
  a membership-only cell a synthetic role.
- **lines ~580–584** — "detect an *edit* to one of several narratives under a
  single slide (which the coarse `(slide_id, role)` key [cannot distinguish])".

**Hypothesis to test first:** the keyed voiceover companion in
`slides_pe_03a_chain_of_thought` is routed through the **voiceover-narrative
(positional-anchor) path** rather than the keyed `(slide_id, role)` edit path,
and that narrative path does **not** raise a one-sided edit/removal as a keyed
change — so it falls through to "in sync" (edit) or a structural `add` (remove)
with no alert. The `--verify` fix (`7a73434f`) corrected the *duplicate-id*
check for the companion pattern but did **not** touch the **plan/apply
edit-detection** for it. The fix for #443 likely means extending the same
`(slide_id, role)` (or `(slide_id, role, anchor)`) discipline into the
plan/apply narrative path, *or* making the narrative-diversion path emit a
proposal/issue for a one-sided keyed-companion change.

The deck's triggering structure (DE half):

```
# %% [markdown] lang="de" tags=["subslide"]  slide_id="when-cot-is-not-needed"   # the slide
# %% [markdown] lang="de" tags=["voiceover"] slide_id="when-cot-is-not-needed"   # its keyed companion (the mutation target)
```

i.e. a slide and its voiceover companion **share** `when-cot-is-not-needed`
under roles `subslide` vs `voiceover`. All the *other* voiceover cells in the
deck are **id-less** (handled purely positionally); this is the one keyed one,
so it is the only `vo` cell the test selects.

---

## 3. Established facts (evidence, so you don't re-derive them)

- **Deterministic, not nondeterministic.** Despite being called a "flake" in
  prior notes, these failures reproduce **every run**, in **~5 s**, on the
  **same deck**. The "flakiness" is really *CI-invisibility + corpus-dependence*,
  not timing/order nondeterminism. Treat them as a deterministic real-bug
  detector.
- **Reproduces on v1.15.0.** A throwaway `git worktree` at `v1.15.0`, same
  corpus, produced byte-identical failing plans:
  - remove → `1 add, 0 edit, … 0 remove … ; 24 in sync` → `assert propagated or
    _alerted` fails (neither).
  - edit → `0 changes — decks already consistent (25 cell(s) in sync)` →
    `_falsely_consistent` (silent drop).
- **The test file is unchanged since v1.15.0** (`git log v1.15.0..HEAD --
  tests/slides/test_sync_corpus_mutation.py` is empty). So only the *engine* and
  the *corpus* differ over time — and the engine didn't change the outcome.
- **Re-confirmed on post-#448-ledger `master` (PR #468, 2026-06-24).** A second,
  independent run hit the *same* two failures and verified via `git stash` that
  they fail identically on clean `master` — after the whole consistency-ledger
  program (P1/P2) landed. So three engine generations (1.15.0 → 1.16.0 → ledger
  `master`) all drop the mutation. Not a regression introduced by any of them.
- **Other decks in the same buckets pass** — the failure is specific to the
  shared-id companion structure, not to keyed companions in general.

---

## 4. Phase breakdown

### Phase A — Root-cause and fix the sync bug (#443) — ✅ DONE (PR #471)
> **Outcome.** Root cause was the **asymmetric (id-less DE ↔ id'd EN) companion**,
> not the `vo_anchor` path this section hypothesised. Fixed by
> `_alert_asymmetric_companion_drift` (alert, not propagate). Bundled fast tests
> `test_issue443_*` in `tests/slides/test_sync_limitations.py`. See the RESOLVED
> box at the top. The original goal/components/acceptance are kept below as written.

- **Goal.** A one-sided edit *or* removal of a keyed voiceover/notes companion
  (sharing its slide's id) is propagated to the other half, or alerted
  (deferred / issue), never silently dropped.
- **Components.** `src/clm/slides/sync_plan.py` (the narrative-diversion / keyed
  edit-detection paths in §2), `src/clm/slides/sync_apply.py`
  (`apply_plan`), `src/clm/slides/sync_writeback.py` (`role_of`),
  `src/clm/slides/sync_recover.py` / `sync_code.py` as the propagation
  reaches them.
- **Acceptance.** The two `test_sync_corpus_mutation` tests pass on the corpus;
  add a **bundled, minimal fixture** reproducing the shared-id-companion
  structure as a **fast unit test** (no external corpus) so the regression is
  caught in CI forever (this is the bridge to Phase B). **Model it on
  `tests/slides/test_sync_limitations.py`** — that file (added with the #448
  ledger work) is the existing precedent: tiny synthetic `.de.py`/`.en.py`
  decks, fast suite, no corpus, counting stand-in translator/judge, driving
  `build_sync_plan` + `apply_plan` directly. It already documents two other
  engine limitations the same way. `clm slides sync verify` stays green; no
  #269/#282 regressions (run the existing sync suites).

### Phase B — Close the CI-coverage gap — ✅ DONE
> **Outcome — Option 1 (committed synthetic corpus), as recommended.** Added
> `tests/data/sync_corpus/` — one bilingual deck (`deck_features.{de,en}.py`)
> in post-sync-clean shape carrying all four mutation cell classes (neutral
> shared code, id-less localized markdown, a **shared-id** voiceover companion,
> keyed markdown). `_corpus_dir()` in `test_sync_corpus_mutation.py` now returns
> `(root, is_bundled)` and falls back to this corpus when no real PythonCourses
> checkout is found (env / maintainer path still win, preserving release-time
> realism). The `skipif` is gone.
>
> **Second blocker found and fixed:** the tests were also `slow`, and **every CI
> job excludes `slow`** (`ci.yml`: fast/integration/e2e all carry `and not
> slow`). So un-skipping alone would not have run them in CI. The `slow` marker
> is now **conditional** — applied only on the real corpus (genuinely slow, and
> CI doesn't have it), dropped on the tiny bundled corpus (~2 s) so the
> **`integration` CI job runs all six mutation tests**. Verified: all 6 pass
> against the bundled corpus in ~2 s; the real-corpus path is unchanged
> (`slow`+`integration`, still in the `-m "not docker"` release gate).

- **Goal.** Mutation-oracle coverage of the real sync invariants runs **in CI**,
  not only on a developer's machine at release time.
- **Options to weigh (pick + justify):**
  1. **Commit a tiny synthetic corpus** under `tests/data/` — a handful of
     `*.de.py`/`*.en.py` pairs engineered to carry each mutation's cell class
     (neutral, idless-localized, keyed voiceover incl. the **shared-id**
     companion, keyed markdown). Point the corpus discovery at it as a
     **default fallback** so the tests run unconditionally (drop or downgrade
     the `skipif`). This is the highest-value fix: it makes the oracle a real CI
     gate without depending on PythonCourses. (`test_sync_limitations.py`
     already proves the synthetic-deck approach works in the fast suite — reuse
     its deck builders rather than inventing new ones.)
  2. **Keep the live-corpus run but make its absence loud** — e.g. an
     `xfail(strict)` or an explicit `pytest.fail` in CI if the corpus is missing
     *and* a flag says it should be present, so "skipped" can't masquerade as
     "passed". Weaker than (1); use only if a synthetic corpus proves
     impractical.
  3. **A nightly/opt-in CI job** that checks out a pinned PythonCourses commit
     and runs the corpus suite. Heaviest; cross-repo coupling; last resort.
- **Acceptance.** A green CI run demonstrably *exercises* (not skips) the
  shared-id-companion mutation. Whatever is bounded/sampled is `log`-stated, not
  silent.

### Phase C — audit sibling oracles — ✅ DONE
> **Outcome — split decision.** `tests/slides/test_sync_corpus_noop.py` had the
> same `slow`+`skipif`-on-corpus gap, so it gets the same `(root, is_bundled)`
> fallback + conditional `slow`. **But** its assertions are two kinds: a
> *scale-independent invariant* (`test_noop_plans_apply_with_zero_bytes_and_zero_llm`
> — a no-op plan must write zero bytes / make zero LLM calls) and *corpus-scale
> measurement floors* (`total_pairs >= 100`, `item2_population > 1000`, the
> no-op-pair floor — all tuned to the 212-pair real corpus). The floors would be
> **vacuous and misleading on one synthetic pair**, so they stay gated to a real
> corpus via `@_real_corpus_only` (`skipif(_BUNDLED)`); only the invariant runs
> on the bundled corpus in CI. That closes the safety gap (a "nothing to do" plan
> that nonetheless writes is caught in CI) without faking the measurement floors.
> Verified: the invariant passes on the bundled corpus; the real-corpus run is
> unchanged. No other `CLM_SYNC_CORPUS_DIR` / corpus-`skipif` users remain (grep
> confirmed only these two files).

- `tests/slides/test_sync_corpus_noop.py` (the no-op backstop) uses the **same**
  `skipif`-on-corpus pattern and is therefore **also CI-invisible**. Decide
  whether it should ride on the Phase B synthetic corpus too. Grep for other
  `CLM_SYNC_CORPUS_DIR` / corpus-`skipif` users.

---

## 5. Current status

- **Nothing fixed.** This is a fresh investigation; no code changed for #443.
- **1.16.0 shipped** with the bug present (knowingly, as a non-regression). The
  release recorded the detour and filed #443; see the project memory
  `project_issue_364_366_sync_coupling.md` (final paragraphs) and
  `MEMORY.md`.
- **The #448 consistency-ledger program (P1/P2) has since landed on `master`**
  (PRs #463–#468) **without addressing this bug** — PR #468 re-confirmed the
  failures persist. So the ledger is orthogonal; do not expect it to be part of
  the fix (but Phase B's CI-gating goal still stands regardless).
- **Open question for Phase A:** is the correct behavior to *propagate* a
  one-sided keyed-companion edit (re-translate / judge-reconcile, as the test's
  `edit_is_judge_reconciled` name implies) or to *defer/alert*? The test asserts
  "propagated **or** alerted", so either satisfies #269 — but pick the behavior
  consistent with how other keyed cells are handled, and confirm with the
  voiceover-anchor invariants (#199).

---

## 6. Next steps (do this first)

1. **Reproduce.** From a worktree with a synced venv and the PythonCourses
   corpus present:
   ```bash
   uv run pytest \
     "tests/slides/test_sync_corpus_mutation.py::test_keyed_companion_remove_propagates" \
     "tests/slides/test_sync_corpus_mutation.py::test_keyed_companion_edit_is_judge_reconciled" \
     -m "not docker" -n 0 -rA --tb=long
   ```
   (`-n 0` runs serially; **`-m "not docker"` is required** — without it the
   default marker filter *deselects* these `slow`+`integration` tests and you'll
   see "2 deselected", a foot-gun that already cost time once.)
   Override the corpus path with `CLM_SYNC_CORPUS_DIR=<dir>` if needed; default
   is `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides`.
2. **Trace one mutation by hand.** Copy the failing pair into a temp dir and
   call the engine directly, mirroring the test's `_sync_pair` helper
   (`_record_watermark(de,en)` → write mutated EN → `build_sync_plan(de, en,
   watermark_cache=wm)` → inspect `plan`). Add prints/breakpoints in
   `sync_plan.py` to see whether the keyed companion enters the keyed
   edit-detection path or the `vo_anchor` narrative path, and where its change
   is dropped.
3. **Confirm the hypothesis in §2**, then design the fix (extend `(slide_id,
   role)`/anchor discipline into the plan path, or emit a proposal/issue from
   the narrative-diversion path).
4. **Write the bundled fast unit test** (Phase A acceptance) before/with the
   fix, so Phase B can lean on it.

**Gotchas**
- The corpus tests drive the **engine API** (`build_sync_plan` + `apply_plan`
  with an injected `judge`), **not** the CLI verb group — so the 1.16.0
  model-free-`apply` policy is irrelevant here; the judge is supplied directly.
- Don't "fix" the test to make it pass — it is the oracle. The bug is in the
  engine.
- A worktree needs its **own** `uv sync --extra all`; a root `.venv` resolves
  `clm` to the main repo. (See `MEMORY.md` → "Worktree venv sync".)
- Don't switch a worktree to literal `master`; reset its own branch off
  `origin/master`.

---

## 7. Key files & architecture

| File | Role in this investigation |
|---|---|
| `tests/slides/test_sync_corpus_mutation.py` | The failing oracle. `_corpus_dir()` (corpus discovery + `CLM_SYNC_CORPUS_DIR`), `pytestmark = [slow, integration, skipif(corpus absent)]`, `corpus_pairs` fixture (selects post-sync-clean pairs per cell class), `_sync_pair` (engine driver), `_run_mutation` (the propagate-or-alert assertion, ~L290–323), the two failing tests at ~L368 / ~L397. |
| `tests/slides/test_sync_corpus_noop.py` | Sibling oracle, **same `skipif` gap** (Phase C). |
| `tests/slides/test_sync_limitations.py` | **The model for Phase A's bundled fast test** (added with #448). Tiny synthetic decks, fast suite, no corpus, counting stand-in translator/judge; documents two *other* engine limitations the same way. Reuse its deck builders. |
| `src/clm/slides/sync_plan.py` | `build_sync_plan`; the **keyed `(slide_id, role)` edit-detection** vs the **`vo_anchor` narrative-diversion** paths (§2 line refs). Prime suspect. Changed since 1.16.0 only by #448 (ledger) + #429 (reflow hash) — neither touches this path. |
| `src/clm/slides/sync_apply.py` | `apply_plan`; where a detected change is written / deferred. |
| `src/clm/slides/sync_writeback.py` | `role_of` — the `(slide_id, role)` role assignment (voiceover/notes/code/markdown). |
| `src/clm/slides/sync_verify.py` | The **precedent fix** (`7a73434f`): `_slide_id_role_list`, role-aware `_duplicate_id_violations`. Shows the intended keying; the plan/apply path likely needs the same. |
| `src/clm/slides/sync_ledger.py` | The #448 per-slide **trust overlay** (landed after 1.16.0). A `--ledger` overlay, **not** part of the keyed edit-detection — it did not fix #443. Understand it only so you don't mistake it for the diff path. |
| `src/clm/slides/sync_recover.py`, `sync_code.py` | Reconciliation/translation the propagation flows into. |

Entry points: CLI `clm slides sync` (verb group, `src/clm/cli/commands/slides/sync.py` + `sync_autopilot.py`) → engine `build_sync_plan` → `apply_plan`. The corpus tests bypass the CLI and call the engine directly.

---

## 8. Testing approach

- **Reproduce** with the command in §6.1 (`-m "not docker" -n 0`).
- **Phase A regression test** must be a **bundled fast unit test** (synthetic
  `.de.py`/`.en.py` strings or a tiny `tests/data` fixture) that does **not**
  depend on the external corpus — that is the only way the fix is protected in
  CI. Model it on `_sync_pair` (seed watermark → mutate one side → assert
  propagated-or-alerted).
- **Regression guard:** run the broader sync suites after any engine change:
  ```bash
  uv run pytest tests/slides -m "not docker" -n 8 -q          # full slides incl. the corpus oracle (corpus present)
  uv run pytest -k "sync" -m "not docker" -n 8 -q             # sync-keyworded sweep
  ```
  Watch for `--verify`, #269 (`test_sync_drops*`), #282, and `vo_anchor`/#199
  voiceover-placement tests.
- **Pre-release sanity:** the full gate is `uv run pytest -m "not docker"`
  (Docker tests are CI-only). After Phase B, this should run the mutation oracle
  unconditionally rather than skipping it.

---

## 9. Session notes

- The word **"flaky"** in the original ask (and in an earlier memory line) is a
  misnomer worth correcting with the user: these are **deterministic** failures
  that are merely **invisible to CI**. The fix is not "quarantine a flake"; it's
  "fix a real bug **and** make the oracle a real CI gate."
- This surfaced because CLM treats `pytest -m "not docker"` (which *includes*
  `slow`+`integration`) as the pre-release gate, while the PR gate (the fast
  suite + CI) excludes these. So the only time the corpus oracle runs is at
  release — the worst possible time to discover a multi-hour detour. Phase B is
  what stops that from recurring.
- For the release decision itself: the authoritative publish gate is **CI green
  on the released commit** (the release workflow re-checks it). These tests are
  not in that gate, and the failures were proven non-regressions, so 1.16.0
  shipped correctly. But that reasoning shouldn't have to be re-done every
  release — hence Phase B.
- Full context on the #440/#442 sync redesign that shipped in 1.16.0 lives in
  the project memory `project_issue_364_366_sync_coupling.md` (very detailed,
  per-phase) and the design notes under `docs/claude/design/` (esp.
  `sync-agent-toolkit-redesign.md`). Read those before assuming how the engine
  is *supposed* to key cells.
