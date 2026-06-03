# Handover: Per-Topic Solution Release (issue #208)

**Branch**: `worktree-logical-jingling-fiddle` (all work pushed to origin) ·
**Tip**: `4e06d63` · **Issue**: [#208](https://github.com/hoelzl/clm/issues/208)
**Design**: `docs/claude/{requirements,design}/per-topic-solution-release.md`

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
  - **3b `clm git --channel` push `[TODO]` ← ACTIVE NEXT.**
  - 3d flip `--provenance-manifest` default ON + `clm release sync --push` `[TODO]`.
- **Step 4 — multi-cohort tests** `[TODO]` (mostly emergent).
- **Step 5 — recording → slide-version provenance** `[TODO]`.
- **Follow-ups** `[TODO]`: `SharedImageFile` (shared image mode); `clm release
  week` (section-selector index space is disabled-inclusive — a landmine);
  info-topic docs (`commands.md`/`spec-files.md`/`migration.md`, project rule).

## 4. Current Status

Steps 1–2 complete; step 3 half done. The full flow works **today** with the
manifest opt-in:

```bash
clm build course.xml --provenance-manifest
clm release add  course.xml functions --channel jan   # ledger resolved from spec
clm release sync course.xml --channel jan             # source+dest resolved; promote+freeze
```

- Working tree clean; HEAD == origin; 8 commits pushed.
- **Tests**: 46+ across the feature, all green; every pre-commit gate (ruff,
  mypy, full fast suite) passed on every commit.
- `--provenance-manifest` is **opt-in (default off)** on purpose: it must not
  land in student-facing output repos until 3b adds the `.clm-*` `clm git`
  exclusion. Flip it on in 3d.
- No blockers.

## 5. Next Steps — Step 3b: `clm git --channel` push

Make the cohort repos (which are NOT `<output-targets>`) pushable. In
`src/clm/cli/commands/git_ops.py`:

1. Add `source: str = "output"` to `OutputRepo.__init__` (it's a plain class,
   not attrs; default keeps existing behavior).
2. Add `find_release_channel_repos(spec_file, channel_filter)` mirroring
   `find_output_repos` (`git_ops.py:227`): enumerate `spec.release_channels`,
   resolve each channel's `path` under the course root, derive its remote via
   `GitHubSpec.derive_remote_url(channel_name, language="", remote_path=ch.remote_path)`,
   yield ONE `OutputRepo(..., source="channel", language="")` per cohort.
3. Add `--channel NAME` to the 6 `git` subcommands; when set, operate on channel
   repos. The per-repo loop, `run_git`, dry-run, and `has_remote()` are already
   generic over a list of `OutputRepo` — no change needed there.
4. **`.clm-*` exclusion**: ensure `clm git commit` does not stage `.clm-*`
   build sidecars (esp. `.clm-manifest.json`), so the private manifest never
   ships. (Same invariant the release sync already enforces by copying only
   manifest-listed files.)
5. Then `clm release sync --push` delegates to this machinery (no second git
   impl); and 3d flips `--provenance-manifest` default to on.

**Gotchas**: `git_ops.py` `OutputRepo` is mutable, not attrs. `run_git` honors
the `_dry_run_mode` ContextVar. `resolve_course_paths(spec_file)` returns
`(course_root, default_output_root)` with `course_root = spec_file.parents[1]`
(spec lives in a subdir). Channel remote derivation mirrors `OutputTargetSpec`.

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

## 7. Testing Approach

Unit/CLI tests, all in the fast suite. Run:
```
uv run pytest tests/core/test_provenance_manifest.py tests/core/test_git_info.py \
              tests/core/test_release_channels_spec.py tests/release -q
```
(Worktree needs its own `uv sync --extra all` first.) `tests/test-data/course-specs/test-spec-1.xml`
is the realistic fixture (1 DataFile, duplicated images, a topic-scoped
dir-group owned by `some_topic_from_test_1`). Still needs tests:
`clm git --channel` (3b), multi-cohort divergence (step 4), recordings (step 5).

## 8. Session Notes

- User prefers granular commit history kept (atomic, each green) — do NOT squash
  the branch; choose squash-vs-merge at PR time. Commit at sensible checkpoints
  without asking; **push needs an explicit request**.
- PlantUML/DrawIo source diagrams emit only a *source-tree* intermediate image;
  their output copy is a separate image `CourseFile` already covered — don't
  double-count or treat them as missing.
- Adding fields to `NotebookPayload` must go through `model_validate` (Issue #17
  landmine) — relevant if step 5 or a future change stamps payloads.
