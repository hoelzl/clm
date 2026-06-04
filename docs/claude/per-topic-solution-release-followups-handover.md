# Handover: Per-Topic Solution Release — Follow-ups (issue #208)

**Branch**: `worktree-logical-jingling-fiddle` ·
**Issue**: [#208](https://github.com/hoelzl/clm/issues/208) ·
**Parent handover** (the shipped 5-step feature): `docs/claude/per-topic-solution-release-handover.md`

The 5-step #208 plan is **complete and pushed** (provenance manifest → release
engine → spec channels + `clm git`/`clm release` push → multi-cohort tests →
recording slide-version provenance). This doc covers only the **remaining
follow-ups**. The biggest, follow-up 1, is a real design task — the rest are
small and independent.

## 1. Overview

Four follow-ups remain, in rough value order:

1. **Step-5 consumer wiring** — populate recording provenance at record time and
   expose it. The drift *primitive* shipped (step 5) but nothing feeds it.
2. **`SharedImageFile` manifest gap** — the one output kind the provenance
   manifest doesn't enumerate.
3. **`clm release week`** — a convenience selector deferred since step 2.
4. **Deferred 3d review finding #6** — a real-build snapshot integration test.

## 2. Design Decisions (carried in / load-bearing)

- **Recordings is an optional extra.** `build` must never import `clm.recordings`.
  The provenance manifest + the shared digest live in `clm.core`
  (`core.provenance_manifest`) precisely so both the release engine and the
  recordings drift check can use them without a `[recordings]` dependency.
- **The manifest is the join key.** A topic is *not* recoverable from an output
  path, so "did this slide change?" must diff a stored per-topic digest against
  the current `.clm-manifest.json` — never re-hash slide files.
- **One digest algorithm.** `core.provenance_manifest.topic_digest_from_files`
  is the single rollup used by the release freeze *and* recording drift; a
  frozen-literal test pins its bytes. Do not fork it.
- **Provenance is stamped by callers, not the model.** The recording fields
  (`section_id`/`topic_id`/`slide_digest`, and the pre-existing
  `git_commit`/`git_dirty`) are optional and default `None`; the live session
  currently passes none. Wiring is a deliberate caller-side concern.

## 3. Phase Breakdown

- **Follow-up 1 — step-5 consumer wiring** `[DONE]` (commit `c4a45f4`, pushed).
  1a record-time stamping + 1b `clm recordings drift` both shipped.
- **Follow-up 2 — `SharedImageFile` manifest enumeration** `[DONE]`.
  `enumerate_expected_outputs` now covers shared-mode images.
- **Follow-up 3 — `clm release week`** `[TODO]` ← ACTIVE NEXT.
- **Follow-up 4 — real-build `--snapshot` manifest-free test** `[TODO]`.

## 4. Current Status

- Steps 1–5 shipped on `origin/worktree-jingling-...` (`de27496` is the step-5
  tip; `113ccdb` the handover). Tree clean, suite green, no blockers.
- **Follow-up 1 DONE** (`c4a45f4`, pushed to
  `origin/worktree-logical-jingling-fiddle`). All 157 new+affected tests
  green (full recordings suite 859; core+cli 1859); ruff + mypy clean;
  pre-commit passed. Decision taken: manifest
  resolution is **convention + override** (`course_root/output`,
  deterministic-first target subdir; `clm recordings drift` adds
  `--source`/`--manifest`/`--spec-file`). `slide_digest=None` → drift `unknown`
  when no manifest, never an error.
- Follow-ups 2–4 not started; each is independent.

### Follow-up 1 — what shipped (for the record)

- `Course.resolve_deck_topic(section_name, deck_name, lang) -> (section_id,
  topic_id)` (`core/course.py`): inverse of the dashboard deck listing
  (`section.name[lang]` / `nb.file_name(lang,"")`). `file_name` guarded for
  split companions; both halves share `topic.id`. Tests:
  `tests/core/test_resolve_deck_topic.py`.
- `core.provenance_manifest.find_course_manifest_path(spec_file=None, *,
  output_root=None)`: convention locator (default `course_root/output`, else
  first sorted `<target>/.clm-manifest.json`). Shared by arm-time stamping and
  the drift command so both read the same target. Tests in
  `tests/core/test_provenance_manifest.py`.
- `recordings/record_provenance.py` — `RecordProvenance` frozen dataclass +
  `build_record_provenance(course, spec_file, section_name, deck_name, lang)`:
  total/never-raising assembler (resolver + `recordings.git_info.get_git_info`
  on the spec's `course_root` + manifest digest). Tests:
  `tests/recordings/test_record_provenance.py`.
- Threading: `ArmedDeck` gained a `provenance` field; `RecordingSession.arm`/
  `record` gained a `provenance=` kwarg; `_sync_state_after_rename` forwards
  the five fields to `ensure_part`/`record_retake`. `self._armed` already flows
  through the OBS-stop rename thread, so no other plumbing was needed. Tests in
  `tests/recordings/test_session.py::TestStateWiring`.
- Web: `/arm` + `/record` call a new `_build_deck_provenance` helper
  (`recordings/web/routes.py`) using `app.state.course` + `app.state.spec_file`;
  failure → all-`None` (recording never blocked).
- 1b: `clm recordings drift COURSE_ID` (`cli/commands/recordings.py`) with
  `--source`/`--manifest`/`--spec-file`/`--all`/`--json`; manifest priority
  `--manifest` > `--source` > `--spec-file` > matching `recordings.courses`
  config entry. Tests: `tests/recordings/test_recordings_drift_cli.py`. Docs:
  `clm recordings drift` added to `info_topics/commands.md`.

### Follow-up 2 — what shipped (for the record)

- `core.provenance_manifest.enumerate_expected_outputs` gained a
  `SharedImageFile` branch (the last unenumerated output kind): shared-mode
  images copy once per (language, audience) into the course-level `img/`
  folder. Path computation mirrors
  `SharedImageFile.get_processing_operation` exactly (`output_path_for` +
  `get_relative_img_path`, audiences derived from `target.kinds` via the new
  `_shared_image_audiences` helper). When several topics share one image file
  name the copies collapse to one path; the manifest path-dedup attributes it
  to the first owning topic — inherent to shared mode (bytes exist once).
- Tests (`tests/core/test_provenance_manifest.py`): a shared-mode round-trip
  (`image_mode="shared"` over test-spec-1) **plus** a path-parity test that
  cross-checks the enumeration against the actual `CopyFileOperation` outputs
  of `get_processing_operation` — the guard against the two path computations
  drifting. No CLI/spec surface change, so no info-topic update.

## 5. Next Steps

### Follow-up 1 — wire recording provenance (the meaty one) — `[DONE]` (`c4a45f4`)

> Shipped as described below; the decision taken was **convention + override**
> for manifest resolution. See §4 "what shipped" for the concrete surface.
> The rest of this subsection is the original design narrative, kept for context.

**Goal.** At record/retake time, stamp each `RecordingPart` with
`section_id`, `topic_id`, `slide_digest` (the topic's current
`manifest_topic_digest`), and the already-existing-but-dormant
`git_commit`/`git_dirty`. Then surface drift with a `clm recordings drift`
command. **One wiring change lights up both the long-dormant git provenance and
the new slide drift.**

**The design gap (read this first).** The session at record time has *none* of
the inputs it needs. The two call sites in
`src/clm/recordings/workflow/session.py` — `_sync_state_after_rename` (~line
1759) — call `record_retake(...)` / `ensure_part(...)` with only
`(lecture_id, part, raw_file[, display_name])`. The only context they have is an
`ArmedDeck` (defined in `session.py`, a frozen dataclass):

```python
@dataclass(frozen=True)
class ArmedDeck:
    course_slug: str
    section_name: str
    deck_name: str
    part_number: int = 0
    lang: str = "en"
    lecture_id: str | None = None
```

`lecture_id` is synthetic: `f"{section_name}::{deck_name}"`
(`recordings/web/routes.py` `_resolve_lecture_id`). So the session knows the
*names* of the section and deck but **not**:
- the course `topic_id` / `section_id` (no `Course`/`CourseSpec` reference),
- the course **source repo path** (needed for `get_git_info`),
- the **build output root** holding `.clm-manifest.json`.

These DO exist, but only in the web layer at `/arm` time: the `Course` is built
and stored at `app.state.course` (`recordings/web/app.py` `_build_course`), and
`RecordingsCourseConfig` (`infrastructure/config.py`) carries `spec_file` and
`course_repo`. The session manager is never handed any of them.

**The decision to make.** *Where is the provenance context assembled?* The clean
answer: assemble it once at `/arm` time (where the `Course`, the spec path, and
the config are all in hand) into a small immutable "record provenance context",
attach it to `ArmedDeck` (or a sibling object the session already receives), and
have `_sync_state_after_rename` pass it straight through. Concretely:

1. **Resolve `(section_name, deck_name) -> (section_id, topic_id)`.** Add a
   lookup on `Course`/`CourseSpec` (the deck name maps to a topic's slide deck;
   the section name to its section). This is the only genuinely new logic — there
   is no existing (section,deck)->topic resolver. Mind split decks and bilingual
   naming; reuse whatever the build uses to name a deck.
2. **Capture git info** via `recordings/git_info.get_git_info(course_repo)` at
   arm time (`course_repo` from `RecordingsCourseConfig`, else the spec file's
   repo root).
3. **Compute `slide_digest`** = `manifest_topic_digest(load_manifest(<output
   root>/.clm-manifest.json), topic_id)`. The output root derives from the spec
   via `resolve_course_paths`/the target path. If no manifest exists yet (course
   not built with the now-default provenance manifest), `slide_digest` is `None`
   and drift will read `unknown` — acceptable and correct.
4. **Thread it through** to both `ensure_part(...)` and `record_retake(...)`
   (they already accept the kwargs since step 5 — just pass them).

Keep every input optional: a deck armed from the CLI/tests without a course
identity must still record (all fields `None`). Do **not** make recording fail
when the manifest or spec is missing.

### Follow-up 1b — `clm recordings drift` surface

Add a `drift` subcommand to the `recordings` group
(`src/clm/cli/commands/recordings.py`, `@recordings_group.command`). It loads the
course recording state (`recordings.state.load_state(course_id)`) and the
`.clm-manifest.json`, then prints `course_recording_drift(state, manifest,
stale_only=True)` — "which videos need re-recording after these slide edits?".
A `--json` mode and (optionally) a web-dashboard badge are natural extensions.
This is read-only and can ship before 1a is fully wired (it just reports
`unknown` until parts carry digests).

### Follow-up 2 — `SharedImageFile` manifest enumeration — `[DONE]`

> Shipped; see §4 "Follow-up 2 — what shipped". Original design note below.

`core/provenance_manifest.py` `enumerate_expected_outputs` covers
notebook/code/HTML, `DataFile`, `DuplicatedImageFile`, and dir-group outputs but
**not** `SharedImageFile` (the course-level `image_mode="shared"` layout) — see
its docstring. Add a branch mirroring the `DuplicatedImageFile` one but using the
shared-mode path computation (`output_path_for`/`get_relative_img_path`,
`is_speaker` from `target.kinds`). Needs a shared-mode test course (the default
fixtures are `image_mode="duplicated"`).

### Follow-up 3 — `clm release week`

A convenience to release a whole section's topics by week selector. Deferred
because the section-selector index space is **disabled-inclusive** (a
`enabled="false"` section still consumes an index) — see
`CourseSpec.resolve_section_selectors` and the `--only-sections` notes. Get the
index semantics right or it silently releases the wrong topics.

### Follow-up 4 — real-build `--snapshot` manifest-free test

3d added unit coverage (`_resolve_write_provenance_manifest` /
`_should_emit_provenance_manifest` matrices + a wiring test) but no integration
test runs a real `clm build --snapshot DIR` and asserts no `.clm-manifest.json`
is anywhere under `DIR`. The existing snapshot integration tests
(`tests/snapshot/test_build_integration.py`) stub `main_build`; add one that
doesn't, as a guard against a future refactor moving the manifest write past the
suppression gate.

## 6. Key Files & Architecture

Provenance primitive (no `[recordings]` dep):
- `core/provenance_manifest.py` — `load_manifest`, `manifest_files_by_topic`,
  `topic_digest_from_files`, `manifest_topic_digest`; `enumerate_expected_outputs`
  (the SharedImageFile gap is here).

Recordings (the wiring targets):
- `recordings/state.py` — `RecordingPart`/`TakeRecord` carry
  `section_id`/`topic_id`/`slide_digest` (+ dormant `git_commit`/`git_dirty`);
  mutators `ensure_part`/`assign_recording`/`record_retake`/`restore_take` accept
  them.
- `recordings/provenance.py` — `part_slide_drift` / `course_recording_drift`,
  `SlideDrift(unknown|current|changed)`. The drift query 1b reports.
- `recordings/git_info.py` — `get_git_info(repo_path)` → `{commit, dirty}`.
  **Currently unused by the live flow.**
- `recordings/workflow/session.py` — `ArmedDeck` + `_sync_state_after_rename`
  (the two call sites to thread provenance into).
- `recordings/web/{app.py,routes.py}` — where the `Course` + `lecture_id` live at
  `/arm` time; the natural place to assemble the provenance context.
- `infrastructure/config.py` — `RecordingsCourseConfig.{spec_file,course_repo}`
  (the source paths the session lacks).
- `cli/commands/recordings.py` — the `recordings` command group (home for 1b).

## 7. Testing Approach

- Follow-up 1 stamping: `tests/recordings/test_session.py` exercises the
  arm/record path through the session; add assertions that a recorded part
  carries the expected `topic_id`/`slide_digest`/`git_commit` given a wired
  course context. The (section,deck)->topic resolver wants its own unit test.
- Follow-up 1b: a `CliRunner` test in `tests/recordings/` invoking `recordings
  drift` over a synthetic state + manifest (mirror the synthetic-source harness
  in `tests/release/test_multi_cohort.py` and the drift tests in
  `tests/recordings/test_provenance.py`).
- Follow-up 2: extend `tests/core/test_provenance_manifest.py` with a shared-mode
  course fixture.
- Follow-up 4: a new test in `tests/snapshot/` that runs a real build.

## 8. Session Notes

- **`get_git_info` is dead in the live flow.** It exists and is tested, but
  `session.py` never calls it — so `git_commit`/`git_dirty` on recordings have
  always been `None` in production. Follow-up 1 is the first time they get
  populated; don't be surprised the field "already existed".
- **Commit-split gotcha (cost a failed commit in step 5).** pre-commit runs mypy
  **repo-wide** and does **not** stash *untracked* files. Splitting a change so
  that an untracked new module depends on staged-but-stashed edits in a tracked
  file makes mypy fail against the stashed (old) tree. Land interdependent new
  files together, or stage the tracked dependency in the same commit.
- **Drift `unknown` ≠ `current`** is intentional and must survive any refactor:
  an unprovenanced or absent-from-manifest recording must never report up to
  date, or the surface lies about staleness.
- **Worktree needs its own venv** (`uv sync --extra all`); the recordings suite
  is large (~844 tests) but fast under xdist.
