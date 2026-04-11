# Section Filtering — Implementation Plan

## Status

- Phase 1 — **Done** (2026-04-11). `SectionSpec.enabled` / `SectionSpec.id`
  fields, `parse_sections(keep_disabled=...)`, runtime `Section.id`, and 11
  unit tests in `tests/core/course_spec_test.py::TestSectionEnabledAndId`.
- Phase 2 — **Done** (2026-04-11). `CourseSpec.from_file(keep_disabled=...)`,
  `--include-disabled` on `clm outline` and `clm validate-spec` (plus
  matching MCP parameters on `course_outline` and `validate_spec` tools),
  `validate_spec(..., include_disabled=...)` in
  `src/clm/slides/spec_validator.py` with `(disabled)` suffix on findings
  from disabled sections, and `commands.md` info-topic updates. Tests:
  4 new tests in `tests/cli/test_outline.py::TestOutlineIncludeDisabled`,
  3 new tests in `tests/cli/test_validate_spec.py::TestValidateSpecIncludeDisabled`,
  4 new tests in `tests/slides/test_spec_validator.py::TestValidateSpecIncludeDisabled`,
  4 new tests in `tests/mcp/test_tools.py` on `TestHandleCourseOutline` and
  `TestHandleValidateSpec`.
- Phase 3 — **Done** (2026-04-11). `SectionSelection` frozen dataclass and
  `CourseSpec.resolve_section_selectors(tokens)` in
  `src/clm/core/course_spec.py`. Bare tokens try id → 1-based index →
  case-insensitive substring on de/en. Prefixes `id:`/`idx:`/`name:` force
  a single strategy. Raises `CourseSpecError` on empty input, zero matches,
  ambiguous bare substring, or entirely-disabled selection. Tests: 28 new
  tests in `tests/core/test_section_filtering.py`.
- Phase 4 — **Done** (2026-04-11). `Course.section_selection` field,
  `Course.from_spec(..., section_selection=...)`, and `_build_sections`
  filtering by `resolved_indices` in declared order. `--only-sections`
  click option on `clm build`, `BuildConfig.selected_sections` +
  `resolved_section_selection`, selector resolution in
  `initialize_paths_and_course` with `keep_disabled=True` spec parsing
  and per-skipped-disabled warnings to log + stderr. New branch in
  `process_course_with_backend` that skips `git_dir_mover`, rmtrees only
  the selected-section subdirectories (warns on missing dirs), and skips
  `course.process_dir_group`. Helper `_compute_section_dirs_for_cleanup`
  in `src/clm/cli/commands/build.py`. Tests: 13 new tests in
  `tests/cli/test_build_only_sections.py` covering CLI argument parsing,
  `Course.from_spec` section filtering, directory cleanup set
  computation, section-level cleanup semantics with sentinel files, and
  tolerance for missing section directories.
- Phase 5 — **Done** (2026-04-11). `FileEventHandler` gains
  `selected_section_source_dirs: set[Path] | None` constructor parameter
  and an `_is_in_selected_sections` helper that guards `on_created`
  events. `watch_and_rebuild` computes the set of selected sections'
  topic paths when `config.resolved_section_selection` is set and
  threads it through. `on_file_modified` is unchanged; it relies on
  `course.find_course_file` already returning `None` for files outside
  the filtered `course.files`. Tests: 11 new tests in
  `tests/cli/test_watch_only_sections.py` covering creation event
  filtering (selected/unselected/multi-dir/no-filter), modification
  event behavior, and the private `_is_in_selected_sections` helper
  (nested paths, sibling paths, empty set).
- Phase 6 — **In-repo docs done** (2026-04-11). Updated:
  - `src/clm/cli/info_topics/spec-files.md` — `enabled` / `id`
    attribute reference + example (Phase 1)
  - `src/clm/cli/info_topics/commands.md` — `--include-disabled` on
    `clm outline` and `clm validate-spec` (Phase 2), `--only-sections`
    row in the `clm build` options table plus a new "Iterating on a
    single section" subsection documenting selector syntax, errors,
    and the "does not do" list
  - `src/clm/cli/info_topics/migration.md` — new "Migrating from
    `-build.xml` subset specs to `enabled=\"false\"`" section with
    the 3-step recipe
  - `CHANGELOG.md` — single consolidated `[Unreleased]` entry covering
    all five code phases
  - `CLAUDE.md` — Key Commands block and new "Section Filtering"
    subsection under Recent Features
  - `docs/user-guide/spec-file-reference.md` — `enabled`/`id`
    attribute table under `#### <section>` with a roadmap example
  - `docs/user-guide/README.md` — new "Section Filtering" subsection
    under Key Features with example commands and the dir-group caveat
  - **Still pending (not in this repo):** AZAV ML course migration
    and any sibling-course parallel-spec migration. That work lives
    in the respective course repositories and is tracked against
    each course's own handover. The in-repo changes unblock it.
  Verification: 222 tests pass across all section-filtering +
  affected code paths (`course_spec`, `test_section_filtering`,
  `course`, `outline`, `validate_spec`, `spec_validator`,
  `build_only_sections`, `watch_mode`, `watch_only_sections`,
  `mcp/tools`, `cli/info`) in 6.75s.

## Overview

Implements the [Section Filtering proposal](../../proposals/SECTION_FILTERING.md):
`enabled` attribute on sections and `--only-sections` CLI flag. Six phases,
each independently shippable.

- Phases 1–2 deliver the disabled-sections half of the feature
  (`enabled="false"` + tooling support).
- Phases 3–5 deliver `--only-sections` (resolver, build pipeline, watch mode).
- Phase 6 rolls out documentation and migrates real courses.

Phases 1–2 and 3–5 are two independent critical paths — phase 2 does not
block phase 3. If only one path ships before a release, disabled sections
(phases 1–2) is the safer subset.

## Prerequisites

- Read the full proposal.
- Re-read `src/clm/cli/commands/build.py:process_course_with_backend`
  (around lines 528–595) to understand the full-build flow, especially the
  `git_dir_mover` wrapping and the `process_dir_group` call at the end.
- Re-read `src/clm/core/course.py:collect_output_directories` (lines
  540–560) and the derived `Course.files` / `Course.topics` properties
  (lines 195–200). The plan leans on the fact that filtering
  `Course.sections` cascades automatically.
- Re-read `src/clm/cli/git_dir_mover.py` to confirm the short-circuit on
  `keep_directory=True`.
- Run `pytest -m "not docker"` on master before starting to establish a
  clean baseline.

## Phase 1 — `SectionSpec.enabled` and `SectionSpec.id` — **Done (2026-04-11)**

**Goal:** A spec file with `enabled="false"` on a section parses successfully,
is dropped from `.sections` by default, and is retained with an `enabled`
flag when `keep_disabled=True`. `id` attributes round-trip into both
`SectionSpec` and runtime `Section`.

**Changes:**

- `src/clm/core/course_spec.py`
  - Add `enabled: bool = True` and `id: str | None = None` fields to
    `SectionSpec`.
  - Extend the section-parsing code in `CourseSpec.parse_sections` (or its
    private helpers) to read both attributes.
  - Reject any `enabled` value that is not a case-insensitive match for
    `"true"` or `"false"` with a clear spec error.
  - Add a `keep_disabled: bool = False` parameter to `parse_sections`. When
    `False`, drop disabled sections entirely. When `True`, retain them so
    tools like `--include-disabled` can enumerate the full roadmap.
- `src/clm/core/section.py`
  - Add an optional `id: str | None = None` field to the runtime `Section`.
  - Do **not** add an `enabled` field; the proposal explicitly keeps it out
    of the runtime model.
  - Wherever runtime `Section`s are constructed from `SectionSpec`s,
    propagate `id` through.
- `tests/core/test_course_spec.py`
  - Add the unit tests listed under "Unit tests — tests/core/test_course_spec.py"
    in the proposal's Test Strategy section.

**Verification:**

- `pytest tests/core/test_course_spec.py -v` green.
- `clm build` on an existing course with no new attributes is behaviorally
  identical to master. Compare by running a full build before and after and
  diffing the output tree.
- Hand-craft a tiny test spec that has one `enabled="false"` section whose
  `<topics>` reference a non-existent directory. `clm build` on this spec
  should succeed and produce exactly the enabled sections.

**Out of scope for this phase:** CLI flags, selector resolution, any
`--only-sections` wiring, tooling flag updates, MCP changes.

## Phase 2 — Tooling support for disabled sections — **Done (2026-04-11)**

**Goal:** `clm outline`, `clm validate-spec`, and their MCP counterparts
gain `--include-disabled` / `include_disabled` flags. By default disabled
sections remain invisible.

**Changes:**

- `src/clm/cli/commands/outline.py`
  - Add `--include-disabled` click option.
  - When set, re-parse the spec with `keep_disabled=True` and render
    disabled sections in the output with a `(disabled)` marker in both
    text and JSON formats.
- `src/clm/cli/commands/validate_spec.py`
  - Add `--include-disabled` click option.
  - Default behavior already falls out of `parse_sections`.
  - When `--include-disabled` is set, parse with `keep_disabled=True`,
    validate disabled sections' topics anyway, and suffix each reported
    issue with `(disabled)` so the user can see which findings come from
    deferred roadmap content.
- `src/clm/mcp/tools.py`
  - Thread an `include_disabled` parameter through `handle_course_outline`
    and `handle_validate_spec`.
  - Update the MCP tool schemas in `src/clm/mcp/server.py` (or wherever
    the schemas live).
- `src/clm/cli/info_topics/commands.md` — small addition documenting the
  new flag (defer most doc work to phase 6 but include this flag now so
  the new behavior is discoverable).
- Tests:
  - `tests/cli/test_outline.py` — default hides disabled; `--include-disabled`
    shows them with the marker.
  - `tests/cli/test_validate_spec.py` — default does not report disabled
    topics; `--include-disabled` reports them with `(disabled)` suffix.
  - `tests/mcp/test_tools.py` — cover the new MCP parameter on both
    handlers.

**Verification:**

- All new tests green.
- Manual check: run `clm outline` and `clm validate-spec` on the test spec
  from Phase 1 with and without `--include-disabled`.

**Depends on:** Phase 1.
**Independent of:** Phases 3–5.

## Phase 3 — Selector resolver — **Done (2026-04-11)**

**Goal:** A pure function that takes a list of selector tokens and a
`CourseSpec` (parsed with `keep_disabled=True` so indices are stable and
disabled detection works) and returns the resolved section subset plus
warnings for skipped disabled sections. Still no CLI wiring.

**Changes:**

- `src/clm/core/course_spec.py` (or new `src/clm/core/section_selection.py`,
  decide during implementation based on file size)
  - Define a `SectionSelection` result type:
    ```python
    @frozen
    class SectionSelection:
        resolved_indices: list[int]       # 0-based into disabled-inclusive list
        skipped_disabled: list[str]       # human-readable labels for warning emission
    ```
  - Implement `CourseSpec.resolve_section_selectors(tokens: list[str]) -> SectionSelection`:
    1. Reject empty input (empty list, empty string, whitespace-only
       tokens).
    2. For each token, strip whitespace and detect prefix (`id:`, `idx:`,
       `name:` — case-insensitive).
    3. Bare tokens: try ID match, then 1-based index, then
       case-insensitive substring against `<de>` or `<en>`. Stop at the
       first strategy that yields ≥1 match.
    4. Prefixed tokens: only try the named strategy.
    5. Index matching counts disabled sections (indices are stable under
       `enabled` toggles).
    6. Ambiguous bare substring → raise with the matches listed.
    7. Zero matches → raise with the full section listing (index, id,
       de name, en name).
    8. If a token resolves to a disabled section, add it to
       `skipped_disabled` but do **not** include its index in
       `resolved_indices`.
    9. If after processing all tokens `resolved_indices` is empty but
       `skipped_disabled` is non-empty → raise with the "entire selection
       disabled" message.
- `tests/core/test_section_filtering.py` (new file)
  - Full coverage per the proposal's unit test list.

**Verification:**

- `pytest tests/core/test_section_filtering.py -v` green.
- `pytest -m "not docker"` still passes overall.

**Depends on:** Phase 1.
**Independent of:** Phase 2.

## Phase 4 — `--only-sections` build pipeline — **Done (2026-04-11)**

**Goal:** `clm build spec.xml --only-sections <selector>` performs a
section-level incremental build: rebuilds only the selected sections, skips
dir-groups, leaves unselected section output untouched, and warns on
missing (possibly renamed) section directories.

**Changes:**

- `src/clm/core/course.py`
  - Add `section_selection: SectionSelection | None = None` parameter to
    `Course.from_spec`.
  - After sections are constructed from `spec.sections`, if
    `section_selection` is set, filter the section list to only those
    whose index is in `section_selection.resolved_indices`. Preserve
    declared order.
  - `Course.files` and `Course.topics` cascade automatically — no
    additional filtering needed.
- `src/clm/cli/commands/build.py`
  - Add `@click.option("--only-sections", type=str, default=None, ...)` to
    the `build` command.
  - Extend `BuildConfig` with `selected_sections: list[str] | None` (raw
    tokens with prefixes preserved, for error messages and logging).
  - In `prepare_course` (or wherever the spec is currently parsed):
    1. Parse the spec once with `keep_disabled=True` if
       `selected_sections` is set — we need the disabled-inclusive list
       for selector resolution.
    2. Call `course_spec.resolve_section_selectors(tokens)`.
    3. Emit each entry in `skipped_disabled` as a `logger.warning(...)`
       and a `BuildWarning` so it surfaces in both log and build-reporter
       output.
    4. Pass the `SectionSelection` into `Course.from_spec`.
  - In `process_course_with_backend`, add an explicit branch on
    `config.selected_sections`:
    - Do **not** enter the `git_dir_mover` context manager.
    - Log a prominent info line: *"`--only-sections` mode: incremental
      build of N sections; dir-group processing will be skipped."*
    - For each selected section, compute the per-`(target, lang, kind)`
      `section_dir` (reuse the logic from `collect_output_directories`).
      For each expected dir:
      - If it exists, `shutil.rmtree(section_dir, ignore_errors=True)`.
      - If it does not exist, log the rename warning documented in the
        proposal.
    - Call `course.precreate_output_directories()` (idempotent; still
      needed for Docker worker visibility).
    - Run `execution_stages()` as normal — the worker pipeline
      automatically scopes to `course.files`, which is already filtered.
    - **Skip** the `await course.process_dir_group(backend)` line.
    - Continue with `build_reporter.finish_build()` and the rest of the
      finally block.
  - Factor out a small helper (`_compute_section_dirs_for_cleanup(course)
    -> list[Path]`) if the logic gets long. Keep the diff focused.
- `tests/cli/test_build_only_sections.py` (new file)
  - Integration tests per the proposal's Test Strategy section (rebuild
    one of three sections, sentinel preservation, nonexistent selector
    error, empty selector error, disabled-in-mix warning, rename warning,
    dir-group untouched).

**Verification:**

- `pytest tests/cli/test_build_only_sections.py -v` green.
- Manual check on a real course (pick any small one): build full, then
  `clm build spec.xml --only-sections <one-section>`, verify with
  `ls -lR` or `find ... -mtime` that other sections' files have not been
  touched and the selected section's files have been regenerated.
- `pytest -m "not docker"` green end-to-end.

**Depends on:** Phases 1 and 3.
**Independent of:** Phase 2.

## Phase 5 — Watch mode support — **Done (2026-04-11)**

**Goal:** `clm build spec.xml --only-sections <selector> --watch` correctly
ignores file events outside selected sections. Modifications to unselected
files do nothing; creations in unselected sections are not added to the
course.

**Changes:**

- `src/clm/cli/file_event_handler.py`
  - Add `selected_section_source_dirs: set[Path] | None = None` to
    `FileEventHandler.__init__` and store it on `self`.
  - Add a small private helper `_is_in_selected_sections(self, path:
    Path) -> bool` that returns `True` if the set is `None` (no
    filtering) or if `path` resolves to a location under any of the
    configured source dirs.
  - In `on_file_created`, guard the `_schedule_debounced_task` call on
    `_is_in_selected_sections`.
  - `on_file_modified` needs no change: it already relies on
    `course.find_course_file`, which naturally returns `None` for files
    outside the already-filtered `course.files` list.
- `src/clm/cli/commands/build.py`
  - In `watch_and_rebuild`, when `config.selected_sections` is set,
    compute the set of source directories for the selected sections
    (iterate `course.sections`, collect `topic.source_dir` or equivalent
    per topic in each section) and pass it into `FileEventHandler`.
- `tests/cli/test_watch_only_sections.py` (new file)
  - Cover: modification in unselected section ignored; modification in
    selected section triggers rebuild; creation in unselected section not
    added; creation in selected section added and built.
  - Use a short debounce delay (e.g., `0.05s`) to keep the test fast.

**Verification:**

- New test file green.
- Manual smoke test: run `clm build spec.xml --only-sections w03 --watch`,
  edit a file in another section, confirm no rebuild in the logs. Edit a
  file in w03, confirm rebuild.

**Depends on:** Phase 4.

## Phase 6 — Docs, migration, rollout — **In-repo docs done (2026-04-11); course migrations pending in downstream repos**

**Goal:** Downstream users can discover and adopt the feature. At least
one real course migrates off the `-build.xml` pattern.

**Changes:**

- `src/clm/cli/info_topics/spec-files.md` — document the `enabled` and
  `id` attributes with a short `<section>` example.
- `src/clm/cli/info_topics/commands.md` — document `--only-sections` on
  `clm build` and `--include-disabled` on `clm outline` / `clm validate-spec`.
  Include a short "iterating on one section" example.
- `src/clm/cli/info_topics/migration.md` — short section: "Replacing
  `-build.xml` with `enabled=\"false\"`" with the 3-step migration recipe.
- `docs/user-guide/spec-file-reference.md` — same updates for human
  readers.
- `docs/user-guide/building-courses.md` (or wherever the build guide
  lives) — a new section titled "Iterating on a single section" with the
  `--only-sections` recipe, the rename-warning caveat, and the
  dir-group-skip caveat.
- `CHANGELOG.md` — entry under an unreleased heading summarizing both
  halves of the feature.
- `CLAUDE.md` — add `clm build --only-sections` and the `enabled`
  attribute to the relevant sections (Key Commands and Recent Features).
- Migrate the AZAV ML course (outside this repo, likely):
  1. Add `enabled="false"` to currently-commented sections in
     `machine-learning-azav.xml`.
  2. Remove the commented `<!-- ... -->` blocks.
  3. Verify `clm build machine-learning-azav.xml` succeeds.
  4. Verify `clm build machine-learning-azav.xml --only-sections <one>`
     succeeds.
  5. Delete `machine-learning-azav-build.xml`.
  6. Update any scripts or automation that reference the `-build` path.
- Identify one additional course with a parallel `-build` spec and
  migrate it the same way.

**Verification:**

- `clm info spec-files` and `clm info commands` show the new content
  with correct `{version}` substitution.
- AZAV ML full build and `--only-sections` build both succeed.
- `pytest -m "not docker"` green end-to-end.

**Depends on:** Phases 1–5.

## Risks and mitigations

- **Section indices shift silently when sections are reordered** (not
  toggled — reordered). Users who rely on index-based selectors may be
  surprised. Mitigation: zero-match errors list all sections with their
  current index, and documentation recommends `id:` for stable references.
- **Rename warning false positives on first builds.** A section built for
  the first time has no preexisting output directory, which triggers the
  same warning as a rename. The warning text explicitly mentions "first
  build" so a user seeing it on a fresh spec is not misled.
- **Dir-groups skipped in `--only-sections` mode could confuse users who
  expect a production-ready build.** Mitigation: the "dir-group processing
  will be skipped" log line on entering `--only-sections` mode makes this
  loud and explicit.
- **Watch-mode filtering is pinned at startup.** Adding a section to the
  spec while a filtered watch is running will not pick it up; the user
  must restart. Documented in the watch-mode subsection of the proposal.
- **`enabled` backward-compat silent ignore.** Old `clm` binaries reading
  new-format specs will attempt to build disabled sections and likely
  fail. Given the small user base we accept this as an ask-users-to-upgrade
  issue; see the backward-compat subsection of the proposal.

## Rollback

Each phase is independently revertable:

- **Phases 1–2** are purely additive (new attribute, new flags). Reverting
  them restores full backward compatibility; existing specs never used
  these attributes.
- **Phase 3** is a new file / new function. Reverting removes dead code
  with no external callers.
- **Phase 4** adds a new CLI flag and a new branch in
  `process_course_with_backend`. Reverting disables the flag; the
  full-build path is untouched because it takes a separate branch.
- **Phase 5** changes `FileEventHandler.__init__` by adding an optional
  parameter. Reverting means removing the parameter and its guard; normal
  watch mode is unaffected because the parameter defaults to `None`.
- **Phase 6** is docs + data migration. Reverting the AZAV ML migration
  means restoring `machine-learning-azav-build.xml` and removing
  `enabled="false"` from the full spec; reverting docs means reverting
  the CHANGELOG/README/info-topic edits.

## Sequencing suggestion

- Ship phases 1 → 2 as one PR (or two closely-spaced PRs) for the
  disabled-sections feature. This alone unblocks the AZAV ML migration
  if `--only-sections` is delayed.
- Ship phases 3 → 4 → 5 as three separate PRs for the `--only-sections`
  feature. Phase 3 lands as internal-only machinery; Phase 4 adds the
  CLI; Phase 5 adds watch-mode support.
- Ship phase 6 as the release PR that updates docs and performs the
  AZAV ML migration.
