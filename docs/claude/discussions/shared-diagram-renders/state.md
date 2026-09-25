---
status: active
owner: maintainers
updated: 2026-09-25
review-by: 2027-03-25
---

# State: sharing generated diagram renders across topics (#987)

The design is `docs/claude/design/shared-diagram-renders.md` (decided
2026-09-25, S8 — transcript under
`recordings-rerecord-backlog/transcripts/2026-09-25-s8.md`, one session
covering three threads). #987 itself is step 1; #1008 is the validate
checks; topic-level declaration is deferred.

## Settled (S8)

- **Mechanism**: `<include>` of the diagram **source**
  (`as="drawio/<name>.drawio"`) on the consuming `<topic>` in each spec
  that uses it. The consumer renders through its own pipeline into its own
  `img-generated/`; two consumers in one section write byte-identical
  renders and the output-write registry dedups them — identical by
  construction, which dissolves the "rendered once, delivered to every
  consumer" requirement rather than implementing it.
- **Step 1 work** (#987): the test that makes it claimed-wired;
  `include_source_is_topic_dir` → info for diagram sources; a parse-time
  check that `as` keeps the `drawio/`/`pu/` prefix; the recipe in
  `clm info spec-files`; the PythonCourses migration (two specs, delete
  the static copies).
- **Step 1b** (#1008): `image_ref_missing` (the safety net that answers
  the "forgotten include = broken image" objection to spec-level
  declaration) and `image_name_conflict`.
- **Deferred**: topic-level declaration. Revisit when a topic's include
  set must be repeated in more than three specs *and* `image_ref_missing`
  has been tripping, or when a second cross-topic dependency kind appears.
  Measured repetition today: two specs.

## Corrections of the agent's priors

- The issue said pointing an include at another topic's diagram source is
  "unclear or unsupported". The code path exists: `CourseFile.from_virtual`
  classifies by the virtual suffix, `img_path` derives from the virtual
  path, `Course` schedules the output copy after the conversion, and the
  `--no-diagrams` branch in `add_virtual` shows it was anticipated. It is
  **untested**, so not "supported" — the distinction the claimed-wired rule
  exists for.
- Including the owner's **render** (`img-generated/x.png`) looked like the
  simpler variant; it is worse (ships whatever is committed, can race the
  owner's render job in the same build, never re-renders in the consumer).
- `as` at topic root is a trap: the render target is
  `virtual_path.parents[1] / img-generated`, so `as="x.drawio"` would aim
  at the module directory.

## Open (owner)

- None. The carve-out severity (info vs. keeping the warning) is the only
  taste call, and info is the design's choice.

## Known weak points

- Each consumer renders the diagram once (cache keyed on the virtual
  path); seconds per diagram per consumer, not measured on a full corpus.
- A second committed render appears in the consumer's `img-generated/`
  (course repos commit that directory) — build-owned, regenerated, one
  `git status` line on first build.
- `clm course sync-includes` would materialize the `.drawio` into the
  consumer's `drawio/`; the ledger cleans it up, but nobody has run it on a
  diagram include.

## Next conversational boundary

Implement #987 step 1 (resolve-issue, test-first), then #1008. No
further design conversation is expected unless the migration in
PythonCourses surfaces a third consumer or an `image_ref_missing` false
positive.
