# External Planning Documents

CLM is consumed by other repositories (primarily `hoelzl/PythonCourses`,
which uses CLM to build multi-language course materials). Major CLM
features whose requirements originate in course-authoring work are
planned in the consuming repo, not here — that's where the
requirements, constraints, and acceptance criteria live, and the
single-source-of-truth avoids drift between the design and the
authoring need that drove it.

**If you're about to design or implement a CLM feature, check this
index first.** A plan may already exist; updating it is cheaper than
re-deriving the requirements.

## Active plans

| Feature | Plan | Notes |
|---|---|---|
| Slide format redesign — slide_id rollout, language-split source format, LLM-driven voiceover coverage check, build-snapshot harness, CLI cleanup & verb-grouping | `hoelzl/PythonCourses`: [`docs/handover-slide-format-redesign-clm.md`](https://github.com/hoelzl/PythonCourses/blob/master/docs/handover-slide-format-redesign-clm.md) | CLM-side phases 0-8. Phases 0-5 shipped; 6 (split-source build), 7 (cross-language sync), 8 (sibling Jinja macros for non-Python prog_langs) pending. Course-side companion in the same repo at `docs/handover-slide-format-redesign-course.md`. |
| `voiceover compare` + artifact cache | `hoelzl/PythonCourses`: [`planning/CLM_VOICEOVER_COMPARE_SPEC.md`](https://github.com/hoelzl/PythonCourses/blob/master/planning/CLM_VOICEOVER_COMPARE_SPEC.md) | Purpose-built "what changed between two slide revisions" command plus shared artifact-reuse layer. |
| Section/topic module binding | `hoelzl/PythonCourses`: [`planning/CLM_SECTION_MODULE_BINDING_DESIGN.md`](https://github.com/hoelzl/PythonCourses/blob/master/planning/CLM_SECTION_MODULE_BINDING_DESIGN.md) | Disambiguation of `<topic>` references when the same topic suffix exists in multiple modules. Partially shipped — check status before extending. |
| `validate_slides` voiceover_gap on bilingual slides | `hoelzl/PythonCourses`: [`planning/CLM_VALIDATE_SLIDES_VOICEOVER_GAP_LIMITATION.md`](https://github.com/hoelzl/PythonCourses/blob/master/planning/CLM_VALIDATE_SLIDES_VOICEOVER_GAP_LIMITATION.md) | Known false-positive in the `voiceover_gap` review check. |
| `voiceover sync` Windows crash (bug report) | `hoelzl/PythonCourses`: [`planning/CLM_VOICEOVER_SYNC_CRASH_REPORT.md`](https://github.com/hoelzl/PythonCourses/blob/master/planning/CLM_VOICEOVER_SYNC_CRASH_REPORT.md) | Exit code 127 on Windows when running `clm voiceover sync`. |
| Original tooling spec (motivation + early scope) | `hoelzl/PythonCourses`: [`planning/CLM_TOOLING_SPEC.md`](https://github.com/hoelzl/PythonCourses/blob/master/planning/CLM_TOOLING_SPEC.md) | Older design context for CLM's authoring-assistance role. Useful background reading; the slide-format-redesign plan supersedes the format-specific parts. |

## Conventions

- **Single source of truth.** The plan lives in the consuming repo;
  don't copy it here. Update the plan in place when scope or
  acceptance changes.
- **Cross-reference commits.** When a CLM commit implements part of
  one of these plans, mention the plan phase in the commit message
  (e.g., "Implements slide-format-redesign Phase 2"). The
  consuming-repo plan can then be updated to note "Shipped in CLM
  commit `<sha>`".
- **Adding a new plan.** When a new cross-repo feature kicks off,
  add a row here at the same time the plan is created in the
  consuming repo. One-line addition; keeps the index complete.
- **Removing a plan.** When a plan completes (everything shipped,
  no follow-ups), move its row to an "Archived" section below
  rather than deleting outright — link rot is mitigated and
  someone reading old commits can still find the design rationale.

## Archived plans

*(None yet.)*
