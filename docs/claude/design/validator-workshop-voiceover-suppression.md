# Suppress Voiceover-Gap Findings Inside Workshops

**Status**: Resolved — ready to implement (Q1–Q4 decided 2026-05-14)
**Author**: Claude (Opus 4.7)
**Date**: 2026-05-14
**Scope**: `src/clm/slides/validator.py` (function `_extract_voiceover_gaps`),
new tests in `tests/slides/test_validator.py`.

---

## 1. Problem

The `voiceover` review check in the slide validator currently expects every
slide, subslide, and code cell in a file to be covered by a voiceover cell.
That assumption is wrong for **workshops**.

In CLM, a workshop is a stretch of cells bounded by markdown cells tagged
`workshop` (inclusive, start) and `end-workshop` (exclusive, end) — see
`src/clm/slides/workshop_scope.py`. Workshops are trainer-driven exercise
blocks: the trainer narrates them live during the lecture, so the
*authoring convention* is **not to attach voiceover to cells inside a
workshop**. Only the workshop's opening heading carries a voiceover
(introducing the exercise to the audience watching the recorded video).

Because the current validator does not know about workshop scope, course
authors get a flood of false-positive `voiceover_gap` findings on every
workshop subslide and exercise code cell. This is a different failure
mode from the bilingual false positives already addressed (see
`docs/claude/CLM_VALIDATE_SLIDES_VOICEOVER_GAP_LIMITATION.md`) — the noise
now comes from a structural pattern that *should* be silent, not from a
pairing-logic bug.

## 2. Goals

1. **Suppress `voiceover_gap` entries for cells inside a workshop range**,
   matching the authoring convention that workshops are narrated live.
2. **Preserve `voiceover_gap` reporting for the workshop *entry point***
   (the `workshop`-tagged heading) — so the trainer's intro voiceover for
   the workshop is still gap-checked.
3. **Re-use the existing workshop-scope module** (`clm.slides.workshop_scope`)
   rather than re-implementing the boundary logic.
4. **Leave the rest of the validator alone** — format, pairing, tags, and
   the other review checks (`code_quality`, `completeness`) are unaffected.
5. **Keep behavior unchanged for files with no workshop** — i.e., if no
   markdown cell carries `workshop`, the gap report is byte-for-byte
   identical to today's output.

## 3. Non-Goals

- Adding new findings (e.g., flagging *unexpected* voiceover cells *inside*
  a workshop). Out of scope; the validator should be lenient, not stricter.
- Changing the deterministic checks (`format`, `pairing`, `tags`). The
  `_check_ordering` function uses adjacency rules that already work fine
  inside workshops because voiceover cells are usually absent there.
- Re-thinking the `_extract_completeness` workshop list — that one already
  groups workshop headings into `workshop_exercises` and is unrelated.
- Modeling sub-workshops or nested workshops. The existing
  `workshop_scope` semantics already say "a new `workshop` heading closes
  the previous one"; we inherit that without change.
- Suppressing voiceover gaps in any other structural region (e.g., `del`
  cells, `notes`-only sections). Workshops are the only documented
  authoring convention with "no voiceover by design".

## 4. Background: What Already Exists

### 4.1 Workshop-scope module

`src/clm/slides/workshop_scope.py` exposes three helpers:

- `find_workshop_ranges(cells) -> list[tuple[int, int]]` — returns
  half-open `[start, end)` index ranges, one per workshop in the file.
  The cell at `start` is the markdown cell tagged `workshop`; cells at
  indices `start..end-1` are inside the workshop.
- `is_in_workshop(idx, ranges) -> bool` — membership check.
- `find_workshop_start_index(cells)` — convenience for the first range.

The module's `_CellLike` protocol requires `cell_type: str` and
`tags: Sequence[str]`. The `Cell` dataclass in
`src/clm/notebooks/slide_parser.py` already exposes both via `@property`
(`Cell.cell_type` → `metadata.cell_type`, `Cell.tags` → `metadata.tags`),
so we can pass `cells` directly without an adapter.

### 4.2 Voiceover-gap extractor

`_extract_voiceover_gaps` in `src/clm/slides/validator.py:559` builds two
collections in one pass over `cells`:

1. `content_cells`: the cells that *could* need voiceover (j2 and
   narrative cells are filtered out).
2. `covered`: indices (into `content_cells`) that a later voiceover has
   already matched, using per-language pointers (`last_de`, `last_en`,
   `last_any`).

Then a second pass over `content_cells` emits an entry per uncovered
slide/subslide/code cell.

The current loop tracks the position in `content_cells` only; the index
into the *original* `cells` list is not retained. That's the one piece
we'll need to keep around for the workshop check.

## 5. Proposed Change

### 5.1 Behavior

A content cell at original index `i` is **suppressed from the gap report**
when:

- it is inside a workshop range (`is_in_workshop(i, ranges)` is true), AND
- it is *not* the workshop-entry cell itself (i.e., its own tag set does
  not contain `workshop`).

Equivalently: a workshop's `workshop`-tagged heading still requires
voiceover (it introduces the exercise on the recorded video); everything
else inside `[start+1, end)` is silent.

Bilingual symmetry: because both the DE and EN heading cells carry the
`workshop` tag in a properly normalized bilingual file, *both* are
checked. If a workshop has DE-only or EN-only heading cells, the gap
finding for the missing language remains — which is the right signal,
because the trainer's intro voiceover is in fact missing on one side.

The `covered` tracking is **unchanged**. We do not skip the voiceover
sweep over workshop cells — if a voiceover happens to exist mid-workshop,
let it continue to cover the most recent same-language content pointer as
usual. We only suppress the *reporting* of uncovered workshop cells. Two
reasons:

1. It keeps the pointer state consistent across a workshop boundary, so
   a voiceover that immediately follows `end-workshop` correctly covers
   the post-workshop content cell rather than something inside the
   workshop.
2. It means future voiceover lines added by an author inside a workshop
   silently work without behavior changes elsewhere.

### 5.2 Implementation sketch

Two small edits inside `_extract_voiceover_gaps`:

```python
def _extract_voiceover_gaps(cells: list[Cell], file_path: str) -> list[dict]:
    # NEW: compute workshop ranges once. Indices are into `cells`.
    workshop_ranges = find_workshop_ranges(cells)

    gaps: list[dict] = []
    content_cells: list[Cell] = []
    content_origin: list[int] = []   # NEW: original index per content_cell
    covered: set[int] = set()
    last_de: int | None = None
    last_en: int | None = None
    last_any: int | None = None

    for orig_idx, cell in enumerate(cells):   # CHANGED: enumerate
        meta = cell.metadata
        if meta.is_j2:
            continue
        if meta.is_narrative:
            # ... (unchanged covered/last_* bookkeeping)
            continue
        idx = len(content_cells)
        content_cells.append(cell)
        content_origin.append(orig_idx)       # NEW
        # ... (unchanged last_* pointer updates)

    for idx, cell in enumerate(content_cells):
        if idx in covered:
            continue
        meta = cell.metadata
        if not (meta.is_slide or meta.is_subslide or meta.cell_type == "code"):
            continue
        # NEW: suppress when inside a workshop, except the workshop heading itself
        orig_idx = content_origin[idx]
        if is_in_workshop(orig_idx, workshop_ranges) and "workshop" not in meta.tags:
            continue
        # ... (unchanged entry build + append)
```

Imports add:

```python
from clm.slides.workshop_scope import find_workshop_ranges, is_in_workshop
```

Lines of net change: ~10. No public-API change; no schema change to
`ReviewMaterial.voiceover_gaps` (entries that *are* emitted look exactly
like today's).

### 5.3 Performance

`find_workshop_ranges` is a single O(N) scan over the cell list. We
already make one pass to build `content_cells`; adding `is_in_workshop`
(O(R) per content cell, where R = number of workshops, typically 0–3)
adds a negligible constant factor. No measurable impact even on large
notebooks.

## 6. Open Design Questions

**Resolved 2026-05-14**: Q1 → Option A. Q2 → no. Q3 → workshops are
file-local; missing `end-workshop` is the common case and runs to EOF as
described. Q4 → agreed. Q5 → agreed (CHANGELOG line, no schema bump).
Implementation proceeds under these answers.

### Q1: Should the workshop heading itself require voiceover?

The user's framing was "except *possibly* the first cell". Two options:

- **Option A (proposed)** — workshop heading cells require voiceover; all
  other cells inside the workshop are silent. Rationale: in recorded
  video courses, the trainer narrates the workshop intro on the slide so
  the viewer knows what to do.
- **Option B** — the entire workshop range is voiceover-optional,
  including the heading. Rationale: trainer-led only; everything in a
  workshop is narrated live with no scripted voiceover.

If the team's authoring convention is uniformly "no voiceovers in
workshops at all", Option B becomes a one-line change (drop the
`"workshop" not in meta.tags` clause). We recommend Option A because it
keeps the validator pointing at one real authoring obligation (the
workshop introduction) instead of silencing the whole block.

### Q2: Should we also flag *unexpected* voiceover inside a workshop?

E.g., a voiceover cell on a subslide deep inside an exercise — is that a
finding? Likely not worth surfacing now: it would be a new check
(positive presence), not a gap. We can defer until we see a real case
that motivates it. **Recommend: no, out of scope.**

### Q3: What if a workshop spans multiple files (rare, but possible)?

`find_workshop_ranges` only sees one file at a time. If a workshop is
opened in file A and not closed before file A ends, file A's range
extends to EOF (existing behavior). File B starts with no open workshop,
so its early cells would be checked normally. This matches today's
authoring rule that `workshop`/`end-workshop` are file-local. **Recommend:
no change.**

### Q4: Does this interact with the `voiceover-gap` MCP/CLI surface?

`src/clm/cli/commands/validate_slides.py:174-179` and
`src/clm/mcp/tools.py:354-359` both just pass `voiceover_gaps` through.
Because we suppress entries at extraction time, both surfaces inherit
the fix without changes. **Recommend: no extra wiring.**

### Q5: Backward compatibility for callers reading `voiceover_gaps`?

The schema is unchanged: entries that *are* emitted have the same keys.
Callers that count `len(voiceover_gaps)` will see lower counts on files
with workshops — that is the point. No version bump or migration note
required, but a CHANGELOG line under "Fixed" is appropriate.

## 7. Test Plan

Add a new test class `TestVoiceoverGapsInsideWorkshop` in
`tests/slides/test_validator.py`, alongside `TestVoiceoverGapsExtraction`.

| Case | Expected |
|---|---|
| Workshop heading without voiceover → flagged | 1 gap on heading (DE+EN → 2 gaps) |
| Workshop heading **with** voiceover, subslides without → no gaps | `gaps == []` |
| Subslide inside a workshop, no voiceover → not flagged | `gaps == []` |
| Code cell inside a workshop, no voiceover → not flagged | `gaps == []` |
| Code cell **after** `end-workshop`, no voiceover → flagged | 1 gap |
| Two workshops, second one has no heading voiceover → flagged on heading only | 1 gap (or 2 for DE+EN) |
| File with no workshop at all → identical to today's output | unchanged |
| Workshop-tagged heading has DE voiceover only → 1 gap on EN heading | 1 gap, `lang="en"` |
| Workshop spanning to EOF (no `end-workshop`) suppresses gaps to EOF | only heading checked |

Regression assertion: re-run `test_detects_missing_voiceover` and the
bilingual tests already in `TestVoiceoverGapsExtraction` — they don't
include workshops, so output must be unchanged.

Manual smoke test (matching the original false-positive report): run
`clm validate-slides --review` on an AZAV ML workshop-heavy slide file
and confirm the workshop-internal gap noise drops to zero.

## 8. Risks

- **Mis-detection of workshop boundaries on malformed files.** If
  `workshop`/`end-workshop` tags are inconsistent, `find_workshop_ranges`
  silently falls back to EOF-closing. That is the same behavior the
  worker output filter already relies on, so the validator inherits the
  established interpretation — no new failure modes.
- **Over-suppression on files that *should* have voiceover throughout a
  workshop.** If a team's convention diverges and they do narrate every
  workshop cell on video, those teams will see fewer findings than they
  expect. They can leave voiceover cells in place (no warning emitted),
  and the missing-voiceover signal for *those* cells will simply be
  silent. If this becomes a real complaint we can promote Q1 to a
  configurable mode (`--workshop-voiceover=strict|heading-only|off`),
  but starting unconfigurable keeps the change minimal.

## 9. Rollout

1. Implement the change behind no flag (it's a strict false-positive
   reduction; opt-in is unnecessary).
2. Add the test class from §7. Confirm `pytest tests/slides/` passes.
3. Add a CHANGELOG entry under "Fixed": *Validator no longer reports
   missing voiceover for cells inside `workshop`/`end-workshop` ranges
   (the workshop heading itself is still checked).*
4. No info-topic update needed: `spec-files.md` already documents the
   `workshop`/`end-workshop` tags; `commands.md` already documents
   `validate-slides`. The semantic clarification belongs in the
   CHANGELOG and (optionally) a one-sentence note in
   `docs/user-guide/spec-file-reference.md` next to the workshop tag
   description.
