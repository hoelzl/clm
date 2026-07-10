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
| `completed` | Full solution shown in the completed/speaker output |

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
| `workshop` | Marks the heading cell that opens a workshop section (markdown only) |
| `end-workshop` | Marks the first cell **after** the workshop scope — valid on any cell type (since {version}). The tagged cell is *outside* the workshop: tagging the workshop's final code cell excludes that cell from the range (it renders completed, not blanked; identical output for `keep`-tagged cells). |
| `answer` | Solution text; cleared in code-along output |
| `private` | Visible only in trainer/speaker output |
| `del` | Removed from all outputs |
| `nodataurl` | Prevents image inlining as data-URL |

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
