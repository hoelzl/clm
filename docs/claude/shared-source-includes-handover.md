# Handover — Shared-Source Includes & Output-Write Dedup

Companion to
[`docs/claude/design/shared-source-includes-and-output-dedup.md`](design/shared-source-includes-and-output-dedup.md)
(locked 2026-05-10). Tracks implementation progress across two PRs.

---

## Start here (fresh session)

**Worktree**: `C:\Users\tc\Programming\Python\Projects\clm\.claude\worktrees\curious-twirling-owl`
(do all work from there; do NOT `cd` to the main repo).

**Branch**: `claude/shared-source-includes` (already checked out in the worktree).

**Last commits on the branch**:

```
9cd89bd  fix(core): exclude .clm-include ledger from topic file map  <-- PR1.7
1a9c967  docs(handover): mark PR1.6 complete; pivot to PR1.7 smoke test
1835064  docs: document <include> element and clm sync-includes  <-- PR1.6
e98faaa  docs(handover): record PR1.5 commit hash
72289b1  feat(cli): add clm sync-includes command            <-- PR1.5
af3b61e  feat(validate-spec): surface <include> spec issues
10321a5  docs(handover): record PR1.3 commit hash
0f4185b  feat(course): resolve <include> entries during section build
c122608  feat(spec): add <include> element with virtual file splice
1866962  build(uv): refresh uv.lock for exclude-newer=2026-04-20
27042ca  build(uv): bump exclude-newer to 2026-04-20   <-- master tip
```

**Test command**: `uv run pytest -x -q` (fast suite, ~62s, runs via pre-commit too).
Last green: 4639 passed at PR1.6 (docs-only commit). One known flaky session
test (`tests/recordings/test_session.py::TestShortTake::test_short_take_can_be_followed_by_real_take`)
times out under xdist load but passes in isolation — unrelated to this feature.

**Auto Mode is on**: user prefers continuous execution. Don't re-ask the
locked design questions; they're listed under "Decisions log" below. Course
corrections will arrive as user messages.

**Next phase to pick up**: PR1.8 — pre-PR checks. Run
`uv run pytest -m "not docker"` (Docker tests run in CI only),
`uv run ruff check src/ tests/`, `uv run ruff format src/ tests/`, and
let pre-commit run mypy. Fix anything that comes up, then this branch
is ready to push. The branch needs at minimum: PR1.7's `.clm-include`
filter fix (`path_utils.py` + `topic.py` + two tests) committed before
PR1.8 runs, plus a CHANGELOG bullet noting the fix.

**PR1.7 — done 2026-05-10** (smoke test in `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\`). Caught and fixed a real bug along the way: the
`.clm-include` per-topic ledger was leaking into student/trainer/speaker
output (60 stray files). Fix landed in this row; see the PR1.7 + PR1.7a
rows in the phases table below, and "PR1.7 smoke test outcome" for the
diff methodology.

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

## PR 1 — Feature 1 phases

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
| 8 | Pre-PR: `uv run pytest -m "not docker"`, `uv run ruff check`, `uv run ruff format`, mypy via pre-commit | [ ] | Per CLAUDE.md release rules. |

## PR 2 — Feature 2 phases

Starts after PR 1 merges. Branch name TBD.

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | `OutputWriteRegistry` module + content hashing helper | [ ] | Probably under `src/clm/core/`. |
| 2 | Hook into `backend.copy_file_to_output()` and the notebook output writer | [ ] | Skip paths owned by `ImageRegistry` (it already does this for shared images). |
| 3 | `BuildReporter` integration (counts + JSON `output_conflicts` key) | [ ] | Existing reporter at `src/clm/cli/build_reporter.py`. |
| 4 | Tests (unit + integration with synthetic two-topic collision; one with the C# repeated `NUnitTestRunner.cs` pattern) | [ ] | |
| 5 | Docs + CHANGELOG | [ ] | |
| 6 | Pre-PR checks | [ ] | |

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

## Key code surface (frozen at design time, with current line numbers
where they shifted)

- Spec parsing: `src/clm/core/course_spec.py` — `parse_sections` (line ~875), `parse_dir_groups` (line ~864 after PR1.1 insertions). `IncludeSpec` (~46), `_parse_includes` (~112), `_normalize_include_path` (~80). `SectionSpec.includes_for` is part of the SectionSpec class (~190).
- Topic resolution: `src/clm/core/topic_resolver.py:60` (`build_topic_map`), `src/clm/core/course.py:540+` (`_build_sections`).
- File discovery: `src/clm/core/topic.py` — `DirectoryTopic.build_file_map`, `add_files_in_dir`, plus PR1.2 additions: `ResolvedInclude` (~32), `Topic.includes` field (~73), `add_virtual_file` (~125), `apply_includes` (~190).
- CourseFile: `src/clm/core/course_file.py` — base (~25), `_find_file_class` (~134). PR1.2 additions: `source_origin` field (~38), `source_path` property (~46), `from_virtual` (~64).
- Output write: `src/clm/core/operations/copy_file.py:20`, `backend.copy_file_to_output()`. PR1.2 changed `input_path=self.input_file.path` → `self.input_file.source_path`.
- Notebook other-files copy: `src/clm/workers/notebook/notebook_processor.py:1529` (`write_other_files_sync`). Read in `compute_other_files` at `src/clm/core/operations/process_notebook.py:91` now uses `source_path`.
- Docker mounts: `src/clm/infrastructure/workers/worker_executor.py:147` (`/source` mount). Includes work over the existing single-source mount; no Docker image rebuild needed (see design doc §F1.G3).
- Image registry (don't double-warn from PR2): `src/clm/core/image_registry.py:62`.

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
