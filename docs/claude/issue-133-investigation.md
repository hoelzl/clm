# Issue #133 — split vs bilingual `lines_to_next_cell` divergence

**Status:** investigated 2026-05-25; **producer-side fix shipped 2026-05-30**
(`_strip_lines_to_next_cell` in `notebook_processor.py`); consumer-side
workaround `scripts/diff_build_outputs.py` retained as a build-equivalence
gate.

> **Producer fix as shipped.** The fix is *not* Option A below (strip only
> where the source-successor was filtered out). Re-running the trace against
> the actual data showed Option A does not converge: in the bilingual DE
> build the divergent cells already carry *no* `lines_to_next_cell` (their
> immediate source neighbour is an other-language code clone with identical
> source, so jupytext records nothing), while the `clm slides split` DE deck
> records `1`/`2` because the same cells sit next to a DE markdown cell — and
> nothing is filtered in the split form for an Option-A hook to fire on.
> Both forms yield the *same* surviving cell sequence, so the only
> normalization that converges them is to drop the field unconditionally
> from build output. `_process_notebook_node` now calls
> `_strip_lines_to_next_cell(new_cells)` after filtering. Source files are
> never touched; the field is a jupytext layout artifact with no meaning for
> executed `.ipynb`/HTML. This matches what `diff_build_outputs.py` already
> normalized away — the normalization simply moved producer-side.
**Severity:** Low (cosmetic metadata; HTML rendering identical;
Phase D byte-equivalence gate affected).
**Related issues:** [#128](https://github.com/hoelzl/clm/issues/128) (fixed
in #131), [#132](https://github.com/hoelzl/clm/issues/132) (CRLF on split
output, open), [#133](https://github.com/hoelzl/clm/issues/133) (this one,
open).

## TL;DR

`clm slides split` produces files whose built `.ipynb` and `.py` outputs
diverge from the bilingual build on the `lines_to_next_cell` cell-metadata
field. The cause is **not** trailing whitespace in the split file — both
files have byte-identical inter-cell whitespace where they diverge.
The cause is **jupytext's PEP 8 lookahead through neighboring cells**:
the same physical blank-line count is interpreted differently depending
on whether the next cell is code or markdown, and on what kind of
instruction the *following* code cell starts with. Bilingual interleaves
DE/EN code cells; split places a DE markdown cell next to the same DE
code cell — different lookahead, different "expected" blank-line count,
different metadata.

A "normalize whitespace between cells" feature (whether implemented as a
new `clm normalize` operation or by invoking `ruff format`) would
**not** fix this. It would also be in tension with `ruff format`'s PEP 8
blank-line rules around top-level `def`/`class`, which differ between
bilingual and split layouts. Don't go that route.

The right producer-side fix is post-filter metadata cleanup in the
notebook worker. The right consumer-side workaround until that ships is
the comparison script described below.

## Empirical trace (the bit you'll want when picking this back up)

**Reproducer setup** (CLM `f3f20be`, PythonCourses unchanged):

1. `clm slides split slides/module_550_ml_azav/topic_055_prompt_templates/slides_010_prompt_templates.py --force`
2. Save the bilingual file aside (CLM rejects coexistence by design).
3. Build both forms into separate `--output-dir`s with
   `--http-replay=replay --ignore-cache --only-sections idx:6`.
4. Diff.

**Direct minimal reproducer without a build cycle** (this is the trace I
ran during the investigation — it isolates the jupytext step from CLM's
filter/template steps and produces the same metadata divergence on the
same two cells):

```python
import jupytext
from pathlib import Path
from clm.slides.split import split_text

deck = Path("slides/module_550_ml_azav/topic_055_prompt_templates/slides_010_prompt_templates.py")
text = deck.read_text(encoding="utf-8")
de_text, en_text = split_text(text)

tmp = Path("/tmp")
(tmp / "bil.py").write_text(text, encoding="utf-8", newline="\n")
(tmp / "de.py").write_text(de_text, encoding="utf-8", newline="\n")

nb_bil = jupytext.read(tmp / "bil.py")
nb_de = jupytext.read(tmp / "de.py")
```

In `nb_bil` (149 cells, no filter applied), only cell 142 carries
`lines_to_next_cell` (=2, attached to the EN markdown cell of the
"Task 4 / Aufgabe 4" slide — author-induced spacing).

In `nb_de` (86 cells), the DE-side picks up **two new** entries:

| Cell | `lines_to_next_cell` | Cell source starts with |
|---|---|---|
| 81 | 1 | `for vague in vague_questions: …` |
| 83 | 2 | `def clarify_and_answer(vague: str) -> tuple[str, str]: …` |

Those are exactly the cells the issue identifies. They are not empty;
the issue body says "empty-source DE code cells" because CLM's
`is_cell_contents_included` clears their source later, during the
code-along build. The metadata is attached upstream of that, at the
`jupytext.reads` step in `notebook_processor.process_notebook_for_spec`.

## Why jupytext attaches the metadata for one form but not the other

The interesting code path is in `jupytext`:

- `cell_reader.py:171` — `lines_to_next_cell` is written to the cell
  metadata only when the **actual** blank-line count differs from
  `pep8_lines_between_cells(prev_lines, next_lines, ext)`.
- `pep8.py:82` — the "expected" computation:

  ```python
  if cell_ends_with_function_or_class(prev_lines):
      return 2 if cell_has_code(next_lines) else 1
  if cell_ends_with_code(prev_lines) and next_instruction_is_function_or_class(next_lines):
      return 2
  return 1
  ```

- `pep8.py:6` — `next_instruction_is_function_or_class` walks past `#`
  comment lines, blank lines, decorator `@…` lines, and continuation
  lines — i.e. through an entire markdown cell rendered as `#`-comments
  — into the *next* substantive line. If that line begins with `def`,
  `async`, or `class`, the function returns `True`.

Now the specific divergent positions:

**Cell 81 in split DE — `for vague` followed by a DE markdown cell:**

Inter-cell whitespace is one blank line (verified by `cat -An` on both
files at the divergent positions; see the appendix). The next cell
starts `# %% [markdown] lang="de" …` and its `#`-prefixed body runs for
~30 lines, after which jupytext's lookahead reaches the **next** code
cell — which starts `def clarify_and_answer(…)`. So
`next_instruction_is_function_or_class` returns `True`, jupytext expects
**2** blank lines, file has **1**, metadata `lines_to_next_cell=1` is
recorded.

In bilingual, the same DE `for vague` cell is followed by an EN code
cell that *also* starts `for vague in vague_questions:`. Lookahead
returns `False` (it's `for`, not `def`/`class`), expected is **1**,
actual is **1**, no metadata.

**Cell 83 in split DE — `def clarify_and_answer` followed by a DE markdown cell:**

`cell_ends_with_function_or_class(prev_lines)` is `True` (the cell ends
inside the function body, last non-blank, non-`)` line is indented). So
expected = **2 if next has code, else 1**. The next cell is markdown
(`# %% [markdown] …`), so `cell_has_code(next_lines)` walks the lines and
finds nothing but `#`-comments and blanks → returns `False` → expected
= **1**. But the actual count between `def clarify…` and the next cell
marker is **2** blank lines (PEP 8 spacing after a function def). So
metadata `lines_to_next_cell=2` is recorded.

In bilingual, the `def clarify…` cell is followed by an EN code cell
that also starts `def clarify_and_answer`. `cell_has_code(next_lines)`
returns `True` → expected = **2**. Actual = **2**. No metadata.

**This is the asymmetry — same source whitespace, different lookahead
result.**

## Why "normalize whitespace between cells" doesn't help

1. The whitespace **is** already identical at the divergent positions
   (verified). There is nothing to canonicalize that would change the
   outcome.
2. Even with strict canonicalization (e.g. "always exactly one blank
   line between cells"), jupytext's lookahead still differs because the
   *cell type sequence* differs. You'd need to canonicalize the cell
   sequence, not the whitespace — and that defeats the point of split.
3. `ruff format` was floated as the canonicalizer. It does reformat
   blank lines around top-level `def`/`class` (verified — three blank
   lines collapse to two; one blank line expands to two before a `def`).
   But ruff treats `# %%` markers as ordinary comments, so its choices
   depend on the *surrounding code* — which differs between bilingual
   and split for exactly the same reason `lines_to_next_cell` differs.
   Running ruff would change the divergence pattern without removing
   it, while also forcing a one-time large diff on every existing deck.
4. PythonCourses currently has no slide pre-commit hook; CLM's
   pre-commit applies ruff only under `src/` and `tests/`. Introducing
   one would be a significant policy change for a Low-severity cosmetic
   issue.

Conclusion: **don't add a whitespace-normalize operation to
`clm normalize` to fix #133.** If we ever want one for other reasons
(smaller diffs across commits, author readability), do it without
invoking ruff and accept that it will not address #133.

## The right producer-side fix (when we want to do it)

`notebook_processor.py:903-927` (`_process_notebook_node`) is the
location. The cells in `nb` still carry `lines_to_next_cell` from
`jupytext.reads`, computed against neighbors that may have been
*filtered out* one line later.

Two viable variants:

- **Option A — strip stale `lines_to_next_cell` only where the metadata
  is provably stale.** Track which source indices survive the
  `is_cell_included` filter; for each survivor whose original
  next-index didn't survive, pop `lines_to_next_cell` from
  `cell["metadata"]`. Conservative: preserves user-authored spacing
  intent (e.g. cell 142's `lines_to_next_cell: 2`) for cells whose
  immediate source neighbor survives.
- **Option B — pop `lines_to_next_cell` from every cell after
  filtering.** Simpler. Loses author spacing intent (the `: 2` on cell
  142 becomes implicit). Probably acceptable for the output of a
  filtered build; would not be acceptable for source files.

Start with Option A. The implementation is ~10 lines and lives entirely
in `_process_notebook_node`. Worth a unit test on a fixture with two
adjacent same-language code cells that survive filtering (metadata
should be preserved) plus a code-markdown-code triplet where the middle
cell is filtered out (metadata should be stripped on the surrounding
cells).

There's no urgency. The consumer-side workaround covers the Phase D
gate; ship the producer fix opportunistically next time we touch
`notebook_processor.py`.

## The consumer-side workaround (shipped)

`scripts/diff_build_outputs.py` — takes two build directories, walks
both trees, and reports byte-identical / identical-after-normalization /
still-differing files. Normalizers:

- **`.ipynb`** — JSON-parse, drop `cell["id"]` (nbformat's random UUIDs;
  unavoidable noise across builds) and `cell["metadata"]["lines_to_next_cell"]`
  (the issue at hand), then re-serialize with stable key ordering.
- **`.py`** — collapse any run of blank lines that immediately precedes
  a `# %%` cell marker to exactly one blank line. This catches the
  downstream side of the same metadata — jupytext writes blank lines
  based on `lines_to_next_cell`, so the `.py` output's blank-line counts
  differ wherever the metadata does. Blank lines inside a cell body
  (e.g. between two top-level `def`s in a code cell) are not touched.

Usage:

```
uv run python scripts/diff_build_outputs.py <dir_a> <dir_b>
uv run python scripts/diff_build_outputs.py <dir_a> <dir_b> --show-diffs
uv run python scripts/diff_build_outputs.py <dir_a> <dir_b> --raw   # debug: skip normalizers
```

Exit codes: `0` if every file matches (raw or normalized), `1` if any
file still differs, `2` on argument errors.

Smoke-tested 2026-05-25 against a simulated bilingual-vs-split build of
`slides_010_prompt_templates.py`: 2 files diverge raw (`.ipynb` +
`.py`), both become identical after normalization.

## Phase D pilot using this script

Per
[PythonCourses `docs/handover-slide-format-redesign-next-steps.md`
§"Recommended pilot path"](../../../../Courses/Own/PythonCourses/docs/handover-slide-format-redesign-next-steps.md),
the Phase D byte-equivalence gate is:

1. Build the bilingual form → `/tmp/build-bilingual/`.
2. `clm slides split deck.py`, swap the bilingual file aside.
3. Build the split form → `/tmp/build-split/`.
4. `uv run python scripts/diff_build_outputs.py /tmp/build-bilingual /tmp/build-split`.
5. Expect **zero** files in the "differ after normalization" bucket.
   Anything in that bucket is a real divergence worth investigating.

## Appendix — raw inter-cell whitespace verification

For the curious, here's the proof that whitespace at the divergent
position is byte-identical between bilingual and split DE. The position
of the `for vague in vague_questions:` code cell that gets
`lines_to_next_cell=1` in split DE but no metadata in bilingual:

```
=== BILINGUAL: 1289-1297 ===
    print(f"VAGE:    {vague}")
    print(f"PRÄZIS: {clarified}")
    print()
                                    ← exactly one blank line
# %% lang="en" slide_id="…cell-115"  ← next cell: EN code
for vague in vague_questions:
    …
```

```
=== SPLIT DE: 700-708 ===
    print(f"VAGE:    {vague}")
    print(f"PRÄZIS: {clarified}")
    print()
                                    ← exactly one blank line (same)
# %% [markdown] lang="de" tags=["slide"] slide_id="task-4-chain-by-hand"  ← next cell: DE markdown
#
# ## Aufgabe 4 -- Von Hand verketten
…
```

Whitespace = identical. Lookahead target = different (EN `for` vs DE
markdown that eventually leads to a `def`). Metadata = different.
