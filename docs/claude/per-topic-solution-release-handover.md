# Handover: Per-Topic Solution Release (issue #208)

**Branch**: `worktree-logical-jingling-fiddle` ·
**Issue**: [#208](https://github.com/hoelzl/clm/issues/208) ·
**Design**: `docs/claude/{requirements,design}/per-topic-solution-release.md`

Latest increment: **step 3d — manifest default-ON + `clm release sync --push` +
info-topic docs (DONE)**. Steps 1, 2, 3a, 3b, 3c, 3d are all complete. The most
recent commits may be local until you push; run `git log --oneline origin/master..`
to see what is ahead of origin. With 3d, **Step 3 is fully done**.

## 1. Feature Overview

After a workshop is discussed, students should receive that topic's full
solution — only that topic's, with multiple concurrent cohorts each on their
own schedule, and solutions **frozen** so later course edits never change what
a cohort already received. Achieved with **one build artifact** (a provenance
manifest) + **one orchestration layer** (`clm release`) that promotes frozen
`completed` artifacts per topic into per-cohort git repos. Reuses the existing
`partial`/`completed` kinds, per-target `clm git`, and `get_git_info`.

## 2. Design Decisions (the load-bearing ones)

- **One repo per cohort** (not a shared repo with subfolders): 1:1 with a git
  remote like an output target; no push coupling. Channels declared in a
  **structural, stable** `<release-channels>` spec block; the **volatile**
  per-topic release state lives in a separate **ledger**, never the spec.
- **Promote frozen artifacts, never rebuild**: `clm release sync` copies bytes
  by manifest; the **frozen manifest** (`.clm-released.json` in the destination
  repo) is the freeze boundary — once a topic is frozen it is never
  re-propagated even if the source changes; `--refreeze` is the only override.
  Guards against the project's known build non-determinism.
- **Keystone = provenance manifest** (`.clm-manifest.json`): maps every output
  file → `{section_id, topic_id, source_commit, content_hash}`. Required
  because **the owning topic is NOT recoverable from the output path**. Private
  (≈190 KB for AZAV ML); never shipped. The shipped frozen manifest is
  per-topic (≈15 KB), not per-file.
- **Ledger format = plain text, one topic id per line** (chosen over YAML to
  avoid a new dep — pyyaml is only transitive via vcrpy — and for clean
  one-line-per-release diffs).

## 3. Phase Breakdown

- **Step 1 — provenance manifest** `[DONE]` (`c56e61e` `4c30600` `c0ccf5c`
  `868bebf`). Build emits `.clm-manifest.json`; covers notebooks/code/HTML,
  data assets, duplicated images, dir-group outputs (+ dir-group ownership).
- **Step 2 — release engine + CLI** `[DONE]` (`99d33fb` `82fd48e`). `clm.release`
  package + `clm release add/status/sync`.
- **Step 3 — spec channels + git push** `[DONE]`.
  - 3a `<release-channels>` parsing `[DONE]` (`3c47f67`).
  - 3c `clm release --channel` resolution `[DONE]` (`4e06d63`).
  - 3b `clm git --channel` push `[DONE]`. Cohort repos (NOT `<output-targets>`)
    are pushable via `--channel NAME`/`--all-channels` on all 6 `clm git`
    subcommands. The private `.clm-manifest.json` is staged-excluded (and
    self-healed out of any pre-exclusion commit); `.clm-released.json` ships.
  - **3d manifest default-ON + `clm release sync --push` + docs `[DONE]`**
    (`cd52b1d` build, `23ff7e2` release, `28aabd1` docs). `clm build` writes the
    provenance manifest by default (`--no-provenance-manifest` opts out),
    suppressed under `--snapshot`/`--verify-against` and skipped in
    `--only-sections`/errored/timed-out builds. `commit_and_push_repo` extracted
    from `clm git sync` and reused by the new `clm release sync --push`. Full
    user-facing surface documented in the three info topics. Adversarial review
    (5 dimensions, 11 confirmed findings): 10 fixed, 1 deferred (see Session
    Notes).
- **Step 4 — multi-cohort tests `[DONE]`** (`tests/release/test_multi_cohort.py`,
  8 end-to-end tests). Pins the concurrent-cohort guarantees: each cohort
  freezes/ships only its own released topics; a frozen topic **and the skeleton**
  are never re-propagated on a source change (so cohorts on different schedules
  diverge); `--refreeze` is the only override; unchanged re-sync is a no-op; two
  channels resolve+diverge from one spec; `topic_digest` is order-independent;
  a released-but-unbuilt topic is skipped (retried) then frozen once built.
  Engine was already capable — this is coverage. Non-vacuity + scope confirmed
  by an adversarial review (3 gaps it found are closed).
- **Step 5 — recording → slide-version provenance `[DONE]`** (`de27496`). The
  provenance *primitive*: `core.provenance_manifest.topic_digest_from_files` /
  `manifest_topic_digest` (one order-independent per-topic digest; `release.sync`
  deduped onto it, value byte-identical, frozen-literal regression test);
  `RecordingPart`/`TakeRecord` gain optional `section_id`/`topic_id`/`slide_digest`
  (backward-compatible, threaded through every mutator incl. retake/restore);
  `recordings.provenance.{part_slide_drift,course_recording_drift}` answers "did
  this slide change since the video was shot?" as a manifest diff (unknown ≠
  current). **Consumer wiring is the follow-up** (see below).
- **Follow-ups** `[TODO]`: `SharedImageFile` (shared image mode); `clm release
  week` (section-selector index space is disabled-inclusive — a landmine);
  one real-build integration test asserting a `--snapshot` DIR stays
  `.clm-manifest.json`-free (3d review finding #6, deferred — the write decision
  is already unit-covered by `_resolve_write_provenance_manifest` /
  `_should_emit_provenance_manifest` matrices + the wiring test, but a real build
  would guard against a future refactor moving the write past the gate).
  **Info-topic docs are DONE** (landed in 3d, `28aabd1`).

## 4. Current Status

**All 5 steps of the #208 plan are complete** (1, 2, 3a/3b/3c/3d, 4, 5); what
remains is consumer wiring + smaller follow-ups (see §5). The release flow works
end-to-end with the manifest **on by default**:

```bash
clm build course.xml                                  # writes .clm-manifest.json by default
clm release add  course.xml functions --channel jan   # ledger resolved from spec
clm git init course.xml --channel jan                 # one-time: make the cohort repo
clm release sync course.xml --channel jan --push -m "Release functions"  # promote+freeze+push
```

- **Tests**: ~100 across the feature, all green. 3d additions:
  `_should_emit_provenance_manifest` / `_resolve_write_provenance_manifest`
  matrices + wiring in `test_build_command.py`; the `--push` real-git suite +
  refreeze/idempotency/self-heal/de-masked-exclusion in `test_release_cli.py`;
  the remote-ahead guard in `test_git_release_channels.py`. Step 4 adds 8
  end-to-end multi-cohort divergence tests in `test_multi_cohort.py`. ruff +
  mypy clean.
- **3d adversarial review** (5 dimensions, 11 confirmed findings): 10 fixed in
  the same diff, 1 deferred (the snapshot real-build coverage test — see §8).
- The manifest exclusion is **self-healing** and shared: both `clm git sync` and
  `clm release sync --push` go through `commit_and_push_repo` →
  `_stage_all_excluding_sidecars` (`git rm --cached --ignore-unmatch` before the
  exclude-add), so a pre-exclusion-tracked manifest is purged on the next push.
- No blockers.

## 5. Next Steps — all 5 steps done; remaining follow-ups

The 5-step plan (#208) is complete. Remaining work is follow-ups, roughly by
value (a dedicated, actionable handover for these lives at
`docs/claude/per-topic-solution-release-followups-handover.md`):

1. **Step-5 consumer wiring** (the most impactful gap). The drift primitive
   ships, but nothing populates it at record time: `src/clm/recordings/workflow/
   session.py` (`_sync_*` around line 1759) calls `ensure_part`/`record_retake`
   with **no** provenance kwargs — and note it never passed `git_commit` either,
   so `recordings/git_info.get_git_info` is currently unused in the live flow.
   Wiring it means: at record/retake time resolve the deck's `(section_id,
   topic_id)`, load the course's `.clm-manifest.json`, compute
   `manifest_topic_digest(...)`, and pass all three (plus the already-available
   git info) through. That single change lights up BOTH the long-dormant
   git provenance and the new slide drift. Then add a read surface — e.g. a
   `clm recordings drift SPEC` command over `course_recording_drift(...,
   stale_only=True)` (and/or a web-dashboard badge). Needs a small design note on
   where the manifest comes from at record time and how a lecture maps to a topic.
2. **`SharedImageFile` step-1 manifest gap** (`image_mode="shared"`): the only
   output kind the provenance manifest does not yet enumerate (see
   `enumerate_expected_outputs` docstring). Needs a shared-mode test course.
3. **`clm release week`**: the section-selector index space is
   disabled-inclusive — a landmine (deferred since step 2).
4. **Deferred 3d review finding #6**: a real-build `--snapshot` integration test
   asserting the output tree stays `.clm-manifest.json`-free.

**Carry-forward gotchas**: `git_ops.py` `OutputRepo` is mutable (not attrs);
`run_git` honors the `_dry_run_mode` ContextVar; `resolve_course_paths` returns
`(course_root, default_output_root)` with `course_root = spec_file.parents[1]`.
The commit "anything to commit?" decision gates on the **index**
(`has_staged_changes`, i.e. `git diff --cached --quiet`), NOT the working tree.

## 6. Key Files & Architecture

Build/provenance side (`src/clm/core/`):
- `provenance_manifest.py` — emit `.clm-manifest.json`; `load_manifest`,
  `manifest_files_by_topic` readers; `topic_digest_from_files` /
  `manifest_topic_digest` (step 5 — the shared per-topic rollup). Enumeration
  reuses the build's own path computation + existence-filter.
- `git_info.py` — `get_git_info()` (commit+dirty), core copy free of the
  `[recordings]` extra.
- `course_spec.py` — `DirGroupSpec.{section_id,topic_id}` (dir-group ownership);
  `ReleaseChannelSpec`/`ReleaseChannelsSpec` + `CourseSpec.release_channels` +
  `parse_release_channels`.
- `dir_group.py` — `DirGroup.spec` retained for ownership.

Release engine (`src/clm/release/`):
- `ledger.py` — `Ledger` (plain-text), `partition_known`.
- `frozen_manifest.py` — `FrozenManifest` (`.clm-released.json`), `FrozenRecord`.
- `sync.py` — `plan_sync`/`apply_sync`, `SyncPlan`/`SyncResult`; the per-topic
  freeze digest is now `core.provenance_manifest.topic_digest_from_files`.

Recordings provenance (`src/clm/recordings/`):
- `state.py` — `RecordingPart`/`TakeRecord` carry `section_id`/`topic_id`/
  `slide_digest` (step 5); mutators thread them through.
- `provenance.py` — `part_slide_drift` / `course_recording_drift` + `SlideDrift`
  (`unknown`/`current`/`changed`). Pure manifest diff; no video/slide re-read.

CLI / build wiring:
- `src/clm/cli/commands/release.py` — `clm release` group (add/status/sync +
  `_resolve_channel`). Registered in `src/clm/cli/main.py`.
- `src/clm/cli/commands/build.py` — `--provenance-manifest` flag (default off),
  `BuildConfig.write_provenance_manifest`, source-commit capture, post-sweep
  `write_provenance_manifests` hook.
- `src/clm/cli/commands/git_ops.py` (3b) — `OutputRepo.source`
  (`"output"`/`"channel"`) + language-free `display_name`;
  `find_release_channel_repos` (mirrors `find_output_repos` over
  `<release-channels>`); `_select_repos` (output-vs-channel dispatch + the
  `--target`-exclusivity / unknown-channel / no-`<release-channels>` guards);
  `_stage_all_excluding_sidecars` (self-healing recursive manifest exclusion);
  `has_staged_changes` (index-scoped commit gate); `--channel`/`--all-channels`
  on all 6 subcommands; `.clm-manifest.json` in the `init_repo_fresh` gitignore
  template. `GitHubSpec.derive_channel_remote_url` lives in `course_spec.py`
  (clean `{slug}-{channel}` names, no empty-language `--` wart).

## 7. Testing Approach

Unit/CLI tests, all in the fast suite. Run:
```
uv run pytest tests/core/test_provenance_manifest.py tests/core/test_git_info.py \
              tests/core/test_release_channels_spec.py tests/release \
              tests/cli/test_git_release_channels.py tests/cli/test_git_ops.py -q
```
(Worktree needs its own `uv sync --extra all` first.) `tests/test-data/course-specs/test-spec-1.xml`
is the realistic fixture (1 DataFile, duplicated images, a topic-scoped
dir-group owned by `some_topic_from_test_1`).
`tests/cli/test_git_release_channels.py` (3b) covers URL derivation, channel
discovery, `_select_repos` guards, and **real-git** end-to-end checks: manifest
exclusion (root + nested), self-heal of a pre-tracked manifest, manifest-only
no-op commit, `sync --channel` push to a local bare remote, and `reset
--channel` no-remote skip. Its e2e class autouse-patches `remote_exists`/
`remote_has_commits` to stay offline. Still needs tests: multi-cohort
divergence (step 4), recordings (step 5).

## 8. Session Notes

- User prefers granular commit history kept (atomic, each green) — do NOT squash
  the branch; choose squash-vs-merge at PR time. Commit at sensible checkpoints
  without asking; **push needs an explicit request**.
- PlantUML/DrawIo source diagrams emit only a *source-tree* intermediate image;
  their output copy is a separate image `CourseFile` already covered — don't
  double-count or treat them as missing.
- Adding fields to `NotebookPayload` must go through `model_validate` (Issue #17
  landmine) — relevant if step 5 or a future change stamps payloads.
- **3b manifest-exclusion invariants (do NOT regress):** (1) the staging
  chokepoint is `_stage_all_excluding_sidecars` — `git rm --cached
  --ignore-unmatch` (self-heal a pre-tracked manifest) *then* `git add -A`
  with `:(exclude)` + `:(exclude,glob)**/` pathspecs (root + nested). (2) The
  "anything to commit?" gate is `has_staged_changes` (`git diff --cached
  --quiet`), NOT the working-tree `has_uncommitted_changes`; otherwise an
  untracked, non-ignored manifest as the sole change makes `git commit` exit
  non-zero and prints `Error`. (3) `.clm-released.json` (frozen manifest) must
  stay committed — only `.clm-manifest.json` is excluded. (4) Channel repos are
  language-free (`language=""`, `source="channel"`); `display_name` drops the
  empty segment. (5) `--target` and `--channel`/`--all-channels` are mutually
  exclusive; unknown channel / no-`<release-channels>` error loudly (mirror
  `clm release`).
- A `git rm --cached --ignore-unmatch` is a safe no-op on a repo with no commits
  yet (used inside `init_repo_fresh` before the first commit) — verified.
- **3d manifest-write invariants (do NOT regress):** the manifest write is a full
  *overwrite* of the prior index, so it is gated by **two** helpers in `build.py`,
  both mirroring the post-build sweep's conservative skips. (1)
  `_resolve_write_provenance_manifest` (entry point) drops it for `--snapshot` /
  `--verify-against` — the manifest's `built_at`+`source_commit` are
  non-deterministic and `--strict-verify` skips nothing, so it must never enter a
  reproducibility baseline. (2) `_should_emit_provenance_manifest` (post-build)
  additionally skips `--watch`, **`--only-sections`** (a partial overwrite would
  silently drop every unselected section's provenance — the release join key —
  causing `apply_sync` to omit those topics), and **errored/timed-out** builds
  (incomplete tree). Both are pure + unit-tested; keep new guards there, not
  inline at the write site.
- **3d shared git helper:** `commit_and_push_repo(repo, message, *, amend,
  force_with_lease, remote_ahead_hint)` in `git_ops.py` is the single
  commit+push implementation for BOTH `clm git sync` and `clm release sync
  --push`. It assumes the caller already echoed the `[name] path` header and
  verified `has_git`; it prints NO trailing blank (caller prints exactly one).
  The remote-ahead recovery hint is caller-specific (passed in), because
  `clm git` and `clm release` have different recovery recipes. Do not duplicate
  git logic in `release.py`.
- **3d deferred review finding (#6):** there is no real-build integration test
  asserting a `--snapshot` DIR stays `.clm-manifest.json`-free. The decision is
  unit-covered (resolver + emit matrices + the wiring test proving the flag
  reaches `main_build` as False under `--snapshot`), so this is hardening against
  a future refactor that moves/duplicates the manifest write past the gate — add
  one small real build with `--snapshot` + assert-absent if that risk grows.
- **3d test gotcha:** real-git cohort fixtures must NOT list `.clm-manifest.json`
  in `.gitignore` when the test's purpose is to prove the *staging* exclusion —
  otherwise `.gitignore` masks the `:(exclude)` chokepoint and the test passes
  even if `_stage_all_excluding_sidecars` is gutted (the 3d review caught exactly
  this). See `_init_cohort_repo(gitignore_manifest=False)` in
  `tests/release/test_release_cli.py`.
- **Step 4 test harness:** `tests/release/test_multi_cohort.py` fabricates a
  frozen source synthetically — `_write_source_entries(root, source_commit=,
  entries=[(path, topic_id, body), ...])` writes a `.clm-manifest.json` + bodies
  with `content_hash` derived from each body, so a changed body yields different
  copied bytes AND a different `topic_digest` (a real rebuild). Pass `entries`
  in a chosen order to exercise `_topic_digest`'s order-independence; vary
  `_build_source(..., skeleton_body=)` to prove the skeleton is pinned across a
  source change (not just present). No real build runs — these are engine/CLI
  tests, fast, no git. The divergence guarantee is asserted on real dest bytes
  AND `FrozenRecord.source_commit`/`topic_digest`, not just on log strings.
- **Step 5 invariants:** (1) the per-topic digest rollup is now ONE function
  (`core.provenance_manifest.topic_digest_from_files`) shared by the release
  freeze and recording drift; a frozen-literal test (`...5d02884ff145...`) pins
  its output, because a change to the rollup would silently invalidate every
  shipped `.clm-released.json` digest. (2) The new recording fields are
  optional/`None`-default pydantic fields → old `state.json` loads unchanged;
  every `RecordingPart(`/`TakeRecord(` site in `state.py` carries them (incl.
  retake/restore demote+swap), so provenance never silently drops. (3) Drift
  `unknown` is deliberately distinct from `current` — an unprovenanced or
  absent-from-manifest recording must never read as up to date. (4) **Key
  discovery:** `recordings/git_info.get_git_info` is currently UNUSED by the live
  session flow — `session.py` populates neither `git_commit` nor the new slide
  fields at record time. So step 5 ships the primitive consistently with how
  recording provenance already works; the live wiring is the §5 follow-up and
  lights up both at once.
