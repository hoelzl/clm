# Sharing generated diagram renders across topics

**Status**: DECIDED 2026-09-25 (design only — the code path described in
§2 exists but is untested, so per the claimed-wired rule nothing here is
"supported" until the §6 issues land) | **Created**: 2026-09-25
**Issues**: #987 (this design) | **Related**:
`docs/claude/design/shared-source-includes-and-output-dedup.md` (the
`<include>` and output-write-registry mechanics), #664 (`img-generated/`),
#855 (multi-dot render names),
`docs/claude/discussions/shared-diagram-renders/state.md` (the argument)

---

## 1. The case

`topic_0360_rag_introduction` owns `drawio/cosine-distance.drawio` and
`drawio/vector-difference.drawio` (rendered into its `img-generated/`).
`topic_0400_vector_embeddings` shows the same two diagrams
(`<img src="img/cosine-distance.png">`) and is used by specs that do not
include `rag_introduction` (`machine-learning-azav.xml` and `-2026-08.xml`
use both; `ai-development-with-python-azav.xml` and `-2026-04.xml` use only
`rag_introduction`). It carries **static copies** in `img/`. When both topics
sit in one section their image directories collapse onto the section's output
`img/`, and the build reported 48 "multiple writers produced different
content, last writer won" conflicts until the copies were re-synced by hand
(PythonCourses `fadaaf1f`, 2026-09-21). Nothing links the copy to its source,
so the next re-render brings the conflict back.

## 2. What exists (verified 2026-09-25)

- **`<include>` is a virtual splice.** `Topic.add_virtual` presents the
  included file as if it lived at `<topic>/<as>`; `CourseFile.from_virtual`
  picks the class **by the virtual path's suffix**, so an included `.drawio`
  becomes a `DrawIoFile` (`course_file.py:_find_file_class`). The conversion
  operation reads the bytes from `source_origin` and writes the render to
  `img_path`, which is computed from the *virtual* path:
  `<virtual>.parents[1] / img-generated / <stem>.<ext>` — i.e. into the
  **consumer's** `img-generated/`. `Course` then registers that render as a
  generated `DuplicatedImageFile` and schedules its copy to output `img/`
  after the conversion (`course.py`, the `mark_generated` block). The
  `--no-diagrams` branch in `add_virtual` shows included diagram sources were
  anticipated. **No test exercises an included `.drawio`/`.pu`**
  (`tests/core/topic_test.py` covers directory and `.py` includes only).
- **`clm validate` warns `include_source_is_topic_dir`** for any include
  whose source is under `slides/…/topic_*` ("couples two topics' contents —
  prefer a canonical location such as `examples/`").
- **The output-write registry** (`core/output_write_registry.py`) dedups
  byte-identical writes to one output path and surfaces differing ones as
  the conflict warning above; images are in scope since #661.
- **No validator resolves image references.** A deck referencing
  `img/x.png` that nothing produces is found by looking at the built HTML,
  not by `clm validate`.
- **`clm course sync-includes`** materializes includes on disk under a
  `.clm-include` ledger; it would copy a `.drawio` source into the consumer's
  `drawio/` the same way (and clean it up the same way).

## 3. Decision

### 3.1 Step 1 (now): include the diagram *source*, per consuming topic

The supported sharing mechanism is the existing `<include>` pointed at the
owner's **source**, declared on the consuming `<topic>` in each spec that
uses it:

```xml
<topic id="vector_embeddings">
    <include source="slides/module_550_ml_azav/topic_0360_rag_introduction/drawio/cosine-distance.drawio"
             as="drawio/cosine-distance.drawio"/>
    <include source="slides/module_550_ml_azav/topic_0360_rag_introduction/drawio/vector-difference.drawio"
             as="drawio/vector-difference.drawio"/>
</topic>
```

The consumer renders through its normal pipeline; the render lands in the
consumer's `img-generated/` and ships as its output `img/x.png`. Two
consumers in one section write byte-identical renders (same source bytes,
same worker image, same build) and the registry dedups them — identical by
construction, which is what the issue asked for. The static copies in
`vector_embeddings/img/` are deleted.

To make this *supported* rather than *accidental*:

1. **A test** that includes a diagram source and asserts the render is
   produced under the consumer's `img-generated/` and copied to output
   (claimed-wired rule).
2. **Validator carve-out**: `include_source_is_topic_dir` drops to **info**
   when the source is a diagram source (`is_diagram_source`) — for diagrams
   the coupling to the owning topic *is* the intent, and there is no
   "canonical location outside slides/" for a diagram that one topic
   authors.
3. **Docs**: a "Sharing a diagram between topics" recipe in
   `clm info spec-files` under `<include>`, with the `as="drawio/…"` rule
   from §5.
4. **The PythonCourses migration**: add the includes to the two specs,
   delete the copies, build once, commit the consumer's `img-generated/`.

### 3.2 Step 1b (now, separate issue): two `clm validate` checks

- **`image_ref_missing`** (warning, spec mode): a deck references
  `img/<name>` and nothing in the topic will produce it — not a file in
  `img/`, not the render name of a diagram source in `pu/`/`drawio/`, not an
  include (real or virtual). This is the **safety net that makes per-spec
  declaration acceptable**: a spec that forgets the include fails
  `validate` instead of shipping a broken image, which was the issue's main
  objection to spec-level declaration.
- **`image_name_conflict`** (warning, spec mode): two topics that share a
  section's output `img/` provide the same image name with different bytes.
  Today this surfaces only as a build-time conflict after the output is
  already race-dependent. The suggestion text points at §3.1.

### 3.3 Step 2 (deferred): topic-level declaration

A topic-owned declaration ("this topic uses that diagram", outside any
spec) is **deferred**. Revisit when either holds: (i) a topic's include set
must be repeated in more than three specs and `image_ref_missing` has been
tripping in practice, or (ii) a second cross-topic dependency kind appears
(shared data files, shared voiceover assets) that would justify a topic
manifest carrying more than includes.

## 4. Rejected alternatives

- **Topic-local manifest** (`<topic>/.clm/includes.toml` or similar) now. It
  is a second include grammar that `validate`, `sync-includes`, the
  `.clm-include` ledger, the provenance manifest, `clm export context` and
  the MCP course tools all have to learn, and it moves a cross-topic
  dependency out of the one file where `module=` bindings and section
  inheritance already live. Two specs is the measured repetition; a
  validator check covers the forgetting risk.
- **Include the owner's *render*** (`source="…/img-generated/x.png"
  as="img-generated/x.png"`). Ships whatever is committed, not what this
  build renders; the consumer's copy can race the owner's render job in the
  same build; and it does not re-render when the `.drawio` changes in a
  checkout where the owner is not built. Byte-identical only by luck.
- **A "render once, deliver to every consumer" engine feature.** Rendering
  is cached and takes seconds; the outputs are identical by construction
  and dedup at the registry. An engine-level shared-asset graph is
  machinery without a measured cost to remove.
- **A new spec element** (`<shared-diagram>`, `<asset>`). `<include>`
  already carries source, target and optionality; a new element would
  duplicate its resolution rules.

## 5. Landmines

- **`as` must keep the diagram subdirectory.** `img_path` is derived from
  `virtual_path.parents[1]`, so `as="cosine-distance.drawio"` (topic root)
  would compute the render target in the *module* directory. Always
  `as="drawio/<name>.drawio"` / `as="pu/<name>.pu"`. The step-1 docs and the
  validator carve-out must say so; a parse-time check (diagram-source
  include whose `as` has no `pu/`/`drawio/` prefix → error) is cheap and
  belongs in step 1.
- **A second committed render** appears in the consumer's `img-generated/`
  (course repos commit that directory). It is build-owned and regenerated
  from the same source, so it cannot drift; it is one `git status` line on
  the first build after adding the include.
- **Multi-dot stems** (`embeddings.de.drawio`) render as `embeddings.de.png`
  (#855); the include's `as` must keep the full stem.
- **If the owning topic moves**, every consumer's include breaks with
  `include_source_missing` — an error at validate and build time, i.e. loud,
  which is the right failure mode. `clm course mv` (course-restructure arc)
  should rewrite include sources when it lands.
- **Render cache keys include the (virtual) path**, so each consumer renders
  once; the cost is seconds per diagram per consumer.

## 6. Sequencing and issues

1. **#987 itself = step 1** (test, carve-out, `as` prefix check, docs,
   PythonCourses migration).
2. **Step 1b** = **#1008** (the two validate checks).
3. **Step 2** recorded as deferred on #987 with the revisit condition above.

## 7. Amendments

| Date | Change | Sections | Nature |
|---|---|---|---|
| 2026-09-25 | Initial decision (discussion session S8) | all | — |
