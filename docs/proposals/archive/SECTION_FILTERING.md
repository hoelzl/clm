# Proposal: Section Filtering — Disabled Sections and `--only-sections`

## Status

**Completed** — 2026-04-12. Archived to `docs/proposals/archive/`. The
`enabled` attribute and `id` field on `<section>` are parsed and
validated in `src/clm/core/course_spec.py`; `--only-sections` (with bare
/ `id:` / `idx:` / `name:` selectors) is wired through
`src/clm/cli/commands/build.py`, including scoped cleanup that bypasses
`git_dir_mover` and dir-group processing, and watch-mode filtering of
selected source directories. See `docs/claude/design/section-filtering-plan.md`
for the phased implementation plan that was executed.

Written in response to the AZAV ML course restructure workflow, where multiple
courses currently carry a `-build.xml` "buildable subset" spec alongside the
full roadmap spec and toggle sections via XML comments. This proposal replaces
the commented-out workflow with two composable mechanisms:

1. An `enabled` attribute on `<section>` elements in the course spec.
2. A `--only-sections` flag on `clm build` (and sibling commands) that restricts
   a build to a subset of sections **without wiping unrelated output**.

## Motivation

Several courses in production maintain two parallel specs (e.g.
`machine-learning-azav.xml` and `machine-learning-azav-build.xml`) because:

- The "full" spec references topics that don't exist yet, so it cannot build.
- The "buildable" spec omits those sections, usually by wrapping them in
  XML comments.

Commented-out sections are clumsy — they break XML tooling (outline generation,
MCP queries, validation), drift silently as topics are renamed, and force a
separate spec file just so the build doesn't fail.

Independently, when iterating on a **single section** of a large course (e.g.
rewriting W04 of a 22-week course), `clm build` currently deletes every output
directory and rebuilds all sections. For a 22-week course with a fully built
backlog, this is 20× more work than needed and wrecks the feedback loop.

Both problems are forms of "I want to build less than the whole spec." The two
mechanisms in this proposal cover the two natural entry points: persistent
(baked into the spec file) and ephemeral (passed on the CLI for one build).

## Design

### Mechanism 1 — `enabled` attribute on `<section>`

**XML syntax:**

```xml
<section enabled="false">
    <name>
        <de>Woche 05: LangChain-Grundlagen I</de>
        <en>Week 05: LangChain Foundations I</en>
    </name>
    <topics>
        <topic>langchain_simple_chatbot</topic>
        <topic>prompt_templates</topic>
    </topics>
</section>
```

**Parsing rules:**

- Default is `enabled="true"`. Sections without the attribute behave exactly as
  today — this is fully backward compatible.
- Accepted values are `"true"` / `"false"` (case-insensitive). Any other value
  is a spec error reported by `validate_spec`.
- `SectionSpec` (in `src/clm/core/course_spec.py`) gains a new
  `enabled: bool = True` field.
- `CourseSpec.parse_sections` drops disabled sections entirely from the returned
  list by default. A `keep_disabled=True` parameter retains them (used by
  tooling that needs to enumerate the full roadmap).
- A disabled section **does not need valid `<topics>`**. The `<topics>` element
  can be absent or reference non-existent topic directories without error.
  This is the key property that lets the AZAV ML full spec live as a single
  file: W17 and W19 can stay declared but disabled until their topics exist.
- Disabled sections do not appear in the parsed spec's `.sections` list at all
  by default. Downstream consumers (`course_outline`, `validate_spec`,
  `Course.from_spec`, `validate_slides`, `normalize_slides`) therefore
  automatically ignore them without needing code changes.

**Build behavior:** A build with disabled sections behaves exactly like the
current build on a spec where those sections were commented out — **full
clean-and-rebuild** of the remaining sections. This is the normal, safe path.
No special incremental logic.

**Tooling that lists sections** (`course_outline`, `validate_spec` summary,
the MCP `course_outline` tool) gains an optional `--include-disabled` flag
that reparses the spec with `keep_disabled=True` and marks disabled sections
in the output. Default behavior: disabled sections are omitted.

**`validate_spec --include-disabled`** reports the same errors and warnings as
if all sections had `enabled="true"`, with a `(disabled)` suffix on every
message from a disabled section so users can tell which findings come from
roadmap content they have explicitly deferred.

**Why `enabled` and not `skip`:** `enabled="true"` reads correctly for the
default case. `skip="false"` is a double negative when a reader scans the spec
to see what's in the build.

### Mechanism 2 — `--only-sections` CLI flag

**Usage:**

```bash
# Build only section w01 (bare token: ID → index → substring fallback)
clm build course-specs/machine-learning-azav.xml --only-sections w01

# Build W03 and W04 together
clm build course-specs/machine-learning-azav.xml --only-sections w03,w04

# Build the third section in declared order (1-based, counts disabled)
clm build course-specs/machine-learning-azav.xml --only-sections 3

# Match by name substring — either German or English variant
clm build course-specs/machine-learning-azav.xml --only-sections "Woche 03"
clm build course-specs/machine-learning-azav.xml --only-sections "Week 03"

# Explicit prefixes for disambiguation
clm build spec.xml --only-sections id:w03
clm build spec.xml --only-sections idx:3
clm build spec.xml --only-sections name:"Woche 03"
```

**Selector syntax:**

Each comma-separated token is resolved independently. A token is either
*prefixed* or *bare*:

- **Prefixed tokens** (`id:`, `idx:`, `name:`) resolve only within their
  namespace. Use these when a section ID happens to look like an integer or
  when a name substring accidentally overlaps an ID.
- **Bare tokens** try, in order: exact ID match → 1-based index → case-insensitive
  substring match on either the German or English name. This is the
  everyday form.

**Section indices** are 1-based and count **all** sections in declared order,
including disabled ones. This means toggling `enabled="false"` on a section
does not renumber the sections that follow it. A disabled section can still
be named by its index; the "cannot select a disabled section" check below
applies uniformly.

**Name substring matching** is case-insensitive and tries both the `<de>` and
`<en>` variants of each section name. A token matches a section if it is a
substring of *either* language's name. `"Woche 3"` and `"Week 3"` both match
the same section.

**Token resolution outcomes:**

- **Zero matches:** abort the build with a listing of available sections
  (index, `id` if present, both name variants).
- **Ambiguous bare token** (multiple matches via the same strategy): abort
  and list the matches. Users disambiguate with a prefixed form.
- Each token stops at the first strategy that yields ≥1 match — a bare
  token matching an ID never also tries the index or substring strategies.

Add optional `id` and `enabled` attributes to `SectionSpec`:

```python
@frozen
class SectionSpec:
    name: Text
    topics: list[TopicSpec] = Factory(list)
    enabled: bool = True
    id: str | None = None
```

Section IDs are optional. We recommend adding them for frequently filtered
courses because they are stable under reordering and renaming. Index
selection is fragile when sections are reordered; name substring selection
is fragile when sections are renamed. IDs have neither problem.

**Interaction with `enabled="false"`:** If the selector token list contains a
mix of enabled and disabled sections, **skip each disabled section with a
warning and build the rest**:

```
Warning: skipping disabled section 'w05' (enabled="false"). Re-enable it in
the spec if you want to build it.
```

If the token list matches *only* disabled sections (nothing left to build),
abort with an error explaining that the entire selection was disabled.

### The critical difference — cleanup semantics

**Normal build (no `--only-sections`):** Unchanged. `build.py:544-547` continues
to `shutil.rmtree` each root output directory (wrapped in `git_dir_mover`)
before rebuild. This preserves the invariant that a build output directory
reflects exactly one spec state.

**`--only-sections` build:** Do **not** remove the root output directories and
do **not** wrap cleanup in `git_dir_mover`. The `git_dir_mover` context
manager only runs on the full-build path; `--only-sections` follows the same
"skip `git_dir_mover`" pattern already used by `--keep-directory` and
`--incremental`. Instead:

1. Compute the set of output directories that belong to the selected sections.
   The section→directory mapping already exists in `course.py:555-558`:
   ```python
   section_dir = output_dir / sanitize_file_name(section.name[lang])
   ```
   This runs per `(target, language, kind)` tuple, yielding the full set of
   section subdirectories to touch.
2. For each selected section's expected `section_dir`:
   - If the directory exists, `shutil.rmtree` it.
   - If it does not exist, log a warning: *"Section '<id-or-name>' has no
     existing output directory at `<path>` — this is normal on the first
     build of this section or if it was recently renamed. Run a full build
     to clean up stale directories from old names."* Continue building
     normally.
3. Do not touch the root output directory, other sections' directories, or
   dir-group output.
4. Filter the course so that only the selected sections are constructed.
   Because `Course.files` and `Course.topics` are derived properties flowing
   through `Course.sections` (see `course.py:195-200`), filtering sections
   automatically cascades to every downstream consumer including the worker
   pipeline and the watch-mode dispatcher. **No separate file-level filter
   is required.**
5. **Skip dir-group processing entirely.** `course.process_dir_group(backend)`
   is bypassed when `--only-sections` is active. Dir-groups produce the final
   shipping state of a course; `--only-sections` is a dev-time iteration tool,
   not a publish path. Run a full build when you need dir-groups.

**Safety properties:**

- No destructive action against sections the user did not ask for.
- A partial build directory (previously built with `--only-sections w01,w02`)
  plus a follow-up `--only-sections w03` leaves w01+w02 intact and adds w03.
- A follow-up full build (no `--only-sections`) re-cleans everything — the
  invariant "full build implies whole output matches spec" is preserved.
- No interaction with `git_dir_mover`: the full-build path still preserves
  top-level `.git` dirs as today; `--only-sections` never touches the root
  and so never needs to preserve `.git`. Nested `.git` directories inside
  section subdirectories do not exist in practice; if that changes, a
  targeted `GitDirMover` invocation over the section dirs is a local fix.

**What `--only-sections` does NOT do:**

- It does **not** skip file discovery of non-selected sections' source files
  at the spec-parse level. CLM still parses the full spec to know what the
  section layout looks like so it can produce meaningful selector error
  messages. Only after resolution does it drop non-selected sections.
- It does **not** touch dir-groups, top-level course files (README,
  `pyproject.toml`, etc.), or git metadata.
- It does **not** attempt persistent rename detection. The warning in step 2
  is the entire rename accommodation in v1; full cleanup requires a full
  build. This is a documented limitation.
- It does **not** combine additively across multiple runs in the sense of a
  database — each `--only-sections` invocation removes and rebuilds exactly
  its selected sections. If the spec changes between runs, stale directories
  for removed topics *within the selected sections* are cleaned up as normal
  (the section-level rmtree in step 2 covers this).

### Watch mode interaction

`clm build --only-sections <selector> --watch` is supported in v1 and comes
nearly for free.

The existing `FileEventHandler`
(`src/clm/cli/file_event_handler.py`) calls `course.find_course_file(path)`
before dispatching modifications. When `course.sections` has already been
filtered by `--only-sections`, the derived `course.files` property contains
only files from the selected sections, so `find_course_file` naturally returns
`None` for any file outside the selected scope and the handler skips the
event silently. **`on_file_modified` works without change.**

`on_file_created` needs a small guard: it currently calls `course.add_file`
unconditionally, which would re-add a file from a non-selected section.
Before calling `add_file`, check whether the new path lies under one of the
selected sections' source directories; if not, skip. This is a ~3-line
addition plus threading the selected-section source-dir set through
`FileEventHandler.__init__`.

Watch mode under `--only-sections` still observes the full data directory
(the watchdog observer is not section-aware), but only reacts to events
inside selected sections. The set of selected sections is pinned at watcher
startup; add/remove a section in the spec and you need to restart watch mode.

### Interaction matrix

| Scenario                                        | Cleanup scope                        | Sections built                       |
|-------------------------------------------------|--------------------------------------|--------------------------------------|
| `clm build spec.xml`                            | All root output dirs (as today)      | All enabled sections                 |
| `clm build spec.xml` + disabled sections in XML | All root output dirs (as today)      | All non-disabled sections            |
| `clm build spec.xml --only-sections w03`        | Only `w03` section subdirectories    | Only the `w03` section               |
| `--only-sections w01,w05` where w05 disabled    | Only `w01` section subdirectories    | Only `w01`; w05 skipped with warning |
| `--only-sections w05` where w05 is disabled     | — (error, entire selection disabled) | — (error)                            |
| `--only-sections` matches zero                  | — (error)                            | — (error)                            |
| `--only-sections` ambiguous bare token          | — (error)                            | — (error)                            |
| `--only-sections w03 --watch`                   | Only `w03` on initial build          | Only `w03`; watcher ignores others   |
| `--only-sections ""`                            | — (error)                            | — (error)                            |

## Implementation Notes

### Files touched

- `src/clm/core/course_spec.py`
  - Add `enabled: bool = True` and `id: str | None = None` fields to
    `SectionSpec`.
  - Update `CourseSpec.parse_sections` to read `enabled` and `id` attributes
    and drop disabled sections unless `keep_disabled=True` is passed.
  - Add validation for invalid `enabled` values.
  - Add `CourseSpec.resolve_section_selectors(tokens: list[str]) ->
    SectionSelection` helper. `SectionSelection` captures the resolved subset
    (by 0-based index into the disabled-inclusive section list) and the
    warnings for skipped disabled sections. Raises on empty token, zero
    matches, ambiguous bare tokens, or entirely-disabled selections.
- `src/clm/core/section.py`
  - Runtime `Section` does **not** carry `enabled` — disabled sections are
    filtered out at the spec level before `Course.from_spec` runs.
  - The `id` field propagates through to the runtime `Section` for error
    messages and progress reporting.
- `src/clm/core/course.py`
  - Add `section_selection: SectionSelection | None` parameter to
    `Course.from_spec`. When set, after sections are constructed, filter
    them down to only the selected ones before returning the `Course`.
  - `Course.files` and `Course.topics` are already derived from
    `Course.sections` (lines 195–200) — no additional file-level filtering
    is required.
- `src/clm/cli/commands/build.py`
  - Add `@click.option("--only-sections", ...)` accepting a comma-separated
    string.
  - In `prepare_course` (or equivalent), call
    `course_spec.resolve_section_selectors(tokens)`, emit the
    skipped-disabled warnings, and pass the resolved selection into
    `Course.from_spec`.
  - In `process_course_with_backend`, branch on "only-sections mode":
    - Do **not** enter `git_dir_mover`.
    - For each `(section, target, lang, kind)` tuple, compute the expected
      `section_dir`. If it exists, rmtree it; otherwise log the rename
      warning.
    - Run `process_stage` as normal.
    - Skip `course.process_dir_group(backend)`.
- `src/clm/cli/file_event_handler.py`
  - In `on_file_created`, if a `selected_section_source_dirs: set[Path]` is
    provided, skip paths that are not under any of those directories. Thread
    the set through `FileEventHandler.__init__`.
- `src/clm/cli/commands/outline.py` (and the corresponding MCP tool)
  - Add `--include-disabled` flag that parses the spec with
    `keep_disabled=True` and marks disabled sections in the output.
- `src/clm/cli/commands/validate_spec.py`
  - Default behavior (disabled sections dropped) already falls out of
    `parse_sections`.
  - With `--include-disabled`, validate disabled sections' topics anyway and
    suffix each reported issue with `(disabled)`.
- `src/clm/mcp/` tools
  - `course_outline`, `validate_spec`, and any other tool that enumerates
    sections should support the `include_disabled` parameter.

### CLI plumbing

`BuildConfig` (in `src/clm/cli/commands/build.py`) gains a
`selected_sections: list[str] | None` field holding the raw selector tokens
(with prefixes preserved, for error messages). Empty or unset → normal
full-build behavior. Non-empty → section-filtered behavior described above.

The `--only-sections` value is split on commas and whitespace-trimmed. An
empty value (or a value containing only whitespace) is an error, not a
silent fallthrough to full build. Each token is resolved independently; a
single unresolvable or ambiguous token aborts the build. Disabled-section
tokens within a mixed list produce warnings but do not abort.

Token prefix parsing: a token starting with `id:`, `idx:`, or `name:`
(case-insensitive prefix) is treated as a typed selector. All other tokens
are bare and fall through the ID → index → substring chain.

### Backwards compatibility

- Existing spec files without `enabled` attributes continue to parse
  unchanged.
- Existing builds without `--only-sections` continue to clean and rebuild
  the whole output tree.
- Existing courses that currently use two parallel specs (the "-build"
  subset workflow) can migrate by:
  1. Adding `enabled="false"` to not-yet-ready sections in the full spec.
  2. Deleting the `-build.xml` file.
  3. Updating any scripts/docs that reference the `-build.xml` path.
- Old `clm` binaries reading new-format specs will silently ignore
  `enabled="false"` and attempt to build the disabled sections (likely
  failing on missing topics). Since the CLM user base is small, users will
  be asked to upgrade rather than shipping a minimum-version warning.

## Test Strategy

### Unit tests — `tests/core/test_course_spec.py`
- Spec with `enabled="false"` on a section — verify the section is absent
  from `CourseSpec.sections`.
- Spec with `enabled="true"` explicit — verify it is kept.
- Spec with `enabled="TRUE"` / `enabled="False"` — case-insensitive
  acceptance.
- Spec with `enabled="maybe"` — verify parse error.
- Spec with multiple sections, one disabled — verify ordering of remaining
  sections is preserved.
- Spec with a disabled section that has invalid/non-existent topics — verify
  no parse error (the "single-file roadmap" invariant).
- Spec with `id="w03"` on a section — verify ID round-trips into
  `SectionSpec`.
- `CourseSpec.parse_sections(..., keep_disabled=True)` returns all sections
  with an `enabled` flag readable by callers.

### Unit tests — `tests/core/test_section_filtering.py` (new file)
- Bare token: `["w03"]` matches by ID when ID is present.
- Bare token: `["3"]` matches by 1-based index (counting disabled).
- Bare token: `["Week 03"]` matches by English substring.
- Bare token: `["Woche 03"]` matches by German substring.
- Bare token: index counts disabled sections (disabled w02 still bumps w03
  to index 3).
- Prefixed token: `["id:w03"]` matches only IDs.
- Prefixed token: `["idx:3"]` matches only indices.
- Prefixed token: `["name:Woche 03"]` matches only names.
- Prefix disambiguation: section with `id="3"` and bare `"3"` token
  resolves to the ID; `idx:3` resolves to the third section.
- Zero matches → raises with a helpful listing containing index, id, de
  name, and en name for each section.
- Ambiguous bare substring → raises with the matches listed.
- Selecting *only* disabled sections → raises with the "entire selection
  disabled" message.
- Selecting a mix of enabled and disabled sections → returned
  `SectionSelection` contains warnings for skipped disabled entries and
  resolved indices for the remaining enabled ones.

### Integration tests — `tests/cli/test_build_only_sections.py` (new file)
- Build a tiny 3-section course in a temp dir.
  - `clm build spec.xml` — all three section directories exist.
  - Touch a sentinel file in each section dir.
  - `clm build spec.xml --only-sections section2`.
  - Assert: section2 rebuilt (sentinel gone), section1 and section3
    unchanged (sentinels still present).
- `clm build spec.xml --only-sections nonexistent` — exit non-zero, stderr
  contains section listing.
- `clm build spec.xml --only-sections ""` — exit non-zero with
  "empty selector" error.
- Spec with a disabled section — full build excludes it; `--only-sections`
  on a disabled-only token aborts; `--only-sections` on a mixed list skips
  the disabled one with a warning and builds the rest.
- Rename scenario: build full, rename a section in the spec,
  `--only-sections <renamed>` — verify warning is logged about the missing
  old directory; verify the new directory is created and populated.
- Dir-group scenario: spec with a dir-group — full build creates dir-group
  output; follow-up `--only-sections` leaves dir-group output untouched
  (no rerun, no deletion).

### Integration tests — `tests/cli/test_watch_only_sections.py` (new file)
- Build `--only-sections section2 --watch` in a temp dir.
- Touch a file in section1 — verify no rebuild (use log capture or file
  mtime).
- Touch a file in section2 — verify rebuild.
- Create a new file in section1 — verify it is not added to the course.
- Create a new file in section2 — verify it is added and built.

### MCP/tool tests
- `course_outline` on a spec with disabled sections omits them by default.
- `course_outline --include-disabled` shows them with a disabled marker.
- `validate_slides` on a spec with disabled sections only validates
  enabled sections by default.
- `validate_spec --include-disabled` reports issues from disabled sections
  with a `(disabled)` suffix on each message.

## Open Questions

All design-level questions are resolved; decisions are recorded above. The
remaining deferred items are explicit v2 follow-ups, not blockers:

1. **Section range syntax** (`--only-sections 3-5`). Low-priority v2.
   Can be added to the prefix parser (`idx:3-5`) without touching the rest
   of the pipeline.
2. **Per-topic `enabled` attribute.** Still a non-goal; revisit if a course
   needs finer-grained toggling than section level.

## Acceptance Criteria

This proposal is done when:

- [ ] `enabled="false"` on a section in any course spec causes CLM to skip
      that section in all operations (build, outline, validate_spec,
      validate_slides, normalize_slides, MCP tools), without errors even
      when the disabled section's topics reference non-existent
      directories.
- [ ] `clm build spec.xml --only-sections <selector>` builds only the
      specified sections, does not modify other sections' output
      directories, and does not run dir-group processing.
- [ ] Selectors support bare form (ID → index → substring on either
      language) and explicit `id:` / `idx:` / `name:` prefixes for
      disambiguation.
- [ ] Renaming a section and running `--only-sections` on the new name
      logs a warning pointing the user toward a full build.
- [ ] `clm build spec.xml --only-sections <selector> --watch` correctly
      ignores file events outside selected sections (both modifications
      and creations).
- [ ] Mixed selector lists containing disabled sections skip the disabled
      ones with a warning and build the rest. A selection that is entirely
      disabled aborts.
- [ ] A normal `clm build spec.xml` (no flag) continues to fully clean
      and rebuild, matching current behavior exactly.
- [ ] The AZAV ML course can drop `machine-learning-azav-build.xml` and
      use `enabled="false"` on not-yet-ready sections in
      `machine-learning-azav.xml`.
- [ ] At least one other course in the repository that currently maintains
      a parallel subset spec can do the same.
- [ ] Tests cover: enabled/disabled parsing, selector resolution (bare +
      prefixed, ID/index/substring, both languages), ambiguous/missing
      selectors, incremental cleanup preserves unselected section output,
      full rebuild still cleans everything, mixed-disabled warning,
      entirely-disabled error, rename warning, watch-mode filtering.
- [ ] `clm info spec-files` and `clm info commands` are updated to
      document `enabled` and `--only-sections`.

## Non-Goals

- Per-topic `enabled` attribute (would be a v2 follow-up if needed).
- A GUI or TUI for toggling sections interactively.
- Automatic detection of "this section would fail to build, auto-disable
  it." Disabling is always an explicit author choice.
- Multi-spec composition (XInclude, spec inheritance, etc.). Stays out of
  scope.
- Persistent manifest-based rename detection. The warning above is the
  entire rename accommodation in v1.
- Minimum-CLM-version warnings on new-format specs. Out of scope given the
  current user base; revisit if CLM gains external users.
