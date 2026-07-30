# CLM {version} — Slide File Format Reference

CLM slide files use the **jupytext percent-format**: plain source files
(`.py`, `.cs`, `.cpp`, `.java`, `.ts`, …) with cell boundaries marked by a
comment token + `%%`. The comment token is `#` for Python/Rust/Markdown and
`//` for C++/C#/Java/TypeScript.

## Cell boundary syntax

```python
# %%                       # code cell (Python)
# %% [markdown]            # markdown cell (Python)
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
//  %%                     # code cell (C#/C++/Java/TypeScript)
// %% [markdown]           # markdown cell
```

All metadata is **optional and order-independent** on the marker line:

| Attribute | Values | Purpose |
|---|---|---|
| `[markdown]` | present/absent | Markdown cell (absent = code cell) |
| `lang="de"` / `lang="en"` | `de`, `en` | Language filter; omit for shared cells |
| `tags=["t1", "t2"]` | list of tag names | Presentation and visibility control |
| `slide_id="slug"` | kebab-case ASCII, ≤ 30 chars | Stable cross-reference key |
| `for_slide="slug"` | bare slug | Voiceover companion back-reference |

Cells without a `lang` attribute are **shared** — included in every language build.

## Jinja2 (j2) cells

The file opens with a j2 import and a title macro call; these are not `# %%` cells:

```python
# j2 from 'macros.j2' import header
# {{ header("Deutsches Thema", "English Topic") }}
```

`header()` emits two `slide`-tagged markdown cells — one DE, one EN — both
anchored to `slide_id="title"`. In split-format files the bilingual form is
replaced by `header_de()` (in `.de.*`) and `header_en()` (in `.en.*`).

## Tag reference

### Slide-structure tags

| Tag | Meaning |
|---|---|
| `slide` | Starts a new visual slide; opens a **slide group** |
| `subslide` | Starts a sub-slide within the current slide |
| `notes` | Brief speaker hint; attached to preceding slide |
| `voiceover` | Read-aloud narration script; attached to preceding slide |

### Code-visibility tags

| Tag | Meaning |
|---|---|
| `keep` | Visible in all output kinds |
| `start` | Starter code shown in the code-along output; paired with `completed` |
| `completed` | Full solution shown in the completed/speaker output; **always follows a `start` cell** |
| `alt` | Alternative solution — shown in the completed/speaker output and omitted from the code-along entirely; **never follows a `start` cell** (a `start` → `alt` sequence is the pre-`completed` legacy form: `clm validate --checks tags` errors on it, and a plain `clm slides normalize` migrates it to `start` → `completed` — run the full normalize, not `--operations tag_migration` alone, which skips the `placeholder_start` pass that must precede it) |

`alt` and `completed` have **identical output visibility** (suppressed in the
code-along, shown in the completed/trainer/speaker variants); the distinction
is purely whether a `start` partner exists. `completed` is the solution to
starter code the code-along shows; `alt` is an additional solution the
code-along never shows at all. Legacy corpora still contain `start`/`alt`
pairs — do not infer that pairing from them; it predates `completed`.

The `start` / `completed` pair represents the same logical code block in two
variants. Canonical DE/EN interleaving is:
```
[DE start]  [EN start]  [DE completed]  [EN completed]
```
The cohesion layout `[DE start]  [DE completed]  [EN start]  [EN completed]`
is also valid; `clm slides normalize --operations interleaving` converts to canonical.

### Other tags

| Tag | Meaning |
|---|---|
| `workshop` | Opens a workshop **scope** — a *range of cells*, not a per-cell property (markdown only; see "Workshop scope" below) |
| `end-workshop` | Closes the workshop scope: marks the first cell **after** it — valid on any cell type (since {version}). The tagged cell is *outside* the workshop: tagging the workshop's final code cell excludes that cell from the range (it renders completed, not blanked; identical output for `keep`-tagged cells). See "Workshop scope" below for the implicit closers. |
| `answer` | Solution text; cleared in code-along output |
| `private` | Visible only in trainer/speaker output |
| `del` | Removed from all outputs |
| `nodataurl` | Prevents image inlining as data-URL |

### Workshop scope

A workshop is a **range of cells**. Membership is *positional* — a cell is
inside the workshop because it sits inside the range, never because it
carries the `workshop` tag itself. Computing "which cells are in the
workshop" by looking for the tag per cell gives wrong answers; use the
range rules:

A workshop **opens** at either

- a markdown cell tagged `workshop`, or
- a `slide`/`subslide` **markdown** cell whose `slide_id` starts with
  `workshop-` — the deck-level convention when the announcement slide is a
  regular slide. Voiceover/notes cells sharing that slide's id do not
  open or fragment a scope, and neither opener form counts on a code cell.
  **Caveat (issue #732)**: the notebook build's `partial` output currently
  recognizes only the **tag** form — a deck relying on the slide_id form
  alone passes validation but its partial build detects no range (starter
  deleted, solution emitted in full). Until #732 lands, also tag the
  opening cell with `workshop`.

It **closes** (exclusively — the closing cell is *outside* the workshop) at
the first of:

- the next cell of **any** type tagged `end-workshop`,
- the next workshop opener (a new workshop begins immediately), or
- **end of notebook**. A workshop without `end-workshop` runs to the end of
  the deck — this implicit form is the corpus norm; `end-workshop` is only
  needed when non-workshop content follows the exercise.

Worked example — which cells fall inside:

```
# %% [markdown] tags=["slide", "workshop"] slide_id="workshop-basic-prompting"  ← OPENS
# %% [markdown] slide_id="workshop-basic-prompting" …task text…                   inside
# %% tags=["start"]                                                               inside
# %% tags=["completed"]                                                           inside
# %% [markdown] tags=["slide", "end-workshop"] slide_id="next-section" …        ← OUTSIDE, closes
```

Without the last cell, the scope would run to the end of the deck. Inside
the range, the `partial` output (code-along-style inside the range) drops
`alt`/`completed`/`del`/`notes`/`voiceover` cells, blanks the source of
code cells not tagged `keep`/`start`, blanks `answer` markdown, and clears
the outputs of every remaining code cell — workshop code is never shown as
executed. Outside the range, `partial` mirrors the completed output
(`start` cells are deleted there), so coverage arithmetic must treat the
two regions differently.

## `slide_id` convention

`slide_id` is a **stable, EN-derived, kebab-case slug** that is the cross-language
join key for sync, voiceover, and split operations.

- Slide and subslide cells carry a `slide_id`; narrative cells inherit it.
- The **preserve marker** `!` (e.g., `slide_id="!intro"`) prevents auto-regeneration.
  The `!` is source-level only; all comparisons use the bare form (`intro`).
- Auto-generate missing ids with `clm slides assign-ids`.
- Duplicate bare-form ids within a file are an error.

## Bilingual structure

A bilingual file contains interleaved DE and EN cells:

```python
# j2 from 'macros.j2' import header
# {{ header("Grundlagen", "Basics") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="variables"
# ## Variablen
# Variablen speichern Werte.

# %% [markdown] lang="en" tags=["slide"] slide_id="variables"
# ## Variables
# Variables store values.

# %% tags=["keep"]
name = "Alice"        # shared — identical in both language builds

# %% tags=["start"]
value =               # shared starter code

# %% tags=["completed"]
value = 42            # shared completed code

# %% [markdown] lang="de" tags=["voiceover"] slide_id="variables"
# Erklären Sie Speicherverwaltung.

# %% [markdown] lang="en" tags=["voiceover"] slide_id="variables"
# Explain memory management.
```

Rules:
- Paired DE/EN slide cells must share the same bare `slide_id`.
- Shared cells appear in the same position in both language builds.
- The EN heading is the authority for the slug.

## Split-format (`.de.*` / `.en.*`)

`clm slides split` produces a **split pair** from a bilingual file.
Each half keeps all shared cells byte-for-byte and only the cells for its language:

```python
# .de.py
# j2 from 'macros.j2' import header_de
# {{ header_de("Grundlagen") }}

# %% [markdown] lang="de" tags=["slide"] slide_id="variables"
# ## Variablen

# %% tags=["keep"]
name = "Alice"        # shared — byte-identical to .en.py
```

```python
# .en.py
# j2 from 'macros.j2' import header_en
# {{ header_en("Basics") }}

# %% [markdown] lang="en" tags=["slide"] slide_id="variables"
# ## Variables

# %% tags=["keep"]
name = "Alice"        # shared — byte-identical to .de.py
```

Invariant: `unify(*split(deck))` reproduces the original bilingual file byte-for-byte.
The `slide_id` set and order must agree between the two halves — they are the
cross-language join key. Divergence is detected by `clm validate` (cross-file check).

Voiceover companions (e.g., `voiceover_basics.de.py` / `voiceover_basics.en.py`)
follow the same pattern; their cells use `for_slide` instead of `slide_id` to
reference the slide they narrate.

## Validation and normalization

| Command | What it does |
|---|---|
| `clm validate <path>` | Check format, pairing, tags, slide_ids |
| `clm validate <path> --quick` | Fast syntax-only check (pre-save hook) |
| `clm slides normalize <path>` | Auto-fix spacing, tag migration, interleaving, slide_ids |
| `clm slides assign-ids <path>` | Mint missing `slide_id` values |
| `clm slides split <path>` | Convert bilingual → `.de.*` / `.en.*` pair |
| `clm slides unify <path>` | Merge split pair → bilingual |
| `clm slides sync <path>` | Propagate edits from one half to the other |

See `clm info commands` for full flag reference.
