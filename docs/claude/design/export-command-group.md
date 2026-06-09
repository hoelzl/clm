# Design: the `clm export` command group

Status: accepted (2026-06-08). Implements the unification of the three
course-document commands — `outline`, `schedule`, `summarize` — under a single
top-level group with consistent options.

## Problem

CLM has three top-level commands that each turn a course spec into a
human-readable document:

- `clm outline` — section/topic tree (Markdown or JSON)
- `clm schedule` — day-of-week deck listing for AZAV certification (Markdown or CSV)
- `clm summarize` — LLM-generated per-notebook/section summaries (Markdown)

Two problems:

1. **Top-level sprawl.** Project policy is a small top-level surface with
   related commands grouped (`slides`, `spec`, `course`, `voiceover`, …).
   These three are flat and conceptually identical ("describe the course as a
   document"), so they belong in their own group.

2. **Option drift.** The three commands grew independently and disagree on
   shared concerns:

   | Concern | outline | summarize | schedule |
   |---|---|---|---|
   | language | `-L/--language`, default `en` (None ⇒ both on `-d`) | `-L/--language`, default `en` | `-L/--language/--lang`, default `de` |
   | `-o/--output` | ✓ | ✓ | ✓ |
   | `-d/--output-dir` | ✓ | ✓ | **missing** |
   | `--include-optional` | **missing** | **missing** | ✓ |
   | `--include-disabled` | ✓ | **missing** | **missing** (silently drops) |

   In particular `--include-optional` (omit/keep `optional="true"` modules)
   only existed on `schedule`, even though the same notion applies to any
   document view of the course.

## Decisions

- **Group name:** `export`. Reads as "produce an artifact": `clm export
  outline`, `clm export schedule`, `clm export summary`. Distinct from the
  slide `*-report` commands under `clm slides`.
- **Clean break.** The flat `clm outline` / `clm schedule` / `clm summarize`
  are **removed**, not kept as deprecated aliases. (Mirrors the 1.8 alias
  purge; documented in `migration.md`.)
- **Rename `summarize` → `summary`** (noun, matching `outline`/`schedule`),
  with `summarize` kept as a within-group alias: `clm export summary` is
  canonical, `clm export summarize` also works.
- **Unify options** across all three:
  - `--include-optional` everywhere (the headline change).
  - `--include-disabled` everywhere.
  - `-d/--output-dir` added to `schedule`.
  - `-L/--language` is the canonical spelling; `schedule` keeps `--lang` as an
    alias and its `de` default. Per-command defaults are deliberate and stay
    (`outline` None⇒both, `summary` en, `schedule` de).
  - Command-specific options stay command-specific (`outline -f md/json`,
    `--sections-only`; `schedule -f md/csv`, `--no-topic`, `--data-dir`;
    `summary --audience/--granularity/--model/...`).

## Semantics of `optional` / `disabled` in document views

`optional="true"` and `enabled="false"` are **presentation-only** for these
commands — they never change the build. They gate what appears in the document:

- `section_visible(spec, include_optional)` → an optional **section** is shown
  only with `--include-optional`.
- `subsection_visible(sub, include_optional, include_disabled)` → a subsection
  is shown unless it is disabled (without `--include-disabled`) or optional
  (without `--include-optional`).
- An element that is **both** disabled and optional needs **both** flags to
  appear (disabled wins as the stricter gate).

Per-command application:

- **outline** — filters optional sections and optional/disabled subsections in
  both the Markdown and JSON generators. Disabled whole sections continue to be
  surfaced via the existing `--include-disabled` filesystem path.
- **schedule** — already filtered optional; gains `--include-disabled`, which
  surfaces disabled subsections (and disabled whole sections) by resolving
  their decks from the filesystem (the same approach `outline` uses), tagged
  `(disabled)`.
- **summary** — `--include-optional` gates optional **whole sections**.
  Subsection-level optional inside an *included* section is **not** filtered:
  `summary` flattens a section to its notebooks and has no subsection model.
  This limitation is documented in the command help. `--include-disabled`
  summarizes disabled sections by resolving their files from disk through the
  existing `extract_notebook_content(path, …)` path, tagged `(disabled)`.

## Code structure

New leaf module `src/clm/cli/commands/_export_shared.py` (imports nothing from
the three commands, so no import cycle):

- Option decorators: `spec_argument`, `language_option(default=…, aliases=…)`,
  `output_options`, `selection_options`.
- `check_exclusive_output(output_file, output_dir)`.
- Visibility predicates: `section_visible`, `subsection_visible`.
- `disabled_topic_slides(course, topic_spec, language)` — moved out of
  `outline.py` so both `outline` and `schedule` resolve disabled-topic decks
  from the filesystem the same way.

`export_group` is defined in `_groups.py` next to the other groups and wired in
`main.py`:

```python
export_group.add_command(outline, name="outline")
export_group.add_command(schedule, name="schedule")
export_group.add_command(summary, name="summary")
export_group.add_command(summary, name="summarize")  # alias
cli.add_command(export_group)
```

The flat `cli.add_command(outline/schedule/summarize)` registrations are
removed.

## Follow-up (2026-06-09): split-language filtering + `--include-disabled=merge`

Two issues surfaced once the group shipped against a real split-language course
(`machine-learning-azav`):

### 1. Both languages leaked for split decks (bug)

A split topic ships `slides_x.de.py` + `slides_x.en.py`; each companion carries
the requested `-L` language's title in *both* `Text` slots and an
`output_language_filter` of its own language. The subsection path already
filtered on that (`_topic_deck_titles` / `_topic_decks`), but four other
enumerations did not, so `-L de` emitted both the German and English title:

- **Family A — resolved `NotebookFile`** (filter on `output_language_filter`):
  `outline.generate_outline` flat else-branch, `outline.generate_outline_json`
  topic-slides, `outline._subsections_json` enabled-subsection slides,
  `summary.build_sections_data`.
- **Family B — on-disk paths** (filter on the `.de`/`.en` filename suffix):
  `_export_shared.disabled_topic_files`, the single chokepoint for every
  disabled-topic read across all three commands.

Fix: two shared predicates in `_export_shared.py` —
`notebook_in_language(notebook, language)` and `path_in_language(path,
language)` (the latter built on `split_lang_suffix`) — applied at every
enumeration. `disabled_topic_files` gained a required `language` parameter.
Known limitation: a topic that ships *both* a bilingual file and split
companions still lists both (family dedup is the build's `slide_family_key`
concern, out of scope here).

### 2. `--include-disabled=merge`

`--include-disabled` became an *optional-value* option (the only one in the
CLI): omitted ⇒ excluded; bare / `=marked` ⇒ the original "tagged + appended"
behaviour; `=merge` ⇒ disabled content folded into the **normal declared
order** with **no `(disabled)` marker**, so a roadmap spec reads like a finished
course.

- Threading: the option dest is `disabled_mode: str | None`; each handler calls
  `resolve_disabled_mode()` → `(include_disabled, merge_disabled)` bools. The
  generators keep plain-`bool` signatures and gained a defaulted
  `merge_disabled` — so the MCP `course_outline` caller and the direct-generator
  tests are unaffected.
- outline/summary needed real **declared-order interleaving** (a `built_section_map`
  keyed by `section_match_key` lets the merge path walk the full `keep_disabled`
  section list and recover each enabled section's built object). schedule already
  numbered disabled weeks in declared position, so merge is render-only there
  (`render_markdown(mark_disabled=…)`); the schedule data flags stay truthful so
  the CSV `disabled` column is unaffected.
- Structured outputs keep the disabled bit as **metadata** under `=merge`
  (`outline --format json` `"disabled": true`, `schedule --format csv` `disabled`
  column); merge changes only the human-readable placement and marker.
- Click caveat: a bare `--include-disabled` immediately before the `SPEC_FILE`
  positional is consumed as the value — documented; use `=VALUE` and keep the
  spec first.

## Out of scope

- Folding `clm slides slug-report` / `coverage-report` into `export` (they are
  slide-corpus reports, already grouped under `slides`).
- Subsection-level optional filtering inside `summary` (see limitation above).
- Any change to build behavior.
- Deduping a bilingual file against its own split companions in document views.
