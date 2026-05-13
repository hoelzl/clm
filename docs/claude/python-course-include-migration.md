# Deferred Migration — PythonCourses adopts `<include>` for `simple_chatbot`

> ⚠️ **DEFERRED — DO NOT EXECUTE YET.**
> This document captures a planned migration of the PythonCourses repo's
> `machine-learning-azav.xml` spec to use the new shared-source `<include>`
> element (shipped via CLM PR #61) plus the output-write dedup machinery
> (PR #64). The migration is **paused** until the blockers in
> [§Blockers](#blockers) are resolved.
>
> Do **not** run `clm sync-includes` against this spec, edit the topic XML,
> or delete the physical `simple_chatbot/` copies until the blockers clear.

**Companion docs**
- Implementation history: [`shared-source-includes-handover-archive.md`](shared-source-includes-handover-archive.md) (retired).
- Design: [`design/shared-source-includes-and-output-dedup.md`](design/shared-source-includes-and-output-dedup.md).

**Course repo**: `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\`.
**Spec**: `course-specs/machine-learning-azav.xml`.

---

## Why this migration

The AZAV ML course has the `simple_chatbot` Python package in **three**
locations, kept in manual sync:
- Canonical: `examples/SimpleChatbot/src/simple_chatbot/` (8 files).
- Topic copy: `slides/module_550_ml_azav/topic_040_gradio_intro/simple_chatbot/`.
- Topic copy: `slides/module_550_ml_azav/topic_041_gradio_deep_dive/simple_chatbot/`.

Drift has already emerged (audit run 2026-05-13): `main_streaming_graceful.py`
is in the canonical but missing from both topic copies. The 7 other files are
byte-identical. Exactly the failure mode `<include>` is designed to prevent.

A separate `<dir-group>` at spec line 176 ships `examples/SimpleChatbot/` into
`Projekte/SimpleChatbot/` in output — that path is independent of the topic-
local `simple_chatbot/` copies and is **not** affected by this migration.

---

## Decisions (locked — do not re-litigate)

These were settled in the planning conversation on 2026-05-13. A future
session should treat them as given:

1. **Drift direction = canonical wins.** `main_streaming_graceful.py` enters
   both topic outputs after migration. This is intended: the file is the
   reference solution for the Ctrl+C bonus exercise in
   `slides_020_streaming_cli.py` (already updated 2026-05-13 to point students
   at `Projekte/SimpleChatbot/src/simple_chatbot/main_streaming_graceful.py`
   in DE and `Projects/SimpleChatbot/src/simple_chatbot/main_streaming_graceful.py`
   in EN — see commit-log around that date).
2. **CLM availability = tag a release first.** The migration relies on
   features merged to CLM master via PR #61 and PR #64; no released CLM
   version contains them yet. The course repo's `.venv` will pin to that
   new release, rather than using an editable override.
3. **Materialization mode = `copy`.** Lowest friction for student clones; no
   admin-shell requirement. `symlink`/`hardlink` remain available later if
   wanted.
4. **No other shared-source candidates.** Grep across all PythonCourses specs
   confirms `simple_chatbot` is the only duplication addressable by Feature 1.
   The C# course's `NUnitTestRunner.cs` duplication is documented as
   out-of-scope for `<include>` (C# has no sibling-import escape hatch); PR #64's
   dedup will silently handle it.

---

## Blockers

The migration is deferred until **both** of these are resolved upstream in CLM
(not in the course repo). Listed in priority order:

### B1 — Topic-ID-before-children XML wrinkle — RESOLVED

`_parse_topic` originally read `(topic_elem.text or "").strip()`, and
ElementTree treats text *before* the first child as `topic_elem.text` while
text *after* a child becomes that child's `.tail`. The only safe XML shape
for a `<topic>` carrying `<include>` children used to be:

```xml
<topic>
    gradio_intro
    <include source="examples/SimpleChatbot/src/simple_chatbot" as="simple_chatbot"/>
</topic>
```

If an author wrote `<include>` first and the ID after, the ID was silently
empty and resolution broke.

**Resolved** by adopting both fixes together:
- (a) **Parser change** — `<topic>` now accepts an `id="..."` attribute as
  an alternative to the text-content form, so authors can order children
  however they like:
  ```xml
  <topic id="gradio_intro">
      <include source="examples/SimpleChatbot/src/simple_chatbot" as="simple_chatbot"/>
  </topic>
  ```
- (b) **Validation hardening** — CLM hard-errors when `<topic>` has child
  elements and no resolvable ID (neither `id=` attribute nor non-empty text
  content), with a message that explicitly names the text-after-child
  wrinkle. Specifying the ID twice (attribute *and* text) is also an error.

The text-content form remains supported for childless topics, so no
existing spec needs to change unless it already uses the
text-before-children shape and wants the cleaner attribute form.

Documented in `clm info migration` and `clm info spec-files`. Tests:
`test_parse_topic_with_id_attribute`,
`test_parse_topic_with_legacy_text_id_still_works`,
`test_parse_topic_rejects_id_in_both_attribute_and_text`,
`test_parse_topic_rejects_children_with_empty_id` in
`tests/core/course_spec_test.py`.

### B2 — `.gitignore` leak into build output — RESOLVED

`clm sync-includes --gitignore` (the old form) wrote per-topic `.gitignore`
files into topic dirs to keep materialized include targets out of git. Those
files would leak into student/trainer/speaker output — same class of bug as
the `.clm-include` ledger leak fixed in PR1.7a.

**Resolved** by the `sync-includes` gitignore redesign (see
[`design/sync-includes-gitignore-redesign.md`](design/sync-includes-gitignore-redesign.md)).
The `--gitignore` flag was replaced with `--print-gitignore`, which writes
suggested patterns to stdout and never touches `.gitignore` files. With CLM
no longer writing those files, the leak surface is gone entirely — no
`SKIP_FILE_NAMES` change required, and hand-written topic-level `.gitignore`
files keep working untouched.

Author flow under the new design: paste the output of
`clm sync-includes spec.xml --print-gitignore >> .gitignore` into the
course-root `.gitignore` once. The universal `**/.clm-include` pattern plus
one `slides/**/<as>/` line per declared include is emitted deterministically.

---

## Reactivation criteria

When **all** of the following are true, this doc can be moved to "active" and
the migration plan in [§Migration plan](#migration-plan) executed:

- [x] B1 resolved in CLM master — `<topic id="...">` attribute form accepted;
  hard-error when `<topic>` has children with no resolvable ID; hard-error
  when ID is specified via both attribute and text. Tests in
  `tests/core/course_spec_test.py`.
- [x] B2 resolved in CLM master — `--gitignore` replaced with
  `--print-gitignore` (stdout-only). Regression test
  `TestSyncIncludesNoDotGitignoreLeak` in `tests/cli/test_sync_includes.py`
  confirms zero `.gitignore` files are written under any flag combination.
- [ ] A CLM release is tagged and published containing PR #61, PR #64, and the
  B1/B2 fixes.
- [ ] PythonCourses' `pyproject.toml` pins to that CLM release and `uv.lock`
  is regenerated.

---

## Migration plan

Once all reactivation criteria are met, follow these steps in order.

### Step 1 — Sanity check the drift state

Re-run the SHA-256 audit against canonical vs. each topic copy (see
[§Why this migration](#why-this-migration) above for the script). If anything
has changed beyond `main_streaming_graceful.py`, surface it before continuing.

### Step 2 — Spec edit (`machine-learning-azav.xml`)

**Line 185** — replace:
```xml
<topic>gradio_intro</topic>
```
with:
```xml
<topic id="gradio_intro">
    <include source="examples/SimpleChatbot/src/simple_chatbot" as="simple_chatbot"/>
</topic>
```

**Line 485** — replace:
```xml
<topic http-replay="true">gradio_deep_dive</topic>
```
with:
```xml
<topic id="gradio_deep_dive" http-replay="true">
    <include source="examples/SimpleChatbot/src/simple_chatbot" as="simple_chatbot"/>
</topic>
```

The `id=` attribute form is required here because the topics now carry an
`<include>` child — see B1 above for the reasoning. The legacy
text-before-children form would also work, but the attribute form is
cleaner and order-independent.

### Step 3 — Validate

```powershell
clm validate-spec course-specs/machine-learning-azav.xml
```

Expected: `include_dependencies` info finding listing the deps from
`examples/SimpleChatbot/pyproject.toml`. No errors.

### Step 4 — Remove physical copies + materialize

```powershell
git rm -r slides/module_550_ml_azav/topic_040_gradio_intro/simple_chatbot
git rm -r slides/module_550_ml_azav/topic_041_gradio_deep_dive/simple_chatbot
clm sync-includes course-specs/machine-learning-azav.xml
clm sync-includes course-specs/machine-learning-azav.xml --print-gitignore >> .gitignore
```

`--mode=copy` is the default per [Decision 3](#decisions-locked--do-not-re-litigate).
The second invocation appends suggested ignore patterns to the course-root
`.gitignore` so materialized copies and the `.clm-include` ledger stay
untracked. Patterns are deterministic and paste-safe; re-running them is a
no-op semantically.

### Step 5 — Build smoke + diff

```powershell
clm build --only-sections "name:Woche 04,name:Z04" --output $env:TEMP\clm-migrate\before  # against un-migrated spec (stash first)
clm build --only-sections "name:Woche 04,name:Z04" --output $env:TEMP\clm-migrate\after
```

Compare manifests with a SHA-256-per-file walk. Expectations:
- `simple_chatbot/*` files **identical** between before/after for the 7 shared
  files, **plus** `main_streaming_graceful.py` appearing newly in `after/` for
  both topic outputs (Decision 1).
- **0 `.clm-include` leaks** in `after/` (verifies PR1.7a holds).
- **0 `.gitignore` leaks** in `after/` (verifies B2's fix).
- **`output_dedup_count > 0`** in the build summary JSON. The handover's old
  "420" estimate predated this audit and may be inaccurate — record what you
  observe rather than predicting.
- **0 `output_conflicts`** on a clean build.

### Step 6 — Commit + PR (course repo)

Two commits keep the diff reviewable:
1. `chore(spec): delete duplicated simple_chatbot/ from gradio topics` (git rm).
2. `feat(spec): use <include> for simple_chatbot in gradio topics` (XML edit +
   `.clm-include` ledgers + `.gitignore` from sync-includes).

PR body should reference: this tracking doc, the CLM release tag that contains
PR #61/#64/B1/B2, the recorded `output_dedup_count`, and the
`main_streaming_graceful.py` addition.

---

## Already-shipped prep work (course repo)

The following has **already been done** in the course repo and does not need
to be re-done when the migration reactivates:

- `slides/module_550_ml_azav/topic_045_streaming_generators/slides_020_streaming_cli.py`
  — both DE and EN bonus notes now point students at
  `main_streaming_graceful.py` for the Ctrl+C reference solution. This change
  is a no-op for students until the include lands (the file exists at the
  canonical path and is shipped via the existing `<dir-group>` at spec line
  176, so it's already visible in their `Projekte/SimpleChatbot/`). Verified
  edits on 2026-05-13.

---

## Out of scope (still)

- C# course `NUnitTestRunner.cs` duplication — Feature 1 cannot replace it.
- Other PythonCourses specs — confirmed no other duplications.
- Auto-installing `simple_chatbot`'s deps into the worker env — surfaced via
  `validate-spec` info finding, operator-driven.
- Cross-spec sharing (include in spec A pulling from spec B).
