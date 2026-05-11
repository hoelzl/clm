# Handover — Shared-Source Includes & Output-Write Dedup

Companion to
[`docs/claude/design/shared-source-includes-and-output-dedup.md`](design/shared-source-includes-and-output-dedup.md)
(locked 2026-05-10). Tracks implementation across two PRs:

- **Feature 1 — shared-source `<include>` + `clm sync-includes`.** Shipped
  via [PR #61](https://github.com/hoelzl/clm/pull/61), merged 2026-05-11
  (master tip `f86c36d`). See "PR 1 — Shipped (reference card)" below for
  the eight-phase summary; full per-phase detail preserved in the
  historical sections after the active PR 2 table.
- **Feature 2 — output-write dedup + collision warning.** This handover's
  current focus; not started.

---

## Start here (fresh session)

**Worktree**: `C:\Users\tc\Programming\Python\Projects\clm\.claude\worktrees\methodical-writing-registry`
(do all work from there; do NOT `cd` to the main repo).

**Branch**: `claude/output-write-dedup` (already checked out in the
worktree; tracks `origin/master`, created off `f86c36d`).

**Last commits visible from the branch**:

```
f86c36d  Merge pull request #61 from hoelzl/claude/shared-source-includes  <-- master tip / PR 1 merged
4a22ed6  fix(includes): Linux backslash normalization + Click 8.1/8.2 CliRunner
efaf051  Merge remote-tracking branch 'origin/master' into claude/shared-source-includes
c820699  docs(handover): mark PR1.8 complete; PR 1 ready for review
aadacc6  docs(handover): record PR1.7 commit hash
9cd89bd  fix(core): exclude .clm-include ledger from topic file map  <-- PR1.7
```

**Test command**: `uv run pytest -x -q` (fast suite, ~60s, runs via
pre-commit too). Master baseline at `f86c36d` should be all-green
(verified by CI on the PR1 merge). One known-flaky session test —
`tests/recordings/test_session.py::TestShortTake::test_short_take_can_be_followed_by_real_take`
— times out under xdist load but passes in isolation; unrelated.

**Auto Mode is on**: user prefers continuous execution. Don't re-ask
the locked design questions; they're listed under "Decisions log"
below. Course corrections will arrive as user messages.

**Status**: PR 2 not started. The worktree was just created off the
post-PR1-merge master tip; only this handover refresh sits on the
branch so far.

**Next phase to pick up**: PR2.1 — `OutputWriteRegistry` module +
content-hashing helper as a standalone unit-testable thing, no
integration yet. See the PR 2 phase table below for the full sequence
and design-doc references. Each phase gets its own commit, matching
PR 1's pattern; PR1.7's bisect-friendly history made the `.clm-include`
bug (caught by the smoke test) much easier to reason about, so keep
the per-phase shape unless a phase is genuinely trivial.

---

## Why this exists

The AZAV ML `simple_chatbot` package was duplicated by hand into multiple
topic dirs (`slides/module_550_ml_azav/topic_040_gradio_intro/simple_chatbot/`,
`...topic_041_gradio_deep_dive/simple_chatbot/`) and into
`examples/SimpleChatbot/src/simple_chatbot/`. All three are byte-identical
today and drift if the canonical copy changes. Notebooks import `from
simple_chatbot.budget_guard import BudgetGuard` (sibling-directory import,
not a pip install) so the package must physically appear next to each
notebook at execution time.

Feature 1 (PR 1) gives one canonical source location and a
`<include source=... as=...>` declaration on `<topic>`/`<section>` that
virtually splices the source's files under the topic at build time.

Feature 2 (PR 2) makes the build's file-writer idempotent for
identical-content writes and warns on conflicting writes (independent of
Feature 1, but its value is more visible after Feature 1 lands and many
topics legitimately produce the same output paths).

---

## PR 2 — Feature 2 phases

Branch: `claude/output-write-dedup`. Design doc §"Feature 2: Output-Write
Deduplication and Collision Warning" (lines 337–407 of
`design/shared-source-includes-and-output-dedup.md`) is the authoritative
spec; the rows below break it into incremental commits.

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | `OutputWriteRegistry` module + content-hashing helper | [ ] | Standalone unit-testable module; no integration with the build pipeline yet. Probable home: `src/clm/core/output_write_registry.py` (sibling to `image_registry.py`). Per-build singleton recording, for each absolute output path: `(content_hash, first_writer_source_path, dedup_count, conflict_count)`. Hash: BLAKE2b-128 or SHA-256-truncated (speed > cryptographic strength). Path-equality fast path for files >50 MB (skip hashing, log a single `output_large_file_collision` summary). Size threshold should be configurable via env var or build flag — pick the same plumbing pattern as `CLM_HTTP_REPLAY_MODE` etc. Unit tests live in `tests/core/test_output_write_registry.py`: record first write, dedup identical second write, conflict on differing second write, large-file path-equality fast path. **Decision (locked):** no persistence across builds — registry is per-build only. |
| 2 | Hook into `backend.copy_file_to_output()` and the notebook output writer | [ ] | Two real choke points (jupyterlite + plantuml outputs funnel through these). `backend.copy_file_to_output()` lives at `src/clm/core/operations/copy_file.py` + the backend impl; the notebook output writer is `src/clm/workers/notebook/notebook_processor.py:write_other_files_sync` (~line 1529 at PR1 tip; check current line numbers). **Crucial: skip paths owned by `ImageRegistry`** (`src/clm/core/image_registry.py`) so the existing `image_collision` warning channel stays the sole reporter for image paths — no double-warn. Behavior on second write: same hash → increment count + **skip the actual write** + debug-log; different hash → write file (current behavior preserved), emit `output_path_conflict` warning naming both source files + the output path + both hashes, replace registry's first-writer with the latest, increment `conflict_count`. **Watch for the `.clm-include` class of bug from PR1.7**: the `CopyFileOperation` path picks up files that authors don't expect — make sure the registry only logs the files we mean to log, not build-internal artifacts. |
| 3 | `BuildReporter` integration (counts + JSON `output_conflicts` key) | [ ] | Existing reporter at `src/clm/cli/build_reporter.py`. End-of-build summary line: "{N} output paths written multiple times with identical content (deduplicated); {M} output paths had conflicting writes (last writer won)." JSON output gets a new `output_conflicts` key — machine-readable list of `{output_path, first_writer, second_writer, first_hash, second_hash}` entries. Exit code unchanged (warnings, not errors); the `--strict` promotion to errors is captured in "Out-of-scope" below for a future PR. |
| 4 | Tests | [ ] | **Unit** (in addition to phase 1 tests): registry skips `ImageRegistry`-owned paths; `BuildReporter` JSON has the right keys when no conflicts (empty list, not missing). **Integration** (new file `tests/integration/test_output_dedup.py` or similar): build a synthetic course where two topics produce the same path with identical content — verify single write + counted dedup; same path with differing content — verify warning + last-writer-wins + JSON entry. **C# case (most-likely-to-bite real example)**: the C# course's repeated `NUnitTestRunner.cs` pattern (see "Out-of-scope, captured for future" → Feature 2 motivating cases) — build a synthetic miniature with N topics that produce the same runner file, verify `dedup_count == N-1`. **Regression**: existing `ImageRegistry` collision tests must still fire `image_collision` (not the new `output_path_conflict`) for shared images. |
| 5 | Docs + CHANGELOG | [ ] | `clm info commands` mentions the new warning + JSON key on `clm build`; `CHANGELOG.md` `[Unreleased] > ### Added` gets bullets for the dedup + the new reporter key. No new info-topic file — this is a build-time behavior, not a user-facing command. If the size-threshold flag/env-var lands, document it next to existing build flags. |
| 6 | Pre-PR checks | [ ] | `uv run pytest -m "not docker"`, `uv run ruff check src/ tests/`, `uv run ruff format src/ tests/`, mypy via pre-commit. Per CLAUDE.md release rules. |
| 7 | Smoke validation against the AZAV ML build | [ ] | Optional but high-value (mirrors PR1.7). Run a full course build with the new dedup hook enabled; expect the now-deduped writes from the `<include>`-shared `simple_chatbot/` (60 output variants × 7 files = 420 dedup events) to show up in the reporter summary, and zero `output_path_conflict` warnings on a clean course. Don't commit course-repo state; record the dedup-count outcome in the PR body. |

### Call-path audit (pre-PR2.1, 2026-05-11)

Re-read of design §Feature 2 against the current source. The design says
the two real choke points are `backend.copy_file_to_output()` and "the
notebook-output writer", and that "jupyterlite and plantuml outputs
ultimately funnel through these". **That claim is incomplete** — the
worker processes (notebook, plantuml, drawio, jupyterlite-builder) write
their outputs themselves, not through the backend. PR2.2 must plan for
this; PR2.1 (the standalone registry module) is unaffected.

Concrete writers, grouped by reachability from the orchestrator:

**Mediated by the backend (easy to hook):**

- `src/clm/core/operations/copy_file.py:20` → `backend.copy_file_to_output(copy_data)`
- `src/clm/infrastructure/backends/local_ops_backend.py:52` —
  `_copy_file_to_output_sync` → `shutil.copyfile`. Single choke point
  for copy-style writes. Hook here.
- `src/clm/infrastructure/backends/sqlite_backend.py:136` —
  `atomic_write_bytes(output_file, result.result_bytes())` on
  database-cache hit, *replaying* a previously-executed worker result.
  Hook here too (cached fresh writes get the same dedup treatment).
- `src/clm/infrastructure/utils/path_utils.py:419` — `atomic_write_bytes`
  helper. Only one caller today (the cache-hit replay at 136), but it's
  the natural sink if other call sites move through it later. Hooking at
  the call site rather than the helper is cleaner — keeps the helper
  registry-agnostic.

**NOT mediated (worker writes directly, registry blind unless we add a hook):**

- `src/clm/infrastructure/backends/local_ops_backend.py:81` —
  `copy_dir_group_to_output` uses `shutil.copytree`/`copy2` and
  **bypasses `copy_file_to_output` entirely**. This is the exact analogue
  of PR1.7's "top-level filter gap". `<dir-group>` writes will not be
  in the registry unless we hook here separately. Design lists
  dir-groups under §F2.G2 ("works for every output kind") but the call
  path is independent.
- `src/clm/workers/notebook/notebook_worker.py:214` —
  `open(output_path, "w") as f` then writes notebook contents. Runs in
  a worker subprocess (sometimes inside Docker). Orchestrator first
  sees the bytes when sqlite_backend re-reads them at line 434 for the
  DB cache. Practical hook point: `sqlite_backend.py:434`
  (`output_path.read_bytes()`) — register the bytes the orchestrator
  just read. *Tradeoff:* registry sees the write **after** disk;
  dedup-skip is impossible for fresh worker output, only warn-on-conflict
  works. That's acceptable per design (the dedup story shines for
  copy-style writes; conflict warnings are the cross-cutting value).
- `src/clm/workers/plantuml/plantuml_worker.py:195` —
  `open(output_path, "wb") as f`. Same after-the-fact registration via
  sqlite_backend.py:434.
- `src/clm/workers/drawio/drawio_worker.py:189` —
  `open(output_path, "wb") as f`. Same path.
- `src/clm/workers/jupyterlite/builder.py:149,225,281,323`,
  `lite_dir.py:151,254,290,323`, `miniserve.py:131,149,161` — direct
  `Path.write_text` into the output dir, no funnel. JupyterLite outputs
  are a directory tree; sqlite_backend.py:444 explicitly **skips** the
  DB-cache layer for them ("queue cache is authoritative"), so there's
  no readback to piggyback on. If PR 2 needs jupyterlite coverage, the
  worker has to register the writes itself — likely cross-process, so
  either a per-build registry file on disk or scope-restriction to
  in-process orchestrator writes (acceptable for v1).
- `src/clm/workers/notebook/notebook_processor.py:1165` — recorded
  HTTP cassette persistence (writes to `source_topic_dir`, not output).
  **Out of scope** for the registry (it's not an output write).
- `src/clm/workers/notebook/notebook_processor.py:1535` —
  `write_other_files_sync` writes supporting files to the **kernel
  temp dir** (CWD for notebook execution), not the build output dir.
  **Out of scope.** (The design line "notebook output writer" referred
  to where executed notebooks land — that's the worker's
  `notebook_worker.py:214`, not this function.)

**ImageRegistry skip predicate.** `src/clm/core/image_registry.py:60`
keys by relative-from-`img/` path, not absolute output path. The new
registry's "skip ImageRegistry-owned paths" rule translates to:
**skip any write whose source path contains an `img/` segment**
(determined by walking `source_path.parts` for `"img"`, same logic as
`get_relative_img_path`). Doing this at hook entry means the existing
`image_collision` channel remains the sole reporter for image paths;
the new `output_path_conflict` won't double-warn.

**Recommendation for PR2.2 phasing.** Cover the mediated writers first
(`copy_file_to_output` + sqlite cache-hit replay) — those exercise
the registry, the BuildReporter integration, and the dedup-skip
behavior. Bring in dir-groups and the worker readback site
(sqlite_backend.py:434) as a second sub-phase. JupyterLite cross-process
coverage is most likely a follow-up PR; capture under "Out-of-scope".

---

## PR 1 — Shipped (reference card)

Feature 1 shipped in [PR #61](https://github.com/hoelzl/clm/pull/61),
merged 2026-05-11 as commit `f86c36d`. The eight-phase summary:

| Phase | Commit | What |
|---|---|---|
| 1.1 spec parsing | `c122608` | `IncludeSpec`, `_parse_includes`, `_normalize_include_path` in `course_spec.py`; 12 tests in `course_spec_test.py` |
| 1.2 file discovery | `c122608` | `source_origin` + `from_virtual` in `course_file.py`; `apply_includes`, `add_virtual_file` in `topic.py`; `source_path` reads in `copy_file.py`, `process_notebook.py`, `convert_drawio_file.py`, `convert_plantuml_file.py`; 6 tests in `topic_test.py` |
| 1.3 build-pipeline integration | `0f4185b` | `Course._build_topics` calls `SectionSpec.includes_for`; 6 integration tests in `course_test.py` |
| 1.4 validation | `af3b61e` | `spec_validator._validate_includes` + helpers; 5 finding categories; 17 tests in `test_spec_validator.py` |
| 1.5 `clm sync-includes` CLI | `72289b1` | `cli/commands/sync_includes.py` (~470 LOC); copy/symlink/hardlink modes with Windows-friendly fallbacks; per-topic `.clm-include` JSON ledger; 18 tests in `test_sync_includes.py` |
| 1.6 docs | `1835064` | `info_topics/spec-files.md`, `info_topics/commands.md`, `docs/user-guide/spec-file-reference.md`, `CHANGELOG.md` |
| 1.7 smoke + `.clm-include` filter bugfix | `9cd89bd` | Migrated AZAV ML gradio topics, diffed builds, caught 60-file output leak; fixed via `SKIP_FILE_NAMES` in `path_utils.py` + top-level filter in `topic.add_files_in_dir`. Full methodology in "PR1.7 smoke test outcome" below. |
| 1.7c CI fix on Linux | `4a22ed6` | `_normalize_include_path` POSIX backslash handling + Click 8.1/8.2 `CliRunner(mix_stderr=...)` compat |
| 1.8 pre-PR | (no code) | `pytest -m "not docker"`: 4751 passed / 12 skipped / 4 xfailed; ruff + mypy clean |

Full per-phase notes (with the original rationale for each decision)
remain under "PR 1 — Feature 1 phases (historical detail)" further down
in this doc.

---

## PR 1 — Feature 1 phases (historical detail)

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Spec parsing (`IncludeSpec`, parse `<include>` on `<topic>`/`<section>`) | [x] 2026-05-10 (commit c122608) | `src/clm/core/course_spec.py`: `IncludeSpec` dataclass, `_parse_includes`, `_normalize_include_path`, `SectionSpec.includes_for(topic)`. Validates: empty source rejected, `..` in source/`as` rejected, absolute paths rejected, duplicate `as_path` rejected, Windows separators normalized to forward slashes. 12 new tests in `tests/core/course_spec_test.py`. |
| 2 | File discovery (`DirectoryTopic.build_file_map` virtual splice) | [x] 2026-05-10 (commit c122608) | `course_file.py`: `source_origin: Path \| None`, `source_path` property, `from_virtual()`. `topic.py`: `ResolvedInclude` dataclass, `Topic.includes` field, `add_virtual_file()`, `apply_includes()`. Real local files shadow virtual ones (warning `include_shadowed_by_local`). Skips `__pycache__`, `.venv` during recursion. Updated read sites to use `source_path`: `copy_file.py`, `process_notebook.py:compute_other_files`, `convert_drawio_file.py`, `convert_plantuml_file.py`. 6 new tests in `tests/core/topic_test.py`. |
| 3 | Build-pipeline integration (per-section default propagation, override key = `as`) | [x] 2026-05-10 (commit 0f4185b) | `src/clm/core/course.py`: `_build_topics` now calls `section_spec.includes_for(topic_spec)`, joins each `IncludeSpec.source` onto `course_root` (`.resolve()`), wraps it as `ResolvedInclude`, and passes the list as `includes=` to `Topic.from_spec`. Existence enforcement stays in `Topic.apply_includes` so PR1.4 surfaces the same `include_source_missing` from one place. 6 new integration tests in `tests/core/course_test.py` (helper `_make_include_source_dir`): topic-only, section-default propagation across multiple topics, topic-override-by-`as_path`, topic-add-new-`as_path`, optional-missing-source-silent, required-missing-source-error. |
| 4 | Validation (`include_source_missing`, `include_shadowed`, `include_source_is_topic_dir`, `include_dependencies`, `include_section_inheritance`) | [x] 2026-05-10 (commit af3b61e) | `src/clm/slides/spec_validator.py`: `_validate_includes` helper called from `validate_spec`, plus `_emit_section_inheritance`, `_is_inside_topic_dir`, `_find_include_dependencies`. Per-topic findings for missing/shadowed; per-unique-source for topic-dir and dependencies; per section-level include for inheritance audit. `include_target_collision` is raised at parse time by `_parse_includes` (CourseSpecError → ClickException in the validate-spec CLI); no separate runtime check needed. 17 new tests in `tests/slides/test_spec_validator.py`. |
| 5 | `clm sync-includes` CLI command (`copy` default; `symlink`, `hardlink`, `--remove`, `.clm-include` marker, optional `--gitignore`) | [x] 2026-05-10 (commit 72289b1) | `src/clm/cli/commands/sync_includes.py` (~470 LOC). Wired in `src/clm/cli/main.py` next to `validate_spec_cmd`. **Marker shape:** per-topic JSON ledger at `<topic-dir>/.clm-include` (single source of truth — handles file + directory includes uniformly, untracked targets are never overwritten or removed). The design doc says "marker at the copy root"; that wording was implementation-prescriptive — chose a per-topic ledger because it (a) handles bare-file includes without `.<filename>.clm-include` sidecar ugliness, (b) survives mode changes cleanly, (c) makes `--remove` a one-pass walk of the ledger. **Modes:** copy (default) walks via shutil.copy2; symlink uses `os.symlink(target_is_directory=...)` with graceful OSError → copy fallback (covers Windows-without-admin); hardlink calls `os.link` per file under the tree, with per-file copy fallback when filesystems refuse. Switching modes between runs deletes the previous materialization before recreating it. **--gitignore:** writes per-topic `.gitignore` (idempotent), adds both the materialized `as` paths and the `.clm-include` ledger name. **--dry-run:** prints intended actions without disk changes. Unresolved/ambiguous topics with includes emit a warning ("run `clm validate-spec` to diagnose") and skip; required missing source bumps exit code to 1 after processing everything. 18 new tests in `tests/cli/test_sync_includes.py` covering copy directory/file, optional vs required missing source, --remove (ledger entries deleted, untracked files preserved), hardlink, symlink OSError fallback (forced via patched `os.symlink`), POSIX-only symlink success, section default inheritance + topic override, --gitignore writes + idempotency, --dry-run no-op, inferred data dir, no-includes spec, mode switch. |
| 6 | Docs: `info_topics/spec-files.md`, `info_topics/commands.md`, `docs/user-guide/spec-file-reference.md`, `CHANGELOG.md` | [x] 2026-05-10 (commit 1835064) | `spec-files.md`: full `<include>` reference under `<topic>`/`<section>` (attrs `source`, `as`, `optional`, section inheritance, shadow/collision semantics, validation finding categories), plus brief inline mentions in the `<section>` and `<topic>` blocks linking to the new subsection. `commands.md`: `clm sync-includes` between `validate-spec` and `validate-slides`; full options table, modes + fallback behavior, untracked-target protection. `docs/user-guide/spec-file-reference.md`: narrative `<include>` section near `<dir-groups>` plus the migration recipe for replacing hand-copied sources. `CHANGELOG.md`: three bullets under `[Unreleased] > ### Added` covering the element, `validate-spec` findings, and the `sync-includes` CLI. **Topic-ID-before-children** caveat documented inline in both reference docs because ElementTree treats text before the first child as `text`. |
| 7 | Smoke test: migrate ML AZAV `topic_040_gradio_intro` and `topic_041_gradio_deep_dive` per design doc; full build + diff against pre-migration | [x] 2026-05-10 | See "PR1.7 smoke test outcome" below. Migration produces byte-identical output for all 420 spliced `simple_chatbot` files across every output target × language × kind × format. Course-repo migration was reverted (not committed) per design. **Caught a real bug:** the `.clm-include` per-topic ledger was leaking into student/trainer/speaker output as 60 stray files; fixed in this row's work — see PR1.7a below. |
| 7a | Bugfix: filter `.clm-include` (sync-includes ledger) from `Topic.build_file_map` and output | [x] 2026-05-10 (commit 9cd89bd) | `src/clm/infrastructure/utils/path_utils.py`: new `SKIP_FILE_NAMES = frozenset({".clm-include"})` constant; `is_ignored_file_for_course` now also rejects names in this set. `src/clm/core/topic.py`: `Topic.add_files_in_dir` was only filtering subdir descendants — added the same `is_ignored_file_for_course` check to top-level files so the ledger at the topic root is excluded. Two new tests: `path_utils_test.py::test_is_ignored_file_for_course_skips_sync_includes_ledger` and `topic_test.py::test_build_file_map_skips_sync_includes_ledger`. Verified end-to-end by re-running the smoke build: `.clm-include` leak count dropped from 60 → 0. |
| 8 | Pre-PR: `uv run pytest -m "not docker"`, `uv run ruff check`, `uv run ruff format`, mypy via pre-commit | [x] 2026-05-10 | `pytest -m "not docker"`: **4751 passed, 12 skipped, 4 xfailed** in 156.73s (only 3 pre-existing voiceover `--mode` deprecation warnings). `ruff check src/ tests/`: clean. `ruff format --check src/ tests/`: 508 files already formatted. mypy: clean via pre-commit hook on commit `9cd89bd`. Branch is ready for PR creation; user has not authorized push yet. |
| 9 | Post-merge: Linux/Click 8.2 CI fixes | [x] 2026-05-11 (commit 4a22ed6) | After the master merge into the branch, CI revealed two platform-only failures: (a) `_normalize_include_path` returned backslashes unchanged on POSIX because `Path("a\\b")` is a single component there — fixed by explicit `cleaned.replace("\\", "/")` before constructing the Path; (b) `CliRunner(mix_stderr=False)` is rejected by Click 8.2+ (parameter removed, stderr now always separate) — wrapped the constructor in try/except so Click 8.1 and 8.2 both work. Verified locally; PR #61 then merged as `f86c36d`. |

---

## Lessons from PR 1 worth carrying into PR 2

The full smoke-test postmortem is preserved further down ("PR1.7 smoke
test outcome"); these are the items most likely to bite PR 2:

- **`CopyFileOperation` is the choke point you're hooking, and it
  picks up files authors didn't intend to ship.** PR1.7 found this the
  hard way: `Topic.add_files_in_dir` filtered subdir descendants
  through `is_ignored_file_for_course` but let top-level files at the
  topic root through unfiltered. The `.clm-include` ledger landed in
  every output variant as a result. For PR 2, this means the registry
  will see paths that include build-internal artifacts (anything
  matching `SKIP_FILE_NAMES`, `SKIP_OUTPUT_FILE_PATTERNS`, etc.). The
  registry hook should respect those filters consistently — or, more
  defensively, only register paths the build is actually about to
  write, so filtered-out files never enter the registry's view.
- **`CliRunner(mix_stderr=False)` is non-portable.** Click 8.1 needs
  it; Click 8.2+ rejects it (stderr is always separate). `_invoke` in
  `tests/cli/test_sync_includes.py:64` wraps the constructor in
  try/except; copy that pattern for any new CLI tests in PR 2 (none
  are currently planned, but the reporter could grow CLI flags).
- **POSIX vs Windows path normalization.** `Path("a\\b")` on POSIX is
  one component (`as_posix()` returns it unchanged), so any
  user-supplied path string needs `cleaned.replace("\\", "/")` *before*
  hitting `Path(...)` if you want cross-platform normalization. PR 2's
  registry keys are absolute output paths produced by CLM itself, so
  this is unlikely to bite — but if any test ever constructs synthetic
  paths from string literals containing backslashes, watch for the
  POSIX-vs-Windows split.
- **`uv.lock` drift on `exclude-newer` bump.** Master's `1866962`
  re-ran `uv lock` after `27042ca` bumped `pyproject.toml`'s
  `exclude-newer` without re-locking. If a future master commit does
  the same, the first `uv run` in this worktree will regenerate the
  lock and dirty the tree; commit that as a separate `build(uv):
  refresh uv.lock for exclude-newer=YYYY-MM-DD` commit on PR 2 before
  any feature work, same pattern as `1866962`.
- **Pre-commit hooks run ruff → ruff-format → mypy → fast pytest.** A
  commit that fails any hook did **not** happen; fix and create a NEW
  commit (never `--amend`). The hook order means a mypy fix landing
  alongside the change that needed it ends up in the same commit, but
  a ruff F821 surfacing late may need a second commit. Don't try to
  pre-format or pre-mypy; let the hook do its job.
- **Click + GitHub Actions runs newer Click than the worktree.**
  `>=8.1.0` resolved locally to 8.1.8 (Windows venv pinned by
  `uv.lock`) but CI runners installed 8.2+ via `astral-sh/setup-uv`
  with no project lockfile honored. If PR 2 adds a new dep, sanity-check
  the lockfile pin and the CI runner's resolution before assuming local
  green = CI green.

---

## PR1.7 smoke test outcome (2026-05-10)

Migrated both `topic_040_gradio_intro` and `topic_041_gradio_deep_dive`
in the local course repo to use
`<include source="examples/SimpleChatbot/src/simple_chatbot" as="simple_chatbot"/>`.
Workflow:

1. Built `--only-sections "name:Woche 04,name:Z04"` against the
   unmodified spec to `$TEMP/clm-pr17-smoke/before` (had 3 unrelated
   `APIConnectionError` failures from `slides_020_llm_chatbot.py`
   trying to hit a real LLM even with `http-replay=new-episodes` — the
   static `simple_chatbot/` copies still got written for every output
   variant).
2. Backed up the two physical `simple_chatbot/` copies and the spec to
   `$TEMP/clm-pr17-smoke/backup`.
3. Edited the spec (text content `gradio_intro` / `gradio_deep_dive`
   placed *before* the `<include>` child, per the PR1.6 ElementTree
   wrinkle), deleted the two physical copies, ran
   `uv run clm sync-includes <course-repo>/course-specs/machine-learning-azav.xml`
   from the **worktree** dir (not the course repo, whose `.venv` pins
   clm to a git rev predating `sync-includes`). Both ledgers were
   written; materialized files matched the canonical source
   byte-for-byte (excluding `__pycache__/`).
4. Rebuilt the same `--only-sections` slice to
   `$TEMP/clm-pr17-smoke/after`. The first attempt from the course-repo
   `.venv` (PyPI-pinned clm 1.3.3) silently re-copied the materialized
   files as ordinary "other files" — works correctly because materialization
   is just a filesystem op, but **does not exercise** the new virtual
   splice path. To actually exercise PR1.2's `source_origin`-based read
   sites, install the worktree clm into the course-repo venv via
   `uv pip install -e <worktree> --reinstall-package clm` and run
   `UV_NO_SYNC=1 uv run --no-sync clm build ...`. `UV_NO_SYNC=1` matters:
   plain `uv run` re-syncs the env to the lockfile every invocation and
   undoes the override install.
5. Compared the manifests (SHA-256 of every file, relative path keys).
   Findings:
   - **420 of 420** `simple_chatbot/*` files identical between before/after
     (7 source files × 60 output variants = de+en × 3 output targets × 5
     kinds × 3 formats, minus combinations where a target doesn't ship
     that kind/format).
   - **0 .clm-include leaks** in `after/` (after the PR1.7a fix).
     Pre-fix run had 60 stray ledger files in `after/`, one per
     `<output-variant>/<section-dir>/.clm-include`. That was the bug.
   - **4 path-set additions** in `after/` only: HTML for "03 Gradio A
     Configurable Chatbot" Completed/Trainer × de/en. The `before/`
     build's API-failing run never reached those completed-form outputs;
     the cached `after/` run did. Not migration-related.
   - **~78 same-path-different-bytes diffs** outside `simple_chatbot/` and
     `.clm-include` — kernel timestamps in executed notebooks, varying
     partial cell outputs depending on which API cells failed. Not
     migration-related.
6. Reverted everything: spec restored from backup, both `.clm-include`
   ledgers + materialized dirs removed, original `simple_chatbot/`
   copies restored from backup, course-repo git status returned to its
   pre-smoke state (only the two pre-existing unrelated changes remain).
   Course-repo migration is **not committed** in this branch.

**The bug — `.clm-include` leak.** `Topic.add_files_in_dir` (in
`src/clm/core/topic.py`) was applying `is_ignored_file_for_course` only to
subdirectory descendants; top-level files at the topic root went through
unfiltered. PR1.5's per-topic ledger lives exactly there, so it was
picked up as a regular topic file and copied to every output variant by
`CopyFileOperation`. Two-line fix:

- `path_utils.py`: added `SKIP_FILE_NAMES = frozenset({".clm-include"})`
  and a `file_path.name in SKIP_FILE_NAMES` branch in
  `is_ignored_file_for_course`.
- `topic.py`: added `if is_ignored_file_for_course(file): continue` to
  the top-level branch of `add_files_in_dir`.

Tests:
- `tests/infrastructure/utils/path_utils_test.py::test_is_ignored_file_for_course_skips_sync_includes_ledger`
- `tests/core/topic_test.py::test_build_file_map_skips_sync_includes_ledger`

Both pass. Wider sweep (`tests/infrastructure/utils tests/core
tests/cli/test_sync_includes.py tests/slides/test_spec_validator.py`):
539 passed, 1 skipped (POSIX-only symlink test on Windows).

**Adjacent gap, not fixed here.** `clm sync-includes --gitignore` writes
per-topic `.gitignore` files into topic dirs. Those would *also* leak
to output (no `.gitignore` filter in `is_ignored_file_for_course`).
The smoke test didn't use `--gitignore` so it didn't surface, and adding
`.gitignore` to `SKIP_FILE_NAMES` would silently exclude any author's
hand-written topic-level `.gitignore`. Left as a known follow-up to weigh
against typical author usage — captured in "Out-of-scope, captured for
future" below.

---

## PR1.6 wrinkles (worth remembering)

- **Topic ID must precede child elements.** ElementTree treats text
  *before* the first child as `topic_elem.text`; trailing text after a
  child becomes that child's `.tail`. `_parse_topic` reads
  `(topic_elem.text or "").strip()`, so the only safe form when a
  `<topic>` carries `<include>` children is:
  ```xml
  <topic>
      gradio_intro
      <include source="..." as="..."/>
  </topic>
  ```
  Both info-topic and user-guide examples spell this out explicitly so
  authors don't end up with empty topic IDs by accident.
- **`--gitignore` writes per-topic.** The CLI help string says "at the
  course root" but the actual implementation writes
  `<topic-dir>/.gitignore`. Reference docs describe the per-topic
  behavior because that's what ships; the help-string mismatch is a
  candidate for a follow-up cleanup but not blocking.
- **No literal version numbers in info topics.** `{version}` placeholder
  is replaced at output time by `clm info` (see e.g. `commands.md:1` →
  `# CLM 1.3.3 — ...` once rendered). The PR1.6 commit kept this
  convention.

## PR1.5 wrinkles (worth remembering)

- The design doc said "marker file at the copy root"; the implementation
  uses a **per-topic JSON ledger** at `<topic-dir>/.clm-include`
  instead. Rationale documented in the PR1.5 row above. If a reviewer
  pushes back, the alternative is a per-include marker (directory:
  `.clm-include` inside the materialized dir; file: sibling
  `.<filename>.clm-include`), which is more in line with the wording
  but messier in practice.
- `CliRunner(mix_stderr=False)` is used in the tests so `result.stderr`
  is separately inspectable for warning lines emitted via `_warn`. If
  Click changes that default in a future version this may need
  adjusting.
- Hardlink mode silently falls back to copy on per-file `OSError` (e.g.,
  cross-device link). The ledger records `mode: "copy"` when this
  happens (test `test_switching_modes_replaces_materialization` is
  intentionally lenient about the resulting mode for that reason).
- Symlink test is POSIX-only via `pytest.mark.skipif`. The Windows
  fallback path is exercised separately by mocking `os.symlink` to
  raise `OSError`.
- `CourseFile.from_virtual` etc. are not used by sync-includes — the
  command operates entirely on the filesystem-resolved topic paths
  from `topic_resolver.build_topic_map`. No coupling to
  `Course.from_spec`, which would have required `Course` construction
  for what is fundamentally a pre-build action.

---

## Key code surface

### PR 2 entry points (verify line numbers when you start each phase)

- **Backend write hook (primary integration point):**
  `src/clm/infrastructure/backend.py:36` — abstract
  `copy_file_to_output()`; concrete impls at
  `src/clm/infrastructure/backends/sqlite_backend.py:985`,
  `local_ops_backend.py:52`, `dummy_backend.py:27`. Hook the registry
  *here* (or in a wrapper layer that sits between the operation and
  the backend) so every backend benefits.
- **Notebook output writer (secondary integration point):**
  `src/clm/workers/notebook/notebook_processor.py:1529`
  (`write_other_files_sync`). Writes notebook-generated artifacts and
  other-files copies on the worker side; needs the same registry
  awareness so dedup works for notebook output too.
- **Image registry (don't double-warn):**
  `src/clm/core/image_registry.py:61` (`class ImageRegistry`). Skip
  paths owned by this registry in the new `OutputWriteRegistry`. Read
  this class first — it's the closest analogue and may inform the new
  registry's API shape.
- **Reporter (phase 3 lands here):**
  `src/clm/cli/build_reporter.py:13` (`class BuildReporter`). Add
  counts to the in-memory state and an `output_conflicts` key to the
  JSON serialization.
- **Probable new file:** `src/clm/core/output_write_registry.py` for
  the registry module + content-hashing helper. Sibling to
  `image_registry.py`. Test file: `tests/core/test_output_write_registry.py`.

### PR 1 surface (mostly for context now that Feature 1 has shipped)

- Spec parsing: `src/clm/core/course_spec.py` — `parse_sections` (line ~875), `parse_dir_groups` (line ~864 after PR1.1 insertions). `IncludeSpec` (~46), `_parse_includes` (~112), `_normalize_include_path` (~80). `SectionSpec.includes_for` is part of the SectionSpec class (~190).
- Topic resolution: `src/clm/core/topic_resolver.py:60` (`build_topic_map`), `src/clm/core/course.py:540+` (`_build_sections`).
- File discovery: `src/clm/core/topic.py` — `DirectoryTopic.build_file_map`, `add_files_in_dir` (now filters top-level files too — see PR1.7a), `ResolvedInclude` (~32), `Topic.includes` field (~73), `add_virtual_file` (~125), `apply_includes` (~190).
- CourseFile: `src/clm/core/course_file.py` — base (~25), `_find_file_class` (~134). PR1.2 additions: `source_origin` field (~38), `source_path` property (~46), `from_virtual` (~64).
- Output write: `src/clm/core/operations/copy_file.py:20`. PR1.2 changed `input_path=self.input_file.path` → `self.input_file.source_path`.
- Notebook other-files copy read site: `src/clm/core/operations/process_notebook.py:91` (`compute_other_files`, now uses `source_path`).
- Docker mounts: `src/clm/infrastructure/workers/worker_executor.py:147` (`/source` mount). Includes work over the existing single-source mount; no Docker image rebuild needed (see design doc §F1.G3).
- Path-filter constants for the build (relevant when deciding what
  the registry should record): `src/clm/infrastructure/utils/path_utils.py`
  — `SKIP_DIRS_FOR_COURSE`, `SKIP_DIRS_FOR_OUTPUT`, `SKIP_FILE_SUFFIXES`,
  `SKIP_FILE_NAMES` (new in PR1.7a, currently `{".clm-include"}`),
  `SKIP_OUTPUT_FILE_PATTERNS`, plus the predicates
  `is_ignored_file_for_course` and `is_ignored_file_for_output`.

## Decisions log (locked, do not re-litigate)

- 2026-05-10: design locked. Open questions resolved:
  - Section-level include inheritance: **simple inheritance**, with an
    info-level message during validation listing every topic that
    inherits each section default (PR1.4).
  - Include source pointing at a topic dir in `slides/`: **warn but
    allow**.
  - Include source outside the course root: **disallow in v1** (already
    enforced by `_normalize_include_path` rejecting `..` and absolute paths).
  - `OutputWriteRegistry` persistence across builds: **no**.
  - `clm sync-includes` default mode: **`copy`** (lowest friction for
    student clones; `symlink`/`hardlink` opt-in).
  - `<dir-group dedup="silent">` attribute: **not yet** — wait until the
    `output_path_conflict` warning becomes noisy in practice.
- 2026-05-10: **virtual splice via `source_origin`** chosen over
  build-time auto-sync. Trade-off: ~5 read-site updates in operations vs.
  modifying user's source tree on every build. User confirmed this in
  conversation after PR1.2 was prototyped.
- 2026-05-10: PR split confirmed (Feature 1 first, Feature 2 second).

## Wrinkles & gotchas (discovered during PR1.1/1.2)

- **`is_in_dir` resolves both paths** before checking parentage. Virtual
  paths under `topic.path` resolve correctly even though they don't exist
  on disk, so the parentage check works. But `is_in_dir(..., check_is_file=True)`
  calls `member_path.is_file()` which returns False for virtual paths —
  that's why `add_virtual_file` bypasses `matches_path` and writes directly
  into `_file_map`.
- **`Course.from_spec` requires `slides/` to exist** under the course
  root. When writing isolated tests, create `(course_root / "slides").mkdir()`
  before instantiating the course (see `_make_isolated_topic` in
  `tests/core/topic_test.py`).
- **`Course.from_spec` records `topic_not_found`** loading errors when a
  topic ID isn't physically present under `slides/`. Tests that don't lay
  down a real topic dir need to snapshot `course.loading_errors` before
  `topic.build_file_map()` and diff for new entries with categories like
  `include_source_missing`, rather than asserting the list is empty.
- **mypy + attrs `@frozen`**: list-typed fields with multi-branch
  initialization may need an explicit annotation. PR1.1 ran into this
  with `topics: list[TopicSpec] = []` after refactoring a comprehension
  to a `for` loop. Pre-commit's mypy hook catches it.
- **ruff F821 on TYPE_CHECKING-only forward refs in tests**: a function
  signature like `def f() -> "ETree.Element": from xml.etree import
  ElementTree as ETree` trips F821 because the import isn't visible at
  parse time. Either remove the return-type annotation or import at
  module level.
- **Pre-commit hook order matters**: ruff (lint+autofix) → ruff (format)
  → mypy → pytest. A commit that fails the hook did **not** happen — fix,
  re-stage, create a NEW commit. Never `--amend`. PR1.2's commit hit
  this twice (mypy var-annotated, then ruff F821); both fixed in the
  in-flight commit before it landed.
- **`compute_other_files` filters out images** before sending to the
  worker (`is_image_file(file.path)`); virtual image includes (if any)
  reach the worker via the image-handling code path, not the
  `other_files` payload. Not exercised by current tests.
- **`process_notebook.py:202`** still reads `self.input_file.path`
  (the notebook's own source). Notebooks aren't expected to be virtual
  includes (slide files have a specific name pattern that includes only
  match for sibling data files). If a future use case wants virtual
  notebook includes, this site needs updating too.
- **`uv.lock` drift**: the master commit `27042ca` bumped `pyproject.toml`'s
  `exclude-newer` without re-running `uv lock`. First `uv run` in this
  worktree regenerated it; that's commit `1866962` on this branch
  (separate from the feature). If you start a new worktree, this may
  recur.

## Migration recipe (PR1.7 smoke test, mirrored from design doc)

In `course-specs/machine-learning-azav.xml`:

```xml
<topic id="gradio_intro">
  <include source="examples/SimpleChatbot/src/simple_chatbot"
           as="simple_chatbot"/>
</topic>
<topic id="gradio_deep_dive">
  <include source="examples/SimpleChatbot/src/simple_chatbot"
           as="simple_chatbot"/>
</topic>
```

Then:

1. `clm sync-includes course-specs/machine-learning-azav.xml --remove`
   (delete physical copies marked with `.clm-include`; PR1.5 must add
   markers when materializing).
2. `clm sync-includes course-specs/machine-learning-azav.xml`
   (re-materialize as copies; or `--mode=symlink` if author has admin).
3. `clm build` and diff against a pre-migration build.

The current physical copies in `topic_040_gradio_intro/simple_chatbot/`
and `topic_041_gradio_deep_dive/simple_chatbot/` are byte-identical to
`examples/SimpleChatbot/src/simple_chatbot/` (verified via `diff -r` on
2026-05-10), so a successful migration produces zero output diff.

## Out-of-scope, captured for future

- **Filter `--gitignore`-written `.gitignore` files from build output.**
  `clm sync-includes --gitignore` writes per-topic `.gitignore` files
  that would currently leak to student/trainer/speaker output (same
  class of bug as the PR1.7 `.clm-include` leak). The smoke test didn't
  use `--gitignore` so the leak didn't surface, and adding `.gitignore`
  to `SKIP_FILE_NAMES` is a broader behavior change (could silently
  exclude an author's legitimate topic-level `.gitignore`). Decide based
  on whether topic-level author-written `.gitignore` files exist in
  practice in any course repo.
- `--strict` flag promoting `output_path_conflict` warnings to errors.
- **JupyterLite output coverage in `OutputWriteRegistry`.** Workers
  in `clm.workers.jupyterlite` write directly via `Path.write_text`
  to the output dir from a subprocess; sqlite_backend explicitly
  skips the DB-cache layer for jupyterlite (worker_queue cache is
  authoritative). No in-process choke point to hook. Either ship an
  on-disk per-build registry the workers can append to, or scope v1
  to in-process orchestrator writes only. See "Call-path audit"
  above for details.
- Cross-spec sharing (include in spec A pulling from spec B).
- Auto-installation of an include's `pyproject.toml` dependencies into
  the worker environment (we surface the deps via `validate-spec` info
  message but don't install them).
- `<dir-group dedup="silent">` attribute (only if the warning becomes
  noisy in practice).
- C# course `NUnitTestRunner.cs` duplication: not solvable by Feature 1
  (C# has no sibling-import equivalent; runner must compile in-place).
  Feature 2 will detect identical writes and dedupe them silently in
  output, plus flag any drift.
