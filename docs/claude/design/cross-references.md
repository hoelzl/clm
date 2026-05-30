# Cross-References Between Notebooks (Issue #17)

**Status**: Design proposal — awaiting maintainer decisions
**Issue**: [hoelzl/clm#17](https://github.com/hoelzl/clm/issues/17)
**Target version**: TBD (1.7.x candidate)
**Author**: Claude (design-first investigation)

## Problem statement

Authors want to link from one notebook to another (e.g. a slide deck linking
to its workshop). Today this is impossible to do reliably because:

1. CLM **renames** notebooks at generation time. The output filename is
   `"{number_in_section:02} {sanitized_title}{ext}"`
   (`NotebookFile.file_name`, `src/clm/core/course_files/notebook_file.py`),
   placed under a section directory named from the *section* title
   (`CourseFile.output_dir`). Neither the slot number nor the title is known to
   the author at authoring time — both are derived from spec ordering and the
   notebook's H1.
2. The same source notebook fans out to **many output artifacts**: per
   language (`de`/`en`), per kind (`code-along`, `completed`, `trainer`,
   `recording`, `partial`), and per format (`html`, `notebook`, `code`,
   `jupyterlite`). A correct link must resolve to *the sibling artifact of the
   same language/kind/format* as the file it appears in.
3. A hand-written relative link (`../02 Foo/...`) breaks the moment a topic is
   reordered, retitled, added, or removed.

The issue also notes the second requirement: **validate that every linked
notebook is actually included in the course** so a link never dangles.

## How CLM identifies notebooks today (investigation findings)

### Stable identity: the topic ID

The only stable, author-facing identifier that already exists is the
**topic ID** — the directory/file name with its `topic_NNN_` (or
`slides_NNN_`, `project_NNN_`) numeric prefix stripped, computed by
`simplify_ordered_name` in `topic_resolver.build_topic_map`. For
`slides/module_001/topic_100_introduction/`, the topic ID is `introduction`.

This is exactly what `<topic>introduction</topic>` references in a spec, and
it is stable under reordering and renumbering (the numeric prefix is *not*
part of the ID). It is the natural anchor for a cross-reference scheme.

**Caveat — topic ≠ notebook.** A directory topic may contain *several* slide
files (`find_slide_files` returns all `slides_*.py` in the directory), each
becoming its own `NotebookFile` with its own `number_in_section`. So a topic
ID alone does not always uniquely name one output notebook. Options:

- **Topic-granular references** (link to "the topic"): unambiguous only when a
  topic has exactly one slide notebook. For multi-notebook topics we'd need a
  disambiguator (the slide file stem) or a documented "links to the first
  notebook" rule.
- **Notebook-granular references** (link to one specific deck): requires a
  per-notebook identifier. The slide file stem (`slides_foo` →
  `simplify_ordered_name` → `foo`) is a candidate, or an explicit author-set ID
  (see Decision 2).

### Output naming and fan-out

- Output filename: `NotebookFile.file_name(lang, ext)` →
  `"{number_in_section:02} {sanitized_title}{ext}"`.
- `number_in_section` is assigned by `Section.add_notebook_numbers()` *after*
  the topic map is built — split `.de.py`/`.en.py` companions share one slot.
- Section directory: `CourseFile.output_dir` → `target_dir /
  sanitize_file_name(section.name[lang])`.
- The full per-artifact path is assembled in
  `ProcessNotebookOperation` from `output_specs(...)` (language × format ×
  kind × target). The same operation already computes a course-relative
  prefix for images (`compute_img_path_prefix`) — proof that
  "compute a relative path from this output file to another output location"
  is an established pattern.

### Where a link rewrite would hook in

`NotebookProcessor._process_markdown_cell_contents`
(`src/clm/workers/notebook/notebook_processor.py`) already rewrites markdown
cell content per output variant: `notes`/`voiceover` styling, `.png`→`.svg`,
image-path prefixing, and data-URL inlining. A cross-reference rewrite is the
same shape of transform and belongs here.

**Crucial architectural constraint.** The notebook worker processes **one
notebook in isolation** and has no knowledge of the other notebooks' output
filenames. Therefore the *resolution* (topic-id → renamed relative path for
this specific language/kind/format) must happen **at payload-construction
time** in `ProcessNotebookOperation.payload()`, where the full `Course`
(all sections, topics, assigned numbers, titles) is in scope — exactly like
`img_path_prefix`. The resolved map is then passed across the worker boundary
in `NotebookPayload`, and the worker performs only a mechanical string
substitution. This keeps the worker stateless and the resolution testable
without a kernel.

### Validation hook

`clm validate-spec` (`src/clm/slides/spec_validator.py`) already produces
structured `SpecFinding`s for unresolved/ambiguous topics. A "cross-reference
target not in course" check is a natural new finding type there. But note:
spec validation does **not** parse notebook *contents* today — it only reads
the XML and the filesystem topic map. Detecting cross-references requires
scanning slide-file markdown for the reference syntax, which is new work for
the validator (the build path already reads every notebook, so the build-time
check is cheaper to add there).

## Proposed design

### 1. Authoring syntax

Use a **custom URI scheme inside a normal Markdown link** so it is invisible
to ordinary Markdown tooling until CLM rewrites it, and trivially
regex-detectable:

```markdown
See the [Functions workshop](clm:functions_workshop) for exercises.
```

Where `clm:<reference>` is the CLM cross-reference. The link **text** is
authored normally and left untouched. Only the **href** is rewritten.

Reference grammar (options enumerated under Decisions):

```
clm:<topic-id>                      # link to a topic (single-notebook topics)
clm:<topic-id>/<notebook-stem>      # disambiguate a multi-notebook topic  (optional)
clm:<topic-id>#<anchor>             # link to a heading anchor              (optional, Decision 3)
```

Rationale for `clm:` over alternatives:

- `[text](clm:id)` survives jupytext round-trips and `nbconvert` unchanged
  (it is a syntactically valid URI), so an *unbuilt* notebook opened directly
  in VS Code/JupyterLab simply shows a dead `clm:` link rather than corrupt
  Markdown.
- It is unambiguous to detect (`\]\(clm:...\)`) and cannot collide with real
  relative links.
- It mirrors the existing `<topic>id</topic>` vocabulary, so authors reuse a
  concept they already know.

(Alternative schemes — `[[wikilink]]`, a `{ref}` MyST role, an HTML
`<a data-clmref>` — are compared in Decision 1.)

### 2. Resolution (build-time, per artifact)

A new `CrossReferenceResolver` (proposed home:
`src/clm/core/cross_references.py`) is built once per `Course` after sections
and notebook numbers are assigned. It exposes:

```python
def resolve(
    self,
    reference: str,          # the part after "clm:"
    *,
    from_output_file: Path,  # the artifact currently being written
    language: str,
    kind: str,
    format: str,
) -> ResolvedReference | None
```

and returns either a **relative href** from `from_output_file` to the target
artifact of the *same* `(language, kind, format)`, or `None` if the target is
not in the course / not produced for that variant.

Per-format target rules:

| Format | Link target | Notes |
|--------|-------------|-------|
| `html` | the target notebook's `.html` file | natural, fully working |
| `notebook` (`.ipynb`) | the target `.ipynb` | works in Jupyter/VS Code |
| `code` | the extracted `.py`/source file, **or** drop the link | code export is not a hyperlinked medium — see Decision 5 |
| `jupyterlite` | the in-site notebook URL | deferred until the JupyterLite builder ships |

The relative path is computed exactly like `compute_img_path_prefix` does for
images: walk from `from_output_file` to the target artifact's known output
path. Because both files live under the same target root with deterministic
section-dir + `"{NN} {title}{ext}"` names, the resolver can reconstruct the
target path without touching the filesystem.

The resolved map is attached to `NotebookPayload` as a new field, e.g.:

```python
# Mapping of "clm:" reference -> already-resolved relative href for THIS
# (language, kind, format) artifact. Empty when the notebook has no
# cross-references. Computed in ProcessNotebookOperation.payload().
cross_references: dict[str, str] = {}
```

The worker's `_process_markdown_cell_contents` gains a final step:

```python
if payload and payload.cross_references:
    cell["source"] = rewrite_cross_references(cell["source"], payload.cross_references)
```

`rewrite_cross_references` finds every `](clm:<ref>)` and replaces the href
with the resolved relative path (or applies the missing-target policy from
Decision 4 when a ref is absent from the map).

### 3. Validation: "all linked notebooks are included"

Add a build-time pass (and optionally a `validate-spec` finding):

- During the build, the `Course` already reads every slide file to build the
  file map. Add a light markdown scan that extracts every `clm:` reference per
  notebook.
- For each reference, ask the `CrossReferenceResolver` whether the target
  topic/notebook is part of the **resolved course** (i.e. it appears in some
  built section, honoring the active `--section` selection and `enabled=false`
  filtering).
- Emit a structured finding when a reference points at a topic that is not
  included:
  - category `cross_reference_target_missing` (severity per Decision 4).
- Also surface `cross_reference_ambiguous` when a topic-granular ref hits a
  multi-notebook topic and no disambiguator was given.

Because the resolver is course-aware, this also naturally handles
section-filtered builds (`clm build --section w03`): a link to a topic that is
real but *not in the selected sections* is reported, since the rename target
does not exist in this output.

### 4. Behavior when a target is missing/excluded

Three policies, selectable (Decision 4 picks the default):

- **error** — build fails with a clear finding (consistent with
  `topic_not_found`).
- **warning + drop link** — emit a warning and render the link text as plain
  text (strip the href). Safest for partial/roadmap builds.
- **warning + leave `clm:` href** — visible breakage; not recommended.

### Out of scope for v1

- Anchors / sub-section links (Decision 3) — gated behind a stable
  heading-anchor scheme that CLM does not emit today.
- `jupyterlite` link targets — deferred until the JupyterLite site builder
  exists.
- Cross-*course* references (linking into a different course repo).

## Open product decisions (for the maintainer)

> These are genuine product calls. I have **not** picked any of them — each
> lists options and my recommendation, but the maintainer decides.

### Decision 1 — Reference syntax / scheme

Options:
- **(A)** `[text](clm:topic-id)` — custom URI in a normal Markdown link.
- **(B)** `[[topic-id|text]]` wiki-link style.
- **(C)** MyST `{ref}` / `{doc}` role (`` {doc}`topic-id` ``).
- **(D)** Explicit HTML `<a data-clmref="topic-id">text</a>`.

**Recommendation: (A).** Lowest blast radius: valid Markdown, survives
jupytext/nbconvert untouched, regex-detectable, reuses the existing topic-id
vocabulary, and degrades to an obviously-dead link if a notebook is opened
unbuilt. (C) is attractive for MyST-heavy authors but couples us to MyST
parsing; (B) needs a new inline parser; (D) is verbose for authors.

### Decision 2 — Stable identifier scheme

Options:
- **(A)** Reuse the existing **topic ID** (path-derived, already the spec
  vocabulary). Add an optional `/notebook-stem` disambiguator for
  multi-notebook topics.
- **(B)** Require an explicit author-assigned ID in slide front-matter
  (e.g. a `clm-id:` key), independent of path/title.
- **(C)** Hybrid: default to topic ID, allow an explicit front-matter ID to
  override for notebooks that need a rename-proof handle.

**Recommendation: (A) for v1, with (C) as a documented growth path.** Topic
IDs already exist, are validated, and authors know them. An explicit-ID
mechanism is more work (new front-matter field, new validation for
uniqueness/collision) and can be layered on later without breaking (A).
The key question for the maintainer is **granularity**: are references
topic-level (simple, but ambiguous for multi-notebook topics) or
notebook-level (precise, needs the stem or an explicit ID)?

### Decision 3 — Anchors / sub-section links in scope?

Options:
- **(A)** No — references resolve to a whole notebook only (v1).
- **(B)** Yes — `clm:topic#heading` resolves to a heading anchor.

**Recommendation: (A) for v1.** CLM does not currently emit stable,
predictable heading anchors across HTML/`.ipynb`, and the anchor slugging
differs per renderer. Shipping anchors means first defining and emitting a
stable anchor scheme — a separate, larger piece of work.

### Decision 4 — Behavior when a target is not included

Options:
- **(A)** Hard error, blocks the build (like `topic_not_found`).
- **(B)** Warning + drop the link (render text only).
- **(C)** Configurable, with a chosen default.

**Recommendation: (C) with default = error in strict/CI builds, warning+drop
in local/section-filtered builds.** A dangling cross-reference is a genuine
authoring bug and should fail CI (consistent with the existing
`--fail-on-error` philosophy, Issue #90). But a developer building a single
section locally legitimately excludes link targets, so warn-and-drop avoids
blocking iteration. A `--fail-on-missing-xref / --no-fail-on-missing-xref`
flag (env `CLM_FAIL_ON_MISSING_XREF`) mirrors the existing exit-code controls.

### Decision 5 — References in formats with no natural link target

Options for `code` (extracted source) and similar:
- **(A)** Drop the cross-reference entirely (link text only / comment text).
- **(B)** Emit a relative path comment to the sibling source file.
- **(C)** Leave the `clm:` reference verbatim as documentation.

**Recommendation: (A).** Extracted code is not a hyperlinked medium; turning a
`clm:` href into a bare text label (or, for code cells, leaving the surrounding
comment) is the least surprising. `jupyterlite` should be explicitly deferred
(builder not shipped).

## Implementation plan (once decisions land)

1. **Resolver + identifier** (`src/clm/core/cross_references.py`): build the
   topic-id (and optional notebook-stem) → output-path index from a resolved
   `Course`; implement per-`(language, kind, format)` relative-href resolution.
   Pure, fully unit-testable without a kernel.
2. **Reference extraction** (`src/clm/core/utils/`): a regex/markdown scanner
   that pulls `clm:` references out of slide-file text. Shared by build and
   validator.
3. **Payload wiring**: add `cross_references: dict[str, str]` to
   `NotebookPayload`; populate it in `ProcessNotebookOperation.payload()`.
4. **Worker rewrite**: `rewrite_cross_references` + a call in
   `_process_markdown_cell_contents`.
5. **Validation**: new `SpecFinding` types
   (`cross_reference_target_missing`, `cross_reference_ambiguous`) and a
   build-time check honoring section selection.
6. **Exit-code control** (if Decision 4 = C): flag + env var.
7. **Docs (mandatory before release)**: update
   `src/clm/cli/info_topics/spec-files.md` (syntax) and
   `commands.md` (any new flags), plus `CHANGELOG.md`.

## Groundwork scaffolded in this PR

To de-risk the resolver shape *without* committing to any open decision, this
PR includes a small, decision-agnostic scaffold:

- `src/clm/core/cross_references.py` — a `CrossReferenceResolver` interface
  stub plus a pure `extract_cross_references(text) -> list[str]` helper for the
  `clm:` syntax (Decision 1 default). The extractor is correct regardless of
  the identifier-granularity decision because it returns the raw reference
  strings; *interpreting* them is the resolver's job and is left unimplemented
  pending Decision 2.
- `tests/core/test_cross_references.py` — tests for the extractor only
  (detection, multiple refs per cell, leaving ordinary links and image links
  untouched).

No production code path calls the resolver yet, so nothing changes in build
output until the decisions are made and the resolver is implemented.
