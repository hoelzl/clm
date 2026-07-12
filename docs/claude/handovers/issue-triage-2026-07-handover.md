# Open-Issue Triage & Execution Plan (2026-07-10) — Handover

**Status**: Triage COMPLETE; execution IN PROGRESS — Phases 1–5 DONE
(#600, #539, #524/#382/#362, #568, #559); **Phase 7 fully DONE**
(#609/#610/#611 via PRs #624/#625/#626 on 2026-07-11; #615 landed via
PR #628, merged 2026-07-11 — see the Phase 7 block); **Phase 8 DONE**
(#620 job session ownership + #617 orphan-pool-shutdown, 2026-07-12 — see
the Phase 8 block). Next up **Phase 6 (design tier)** — start with the
#383+#381 verify-then-close pass. This document is the source of truth for
working through the open-issue backlog in priority order. Update phase
statuses here as issues land.

## 1. Feature Overview

A full triage of every open issue in `hoelzl/clm` performed on 2026-07-10
(13 open at the time), producing: (a) two stale issues closed with evidence,
and (b) a sequenced execution plan for the remaining 11. The point of this
document is to preserve the *investigation insights* — several issues are
subtler than their titles suggest, and one is actively blocking ongoing
dogfooding work.

- Repo: https://github.com/hoelzl/clm/issues
- Issues closed during triage: #165, #501 (see §4)
- Issues planned: #600, #539, #524, #382, #362, #568, #559, #484, #383,
  #381, #167

## 2. Design Decisions

**Triage-level decisions made (with rationale):**

- **Closed #165 (mitmproxy transport) as completed.** The migration it
  proposed shipped via issue #355: PR #357 removed the in-process vcrpy
  transport (stage 1), PR #358 dropped the vcrpy dependency and vendored the
  cassette format into `src/clm/infrastructure/http_replay_mitm/vcr_format.py`
  (stage 2). mitmproxy is the sole transport. Verified in `git log` and
  `pyproject.toml` (only historical comments mention vcrpy).
- **Closed #501 (sync ignores voiceover companions) as completed.** All
  phases merged: PRs #505/#510 (groundwork), #513 (Phase 1 detection/read
  modes), #515 (Phase 2 companion write-back), #518 (Phase 3 companion-aware
  bless + ledger parity). Maintainer decision comment on the issue records
  the wholly-inline-or-wholly-sidecar invariant.
- **#600 ranked first** because it dead-ends the agent sync loop that is
  being actively dogfooded (filed 2026-07-10 from a real PythonCourses
  session), and the fix direction is already clear.
- **#383 and #381 treated as one design effort.** #383 (default
  shared/trainer/speaker output structure) subsumes #381 (build/git speaker
  asymmetry) — implementing #383 makes #381 disappear. Related #380 is
  already CLOSED. Breaking default change → design doc first.
- **#568 scoped to fix 1 only** (shared content-addressed transcript cache);
  the issue's fixes 2–4 (transition-stage sharing, batch warmup, frame
  stride) are deferred follow-ups. Rationale: fix 1 alone eliminates the
  dominant cost (~3.5 h avoidable GPU transcription per harvest task) and
  harvest dogfooding on real recordings is the next active project step.
- **#167 (LLM model-selection spike) deprioritized to last**, timeboxed
  (~half a day); the issue itself says "leave as-is" is a valid outcome.
  If pursued, option 2 (internal purpose→model registry over the existing
  OpenAI-compatible client, no new dependency) is the recommended shape.

## 3. Phase Breakdown

Phases are execution waves, not strict dependencies — waves 2–5 can be
reordered if priorities shift. Everything is currently [TODO].

### Phase 1 [DONE] — #600: sync `ambiguous_alignment` dead end (ACTIVE PRIORITY)

**Resolution (2026-07-10)**: implemented as a NEW framed action `stamp_vs_new`
(not answers on `ambiguous_alignment` itself — the design note's §8 watch-item
prescribes "redesign the action" over shape-conditional vocabularies, and the
rival-id-stamp shape is structurally indistinguishable from the resolvable
shape, so advertising `treat_as_new` on `ambiguous_alignment` would offer a
content-duplicating answer there). Both the id-view and pos-view rows frame
`stamp_vs_new` with answer `treat_as_new`: grows the twin verbatim (id row) /
mirrors the removal (pos row, rejected if the survivor moved off base).
`treat_as_stamped_edit` was NOT added — binding is ambiguous when several id'd
cells claim one base (the issue's own 2-for-1 case); the manual stamp-by-hand
path covers it. Partial answering converges (tested). §13 amendments row
added. Shipped on branch `claude/issue-600-stamp-vs-new`.

**Problem**: Replacing an un-id'd positional cell with new `slide_id`-keyed
cells makes `clm slides sync report` frame each new cell AND the vanished
positional base as `outcome: conflict, action: ambiguous_alignment` with an
**empty `answers` vocabulary** — no decision is acceptable to
`apply --decisions`, so the agent loop is stuck until files are edited
out-of-band. (Labels: bug, area:sync, agent-impact, has-workaround.)

**Fix direction (from the issue, agreed sensible)**: give
`ambiguous_alignment` an answer vocabulary:
- `treat_as_new` — positional cell was genuinely removed; route the id'd
  cells through the normal `copy_new_shared`/`translate_new` twin-growing
  path, and resolve the vanished positional members as removals. This alone
  unblocks the common case.
- Optionally `treat_as_stamped_edit` — bind the new id'd cell to the
  unaccounted positional base (the heuristic's suspicion).

**Acceptance**: the issue's exact scenario (positional cell → two new id'd
cells on one side) resolves through `report` → decision doc → `apply
--decisions` → `verify` passes, with clean ledger records; no empty
`answers` arrays in report output for this shape.

**Gotchas (MUST READ FIRST)**:
- This is v3 ledger-engine territory. Read memory topics
  `project_sync_one_sided_cold` (issue #566: one-sided id-keyed new →
  translate_new/copy_new_shared, PR #567; confirm rejected on one-sided
  members) and `project_sync_v3_design_audit` before touching the engine.
- The sync v3 design note has a **§13 amendments log — add a row for ANY
  engine change** (established 2026-07-10, PR #590).
- Documented workaround exists (hand-mirror the structural change with
  slide_id parity into the other half, then `record_remove` +`confirm`) —
  the fix should not break that path.

### Phase 2 [DONE] — #539: duplicate dir-group destination copytree race

**Resolution (2026-07-10)**: the whole-group dedup had already landed on
master (commit `318bfdd5`, the PR #556/#563 arc) but did NOT cover the
issue's actual shape — two same-named dir-groups that merely *overlap* in
one `<subdir>` produce different whole-group keys and still raced. Fixed by
making the dedup per copied directory (`DirGroup._without_seen_copies`:
one key per `(destination dir, source dir, recursive)` pair plus one per
root-file batch; the operation gets an `attrs.evolve`d dir-group with only
the not-yet-covered pairs). Different-source-same-destination pairs are
deliberately NOT deduped (spec conflict — stays visible to the write
registry), but `clm validate` now warns about both shapes as
`duplicate_dir_group_destination` (`_validate_dir_group_destinations` in
`src/clm/slides/spec_validator.py`). Note: dir-group `<name>` parsing is
monolingual (`element_text` ignores `<de>/<en>` children), so per-language
collisions are currently unreachable via real specs.

**Problem**: Two `<dir-group>`s resolving to the same output destination →
two concurrent `shutil.copytree(src, same_dest, dirs_exist_ok=True)` per
target → WinError 32 aborts the whole build at the final dir-group stage,
discarding an otherwise-complete ~13-minute build. Nondeterministic (race).

**Root cause (verified in issue)**: `Course.process_dir_group_for_targets`
(`src/clm/core/course.py`) fans out one op per (dir-group × target) into a
single `TaskGroup`; `DirGroup.get_processing_operation`
(`src/clm/core/dir_group.py`) wraps lang × is_speaker into `Concurrently`.
Nothing dedupes by **resolved destination**;
`local_ops_backend._copy_dir_group_to_output_sync` has no retry.

**Fix direction**: dedupe copy operations by resolved destination path
before the fan-out (first one wins — the copies are identical by intent),
plus a `clm validate`-time warning when two dir-groups resolve to the same
destination for a target.

**Acceptance**: a spec with the same `<subdir>` under two same-named
dir-groups builds cleanly; stress-repeat passes on Windows; validate warns.

### Phase 3 [DONE] — Quick-win batch (3 small PRs, one session)

**Resolution (2026-07-10)**:

- **3a (#524)**: shipped (PR #607) — combined the issue's directions 2+3.
  pyproject.toml pins the full canonical `2026-05-29T00:00:00Z` (exact
  normalization of the old bare date, cutoff unchanged), uv.lock re-locked
  (one-line diff), `update_exclude_newer.py` canonicalizes bare dates to
  `<date+1>T00:00:00Z` (full timestamps pass through),
  `check_exclude_newer.py` is exact-equality, and CI moved back from
  `uv sync --frozen` to `--locked` in all three jobs. The profile half
  needed NO change: `Sync-UvExcludeNewer` is parked (`disabled.ps1`, not
  loaded) and already mirrors the pyproject value verbatim.
- **3b (#382)**: closed as **already shipped** — the minimum fix (warning in
  `GitHubSpec._warn_on_unrecognized_children`, migration info-topic note,
  tests in `tests/cli/test_git_ops.py`) landed in commit 90518611 and was
  released in clm 1.15.0 (2026-06-21); the issue was simply never closed.
  NOTE for Phase 6a: that same commit claims #381 and #383 too (default
  shared/trainer/speaker structure, per-tier remote-path) — Phase 6a should
  START with a verify-then-close pass against those issues' asks instead of
  a fresh design effort.
- **3c (#362)**: maintainer picked **Option A** — `end-workshop` is honored
  on any cell type with end-**exclusive** semantics (the tagged cell is NOT
  part of the workshop; tagging the workshop's last code cell excludes it,
  which for the issue's `keep`-tagged assertion cells renders identically).
  Both range scanners changed (`workers/notebook/output_spec.py` +
  `slides/workshop_scope.py`); openers stay markdown-only; tag moved into
  `EXPECTED_GENERIC_TAGS`; validator extends the orphan warning to code
  cells; slide-format/spec-files info topics document the exclusion nuance.
  Known edge (accepted): per-language *paired* code cells tagged
  asymmetrically aren't covered by normalize/validate symmetry (those only
  pair headings) — conventionally code cells are shared, so split builds
  see the tag in both halves.

**3a — #524: uv.lock exclude-newer timestamp mismatch.** Three
representations are in play: pyproject bare date `2026-05-28`; uv.lock
`2026-05-28T22:00:00Z` (local-midnight from profile's `Sync-UvExcludeNewer`);
uv's own normalization `2026-05-29T00:00:00Z` (next-UTC-midnight). So
`uv sync --locked` / `uv lock --check` always fail on a clean checkout
(CI currently masks this with `--frozen`). Fix: standardize on uv's
canonical form — update `scripts/update_exclude_newer.py` and the profile's
`Sync-UvExcludeNewer` to write `<date+1>T00:00:00Z`, and make
`scripts/check_exclude_newer.py` do an **exact** comparison (today it's
`startswith`, which is why the drift guard misses the mismatch). Consider
pinning the full timestamp in pyproject to leave zero ambiguity. Unblocks
lock-based drift detection in CI (issue #516 follow-up). Note: the profile
script lives outside the repo — coordinate with the user for that half.

**3b — #382: `<github><de>/<en>` silently ignored.** Old clx-era spec form
drops `<de>`/`<en>` children with no warning → `derive_remote_url()` returns
`None` → `clm git init` silently creates local-only repos. Minimum fix: warn
(or actionable error naming `<project-slug>` + `<repository-base>` +
`<remote-template>`) on unrecognized `<github>` children in
`GitHubSpec.from_element()` (`src/clm/core/course_spec.py:597-618`). Add a
migration note to `src/clm/cli/info_topics/migration.md` (info-topic rule!).
Honoring the old form as real per-language remotes: defer / fold into #383.

**3c — #362: `end-workshop` rejected on code cells.** **NOT the one-line
allowlist fix the issue suggests** — key triage insight:
`find_workshop_ranges` (`src/clm/workers/notebook/output_spec.py:39-64`)
**skips non-markdown cells entirely**, so `end-workshop` on a code cell is a
*silent no-op* today; the validator warning is correctly guarding that. The
markdown-only restriction is documented as intentional in
`src/clm/slides/tags.py` (comment landed with the tag itself in b92b8d89,
i.e. it predates the issue — it is not a post-issue decision). Real fix
needs a semantics decision from the maintainer first:
- Option A: honor `end-workshop` on any cell type in `find_workshop_ranges`
  (keep end-**exclusive** semantics) + add to code-cell allowlist + update
  info topics. Watch out: the issue's example tags the workshop's *last*
  code cell, which under exclusive semantics **excludes that cell from the
  workshop** — for their `assert` cell that may actually be desired
  (assertions shown completed), but it must be documented explicitly.
- Option B: keep markdown-only, improve the validator message to explain
  that the tag marks the first cell *after* the workshop and is
  markdown-only by design.
Both code sites: `src/clm/slides/tags.py` (allowlists + STRUCTURAL_TAGS
comment) and `find_workshop_ranges` (also duplicated semantics description
at `output_spec.py:413-423`); `clm slides normalize` symmetrization and
`src/clm/slides/workshop_scope.py` may also key on cell type — check before
changing.

### Phase 4 [DONE] — #568: shared ASR transcript cache (before next harvest round)

**Resolution (2026-07-11)**: implemented as the handover's second option,
generalized — the **whole** voiceover cache root (all four stages, not just
transcripts) moved from `<deck dir>/.clm/voiceover-cache/` to a shared
`<shared-cache-dir>/voiceover/`, where the shared cache dir resolves exactly
like the LLM cache (`$CLM_CACHE_DIR` → `tool.clm.cache_dir` →
`<project-root>/.clm-cache/`). Safe because timeline/alignment keys already
include the slides hash; transitions sharing (part of fix 2's benefit) falls
out for free. Root discovery walks up from the **deck directory** via a new
`start=` anchor on `describe_cache_dir` (keeps worktree re-anchoring active,
unlike an explicit `repo_root`). Legacy per-deck entries are probed
read-only on a shared-root miss and **promoted** into the shared root
(automates the issue's manual-copy workaround; existing GPU transcripts
survive the move; `--refresh-cache` skips the probe). `.clm-cache` added to
`SKIP_DIRS_FOR_COURSE` per the gotcha below. Docs: commands/harvest-agents/
migration/spec-files info topics, harvest.md + configuration.md user guides,
changelog fragment. Fixes 2–4 remain deferred. Shipped from worktree
`starry-nibbling-swing`.

**Problem**: harvest caches ASR transcripts under
`<deck dir>/.clm/voiceover-cache/transcripts/<video-fingerprint>.json` —
per-deck-directory, so forked/moved decks re-transcribe identical videos
(measured ~3.5 h avoidable GPU time for 11 forked W11 decks; 10–40 min per
large video).

**Fix (scope: fix 1 of the issue only)**: content-addressed,
deck-independent transcript cache keyed by video fingerprint in a shared
root — mirror the LLM cache's `[tool.clm] cache_dir` treatment (or default
`--cache-root` to a repo-level `.clm-cache/voiceover/`). The issue's
manual-copy workaround (copying `<fingerprint>.json` between deck caches
made fork harvests 3–5× faster) *is* this fix, generalized — high confidence.
Defer fixes 2–4 (split transition-detect from slide-matching; batch warmup;
frame stride) to follow-up issues.

**Gotchas**: memory topic `project_video_narration_harvest` has §6 ledger +
v3-native-verify LANDMINES; `.clm/` is fully skipped as build input
(PR #432) — a repo-level shared cache dir must get the same treatment.

### Phase 5 [DONE] — #559: CI matrix split (needs a quiet-repo window)

**Resolution (2026-07-11)**: shipped via PR #622 in a quiet-repo window
(zero other PRs in flight). The test job's matrix is now
`python-version × suite` (unit/integration/e2e → 6 jobs); each suite step
gates on `matrix.suite` **and** `needs.changes.outputs.code`, preserving
the docs-only skip pattern (required jobs always run and report).
`--cov-append` stitching removed — each 3.12 suite job uploads its own
report and Codecov merges per commit. The "Require CI green" ruleset
(id 17358657) was updated via `gh api PUT` while the PR's CI ran — the new
job names report from the PR branch's workflow, so pushing the PR first
and swapping the ruleset before merge is the safe ordering. Measured on
the PR run: unit 4m22s is the longest test leg, total wall clock bounded
by Docker Integration Tests at 5m30s (down from ~7.5 min) — matching the
issue's prediction. `release.yml` needed no change (it gates on the CI
workflow *run* conclusion, not job names). Follow-up levers (unit-suite
duration, Docker image registry caching) remain out of scope per the issue.

**Original plan (kept for reference)**: split the sequential
unit→integration→e2e steps into a `python-version × suite` matrix (6 jobs).
Measured: per-job setup is ~40 s (warm caches), so the split costs
~3 machine-min and cuts PR wall clock ~7.5 → ~5.5 min. **The critical
gotcha: required status checks are matched by job NAME.** "Test on Python
3.12/3.13" are required in the "Require CI green" ruleset; the matrix
renames them, so the **ruleset must be updated atomically with the
workflow** (six entries) or every PR sticks on "Expected — waiting for
status".

## 3a. Post-Triage Issues (folded in 2026-07-11)

Six issues were filed after the 2026-07-10 triage, all from active
dogfooding (PythonCourses ML-AZAV sync/build, CppCourses migration).
Re-prioritized against the remaining backlog using the original triage's
logic — agent-loop dead ends and destructive defaults outrank
intrinsically bigger work. **Execution order: Phase 7 → Phase 8 →
Phase 6.** (#614 is deferred like the rest of #568's fixes 2–4 that it
tracks: schedule it just before the next big harvest round.)

### Phase 7 [DONE] — Sync agent-loop regressions + normalize gate (#609, #610, #615, #611)

**Resolution (2026-07-11)**: all four shipped. #609, #610, #611 via PRs
#624, #625, #626; **#615 via PR #628** (merged 2026-07-11 from branch
`claude/issue-615-tag-parity-design`, which another session had scoped as a
root-cause analysis + design, then completed to a full fix). #615 fix
(four layers): tag parity is now an orthogonal aspect row in the v3 differ
— one attributable mover frames a mechanical `mirror_tags`; both-moved /
baseline-carried / incomplete-baseline divergences frame a new
`conflict_tags` (mirrors only the chosen side's tag set, suppresses the
member's other rows for the pass); apply/ledger never blesses members with
unresolved sibling rows (new honest `deferred` record status);
`sync verify` gains a non-gating `tag-parity` warning with three-tier
pairing; `sync-agents`/`commands` info topics + design doc
`docs/claude/design/sync-tag-parity-conflicts.md` + changelog fragment.
Decks damaged by the old banking behavior re-frame automatically — no
ledger migration. Shipped with 38 new tests + a post-implementation
adversarial multi-agent review (11 findings fixed).

- **#609 (PR #624)**: root cause — the `id:title` member is a *single-line
  j2 macro cell*: its `# {{ header_de(…) }}` line is simultaneously the
  cell boundary AND the whole content, so `_validate_body`'s delimiter
  guard rejected every valid answer, and `_replace_body` (which preserves
  `lines[0]`) explains the observed raw-line append. Fix: target-aware
  `_replacement_lines` in `doc_apply.py` — on a macro cell the `body`
  replaces the j2 line in place, accepting the full line or bare title
  text (spliced into the macro's quoted argument; bare form disabled on
  mint-a-new-cell paths which can't derive the right macro name). Also
  hardened the `conflict_shared`/`unify_choose_body` body path to compute
  both sides before mutating. §13 row added.
- **#610 (PR #625)**: implemented the issue's option 3 (minimum guard).
  Differ post-pass `_reframe_group_split_removals`: a pos-keyed
  `mirror_remove` whose gone-side base fp matches a one-sided cold cell of
  another group on that side is reframed as answerless
  `ambiguous_alignment` (+ a `suspected_group_split` observation) — the
  detail tells the agent to mirror the inserted slide (answer its
  `translate_new`) and re-report, which converges losslessly (tested).
  `ambiguous_alignment` joined `_POOL_FREEZING_ACTIONS` so a landing
  sibling row can't erase the two-sided base evidence. Options 1/2
  (fingerprint re-binding / apply-order re-evaluation) remain open as
  possible follow-ups. §13 row added.
- **#611 (PR #626)**: normalize's `interleaving` operation (the source of
  the within-file DE/EN `count_mismatch`/`similarity_failure` reviews AND
  the adjacency reorder — all meaningless in a single-language file) is
  skipped on split halves via `split_lang_suffix`, the validator's own
  signal; all other operations still run. `--dry-run` exits 0 on clean
  split decks.

Original phase plan (kept for reference):

- **#609 (first — hard dead end)**: `sync apply` on an `id:title`
  `translate_edit` rejects EVERY `body` answer with a spurious `# %%`
  delimiter error (the body contains none); `keep_twin` is the only
  accepted answer, which defeats the purpose. Dead end of the same
  severity class as #600. Labels: bug, area:sync, agent-impact,
  has-workaround.
- **#610 (destructive)**: inserting a new id-keyed slide *before* a run of
  un-id'd positional shared cells moves them into the new group on one
  half only → twin framed `mirror_remove` (mechanical apply DELETES the
  twin's untouched cells) + one-sided `verify_cold`. Related to but
  distinct from #600/`stamp_vs_new` (there the cells were replaced; here
  only the group boundary moved). A plain `apply` is data-destroying —
  rank above everything non-dead-end.
- **#615 (silent divergence)**: `confirm` on a `verify_translation` item
  banks a one-sided tag edit (DE `voiceover` vs EN `notes`); report goes
  silent and `sync verify` PASSes while `clm validate` flags the pair —
  sync and validate disagree about what "in sync" means. Correctness gap,
  not a dead end.
- **#611 (quick win, can be its own small PR)**: `normalize --dry-run` on
  language-split halves emits a within-file DE/EN `count_mismatch` review
  per half (100% noise, exit 2), making it unusable as a scripted drift
  gate. Fix is to skip like `clm validate` already does (same
  `.de.py`/`.en.py` signal). Labels: bug, area:slides, agent-impact.

**Gotchas**: all of Phase 1's sync-engine gotchas apply (memory topics
`project_sync_one_sided_cold`, `project_sync_v3_design_audit`; §13
amendments-log row for ANY engine change). #609/#610 have documented
workarounds in their issue bodies — as with #600, the workaround is the
oracle for what the fixed flow should converge to.

### Phase 8 [DONE] — Build reliability: job/session ownership (#620, #617)

**#617 DONE (2026-07-12, "Full" scope)**: root cause (via a dedicated
investigation) — nothing marked a *busy* worker dead, so a worker that died or
hung mid-job left its job stuck in `processing`; the completion loop's
dead-worker requeue (`_cleanup_dead_worker_jobs`, gated on `workers.status =
'dead'`) never fired, and the job lingered until the teardown
`mark_orphaned_jobs_failed` sweep stamped it. The dormant `WorkerPoolManager`
health monitor was never started in the build path. Fixed in four layers:
(1) **liveness recovery** — `start_managed_workers` now starts the health
monitor (`start_monitoring`), scoped to the build's own `session_id` (mirrors
#597/#620; never reaps a concurrent build's workers, and only marks dead on a
real `is_worker_running` process check — the missing-executor branch now skips
instead of killing); a dead worker's in-flight job is then requeued for retry.
(2) **`reset_hung_jobs`** now clears `started_at`/`worker_id` so a
legitimately-requeued job is never mis-stamped an orphan by the teardown scan.
(3) **submit race** — job submission registers the job in `active_jobs` under
`asyncio.shield`, so a cancelled submission can't leave a worker-claimable but
untracked row. (4) **reporting** — `stop_managed_workers` returns its orphans;
`main_build` folds any into the summary (`_record_teardown_orphans`) and marks
it timed-out, forcing a non-zero exit instead of silently banking them. Tests:
monitor session-scoping (`test_pool_manager`), `reset_hung` started_at
(`test_job_queue`), stop-returns-orphans (`test_lifecycle_manager`),
`_record_teardown_orphans` (`test_build_abort_summary`). Shipped as its own PR.

**#620 DONE (2026-07-12)**: implemented exactly the planned fix direction —
session id on job rows + claim filter — as its own PR. Schema v11 adds
`jobs.session_id`; `add_job` stamps it and the backend forwards its
`worker_session_id` (already wired from `lifecycle_manager.session_id`) on
every submit; `get_next_job` resolves the claiming worker's own
`workers.session_id` and filters `(session_id IS NULL OR session_id = ?)`.
The one filter site covers **both** claim paths (Direct `worker_base`,
Docker `worker_routes`) since both pass `worker_id` and the query runs
host-side. A worker with no resolvable session stays unrestricted (legacy /
tests), so a build can never deadlock on its own jobs; a killed/concurrent
build's residue (different session) is simply never claimed and sits
harmless. The issue's **optional** extras were deliberately deferred: fix #2
(requeue an unmappable job instead of failing it — largely moot once foreign
jobs aren't claimed, and a naive requeue risks silent retry loops) and fix #3
(dead-session sweep — unsafe without real dead-session detection). Shipped
with schema-migration, session-filter, and backend-wiring tests (the schema
version-canary in `test_worker_heartbeats.py` bumped to 11). **#617 (own-pool
teardown orphaning) is next — investigate as its own PR.**

Investigate together — likely the same #564/#594/#599 family, and #617's
diagnostic even names the suspected race.

- **#620 [DONE]**: `jobs` rows carried no session ownership, so workers claimed a
  killed/concurrent build's pending jobs and failed them with
  `is not in the subpath of` path errors attributed to innocent slide
  files (a killed Docker build deterministically poisoned the next one).
  This was the residual half of #564's gap: #564 filtered claiming by
  `execution_mode`, #594/#599 added ownership for *workers*, jobs had
  none. Fixed by stamping jobs with the owning build session and filtering
  claims by it (see the resolution note above).
- **#617 [DONE]**: intermittent `worker died mid-job (orphaned at pool
  shutdown)` failing a batch of jobs mid-stage in a single uninterrupted
  Direct-mode build (8 of 38 jobs in the observed run; byte-identical
  re-run clean). Root cause was the absence of any busy-worker liveness
  recovery; fixed by wiring the session-scoped health monitor plus three
  hardening layers (see the resolution note above).

### Phase 6 [TODO] — Design-tier work (when capacity allows)

**6a — #383 + #381: output-structure defaults cluster.** Make
shared/trainer/speaker (access-control-by-path) the default when a spec has
no `<output-targets>`; default participant kinds to code-along+completed
(subsumes closed #380); revisit remote-template default
(`{repository_base}/{remote_path}/{repo}`) and the mandatory-but-unused
`<repository-base>` coupling in `derive_remote_url`
(`src/clm/core/course_spec.py:683`). Fixing this dissolves #381 (build
always emits `speaker/` but `clm git` skips it unless
`<include-speaker>true`; asymmetry at `src/clm/cli/commands/git.py:381-383`
vs `OutputTarget.default_target()` ALL_KINDS). **Breaking default change**:
write a design doc in `docs/claude/design/` first; needs migration info
topic + downstream course-repo coordination.

**6b — #484: offload cache-hit replay off the event loop.** Follow-up to
PR #482's freeze fix. The full "split, don't lock" design is written in the
issue: thread-safe cache reader (per-thread `DatabaseManager`), heavy pure
work (unpickle, hash, `atomic_write_bytes`) on the submit thread, registry
stays single-threaded with a `precomputed_hash=` param. **The trap it warns
about**: a coarse `OutputWriteRegistry` lock would serialize file-hashing
across submit thread and poll loop, re-introducing the stall. Not currently
biting (semaphore caps stalls ~1 s) — do as scheduled tech debt or when a
hit-heavy-rebuild stall is observed.

**6c — #167: LLM model-selection spike.** Timebox ~half a day. Recommended
outcome: option 2 (purpose→model registry over the existing
OpenAI-compatible client, zero new deps) or close as status-quo.

## 4. Current Status

- **Triage complete** (2026-07-10). All 13 then-open issues read in full;
  claims verified against `origin/master` history and source.
- **#165 CLOSED** with evidence comment (vcrpy removal shipped via #355,
  PRs #357/#358).
- **#501 CLOSED** with evidence comment (phases shipped via PRs
  #505/#510/#513/#515/#518).
- **Phases 1–3 DONE** (all 2026-07-10): #600 via PRs #602/#603, #539 via
  PR #604, #524 via PR #607, #382 closed as pre-shipped (1.15.0), #362 via
  PR #608 (maintainer picked Option A). Resolution details live in each
  phase's block in §3.
- **Phase 4 DONE** (2026-07-11): #568 fix 1 — shared deck-independent
  voiceover cache with legacy promotion (see the Phase 4 resolution block).
  Fixes 2–4 of the issue stay open as deferred follow-ups.
- **Phase 5 DONE** (2026-07-11): #559 via PR #622; "Require CI green"
  ruleset swapped to the six matrix job names in the same window (see the
  Phase 5 resolution block). Measured result: PR wall clock now bounded by
  the 5.5-min Docker job.
- **#605** (bilingual dir-group `<name>` silently dropped — filed out of
  Phase 2) was fixed in parallel by another session via PR #606.
- **Post-triage issues folded in** (2026-07-11): #609, #610, #611, #615
  (sync/normalize — Phase 7), #617, #620 (build reliability — Phase 8),
  #614 (deferred harvest follow-up, tracks #568 fixes 2–4). See §3a.
- **Phase 7 fully DONE** (2026-07-11): #609 via PR #624, #610 via
  PR #625, #611 via PR #626, **#615 via PR #628** (resolution details in
  the Phase 7 block). All four issues CLOSED.
- **Phase 8 DONE** (2026-07-12): #620 job session ownership (schema v11
  `jobs.session_id`, stamp on submit, session-filtered claim) + #617
  orphan-pool-shutdown (session-scoped health monitor for busy-worker
  liveness recovery, `reset_hung_jobs` started_at clear, shielded submit
  registration, teardown orphans folded into the summary/exit). Resolution
  details in the Phase 8 block.
- No blockers, no pending decisions. Remaining, in priority order:
  Phase 6 design tier (#383+#381 — start with a verify-then-close pass,
  see the Phase 3 resolution note — plus #484, #167), #614 before the next
  harvest round.

## 5. Next Steps

**Phases 1–5, 7, and 8 are DONE — next is Phase 6a (#383+#381)**, which
should START with a verify-then-close pass against commit 90518611 (see the
Phase 3 resolution note) rather than a fresh design effort, then #484 and
#167. Schedule #614 just before the next big harvest round. The plan below
documents how Phase 1 was executed (kept for reference):

1. Read memory topics `project_sync_one_sided_cold` and
   `project_sync_v3_design_audit`, plus the sync v3 design note (find via
   `docs/claude/design/`, the one with the §13 amendments log) and
   `project_sync_assessment_2` landmines.
2. Locate where `ambiguous_alignment` conflicts are emitted with empty
   `answers` (search `ambiguous_alignment` under `src/clm/`) and where
   decision vocabularies are validated in `apply --decisions`.
3. Implement `treat_as_new` (minimum) per Phase 1 above; add
   `treat_as_stamped_edit` only if the binding semantics are clean.
4. Repro/regression test from the issue's exact diff (un-id'd positional
   `df.drop_duplicates()` → `dedup-assign` + `dedup-check`, and the vanished
   `pos:duplicates/code/1` member).
5. Update the `clm info` topic that documents sync decision vocabularies
   (info-topic maintenance rule in AGENTS.md), add a changelog fragment in
   `changelog.d/`, add a §13 amendments-log row, ship via the standard
   worktree → PR flow (`/ship-a-pr` skill).

## 6. Key Files & Architecture

Files identified during triage (issue → key sites); Phases 1–4 touched
their listed sites — see each phase's resolution block for what changed:

- `#600` → sync v3 engine under `src/clm/` (search `ambiguous_alignment`);
  decision-doc apply path in `src/clm/cli/commands/slides/sync.py`
- `#539` → `src/clm/core/course.py` (`process_dir_group_for_targets`),
  `src/clm/core/dir_group.py` (`get_processing_operation`),
  `local_ops_backend._copy_dir_group_to_output_sync`
- `#524` → `scripts/check_exclude_newer.py` (startswith bug),
  `scripts/update_exclude_newer.py`, `pyproject.toml [tool.uv]`,
  user-profile `Sync-UvExcludeNewer` (outside repo)
- `#382` → `src/clm/core/course_spec.py:597-618`
  (`GitHubSpec.from_element`), `:683` (`derive_remote_url`);
  `src/clm/cli/commands/git.py:835-837` (local-only branch)
- `#362` → `src/clm/slides/tags.py` (allowlists),
  `src/clm/workers/notebook/output_spec.py:39-64` (`find_workshop_ranges`),
  `src/clm/slides/workshop_scope.py`, `src/clm/slides/validator.py`,
  `src/clm/slides/normalizer.py`
- `#568` → `src/clm/voiceover/cache.py` (`resolve_cache_root` shared
  resolution, `legacy_cache_root`, `_legacy_cache` promotion),
  `src/clm/infrastructure/llm/cache.py` (`describe_cache_dir` `start=`
  anchor), `src/clm/infrastructure/utils/path_utils.py` (`.clm-cache` in
  `SKIP_DIRS_FOR_COURSE`); legacy per-deck layout was
  `<deck>/.clm/voiceover-cache/` (subdirs `transcripts/`, `transitions/`,
  `timelines/`, `alignments/` — still probed read-only)
- `#559` → `.github/workflows/ci.yml` + the GitHub "Require CI green"
  ruleset (id 17358657; updated via `gh api PUT` alongside PR #622)
- `#609/#610/#615` (Phase 7) → sync v3 engine under `src/clm/` (the #609
  delimiter check is in the apply/decision-validation path of
  `src/clm/cli/commands/slides/sync.py` or the engine it calls; #610 is
  group-anchoring/keying territory; #615 is the `confirm` banking path) —
  not re-located during this fold-in, start from the issues' repro output
- `#611` (Phase 7) → `src/clm/slides/normalizer.py` review pass; copy the
  split-file exemption from `src/clm/slides/validator.py`
- `#620` (Phase 8, DONE) → `src/clm/infrastructure/database/schema.py`
  (`jobs.session_id` column + v10→v11 migration, `DATABASE_VERSION`),
  `src/clm/infrastructure/database/job_queue.py` (`add_job` stamp +
  `get_next_job` session-filter, `Job.session_id`),
  `src/clm/infrastructure/backends/sqlite_backend.py` (`_submit_job_blocking`
  forwards `worker_session_id`). Tests: `tests/infrastructure/database/
  test_job_queue.py` + `test_schema.py`, `tests/infrastructure/backends/
  test_sqlite_backend_resilience.py`; version-canary bumped in
  `test_worker_heartbeats.py`
- `#617` (Phase 8, DONE) → `src/clm/infrastructure/workers/pool_manager.py`
  (`_monitor_health` session-scoped + skip-on-missing-executor),
  `src/clm/infrastructure/workers/lifecycle_manager.py`
  (`start_managed_workers` starts the monitor; `stop_managed_workers` returns
  its orphans), `src/clm/infrastructure/database/job_queue.py`
  (`reset_hung_jobs` clears `started_at`),
  `src/clm/infrastructure/backends/sqlite_backend.py` (shielded submit
  registration), `src/clm/cli/commands/build.py` (`_record_teardown_orphans`
  + the `main_build` finally wiring). The existing `_cleanup_dead_worker_jobs`
  requeue (already in the completion loop, gated on `workers.status='dead'`)
  is what the monitor now feeds. Tests in `test_pool_manager.py`,
  `test_lifecycle_manager.py`, `test_job_queue.py`, `test_build_abort_summary.py`
- `#484` → `src/clm/infrastructure/backends/sqlite_backend.py`
  (`_execute_operation_impl`, poll loop `record_write` at ~:564),
  `src/clm/core/output_write_registry.py` (:292,294 hash-in-critical-section)
- `#383/#381` → `src/clm/core/output_target.py` (`default_target`,
  ALL_KINDS), `src/clm/infrastructure/utils/path_utils.py:413`
  (PRIVATE_KINDS), `src/clm/cli/commands/git.py:381-383`
  (`find_output_repos` speaker skip), `src/clm/core/course_spec.py`

**Conventions to continue**: every behavior/CLI/spec change updates the
matching `src/clm/cli/info_topics/*.md`; changelog via `changelog.d/`
fragments (never `[Unreleased]`); each fix on its own fresh branch off
`origin/master` (never stack; never switch a worktree to literal `master`);
sync-engine changes get a §13 amendments-log row.

## 7. Testing Approach

- Standard repo strategy: fast suite (`pytest`, ~72 s, runs on pre-push
  hook); `pytest -m "not docker"` pre-release; markers per
  `docs/developer-guide/testing.md`.
- Per phase: #600 needs an engine-level regression test reproducing the
  issue's report JSON shape plus an apply-path test for the new answer
  verb(s); #539 needs a dedup unit test plus (ideally) a
  concurrency-marked repro — note the issue verified concurrent copies to
  *different* dests are safe, so only same-dest needs guarding; #524 is
  script-level (exact-match check test); #362 needs `find_workshop_ranges`
  tests for the chosen semantics; #559's "test" is CI itself — watch the
  first PR after the ruleset swap carefully.
- Phase 4 (#568) shipped with resolution-tier tests (env / pyproject /
  walk-up), legacy-promotion tests for all four artifact kinds, and a
  fork-scenario test (`TestForkedDecksShareCache`) in
  `tests/voiceover/test_cache.py`, plus a `start=` anchor test in
  `tests/infrastructure/llm/test_cache_dir_resolution.py`; fast suite green
  (8436 passed).

## 8. Session Notes

- Triage session: https://claude.ai/code/session_01XEj1XR9vYjEf5XCMD4EMJ6
  (2026-07-10, worktree `starry-nibbling-swing`).
- The single most easily-lost insight is the #362 one (validator warning is
  *correct* today; the "fix" in the issue text would create silent no-ops) —
  don't let a future session blindly apply the issue's suggested one-liner.
- #600's `has-workaround` label is accurate; the workaround (hand-mirror
  structural change with slide_id parity, then `record_remove` + `confirm`)
  is documented in the issue body and matches the documented guardrails —
  useful as the oracle for what the fixed flow should converge to.
- User priorities inferred from active work: sync agent-loop dogfooding
  (PythonCourses) and harvest dogfooding on real recordings are the two
  live workstreams — hence #600 and #568 ranking above intrinsically
  "bigger" issues like #383.
