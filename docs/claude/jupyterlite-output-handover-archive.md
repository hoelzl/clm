<!-- HANDOVER-ARCHIVE — companion to jupyterlite-output-handover.md -->

# Handover Archive: JupyterLite Output Target

> ⚠️ **RETIRED HANDOVER CONTENT — NOT ACTIVE**
>
> This document archives details of phases that have been completed and
> retired from the active handover. It must **not** be used with
> `/resume-feature`, `/implement-next-phase`, or similar commands that
> expect an active work plan — those commands should read the active
> handover document instead.
>
> **Active handover**: [jupyterlite-output-handover.md](./jupyterlite-output-handover.md)

---

## Retired on 2026-04-16

### Phase 1 — Spec plumbing and validation [DONE]

Commit: `4e19ca3` on branch `claude/jupyterlite-phase1`.

**Accomplished**: JupyterLite is recognized by the spec parser and validator,
but produces no output yet. Existing courses build byte-identical artifacts.

**Files**:
- `src/clm/core/course_spec.py` — add `"jupyterlite"` to `VALID_FORMATS`
  (line 272); add `JupyterLiteConfig` dataclass; parse optional
  `<jupyterlite>` child on `<course>` root **and** on each
  `<output-target>`.
- `src/clm/core/output_target.py` — change `OutputTarget.from_spec()` so
  `formats=None` expands to `{"html", "notebook", "code"}` explicitly,
  **not** `VALID_FORMATS`. This is the opt-in gate. Also add
  `effective_jupyterlite_config()` returning target-level if set, else
  course-level (wholesale replacement, not field-merge).
- `src/clm/core/course.py` (or wherever course-level validation lives) —
  cross-validate: target with `jupyterlite` format ⇒
  `effective_jupyterlite_config()` must not be `None`.
- `src/clm/cli/info_topics/jupyterlite.md` — new info topic.
- `src/clm/cli/info_topics/spec-files.md` — document the new format and the
  `<jupyterlite>` block.
- `tests/core/` — regression test pinning the default format set to
  `{"html", "notebook", "code"}`; validation tests for the cross-check.

**Acceptance** (met): all existing tests green; new tests green; a
hand-crafted spec with `<jupyterlite>` + a target requesting the format
passes validation but emits a "not yet implemented" stub on build.

**Implementation notes from the session** (useful context for Phase 2):

- `ALL_FORMATS` was renamed to **`DEFAULT_FORMATS`** (literal frozenset) and
  decoupled from `VALID_FORMATS`. Callers in `src/clm/core/output_target.py`,
  `tests/core/test_output_target.py`, and
  `tests/core/test_multi_target_course.py` were updated. The test at
  `tests/core/test_output_target.py:TestOutputTargetConstants` pins
  `DEFAULT_FORMATS == {html, notebook, code}` and also asserts
  `DEFAULT_FORMATS < VALID_FORMATS` with `jupyterlite` in the difference —
  this is the load-bearing regression test for the opt-in gate.
- `JupyterLiteConfig.from_element()` validates `<kernel>` (required; must be
  `xeus-python` or `pyodide`) and `<app-archive>` (must be `offline` or
  `cdn`). Empty/missing `<launcher>` defaults to `True`.
- `OutputTarget` gained two fields (`jupyterlite`, `course_jupyterlite`) and
  a new method `effective_jupyterlite_config()`. `with_cli_filters()` and
  `from_spec()` propagate both through. `Course.from_spec` passes
  `spec.jupyterlite` as `course_jupyterlite` when constructing targets.
- Cross-validation lives in `CourseSpec.validate()` right after the existing
  duplicate-name/path check. Error message points users at
  `clm info jupyterlite`.
- Phase-1 "stub worker dispatch": `output_specs` in
  `src/clm/infrastructure/utils/path_utils.py` enumerates formats via an
  explicit whitelist (`if "html" in effective_formats`, etc.), so
  `jupyterlite` falls through silently without producing any `OutputSpec`.
  The only visible change is a `logger.warning` in `Course.from_spec` noting
  that the site builder is "not yet implemented (tracked for Phase 2)".
- `TOPICS` dict in `src/clm/cli/commands/info.py` now has four entries;
  `tests/cli/test_info.py::test_topics_registry_complete` pins the count.
- **Don't reintroduce `ALL_FORMATS`** — it's gone by design.

### Retired Status Snapshot

- Design investigation (CLM format architecture, JupyterLite capabilities) —
  complete.
- Design doc written and approved: `docs/claude/design/jupyterlite-output.md`.
- Opt-in model specified: two-gate (course-level config block + explicit
  per-target format listing) with `formats=None` default tightened.
- Tests: 3204 passing (18 new Phase-1 tests).

### Retired Session Notes

None — all session notes from the original handover remained relevant to
Phase 2+ and were preserved in the active handover.
