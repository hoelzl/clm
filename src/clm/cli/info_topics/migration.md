# CLM {version} — Migration Guide

This guide covers breaking changes across major CLM versions.

## Slide format redesign: stable `slide_id`s (additive — no break)

CLM {version} ships **Phase 2** of the slide-format-redesign: the
`clm slides assign-ids` command that generates stable, EN-derived,
kebab-case ASCII `slide_id` values for slide and subslide cells.

Adoption is opt-in — nothing happens until you run the command. The
recommended workflow on a course repo:

```bash
# Preview what would change
clm slides assign-ids slides/ --report-only

# Apply for the headed-slide majority
clm slides assign-ids slides/

# Decide what to do with refusals (extractable headingless slides)
clm slides assign-ids slides/ --report-only --accept-content-derived
clm slides assign-ids slides/ --report-only --llm-suggest   # if Ollama is running
```

Pin a slide's id with the **preserve marker**:

```python
# %% [markdown] lang="de" tags=["slide"] slide_id="!intro"
```

The `!` is source-level only — referenced everywhere as the bare form
`intro`. `--force` and any future regeneration tool will leave
preserved ids alone.

Voiceover and notes cells inherit the id of the preceding slide
(1:N — multiple narrative cells per slide). The title slide (j2
`header()` macro) anchors `slide_id="title"` automatically. See
`clm info commands` → `clm slides assign-ids` for the full flag
matrix.

## Slide format redesign: `clm validate` enforces `slide_id` (warning now, error in 1.7)

CLM {version} also ships **Phase 3** of the slide-format-redesign:
`clm validate` now inspects `slide_id` metadata and reports findings
under the existing `pairing` check group. The findings run in both
full (`clm validate slides/`) and quick (`clm validate slides/ --quick`)
modes, so the PostToolUse hook surfaces them at edit time.

### Severities and rollout

| Finding | Severity in {version} | Notes |
|---------|----------------------|-------|
| `slide`/`subslide` cell missing `slide_id` | `warning` | **Will become an `error` in CLM 1.7** (same release that retires the Phase 0 deprecation aliases). |
| duplicate `slide_id` across slide groups | `error` | Group-aware: paired DE/EN cells sharing the EN-derived slug are not a duplicate. Bare-form comparison so `!intro` and `intro` collide. |
| voiceover/notes `slide_id` ≠ preceding `slide`/`subslide` anchor | `error` | Walk-back skips j2, code, shared (lang-less), and cross-language narrative cells. The j2 `header()` macro anchors `slide_id="title"` for narrative cells that follow it. |
| paired DE/EN slides carry mismatched bare `slide_id`s | `warning` | Fix with `clm slides assign-ids --force`. |
| `slide_id` value is not a valid kebab-case ASCII slug (≤30 chars) | `warning` | The leading `!` preserve marker is permitted and does not count toward the length cap. |

The two-release window (warning in {version}, error in 1.7) gives
course repositories time to sweep `clm slides assign-ids` across
their decks without the hook spamming warnings for unmigrated files.

### How to migrate

```bash
# 1. Add ids to the headed-slide majority (zero-risk default).
clm slides assign-ids slides/

# 2. Review what the validator reports against the now-half-migrated
#    course; for the warning entries, decide whether to opt into
#    content-derived ids or hand-author them.
clm validate slides/ --quick

# 3. For extractable headingless slides, either:
clm slides assign-ids slides/ --accept-content-derived
# or, with Ollama running:
clm slides assign-ids slides/ --llm-suggest --accept-content-derived

# 4. For hard refusals (no extractable content), hand-author slide_id="..."
#    on the cell directly. Use the preserve marker `!` if you want the id
#    to survive future regeneration: slide_id="!intro".

# 5. Re-validate. Errors (duplicates, narrative adjacency mismatch,
#    invalid slug) need to be cleared before CLM 1.7.
clm validate slides/
```

The `error`-severity findings already fail validation in {version} —
they cover content bugs (typo-introduced duplicates, stale voiceover
copies referencing the wrong slide) that should be fixed regardless
of the migration timeline.

## CLI restructure: verb-grouped subcommands

CLM {version} reorganises the top-level command surface. Several flat
commands moved under new groups for a smaller, more scannable layout:

| Old (still works, deprecated)        | New canonical                |
|--------------------------------------|------------------------------|
| `clm normalize-slides`               | `clm slides normalize`       |
| `clm language-view`                  | `clm slides language-view`   |
| `clm suggest-sync`                   | `clm slides suggest-sync`    |
| `clm search-slides`                  | `clm slides search`          |
| `clm resolve-topic`                  | `clm topic resolve`          |
| `clm authoring-rules`                | `clm authoring rules`        |
| `clm extract-voiceover`              | `clm voiceover extract`      |
| `clm inline-voiceover`               | `clm voiceover inline`       |
| `clm validate-slides PATH`           | `clm validate PATH`          |
| `clm validate-spec SPEC`             | `clm validate SPEC`          |

### Deprecation timeline

- **{version}**: Old names still work and emit a one-line
  deprecation notice on stderr naming the new invocation. The
  notice does not affect exit codes or stdout, so scripts that
  pipe `--json` output through `jq` continue to work; only
  interactive users see the migration hint.
- **1.7 (planned)**: Old names removed.

### `clm validate` consolidates the two validators

`clm validate <path>` replaces both `validate-slides` and
`validate-spec`. It inspects the argument:

- `.xml` file → spec validation (formerly `validate-spec`)
- `.py` file or directory → slide validation (formerly `validate-slides`)

For ambiguous cases (an `.xml` you want fed to the slide validator,
or vice versa), pass `--kind=slides` or `--kind=spec` explicitly.

All flags from both old commands are still available; the new
command refuses combinations that don't apply (`--quick` with
`--kind=spec`, `--include-disabled` with `--kind=slides`).

### How to migrate

For scripts and CI:

- **Greppable rename**: each old name maps 1:1 to a new path. A
  global find-and-replace of `clm normalize-slides` →
  `clm slides normalize` (and similar for the other names) is
  safe.
- **PostToolUse hooks** referencing the old names will print the
  deprecation notice each edit. Update the hook command to the
  new path to silence it.
- **Skill files** (`.claude/commands/*.md` in consuming repos)
  should be updated to the new paths; the deprecation will be a
  noticeable lint signal if you forget any.

For interactive use, no action is needed — the old names keep
working until 1.7, and the deprecation notice tells you the new
path each time you invoke an old one.

## `clm build` no longer wipes the output tree by default

CLM {version} changes how `clm build` manages each output root. The
previous flow — move every nested `.git/` aside, `shutil.rmtree` the
whole tree, then regenerate from scratch — invalidated git's stat-cache
on every build and turned sub-second `git status` calls into multi-minute
re-hashes on large courses.

The new default does the opposite:

- **No wipe.** The existing output tree is left in place across builds.
- **Hash-aware writes.** Each write site checks whether the destination
  already holds byte-identical content; if so, the write is skipped so
  mtime/inode are preserved and git's stat-cache stays valid.
- **Post-build sweep.** Once all stages complete, anything under a
  build-owned root that the build did not write is deleted. This is what
  removes orphans from renamed/removed sections.

### What changed

| Flag | Before | After |
|------|--------|-------|
| (default) | wipe + restore `.git/` + rebuild | no wipe; hash-aware writes + sweep |
| `--keep-directory` | opt out of the wipe | **deprecated** no-op alias; will be removed in 1.6 |
| `--incremental` | implies `--keep-directory`; skip cached writes | skip cached writes; implies `--no-sweep` |
| `--clean` | n/a (new) | opt into the legacy wipe-and-restore flow |
| `--no-sweep` | n/a (new) | opt out of the post-build sweep |

### How to migrate

Most users need no action — the default is faster and produces the same
output for unchanged content. A few scripts may need an explicit flag:

- **You depend on the output tree being wiped at the start of every
  build.** Pass `--clean`. It runs the legacy flow (move `.git/` aside,
  `shutil.rmtree` each root, regenerate). Nested `.git/` directories
  are preserved across the wipe, same as before.
- **You scripted `--keep-directory`.** The flag is now a no-op alias
  with a `DeprecationWarning`; remove it. The deprecation runs through
  CLM 1.5; the flag is removed entirely in 1.6.
- **You scripted `--incremental` to avoid the wipe.** Drop `--incremental`
  unless you also want the disk-write skipping it adds on top of the new
  default. `--incremental` now implies `--no-sweep` as well.

### Governing principle for output trees

The new flow assumes **everything under a build-owned output root is
exclusively CLM's**. Authors do not hand-place auxiliary files there —
the sweep removes `.gitignore`, `README.md`, editor caches, and
similar untracked files at the root of an output tree. If a course
genuinely needs an auxiliary file in its output, the right answer is
to teach CLM to generate it, not to special-case the sweep.

Nested `.git/` directories are spared (so the output tree can be its
own git repo) and any subtree containing a `.git/` is treated as opaque
(so a nested repo's files are left alone).

## Topic ID via `id=` attribute (preferred form when topics have children)

CLM {version} adds an `id=` attribute on `<topic>` as an alternative to the
existing text-content form. The legacy form continues to work unchanged for
plain topics:

```xml
<topic>introduction</topic>          <!-- still works -->
<topic id="introduction"/>           <!-- equivalent -->
```

### What changed

When a `<topic>` carries `<include>` or any other child elements, the
attribute form is now **required**:

```xml
<!-- BEFORE: only the text-before-children shape was safe -->
<topic>
    gradio_intro
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
</topic>

<!-- AFTER: use id= attribute (clearer, order-independent) -->
<topic id="gradio_intro">
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
</topic>
```

CLM now hard-errors when a `<topic>` has child elements but no resolvable
ID. This closes a footgun: XML parsers assign text appearing *after* a
child element to that child's tail rather than to the parent, so an author
who writes the ID after a child would silently end up with an empty topic
ID. The error message points at the wrinkle so authors know what to fix.

Specifying the ID twice (both `id=` attribute *and* text content) is also a
hard error. Pick one form per topic.

### How to migrate

For topics with `<include>` (or any child elements) that currently use the
text-before-children form:

```xml
<!-- Before -->
<topic>
    gradio_intro
    <include source="..." as="..."/>
</topic>

<!-- After -->
<topic id="gradio_intro">
    <include source="..." as="..."/>
</topic>
```

Childless topics need no changes — `<topic>introduction</topic>` keeps
working. Migration is opt-in for those.

Verification: `clm validate-spec course.xml` parses cleanly. Any `<topic>`
with children and a stale post-child ID will surface as a clear
`CourseSpecError` pointing at the section.

## Splitting the `speaker` output kind into `trainer` + `recording`

CLM {version} renamed the single `speaker` output kind into two named
kinds, since they serve genuinely different audiences:

- `trainer` — for trainers teaching the course. Keeps speaker `notes`
  cells but strips `voiceover` cells (those are only meaningful when the
  deck is read aloud for video recording). This is the variant most
  trainers want.
- `recording` — for the trainer recording the course on video. Keeps
  both `notes` and `voiceover` cells. The voiceover cells contain the
  polished narration read on camera.

### What still works

`<kind>speaker</kind>` continues to parse and is treated as
`recording` for one release. Spec parsing logs a deprecation warning
and rewrites the kind internally, so downstream consumers
(`clm build`, `clm validate-spec`, the MCP tools, etc.) only ever see
the canonical kinds. The `--speaker-only` CLI flag also still works
and now selects both `trainer` and `recording`, since both share the
private (`speaker/`) toplevel output directory.

### What changed

- Output paths now always include a kind subdir. Previously a `speaker`
  build wrote to `output/speaker/<course>/Slides/Html/<topic>.html`
  (no kind subdir). The new `recording` and `trainer` kinds write to
  `output/speaker/<course>/Slides/Html/Recording/<topic>.html` and
  `output/speaker/<course>/Slides/Html/Trainer/<topic>.html`
  respectively. The deprecated `speaker` kind alias produces the same
  layout as `recording` (i.e., it now also has a kind subdir).
- The HTML cache producer is now `recording`, not `speaker`. Trainer,
  Completed, and Partial HTML all reuse Recording's cached executed
  notebook by filtering the appropriate cell subset.

### How to migrate course specs

Replace `<kind>speaker</kind>` with whichever new kind matches that
target's intent:

```xml
<!-- Before -->
<output-target name="instructor">
    <path>./output/instructor</path>
    <kinds><kind>speaker</kind></kinds>
</output-target>

<!-- After: live-teaching deck (most trainers) -->
<output-target name="trainer">
    <path>./output/trainer</path>
    <kinds><kind>trainer</kind></kinds>
</output-target>

<!-- After: video-recording deck -->
<output-target name="recording">
    <path>./output/recording</path>
    <kinds><kind>recording</kind></kinds>
</output-target>
```

If a single target should produce both decks (e.g., one repository
holding both for a recording trainer), list both kinds:

```xml
<output-target name="instructor">
    <path>./output/instructor</path>
    <kinds>
        <kind>trainer</kind>
        <kind>recording</kind>
    </kinds>
</output-target>
```

Verification:

- `clm validate-spec course.xml` — the spec parses cleanly without
  any `speaker` deprecation warnings.
- `clm build course.xml --speaker-only` — produces both `Trainer/` and
  `Recording/` subdirs under the private (`speaker/`) toplevel.

## Migrating from `-build.xml` subset specs to `enabled="false"`

CLM {version} introduced the `enabled` attribute on `<section>` elements and
the `clm build --only-sections` flag. Together they replace the common
pattern of carrying a second "buildable subset" spec file alongside the
full roadmap spec.

Before, courses with not-yet-implemented topics typically looked like
this:

```text
course-specs/
├── machine-learning-azav.xml        # full roadmap; wraps unfinished
│                                    # sections in <!-- XML comments -->
└── machine-learning-azav-build.xml  # same spec with those sections
                                      # removed so clm build succeeds
```

Three-step migration:

1. **Add `enabled="false"` to not-yet-ready sections** in the full
   roadmap spec. A disabled section may omit `<topics>` entirely or
   reference topic IDs that do not exist — it is not built or validated.

   ```xml
   <section id="w17" enabled="false">
       <name>
           <de>Woche 17: Fortgeschrittene Themen</de>
           <en>Week 17: Advanced Topics</en>
       </name>
       <topics>
           <topic>not_yet_implemented_topic</topic>
       </topics>
   </section>
   ```

2. **Delete the `-build.xml` subset file.** One source of truth from
   now on.

3. **Update any scripts or automation** that reference the `-build.xml`
   path to use the full spec instead.

Verification:

- `clm build course.xml` — builds the full roadmap minus disabled
  sections.
- `clm build course.xml --only-sections w03` — dev-time iteration on a
  single section (see `clm info commands`).
- `clm outline course.xml --include-disabled` — lists the disabled
  sections with a `(disabled)` marker so you can see the full roadmap.
- `clm validate-spec course.xml --include-disabled` — validates disabled
  sections' topics with a `(disabled)` suffix on each finding so you can
  track which topics still need to be created.

See also: `clm info spec-files` for the `enabled` / `id` attribute
reference and `clm info commands` for the `--only-sections` selector
syntax.

## v1.2.0 to v1.2.1: Voiceover sync argument order change

### Breaking Change

`clm voiceover sync` now accepts **multiple video files**. To support this,
the argument order was flipped:

```bash
# Before (v1.2.0)
clm voiceover sync VIDEO SLIDES --lang de

# After (v1.2.1)
clm voiceover sync SLIDES VIDEO... --lang de
```

`SLIDES` is now the first positional argument, followed by one or more
`VIDEO` paths. Single-video invocations work the same way — just swap the
argument order.

### New default: merge mode

`clm voiceover sync` now **merges** transcript content into existing
voiceover cells by default instead of overwriting them. Use `--overwrite`
to restore the old destructive behavior. Note that `--mode verbatim`
without `--overwrite` is now an error.

---

## v0.3.x to v0.4.0: Unified Package Architecture

### Summary

CLM v0.4.0 consolidated all worker code into a single `clm` package with
optional extras, replacing separate worker packages.

### Breaking Changes

#### Worker packages are no longer separate

Before (v0.3.x):

```bash
pip install -e .
pip install -e ./services/notebook-processor
pip install -e ./services/plantuml-converter
pip install -e ./services/drawio-converter
```

After (v0.4.0+):

```bash
pip install -e ".[all]"           # Everything
pip install -e ".[all-workers]"   # All workers only
pip install -e ".[notebook]"      # Specific worker
```

#### Module paths changed

Before (v0.3.x):

```python
import nb
import plantuml_converter
import drawio_converter
```

After (v0.4.0+):

```python
from clm.workers import notebook
from clm.workers import plantuml
from clm.workers import drawio
```

Command-line entry points:

```bash
# Before
python -m nb
python -m plantuml_converter
python -m drawio_converter

# After
python -m clm.workers.notebook
python -m clm.workers.plantuml
python -m clm.workers.drawio
```

#### Docker images updated

```dockerfile
# Before
COPY ./clm-common ./clm-common
COPY ${SERVICE_PATH} ./service
RUN pip install ./clm-common && pip install ./service
CMD ["python", "-m", "nb"]

# After
COPY . ./clm
RUN pip install ./clm[notebook]
CMD ["python", "-m", "clm.workers.notebook"]
```

### Installation extras (v0.4.0+)

| Extra | Description |
|-------|-------------|
| `[notebook]` | Jupyter notebook processing |
| `[plantuml]` | PlantUML diagram conversion |
| `[drawio]` | Draw.io diagram conversion |
| `[all-workers]` | All workers |
| `[ml]` | ML packages (PyTorch, FastAI, etc.) |
| `[summarize]` | LLM-powered summaries and polish (openai) |
| `[voiceover]` | Video-to-speaker-notes pipeline |
| `[recordings]` | Video recording management and audio processing |
| `[slides]` | Slide authoring tools with fuzzy search |
| `[mcp]` | MCP server for AI-assisted slide authoring |
| `[dev]` | Development tools (pytest, mypy, ruff) |
| `[tui]` | TUI monitoring |
| `[web]` | Web dashboard |
| `[all]` | Everything |

---

## v0.2.x to v0.3.0: Consolidated Package

### Summary

CLM v0.3.0 merged four separate packages (`clm`, `clm-common`,
`clm-faststream-backend`, `clm-cli`) into a single `clm` package.

### Breaking Changes

#### Import paths changed

```python
# Core imports — add .core
from clm import Course          # -> from clm.core import Course
from clm.course_files import    # -> from clm.core.course_files import
from clm.operations import      # -> from clm.core.operations import
from clm.utils import           # -> from clm.core.utils import

# Infrastructure — replace clm_common
from clm_common import           # -> from clm.infrastructure import
from clm_common.backend import   # -> from clm.infrastructure.backend import
from clm_common.database import  # -> from clm.infrastructure.database import
from clm_common.messaging import # -> from clm.infrastructure.messaging import
from clm_common.workers import   # -> from clm.infrastructure.workers import

# Backends — replace clm_faststream_backend
from clm_faststream_backend import SqliteBackend
# -> from clm.infrastructure.backends import SqliteBackend
# Note: FastStreamBackend (RabbitMQ) was removed entirely

# CLI — replace clm_cli
from clm_cli.main import cli    # -> from clm.cli.main import cli
```

#### Convenience imports still work

```python
from clm import Course, Section, Topic, CourseFile, CourseSpec  # OK
```

### Uninstalling old packages

```bash
pip uninstall -y clm clm-cli clm-common clm-faststream-backend
pip install -e .
```
