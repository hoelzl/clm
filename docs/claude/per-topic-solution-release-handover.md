# Handover: Per-Topic Solution Release (issue #208)

**Branch**: `worktree-logical-jingling-fiddle` ·
**Issue**: [#208](https://github.com/hoelzl/clm/issues/208) ·
**Design**: `docs/claude/{requirements,design}/per-topic-solution-release.md`

Latest increment: **step 3b — `clm git --channel` push (DONE)**. The most recent
commits are local until you push; run `git log --oneline origin/master..` to see
what is ahead of origin.

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
- **Step 3 — spec channels + git push** `[IN PROGRESS]`.
  - 3a `<release-channels>` parsing `[DONE]` (`3c47f67`).
  - 3c `clm release --channel` resolution `[DONE]` (`4e06d63`).
  - **3b `clm git --channel` push `[DONE]`.** Cohort repos (NOT `<output-targets>`)
    are now pushable via `--channel NAME`/`--all-channels` on all 6 `clm git`
    subcommands. The private `.clm-manifest.json` is staged-excluded (and
    self-healed out of any pre-exclusion commit); `.clm-released.json` ships.
  - **3d flip `--provenance-manifest` default ON + `clm release sync --push`
    `[TODO]` ← ACTIVE NEXT.** Also: write the deferred info-topic docs as one
    coherent unit here (see Follow-ups).
- **Step 4 — multi-cohort tests** `[TODO]` (mostly emergent).
- **Step 5 — recording → slide-version provenance** `[TODO]`.
- **Follow-ups** `[TODO]`: `SharedImageFile` (shared image mode); `clm release
  week` (section-selector index space is disabled-inclusive — a landmine);
  **info-topic docs** (`commands.md`/`spec-files.md`/`migration.md`, project
  rule) — deliberately deferred to 3d so the whole user-facing surface (`clm
  release`, `<release-channels>`, AND the new `clm git --channel`/`--all-channels`
  flags) is documented together once the feature flips on, rather than landing
  dangling references to undocumented concepts while the manifest is still
  default-off.

## 4. Current Status

Steps 1–2 complete; step 3 is **3a + 3b + 3c done, 3d remaining**. The full flow
works **today** with the manifest opt-in:

```bash
clm build course.xml --provenance-manifest
clm release add  course.xml functions --channel jan   # ledger resolved from spec
clm release sync course.xml --channel jan             # source+dest resolved; promote+freeze
clm git init course.xml --channel jan                 # one-time: make the cohort repo
clm git sync course.xml --channel jan -m "Release functions"   # commit + push the cohort
```

- **Tests**: ~60 across the feature, all green (incl. 30 in
  `tests/cli/test_git_release_channels.py`); ruff + mypy clean. Validated with
  an adversarial multi-agent review of the 3b diff (9 findings, all addressed).
- `--provenance-manifest` is **opt-in (default off)** on purpose. The `clm git`
  staging exclusion that protects student repos is now in place (3b), so 3d can
  safely flip the default to on.
- The manifest exclusion is **self-healing**: `_stage_all_excluding_sidecars`
  runs `git rm --cached --ignore-unmatch` before the exclude-add, so a manifest
  that a *pre-exclusion* commit already tracked is purged on the next
  commit/sync — not left permanently published.
- No blockers.

## 5. Next Steps — Step 3d: flip `--provenance-manifest` ON + `clm release sync --push` + info-topic docs

3b is done; 3d is the next active increment. Three parts:

1. **Flip `--provenance-manifest` default to ON** in
   `src/clm/cli/commands/build.py` (`BuildConfig.write_provenance_manifest` +
   the flag default). Safe now: the `clm git` staging exclusion (3b) keeps the
   manifest out of every distributed repo and self-heals any already-tracked
   copy. Keep `--no-provenance-manifest` as the opt-out.
2. **`clm release sync --push`** in `src/clm/cli/commands/release.py`: after a
   successful `apply_sync`, delegate to the 3b machinery — call
   `find_release_channel_repos(spec_file, channel)` + the same commit/push loop
   `clm git sync` uses (do NOT write a second git impl; consider extracting the
   per-repo commit+push body from `git_ops.sync` into a reusable helper). Note
   `clm release sync` already takes an optional SPEC positional and a
   `--channel`, so the wiring is mostly present.
3. **Info-topic docs** (project CRITICAL rule, deliberately batched here):
   document the whole feature as one coherent unit —
   - `commands.md`: the `clm release` group (add/status/sync) AND the new
     `clm git --channel`/`--all-channels` flags on init/status/commit/push/
     sync/reset.
   - `spec-files.md`: the `<release-channels>` block (`source-target`,
     `<remote-path>`, `<channel name= path= ledger=>`, per-channel
     `<remote-path>` override).
   - `migration.md`: how to adopt per-topic release in an existing course.
   Use `{version}` placeholders, never hardcoded version numbers.

**3b gotchas to carry forward**: `git_ops.py` `OutputRepo` is mutable (not
attrs); `run_git` honors the `_dry_run_mode` ContextVar; `resolve_course_paths`
returns `(course_root, default_output_root)` with `course_root =
spec_file.parents[1]`. The commit/sync "anything to commit?" decision must gate
on the **index** (`has_staged_changes`, i.e. `git diff --cached --quiet`), NOT
the working tree (`has_uncommitted_changes`/`git status --porcelain`), or a
manifest-only delta spuriously errors — see Session Notes.

## 6. Key Files & Architecture

Build/provenance side (`src/clm/core/`):
- `provenance_manifest.py` — emit `.clm-manifest.json`; `load_manifest`,
  `manifest_files_by_topic` readers. Enumeration reuses the build's own path
  computation + existence-filter.
- `git_info.py` — `get_git_info()` (commit+dirty), core copy free of the
  `[recordings]` extra.
- `course_spec.py` — `DirGroupSpec.{section_id,topic_id}` (dir-group ownership);
  `ReleaseChannelSpec`/`ReleaseChannelsSpec` + `CourseSpec.release_channels` +
  `parse_release_channels`.
- `dir_group.py` — `DirGroup.spec` retained for ownership.

Release engine (`src/clm/release/`):
- `ledger.py` — `Ledger` (plain-text), `partition_known`.
- `frozen_manifest.py` — `FrozenManifest` (`.clm-released.json`), `FrozenRecord`.
- `sync.py` — `plan_sync`/`apply_sync`, `SyncPlan`/`SyncResult`, `_topic_digest`.

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
