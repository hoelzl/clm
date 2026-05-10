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
0f4185b  feat(course): resolve <include> entries during section build
c122608  feat(spec): add <include> element with virtual file splice
1866962  build(uv): refresh uv.lock for exclude-newer=2026-04-20
27042ca  build(uv): bump exclude-newer to 2026-04-20   <-- master tip
```

**Test command**: `uv run pytest -x -q` (fast suite, ~80s, runs via pre-commit too).
Last green: 4605 passed (4599 baseline + 6 new include integration tests).

**Auto Mode is on**: user prefers continuous execution. Don't re-ask the
locked design questions; they're listed under "Decisions log" below. Course
corrections will arrive as user messages.

**Next phase to pick up**: PR1.4 — validation pass in
`src/clm/cli/commands/validate_spec.py`. See the "PR 1 — Feature 1 phases"
table below for the full list of validation categories to surface.

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
| 4 | Validation (`include_source_missing`, `include_target_collision`, `include_shadowed`, `include_dependencies`, `include_section_inheritance`, `include_source_is_topic_dir`) | [ ] | **Next.** Touches `src/clm/cli/commands/validate_spec.py` (and/or wherever the existing checks live). |
| 5 | `clm sync-includes` CLI command (`copy` default; `symlink`, `hardlink`, `--remove`, `.clm-include` marker, optional `--gitignore`) | [ ] | New file under `src/clm/cli/commands/`. |
| 6 | Docs: `info_topics/spec-files.md`, `info_topics/commands.md`, `docs/user-guide/spec-file-reference.md`, `CHANGELOG.md` | [ ] | Per CLAUDE.md "Info Topics Maintenance Rule" — version-accurate, `{version}` placeholder. |
| 7 | Smoke test: migrate ML AZAV `topic_040_gradio_intro` and `topic_041_gradio_deep_dive` per design doc; full build + diff against pre-migration | [ ] | Course repo: `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\`. Don't commit the course-repo migration in this PR — record recipe and confirm it works locally. |
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

## PR1.4 detail (next phase)

**Goal**: surface include-related issues from `validate-spec` so authors
catch problems before a build runs. The build itself already records
errors (`include_source_missing`) and warnings
(`include_shadowed_by_local`) via `course.loading_errors` /
`course.loading_warnings`; PR1.4 adds **spec-level** checks that don't
require constructing a Course, plus an info-level message describing
section-default inheritance.

**Categories to surface** (per the design doc and the phase row above):

- `include_source_missing` — `source` path does not exist under the
  course root. (Required-only; optional includes stay silent.)
- `include_target_collision` — two topics in the same output dir would
  produce the same on-disk file via includes (precursor to Feature 2's
  `output_path_conflict`; PR1.4 raises only the spec-detectable case
  where both topics include the same file under the same `as_path`
  *and* live under the same section's output dir).
- `include_shadowed` — a real file already exists at
  `topic.path / include.as_path / ...`. Static spec-level mirror of the
  build-time `include_shadowed_by_local` warning so authors find it
  without doing a full build.
- `include_dependencies` — info-level message listing the include's
  `pyproject.toml` deps (if present), so authors notice they need to be
  added to the worker env. (No installation in v1.)
- `include_section_inheritance` — info-level message: for each
  section-level include, list every topic that inherits it (and which
  topics override). Decision #1 from the locked design.
- `include_source_is_topic_dir` — warning when `source` resolves into
  `slides/.../topic_*` (decision: warn but allow).

**Files**:

- `src/clm/cli/commands/validate_spec.py` — primary entry point.
- Existing checks in the same file are good prior art for output style
  and severity levels.

**Tests**: under `tests/cli/` (mirror existing validate-spec tests).
Cover one happy-path (no findings) and one for each category above.

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
