# CLM Design — Shared-Source Includes and Output-Write Deduplication

Status: **locked** (2026-05-10) — open questions resolved (see end of doc).
Implementation tracked in
[`docs/claude/shared-source-includes-handover.md`](../shared-source-includes-handover.md).

Two related but independent features. Feature 1 lets one canonical source
directory appear inside many topic directories without manual sync. Feature 2
makes the build's file-writing step idempotent and warns when conflicting
content is written to the same output path. They share a doc because the
output-collision problem becomes more visible once Feature 1 lands (multiple
topics will legitimately ship the same file under the same output path).

## Background — what's there today

- A `<topic id="X">` resolves to one filesystem directory (`slides/module_NNN/topic_NNN_X/`)
  via `topic_resolver.build_topic_map` (`src/clm/core/topic_resolver.py:60`),
  optionally narrowed by `module=` per the just-shipped section-module binding
  feature (`docs/claude/design/section-filtering-plan.md` and
  `planning/CLM_SECTION_MODULE_BINDING_DESIGN.md`).
- File discovery for a topic walks the directory recursively
  (`DirectoryTopic.build_file_map`, `src/clm/core/topic.py:110`). There is no
  per-file selection or include mechanism in the spec — every file under the
  topic dir travels to output as a `NotebookFile`, `PlantUmlFile`,
  `DrawIoFile`, `SharedImageFile`/`DuplicatedImageFile`, or `DataFile`
  (`src/clm/core/course_file.py:90`).
- File copy goes through `CopyFileOperation` →
  `backend.copy_file_to_output()` (`src/clm/core/operations/copy_file.py:20`).
  No collision tracking; last writer wins silently.
- The image registry tracks `(relative_path, content_hash)` for
  `SharedImageFile` only and warns on conflicts
  (`src/clm/core/image_registry.py:62`); plain `DataFile` and notebook outputs
  bypass it.
- `<dir-group>` already exists and supports topic-scoped declarations parsed
  by `CourseSpec.parse_dir_groups()` (`src/clm/core/course_spec.py:828`).
  However, dir-groups copy to a separate top-level output directory (e.g.,
  `Projekte/`) and are *not* visible to the kernel that executes a notebook
  in a topic dir.
- Notebooks in `slides/module_550_ml_azav/topic_040_gradio_intro/` import a
  sibling `simple_chatbot` package by name (line 1030 of
  `slides_010_gradio_intro.py`):
  `from simple_chatbot.budget_guard import BudgetGuard`. Resolution depends
  on `simple_chatbot/` being a sibling directory of the notebook in CWD,
  both during direct execution (CWD = temp dir from
  `notebook_processor.write_other_files_sync`) and in Docker workers
  (CWD inside the `/source` mount).
- The package is *not* `pip install`ed anywhere; it ships as source in
  `examples/SimpleChatbot/src/simple_chatbot/`. Three byte-identical copies
  currently exist: the example dir plus two topic dirs (`topic_040`,
  `topic_041`) — all manually kept in sync.

## Problem

**Feature 1: Manual cross-topic file duplication.** Several topics need the
*same* `simple_chatbot/` package directory to be present alongside their
notebook so that `from simple_chatbot.X import Y` works during execution. We
duplicate the package into each topic dir. Every change to the canonical
package must be propagated to N copies by hand, and drift goes undetected
until a build mysteriously diverges from the example.

The natural workaround — install `simple_chatbot` as a wheel — fails our
constraints: students see the package as *source code they can read and
modify*, the package is the curriculum content, not a black-box dependency.
The sibling-directory pattern is intentional. We need a way to keep one
canonical source while still presenting the package as a sibling directory
of the notebook.

**Feature 2: Silent output-path collisions.** When two topics produce a file
with the same relative output path (e.g., both `topic_040/simple_chatbot/main.py`
and `topic_041/simple_chatbot/main.py` map to
`output/.../Projekte/SimpleChatbot/main.py`), CLM writes both, last one wins,
and nobody is told. With Feature 1 we will routinely emit *identical* writes
to the same path (which is fine, but inefficient); without Feature 1 we already
have *non-identical* collisions whose cause is invisible.

A separate but related case: the C# course at
`C:\Users\tc\Programming\CSharp\CSharpCourses\slides\module_310_unit_testing\`
has files like `NUnitTestRunner.cs` (231 lines) repeated identically across
26 topic directories. C# does not have a Python-style sibling-package import
escape hatch — the runner file *must* live in each topic directory so that
the notebook compiles. Feature 1 cannot replace this duplication: the test
runner needs to be physically present in each topic source dir, not virtually
included. Feature 2 should still notice that all those copies write the same
content to identical output paths, and (a) skip redundant writes, (b) flag if
one ever drifts.

## Goals

1. **F1.G1**: One canonical source location for a shared
   directory/file; topics declare that they include it; the build pipeline
   makes it appear next to the notebook for execution and copies it to
   output, with no manual sync.
2. **F1.G2**: Local notebook authoring (running the deck in VS Code or
   Jupyter without going through `clm build`) continues to work — the
   notebook's `from simple_chatbot import X` must succeed when the user
   opens the deck directly.
3. **F1.G3**: Compatible with Direct *and* Docker workers. In particular:
   the Docker image does *not* need to be rebuilt with the included sources
   baked in; sources are mounted/copied at job time the same way the topic's
   own files are.
4. **F1.G4**: Dependencies of the included package (e.g., `gradio`, `openai`)
   are surfaced so the user can verify the worker environment satisfies them,
   but the design does not auto-install them.
5. **F2.G1**: The build's file writer becomes idempotent for the
   identical-content case (skip with a debug log), and emits a *warning* for
   the differing-content case naming both source files and the output path.
6. **F2.G2**: Works for every output kind (notebook output, data files,
   images, dir-groups, jupyterlite output). One choke point.
7. **Backwards compatibility**: existing specs and existing topic
   directories build identically; the new features are opt-in.

## Non-Goals

- Replacing topic-dir-based file discovery with a fully spec-driven file
  manifest. Keeps the "drop a file, it ships" workflow intact.
- Auto-installing transitive dependencies of an included package. We
  surface them; the operator decides.
- Symlink-based source materialization on Windows (junctions are a fallback
  but not the default — copies are simpler and more portable for students
  cloning the repo).
- Removing the C#-style same-file-in-many-topics duplication; Feature 2 is
  scoped to *detecting* and *deduplicating writes*, not eliminating sources.
- Cross-spec sharing (an include in spec A pulling from spec B). Single-spec
  scope only.

---

## Feature 1: Shared-Source Includes

### Spec schema

Add a `<include>` element accepted under `<topic>` (and, as a default, under
`<section>`):

```xml
<sections>
  <section name="Week 04">
    <topic id="gradio_intro">
      <include source="examples/SimpleChatbot/src/simple_chatbot"
               as="simple_chatbot"/>
    </topic>
    <topic id="gradio_deep_dive">
      <include source="examples/SimpleChatbot/src/simple_chatbot"
               as="simple_chatbot"/>
      <include source="examples/SimpleChatbot/.env.example"
               as=".env.example"/>
    </topic>
  </section>

  <!-- Or as a section default applied to every topic that doesn't override -->
  <section name="Week 04" enabled="true">
    <include source="examples/SimpleChatbot/src/simple_chatbot"
             as="simple_chatbot"/>
    <topic id="gradio_intro"/>
    <topic id="gradio_deep_dive"/>
  </section>
</sections>
```

Attributes:

- `source` (required): path to the source file or directory, relative to
  the course root (the directory containing the spec file). Both files and
  directories supported.
- `as` (optional): the relative path inside the topic directory where the
  source should appear. Defaults to the basename of `source`. Must be a
  single relative path, no `..` segments.
- `optional` (optional, default `false`): if `true` and `source` is missing,
  the include is silently skipped instead of erroring. Useful for variant
  decks that may or may not need the package.

Includes on `<section>` apply to every direct `<topic>` child unless that
topic declares an `<include as="X">` with the same `as` value, which
overrides it. The `as` value is the deduplication key within a topic.

### Resolution semantics

A topic's effective file map is the union of:

1. Files physically present in the topic directory
   (`DirectoryTopic.build_file_map`).
2. Files contributed by each `<include>`, mapped to relative paths under
   the topic directory using the include's `as` value.

If a real file in the topic directory and an included file resolve to the
same relative path, **the real file wins** and a warning is emitted
(`include_shadowed_by_local`). This preserves the current "drop a file, it
ships" override path: a topic can locally override one file from an included
package by simply creating a file at the same relative path.

If two `<include>` elements in the same topic resolve to the same relative
path, that's an error (`include_target_collision`) — the spec must pick one.

### Build pipeline integration

Touch points (in order of execution):

**1. Spec parser (`src/clm/core/course_spec.py`)**

Parse `<include>` elements into a new `IncludeSpec` dataclass, attached to
each topic ref alongside the existing fields. Section-level includes are
applied during `parse_sections` as defaults that each topic merges
unless overridden by `as`-key.

**2. File discovery (`src/clm/core/topic.py` → `DirectoryTopic.build_file_map`)**

Extend the method to accept the topic's resolved `IncludeSpec` list and
virtually splice the included paths into the file map under their `as`
target. `CourseFile` instances created from included sources carry an
extra field `source_origin: Path | None` pointing at the canonical location
on disk — this is what we use for content hashing in Feature 2 and for
debugging.

For directory includes, the included directory's tree is walked using the
same logic as a regular topic directory (excluding `__pycache__`,
`.venv`, etc., per the existing `SKIP_DIRS_FOR_COURSE` filter at
`src/clm/core/topic.py:110`).

**3. Notebook execution (`src/clm/workers/notebook/notebook_processor.py`)**

`write_other_files_sync` (line 1529) already writes the topic's non-notebook
sibling files to the temp dir before launching the kernel. With the file
map already including the virtual entries, no additional code path is
needed — the existing copy loop will pick them up. The kernel's CWD remains
the temp dir, and `from simple_chatbot import …` resolves to the copy in
CWD, identical to today's manually-duplicated arrangement.

**4. Docker worker (`src/clm/infrastructure/workers/worker_executor.py`)**

The worker receives the topic's effective file list as part of the job
spec, with absolute host paths. Two host paths participate now: the topic
directory (under `/source`) and the include source (also under `/source`
since includes are restricted to course-root-relative paths). A single
mount of the course root at `/source` already covers both, which the
existing source-mount setup at line 147 provides when `data_dir` is set
to the course root.

**Critical for Docker:** the Docker image does *not* need updating. The
include adds *source files* to the topic's effective set; it does not add
runtime dependencies. Whatever Python deps `simple_chatbot` requires
(`gradio`, `openai`) must be in the image as before — that need is
unchanged by this feature. We surface them (see Validation) so the
operator does not get a surprise `ModuleNotFoundError` mid-build.

**5. Output generation**

Outputs follow the relative-path mapping. An included file with `as=simple_chatbot/main.py`
in `topic_040_gradio_intro` is written to
`{output_root}/Slides/{format}/{kind}/{section}/topic_040_gradio_intro/simple_chatbot/main.py`,
exactly where the manual copy is written today. The two topics' outputs
collide at this stage (Feature 2 handles it).

### Local development workflow

The notebook author wants to *open the deck in VS Code or Jupyter and
run it cell by cell*. For that, `simple_chatbot/` must physically sit
next to the notebook on disk — Python's import system has no notion of
"virtual sibling."

We provide a CLI command:

```
clm sync-includes [SPEC] [--mode=copy|symlink|hardlink] [--remove]
```

- `copy` (default): writes a physical copy of each include into the
  declared `as` location. A marker file `.clm-include` is dropped at the
  copy root so we can identify and clean up later. Marked paths are
  added to `.gitignore` automatically (one entry per topic, idempotent
  patch).
- `symlink`: creates a directory junction (Windows) or symlink (POSIX).
  Faster, no drift, but Windows junctions only work for directories and
  require admin in some setups.
- `hardlink`: file-by-file hardlinks. Cross-filesystem-fragile.
- `--remove`: delete previously-synced copies (uses the `.clm-include`
  marker).

Default is `copy` because that is the lowest-friction option for a
student cloning the repo and not running CLM. The user can opt into
symlink mode via `clm sync-includes --mode=symlink` or a project-level
config.

Pre-build check: `clm build` warns if a declared include is missing
*from the source tree* AND the build was started without
`--allow-virtual-includes` (or equivalent). Reason: in some environments
(course author's machine) we want everything materialized; in CI/Docker,
virtual is fine. Default is virtual-OK; the warning is informational.

`.gitignore` integration is opt-in: `clm sync-includes --gitignore`
prints/applies suggested entries; nothing happens automatically.

### Validation (`clm validate-spec`)

Add three checks:

- `include_source_missing` (error unless `optional="true"`): the path
  named in `source` does not exist on disk relative to the course root.
- `include_target_collision` (error): two includes in one topic share
  the same `as` value.
- `include_shadowed` (warning): a real file in the topic dir has the
  same relative path as a file contributed by an include.
- `include_dependencies` (info): if the include has a `pyproject.toml`
  at its source root or in a containing directory whose `packages`
  field includes the source, surface its `[project] dependencies` in
  the validate-spec output. Operators eyeball this to confirm the
  worker image will satisfy them. (Pure informational — no auto-install.)

### Migration of the current ML AZAV state

After Feature 1 lands:

1. In `course-specs/machine-learning-azav.xml`, add to the relevant
   topics:
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
2. Run `clm sync-includes course-specs/machine-learning-azav.xml --remove`
   to delete the stale physical copies in
   `slides/module_550_ml_azav/topic_04?/simple_chatbot/`.
3. Run `clm sync-includes` (no flags) to materialize the canonical
   copies for local development. Or `--mode=symlink` for the no-drift
   variant if the author has admin/symlink-on-Windows configured.
4. Verify `clm build` produces byte-identical output to before, modulo
   the new dedup behavior from Feature 2.
5. Drop the duplicates from `.gitignore` *or* keep them ignored if
   `.clm-include` markers are present.

---

## Feature 2: Output-Write Deduplication and Collision Warning

### Tracking

Introduce a per-build singleton `OutputWriteRegistry` (analogue of
`ImageRegistry` but covering all writes). It records, for each
absolute output path written during the build:

- the `bytes` content hash (BLAKE2b-128 or SHA-256 truncated; speed >
  cryptographic-strength matters here)
- the source `CourseFile.path` of the *first* writer
- a count of subsequent identical writes

The registry hooks into `backend.copy_file_to_output()` and the
notebook-output writer (the two real choke points; jupyterlite and
plantuml outputs ultimately funnel through these).

### Behavior on second write to same output path

| Existing entry | New write | Action |
|---|---|---|
| missing | (any) | record entry, write file |
| same hash | same hash | increment count, *skip the actual write*, debug-log |
| different hash | different hash | write file (current behavior preserved), emit `output_path_conflict` warning naming both source files and the output path, replace the registry's first-writer record with the latest, increment a `conflict_count` counter |

The conflict-warning message:

```
WARN [output_path_conflict]
  Output path written multiple times with differing content:
    output: {abs_output_path}
    first writer:   {course_file_a.path}  (hash {hash_a})
    second writer:  {course_file_b.path}  (hash {hash_b})
  The second writer's content is in the output. To resolve, ensure
  both sources contain identical content (consider an <include>) or
  give them distinct output paths.
```

End-of-build summary (in `BuildReporter`): "{N} output paths written
multiple times with identical content (deduplicated); {M} output paths
had conflicting writes (last writer won)."

### Hashing strategy

For each write, hash the bytes about to be written. For files copied
verbatim (`CopyFileOperation`), hashing the source on read is fine. For
generated content (notebook output), hash the produced bytes before
writing.

Avoid expensive hashing on huge binary assets: skip files larger than
e.g. 50 MB by default and rely on path equality only (with a single
top-level `output_large_file_collision` warning). Configurable via env
var or build flag.

### Integration with `ImageRegistry`

`ImageRegistry` already does this for shared images. We do not
duplicate its work — the new registry skips paths owned by
`ImageRegistry` and the existing image-warning path stays intact.
Practical effect: image collisions still surface via the existing
channel; the new channel covers everything else (data files, notebook
outputs, dir-group outputs, jupyterlite outputs).

### Where the warning surfaces

- `clm build` log: WARN line per conflict, plus end-of-build summary.
- `BuildReporter` JSON output: machine-readable list under a new
  `output_conflicts` key.
- Exit code: unchanged (warnings, not errors). A future `--strict`
  flag could promote them; out of scope for this PR.

---

## Backwards compatibility

- **Specs without `<include>`**: identical behavior. New parser path
  is only entered when `<include>` is present.
- **Specs with the manual duplicate copy approach (current AZAV ML)**:
  identical output until they migrate. The new dedup-write step will
  start producing a debug log on the (currently silent, identical)
  duplicate writes, but no warning, since the bytes match.
- **`<dir-group>`**: untouched. `<include>` and `<dir-group>` solve
  different problems: dir-groups land in a separate top-level output
  directory (e.g., `Projekte/`); includes land *inside* a topic's
  output. Both can coexist in one spec.
- **CourseFile subclassing**: `source_origin` is added as an optional
  field with default `None`. Existing producers do not need to set it.

---

## Test plan

### Feature 1

**Unit**

- Spec parser: `<include>` with both attributes, with only `source`,
  with `optional="true"`, missing required attributes, with a
  forbidden `..` segment in `as`. Section-level vs topic-level vs
  override.
- Topic file map: include of a directory; include of a single file;
  shadowing by a real local file (warning emitted, local wins);
  collision between two includes (error).
- Validation: `include_source_missing` error, `optional` skip,
  `include_target_collision`, `include_dependencies` info pulls
  `[project] dependencies` from a `pyproject.toml`.

**Integration**

- Build a synthetic course with two topics that share one
  `<include>`. Verify each topic's output dir contains the included
  files. Verify the canonical source is *not* modified.
- Run a notebook that imports the included package in Direct mode.
  Verify execution succeeds and the output cells contain expected
  results.
- Run the same notebook in a Docker worker. Verify execution succeeds
  and that the included files appear under `/source/<topic>/<as>`
  inside the container (or whatever path layout the existing
  `write_other_files_sync` uses for the temp dir). The Docker image
  is *not* rebuilt as part of this test.
- `clm sync-includes`: copy mode produces a copy with `.clm-include`
  marker; `--remove` deletes only marked paths and not unrelated
  ones; symlink mode falls through gracefully on Windows when junction
  creation fails (warning + fallback to copy).

**Smoke**

- Migrate `course-specs/machine-learning-azav.xml`'s `topic_040`
  and `topic_041` per the migration recipe above. Run a full build.
  Compare output to a build done before the migration. Diff should
  show no changes other than (a) the new build writing the canonical
  bytes from `examples/SimpleChatbot/src/simple_chatbot/` rather than
  the manually-duplicated bytes (which are currently identical
  anyway, so diff is empty), and (b) the build reporter showing the
  new dedup count.

### Feature 2

**Unit**

- `OutputWriteRegistry` records first write; second identical write
  is deduplicated (no fs activity) and the count is bumped; second
  differing write emits a warning and overwrites.
- Path-equality fast-path triggers on >50 MB files.

**Integration**

- Build a synthetic course where two topics produce the same data
  file path with identical content. Verify the file is written once,
  not twice; verify `BuildReporter` counts the dedup.
- Build a synthetic course where two topics produce the same output
  path with *different* content. Verify both writes are visible to
  the registry, the warning appears in build logs, the second
  writer's bytes end up on disk, and the JSON reporter contains the
  conflict entry.
- Build the C# unit-testing module (or a synthetic miniature of it)
  with the repeated `NUnitTestRunner.cs` across many topics. Verify
  the dedup count equals N-1 where N is the topic count.

**Regression**

- The image registry continues to fire `image_collision` warnings on
  divergent shared images; the new registry must not double-warn for
  the same path.

### Performance

- Hash overhead for a typical 1500-topic ML course build: measure
  before/after. Acceptable budget: <2% of total build wall time.
  Includes a benchmark run in CI on the existing
  `tests/perf/` infrastructure (or wherever fits).

---

## Resolved decisions (was "Open questions")

1. **Section-level include inheritance**: simple inheritance. Validation
   emits an info-level message listing every topic that inherits a
   section-level include, so the propagation is auditable without
   schema gymnastics.
2. **Include source pointing at a topic directory in `slides/`**: warn
   but allow. Revisit if abuse patterns appear.
3. **Includes pointing outside the course root**: **disallowed in v1**.
   Spec parser raises an error. Avoids Docker-mount surprises and keeps
   builds reproducible. Revisit if a real cross-repo case appears.
4. **Output-write registry persistence across builds**: **no**.
   Per-build only. Avoids cache-invalidation complexity for marginal
   gain.
5. **Symlink-on-Windows default**: **`copy` is the default** for
   `clm sync-includes`. `--mode=symlink|hardlink` are opt-in. No runtime
   detection of "is symlink supported"; the operator picks.
6. **`<dir-group>` overlap warnings**: emit the conflict warning as
   normal. If it becomes noisy in practice, add a `dedup="silent"`
   attribute to `<dir-group>` later.

---

## Estimated size

- Feature 1: ~300–450 LOC across `course_spec.py`, `topic.py`,
  `course_file.py`, the new `clm sync-includes` CLI, and tests.
- Feature 2: ~200–300 LOC across a new `OutputWriteRegistry` module,
  hooks in `copy_file_to_output` and the notebook output writer,
  reporter integration, and tests.
- Spec docs: updates to `src/clm/cli/info_topics/spec-files.md`,
  `docs/user-guide/spec-file-reference.md`,
  `src/clm/cli/info_topics/commands.md` (for `sync-includes`),
  `src/clm/cli/info_topics/migration.md` (mention the migration
  recipe), and `CHANGELOG.md`.

Suggested split: ship Feature 1 first (it's user-visible and unblocks
the AZAV ML simple_chatbot duplication), Feature 2 second (it's an
infrastructure clean-up that primarily benefits the post-Feature-1
state). Each is a separate PR.
