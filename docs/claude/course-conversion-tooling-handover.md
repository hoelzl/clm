# Handover: course-conversion tooling gaps → first-class `clm` commands

**Status:** in progress (started 2026-06-04)
**Branch:** `claude/course-conversion-tooling-gaps`
**PR:** [#230](https://github.com/hoelzl/clm/pull/230) (currently docs-only)
**Source of work:** `docs/claude/course-conversion-tooling-gaps.md` (the 9 gaps)

## Why this exists

A conversion agent brought the PythonCourses corpus up to the CLM 1.8 validator
(escalated `slide_id` / DE-EN-adjacency warnings → errors) and had to reimplement
several recurring operations as **throwaway Python scripts** because CLM has no CLI
for them. Each recurs on every course conversion / corpus-wide validator bump. The
gaps doc (`course-conversion-tooling-gaps.md`) lists 9, high-to-low value. This
handover tracks turning them into shipped commands.

## The 9 gaps (priority order from the doc)

1. **Spec → deck resolution + reverse lookup** (HIGHEST) — `clm spec decks`,
   `clm slides referenced-by`. A deck-filename-stem heuristic silently missed 20
   shipping decks because a `<topic>` resolves to a *directory* and CLM builds
   **every** `slides_*.py` in it. ← **doing this first.**
2. **Deep validation scoped to a spec / shipping set, + category rollup** —
   `clm validate <spec> --deep` / `--shipping-only` / `--summary`. Today
   `clm validate <spec.xml>` only checks spec structure + topic resolution, NOT
   the slide content of the referenced decks (real footgun).
3. **Corpus readiness-gate orchestrator** — `clm course gate <spec-or-dir>
   [--apply]`: run mechanical passes in order, emit a mechanical-done-vs-needs-author
   readiness report.
4. **Scope mints to part of the corpus** — `--only bilingual|split`,
   `--exclude <glob>`, `--shipping-only` on `assign-ids` / `normalize`.
5. **Hard-refusal worklist with cell context** — `assign-ids --report-refusals
   [--context]`, and/or an `--interactive` mint mode.
6. **Slug-quality report** for content-derived ids — flag single-token,
   code-identifier-shaped, truncated slugs.
7. **Unreferenced / orphan / cruft detection** — `clm spec orphans <specs-dir>`,
   grouped by likely intent (`_old` superseded vs `_short`/`_long`/`_partN` alternates).
8. **DE/EN completeness report** — `clm slides coverage-report <dir-or-spec>`:
   per-deck DE-only / EN-only / balanced / N-cell imbalance.
9. **Assisted interleave** for structurally-diverged DE/EN (`--interactive`).

Plus smaller notes in the doc: pair-fill lint, tag-migration `completed`-without-`start`
side effect, and the `validate <spec>` vs `--deep` docs surprise.

## Codebase anchors (verified this session)

The exact APIs the throwaway scripts reinvented already exist — the work is wiring
them to CLI commands, faithfully mirroring **build** resolution semantics so the
"shipping set" is correct.

- **Spec parse:** `clm.core.course_spec.CourseSpec.from_file(path)`. Iterate
  bindings with `spec.iter_topic_bindings()` → `TopicBinding(section, topic_spec,
  effective_module)` (per-topic `module=` overrides section default; `None` = unbound).
- **Topic → decks:** `clm.core.topic_resolver.build_topic_map(slides_dir)` →
  `dict[str, list[TopicMatch]]`; `TopicMatch.slide_files: list[Path]` is the decks
  a topic dir contributes (every `slides_*.py`, via `find_slide_files`).
- **Binding → matches:** `topic_resolver.matches_for_binding(full_map, topic_id,
  module)`. **Build semantics (must mirror):** when *unbound* and a topic ID has
  multiple matches across modules, build picks **first-occurrence-wins**
  (`Course._build_topic_map`, `course.py:1005-1011` / `_topic_path_map[...] =
  matches[0].path`). Module-bound references pick the match in that module.
- **Paths:** `clm.core.course_paths.resolve_course_paths(spec_file, data_dir=None)`
  → `(course_root, default_output_root)`; spec lives in a subdir so `course_root`
  is the spec's grandparent. **`slides_dir = course_root / "slides"`.**
- **Language tag of a deck file:** `clm.slides.pairing.split_lang_tag(path)` →
  `"de"` / `"en"` / `None` (prefix-agnostic; `None` = bilingual deck serving both).
- **Existing reference command:** `clm topic resolve` (`cli/commands/resolve_topic.py`)
  already does single-topic resolution with `--course-spec` scoping and `--json` —
  the new commands follow its shape.

## CLI wiring conventions

- Groups are defined in `cli/commands/_groups.py` and registered in `cli/main.py`
  (`slides_group`, `topic_group`, `authoring_group` exist; **there is no `spec`
  group yet** — add one). Top-level `validate` is registered at `main.py:140`.
- `main.py:160-176` shows the `group.add_command(cmd, name="...")` pattern.
- Tests live in `tests/cli/` (`test_resolve_topic.py`, `test_validate_spec.py` are
  the closest models). Use Click `CliRunner`; see the
  [[feedback-click-82-clirunner-compat]] memory — CI runs Click 8.2+ (no
  `mix_stderr`), so locate JSON in `result.output` by braces, wrap the runner
  constructor in try/except.
- Info topics: update `src/clm/cli/info_topics/commands.md` for any new command
  (CRITICAL per CLAUDE.md — downstream agents rely on it). Use `{version}`, never a
  hardcoded number.
- `CHANGELOG.md` → `## [Unreleased]` → `### Added`.

## Plan for Tool #1 (in progress)

A new shared core helper + two thin commands + a reverse lookup:

1. **Core helper** (new module `clm/core/spec_decks.py`, or extend
   `topic_resolver.py`): `resolve_spec_decks(spec, slides_dir) -> SpecDeckResolution`
   that mirrors build semantics (first-occurrence-wins unbound, module-bound
   otherwise), returning per-topic resolved deck files + any unresolved topics +
   first-occurrence-shadowed duplicates. Single source of truth so #2/#3/#7 reuse it.
2. **`clm spec decks <spec.xml> [--lang de|en|both] [--json]`** — list resolved
   deck paths the spec pulls in. `--lang de` keeps bilingual (`None` tag) + `.de`
   halves; `--lang en` keeps bilingual + `.en`; `both` (default) keeps all.
3. **`clm spec decks --all-specs <specs-dir> [--json]`** — union "shipping set"
   across every `*.xml` spec, each deck annotated with the referencing spec(s).
4. **`clm slides referenced-by <deck.py> [--specs-dir DIR] [--json]`** — reverse
   lookup: which spec/topic pulls a deck in (or "unreferenced").

Tests, `commands.md`, and a CHANGELOG `Added` entry land with the code.

## Progress log

- 2026-06-04: gaps doc committed (`48bdf1c`); branch pushed; PR #230 opened.
- 2026-06-04: handover written; codebase anchors verified. Starting Tool #1.
- 2026-06-05: **Tool #1 DONE** (`cb720cb0`). `clm.core.spec_decks`
  (`resolve_spec_decks` / `find_deck_references`) + `clm spec decks` (new `spec`
  group) + `clm slides referenced-by`. `--lang`, `--all-specs`, `--json`. 18 tests
  (`tests/core/test_spec_decks.py`, `tests/cli/test_spec_decks.py`); `commands.md`
  + CHANGELOG updated. Smoke-tested on the CSharpCourses corpus (64-deck spec,
  multi-spec union, reverse lookup).
- 2026-06-05: consolidated onto PR #230 (rebased the 3 local commits onto the
  gaps-doc commit `48bdf1c`, pushed as a fast-forward); PR title/body updated.
  **Note:** PR branch `claude/course-conversion-tooling-gaps` is checked out in
  the *sibling* worktree `encapsulated-nibbling-feigenbaum`, so it cannot be
  checked out here. This worktree works on its own branch
  `worktree-linked-honking-dolphin` and pushes with an explicit refspec:
  `git push origin worktree-linked-honking-dolphin:claude/course-conversion-tooling-gaps`.
- 2026-06-05: **Tool #2 DONE**. `clm validate <spec> --deep` (structure + deck
  content over the shipping set, reusing `resolve_spec_decks`), `--summary`
  (category/kind/per-deck rollup; new `clm.slides.validation_summary`),
  `--shipping-only` + `--specs-dir` for directory validates (new
  `clm.core.spec_decks.shipping_set` + `clm.slides.validator.validate_files`).
  Found+handled a real bug: `find_slide_files_recursive` deep-walks `*.py` only,
  so `--shipping-only` filters the resolved shipping set instead of walking
  (covers `.cs`/`.cpp`). 27 new tests (`tests/slides/test_validation_summary.py`,
  `tests/cli/test_validate_deep.py`); 203 existing validate tests still green;
  `commands.md` + CHANGELOG updated. Smoke-tested on CSharpCourses (clean corpus →
  0 findings; `--shipping-only` root=89 decks, one module=33). **Next: Tool #3
  (course-gate orchestrator).** Reuse `resolve_spec_decks` (shipping set) +
  `validate_course`/`validate_files` + `summarize_findings`; sequence the
  mechanical passes (`assign-ids --accept-content-derived` → `normalize
  --operations tag_migration` → sync) and split remaining findings into
  mechanically-fixable vs needs-author.
- 2026-06-05: **Tool #3 DONE**. `clm course gate <spec-or-dir> [--apply]` (new
  `course` group). `clm.slides.course_gate.run_course_gate` drives the mechanical
  passes via `normalize_file(operations=[tag_migration, workshop_tags,
  interleaving, slide_ids], assign_options=accept_content_derived, dry_run=not
  apply)` — the normalizer already orchestrates assign-ids + tag-migration and
  surfaces **hard refusals / similarity_failure / count_mismatch as
  `review_items`**, which IS the needs-author signal (no separate classifier
  needed). Dry-run writes nothing; `--apply` writes + re-validates + reports
  residual. Exit non-zero while author work / post-apply error remains (CI gate).
  15 new tests (`tests/{slides,cli}/test_course_gate.py`); 135-test regression
  (normalizer + assign-ids) green; `commands.md` + CHANGELOG updated. Smoke-tested
  on CSharpCourses (dry-run clean, 0 writes confirmed via git status). **Next:
  Tool #4 (scope mints — `--only bilingual|split` / `--exclude` / `--shipping-only`
  on `assign-ids`/`normalize`).** `course gate` already scopes internally, so #4's
  predicates (`split_lang_tag`-based bilingual/split filter; glob exclude) could
  live in a shared helper both reuse.
- 2026-06-05: **Tool #4 DONE**. `--only bilingual|split` / `--exclude GLOB` /
  `--shipping-only` (+`--specs-dir`/`--data-dir`) on both `clm slides assign-ids`
  and `clm slides normalize`. New `clm.slides.deck_scope` (`filter_decks`,
  `course_root_for_path`, `resolve_shipping_set`); shared CLI helper
  `cli/commands/shared.py::resolve_scoped_files` + `has_deck_scope`. Refactor:
  extracted public `assign_ids_in_files` (from `assign_ids_in_directory`) and
  `normalize_files` (from `normalize_directory`) — the directory funcs now
  delegate, so split-pair parity is preserved on a scoped subset. Scoping is
  directory-only (single file / spec → UsageError). 20 new tests
  (`tests/slides/test_deck_scope.py`, `tests/cli/test_deck_scope_cli.py`);
  144-test assign-ids/normalize/gate regression green. `commands.md` + CHANGELOG
  updated. Smoke-tested on a crafted tree (bilingual/split/archive). **Next: Tool
  #5 (hard-refusal worklist with cell context — `assign-ids --report-refusals
  [--context]`).** Refusals already carry `file:line` + `proposed_slug/title`; #5
  adds the cell body + nearest preceding heading/slide_id so an author/agent can
  fill ids efficiently. Could also feed the gate's needs-author output.
- 2026-06-05: **Tool #5 DONE**. `clm slides assign-ids --report-refusals
  [--context]`. New post-processing module `clm.slides.refusal_report`
  (`build_refusal_worklist` / `render_worklist` / `worklist_to_dict`) layered over
  `AssignResult.refusals` — the engine is unchanged. `--report-refusals` swaps the
  assignment listing for a worklist (hard refusals first, then soft); `--context`
  (implies `--report-refusals`) re-reads each affected file *once*, finds the
  refused cell by `line_number`, and attaches its marker, body (trailing-blank
  stripped), and the nearest preceding `slide_id`/heading (independent backward
  scans via `extract_heading` + `strip_preserve_marker`). Without `--context` no
  files are re-read. Honors scoping flags + `--json`; exit codes unchanged (2 on
  hard refusal). 18 new tests (`tests/slides/test_refusal_report.py`,
  `tests/cli/test_assign_ids_refusals.py`); 86-test assign-ids/scope regression
  green; ruff/mypy clean. `commands.md` + CHANGELOG updated. **Next: Tool #6
  (slug-quality report for content-derived ids — `assign-ids --flag-low-quality`
  or `clm slides slug-report`: single-token / code-identifier-shaped / truncated
  slugs).**

## Resuming

Pick up from the "Progress log" tail. Tools #1–#5 are complete; **Tool #6 is
next**; tools #7–#9 follow in priority order. Pushing: this worktree is on
`worktree-linked-honking-dolphin`; push to the PR branch with the explicit
refspec noted in the 2026-06-05 consolidation entry above. Each tool = core helper reuse + thin command(s) + tests +
`commands.md` + CHANGELOG. Keep mirroring build resolution semantics — the whole
point of gap #1 is that ad-hoc heuristics silently miss decks.
