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

## Out of scope

- Folding `clm slides slug-report` / `coverage-report` into `export` (they are
  slide-corpus reports, already grouped under `slides`).
- Subsection-level optional filtering inside `summary` (see limitation above).
- Any change to build behavior.
