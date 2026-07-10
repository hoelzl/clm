# Open-Issue Triage & Execution Plan (2026-07-10) — Handover

**Status**: Triage COMPLETE; execution NOT STARTED. This document is the
source of truth for working through the open-issue backlog in priority
order. Update phase statuses here as issues land.

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

### Phase 3 [IN PROGRESS] — Quick-win batch (3 small PRs, one session)

**#362 decision (2026-07-10, maintainer)**: Option A — honor `end-workshop`
on any cell type with end-**exclusive** semantics (the tagged cell is NOT
part of the workshop), plus allowlist + info-topic updates documenting that
tagging the workshop's last code cell excludes it.

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

### Phase 4 [TODO] — #568: shared ASR transcript cache (before next harvest round)

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

### Phase 5 [TODO] — #559: CI matrix split (needs a quiet-repo window)

Split the sequential unit→integration→e2e steps into a
`python-version × suite` matrix (6 jobs). Measured: per-job setup is ~40 s
(warm caches), so the split costs ~3 machine-min and cuts PR wall clock
~7.5 → ~5.5 min. **The critical gotcha: required status checks are matched
by job NAME.** "Test on Python 3.12/3.13" are required in the "Require CI
green" ruleset; the matrix renames them, so the **ruleset must be updated
atomically with the workflow** (six entries) or every PR sticks on
"Expected — waiting for status". Also: preserve the docs-only skip pattern
(gate *steps* on `needs.changes.outputs.code`, jobs always report success)
for all six jobs; coverage stitching is fine (Codecov merges uploads).
Execute when no PRs are in flight.

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
- **No implementation started** on any of the 11 remaining issues; no
  merged commit on master references #568/#559/#539/#524 (verified via
  `git log --grep`).
- No blockers. One decision pending from the maintainer: #362 semantics
  (Option A vs B in Phase 3c).

## 5. Next Steps

**Phases 1 (#600) and 2 (#539) are DONE — start Phase 3 (quick-win batch:
#524, #382, #362; note the pending #362 Option A/B decision).** The plan
below documents how Phase 1 was executed (kept for reference):

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

Prerequisite for Phase 3c only: ask the maintainer to pick #362 Option A
or B.

## 6. Key Files & Architecture

No code modified yet. Files identified during triage (issue → key sites):

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
- `#568` → voiceover cache layout under `<deck>/.clm/voiceover-cache/`
  (subdirs `transcripts/`, `transitions/`, `timelines/`, `alignments/`);
  harvest code under `src/clm/voiceover/` / `src/clm/cli/commands/`
  (`clm harvest`); LLM cache's `cache_dir` config as the pattern to copy
- `#559` → `.github/workflows/ci.yml` + the GitHub "Require CI green"
  ruleset (repo settings, must change atomically)
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
- Nothing implemented yet, so no test state to report.

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
