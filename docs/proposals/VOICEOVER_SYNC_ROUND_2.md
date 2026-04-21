# Proposal: `clm voiceover sync` — Round 2 Follow-ups

**Status:** Draft — for review
**Scope:** `clm voiceover sync` and adjacent commands. Builds on the shipped
work in `docs/proposals/VOICEOVER_SYNC_IMPROVEMENTS.md` (v1.2.1) and
picks up items that proposal explicitly deferred as out of scope.
**Related:** `docs/claude/voiceover-design.md`,
`docs/claude/voiceover-sync-improvements-handover-archive.md`.

Three follow-ups, driven by the same authoring workflow that motivated
round 1:

1. **Glob expansion for part inputs** — usability win for the
   multi-part case.
2. **Cross-language voiceover propagation** — translate merged changes
   from the recorded language into the other language without needing
   a second recording.
3. **Merging into companion voiceover files** — extend merge support
   to the `extract-voiceover` output.

A fourth item — **verbatim + merge semantics** — was considered and
**deferred** (see "Deferred items" below): standalone value is low and
it may be subsumed by future features.

The three items are independent and can ship in any order. A suggested
sequence is given at the end.

---

## Item 1 — Glob expansion for part inputs

### Problem

`sync SLIDES VIDEO...` accepts multiple videos, but the shell is
responsible for expanding the glob. On Windows `cmd.exe` and PowerShell
do not glob-expand unquoted arguments the way POSIX shells do, so a
user who writes

```
clm voiceover sync slides.py "Teil *.mp4"
```

gets a single literal argument `Teil *.mp4`, which fails the
`click.Path(exists=True)` check. Quoting is the obvious workaround but
we can do better — we already know the caller means "every part file
that matches".

### Proposal

When a positional `VIDEO` argument contains a glob metacharacter
(`*`, `?`, `[`), CLM expands it relative to the current working
directory using `pathlib.Path().glob()` and substitutes the matches in
place, sorted with a natural-numeric comparator so `Teil 2.mp4`
precedes `Teil 10.mp4`.

Non-glob arguments continue to be treated as literal paths and receive
the existing `exists=True` check. Mixed invocations work:

```bash
clm voiceover sync slides.py intro.mp4 "Teil *.mp4" outro.mp4
```

### CLI-level changes

- Remove `exists=True` from the `click.argument("videos", ...)` type
  — we re-validate after expansion so the error message can identify
  the specific literal path that does not resolve.
- Add an early expansion pass in `sync()` that turns the tuple of
  strings into a list of `Path`, raising `click.BadParameter` with
  the offending pattern if a glob expands to zero files.
- Preserve user-supplied ordering across literal arguments;
  glob-expanded groups are sorted within themselves.

### Out of scope

- Recursive globs (`**`). Part files live in a single directory per
  recording session.
- `{a,b}`-style brace expansion. Use two literals or two globs.
- Auto-detection of parts from a slide-file name. The caller still
  names the recording directory explicitly.

### Tests

- `test_sync_cli_expands_glob_videos` — one glob argument, three
  matching files, assert natural-numeric order.
- `test_sync_cli_mixes_literal_and_glob` — preserves positional
  ordering across segments.
- `test_sync_cli_glob_no_match` — zero matches raises
  `click.BadParameter` with the pattern in the message.
- `test_sync_cli_natural_sort_parts` — `Teil 2.mp4`, `Teil 10.mp4`,
  `Teil 1.mp4` → `1, 2, 10`.

### Effort estimate

Small. One CLI-level helper (~40 LOC), four tests, no changes to
`transcribe`/`timeline`/`merge`. No design decisions that touch LLM
or pipeline semantics.

---

## Item 2 — Cross-language voiceover propagation

### Problem

The trainer typically records a lecture once, in one language. The
slide file carries both `lang="de"` and `lang="en"` voiceover cells.
Running `sync --lang de slides.py Teil*.mp4` updates only the German
voiceover; the English voiceover drifts whenever the recorded German
voiceover gains real content (improvised additions, corrections,
noise-filter rewrites under invariant 2).

The existing workflow is "re-record in English," which the trainer
does not want to do for every small change. Machine translation of
the *merged* result is a much cheaper operation and can run as part
of the same `sync` invocation.

### Proposal: a new mode `--propagate-to <lang>`

After the normal merge produces new voiceover for the recorded
language, a second LLM pass translates **the set of baseline→merged
changes** into the target language and applies them to the target
language's voiceover cells.

```bash
clm voiceover sync --lang de --propagate-to en \
  slides.py "Teil *.mp4"
```

Semantics per slide:

1. Compute the merge for the recorded language (this is the existing
   Phase 2 behavior, unchanged).
2. If the recorded-language merge produced **any change** to the
   baseline (new bullet, rewrite, or deletion-via-rewrite), invoke
   the propagation LLM call.
3. Propagation receives
   `(source_baseline, source_merged, target_baseline,
     slide_content, source_lang, target_lang)`.
4. Propagation returns the translated target-language voiceover that
   reflects the same changes, preserving target-language bullets that
   were **not** touched by the merge.
5. Write the translated result to the target-language voiceover cell.

Slides where the merge was a no-op (empty transcript, or merge
returned baseline unchanged) skip propagation — no drift, no LLM
call.

### Propagation prompt shape

Language-specific system prompt (symmetric structure; the
`source_lang` → `target_lang` direction is filled in). The prompt:

- States the invariant "preserve target-language content that has no
  counterpart change in the source diff."
- Supplies the source diff as a structured list of `{kind: "added"
  | "rewritten" | "dropped", original?, revised?}` items (derived
  from the structured `MergeResult`).
- Instructs the model to produce the target-language voiceover as
  bulleted markdown, matching the existing target-language style
  (bullet phrasing, tense, idiom).
- Forbids introducing content not present in the source diff or the
  target baseline.

This is a **single LLM call per slide**, not a two-step
translate-then-edit. Batching follows the same pattern as the existing
`merge_batch` — one batched call with JSON keyed by `slide_id`.

### Structured response

```json
{
  "slide_id": "slides_010v/7",
  "translated_bullets": "- ...",
  "corresponded_changes": [
    {
      "source_change": "rewrite: extend returns a new list → extend mutates in place",
      "target_change": "rewrite: extend gibt eine neue Liste zurück → extend verändert die Liste in place"
    }
  ],
  "target_preserved_unchanged": true
}
```

`corresponded_changes` is dry-run output (which source deltas were
applied to the target); `target_preserved_unchanged` is a boolean
sanity check the LLM sets to `false` if it had to rewrite a target
baseline bullet for a reason other than a direct source
counterpart — surfaced as a warning in `--dry-run`.

### CLI interaction

- `--propagate-to` accepts `en` or `de`. Must differ from `--lang`;
  otherwise errors out.
- `--propagate-to` is a no-op when combined with `--overwrite`
  (overwrite already destroys both languages' prior state by the
  user's request). **Decision point:** error, warn, or silently skip?
  Recommend: error — the combination is almost certainly a mistake.
- `--dry-run` emits two unified diffs, one per language, both scoped
  to the voiceover cells that changed.
- `--tag voiceover` / `--tag notes`: propagation reads and writes
  the same tag in the target language as in the source language.

### Trace log and Langfuse

- Propagation LLM calls emit their own trace-log lines with
  `kind: "propagate"` and a pointer to the source-slide trace ID.
- Langfuse spans are tagged `voiceover-sync`, `propagate`, plus
  source and target language.

### Edge cases

- **Target baseline empty.** Insert translated bullets as a fresh
  target-language voiceover cell. Same degraded-merge behavior as
  round 1.
- **Target baseline exists but recorded-language merge is a no-op.**
  Do nothing (invariant: propagate only when there are actual source
  changes to carry over).
- **Slide has no target-language variant at all** (monolingual slide
  file). Skip with an info log. Do not synthesize a new target-language
  slide — that is a structural change the caller should make
  explicitly.
- **Structured source diff is empty but merged bullets differ from
  baseline** (LLM edited stylistically without reporting a rewrite):
  treat as a full-slide change and translate the whole merged block.

### Out of scope

- Propagating to more than one language in one invocation. Current
  courses are de↔en. Extend to N languages if the need appears.
- Cross-language *correctness* auditing ("your German says X, your
  English says Y, they disagree"). Separate feature.
- Propagating from a language that has no baseline in the source
  cells (i.e., using propagate as a pure translator). Use a bulk
  translation tool for that.

### Tests

- `test_propagate_to_translates_added_bullets` — source gained one
  bullet; target gains the translated counterpart; untouched target
  bullets unchanged.
- `test_propagate_to_translates_rewrite` — invariant-2 rewrite in
  source produces corresponding rewrite in target.
- `test_propagate_to_no_op_when_merge_no_op` — no LLM call when
  merge did nothing.
- `test_propagate_to_rejects_same_language` — `--lang de
  --propagate-to de` errors.
- `test_propagate_to_rejects_overwrite` — errors when combined.
- `test_propagate_to_empty_target_baseline` — inserts fresh cell.
- `test_propagate_to_dry_run_emits_two_diffs`.

### Effort estimate

Medium. Adds a new `propagate_batch` function to
`src/clm/voiceover/merge.py` (parallel in shape to `merge_batch`),
new prompt files `prompts/propagate_de_to_en.md` and
`prompts/propagate_en_to_de.md`, CLI option wiring, trace-log
plumbing, and the tests above. No changes to transcription,
transition detection, or alignment.

---

## Item 3 — Merge into companion voiceover files

### Problem

`clm extract-voiceover` moves voiceover cells out of a slide file and
into a `voiceover_*.py` companion, linked by `slide_id` / `for_slide`.
After extraction, running `sync` against the slide file finds no
baseline (the voiceover cells are gone) and inserts fresh voiceover
cells back into the slide file, defeating the extraction.

The current `sync` flow explicitly declares companion-file merging
out of scope (round 1 proposal, §"Interaction with existing flags").
Round 2 closes that gap.

### Proposal

`sync` detects the presence of a companion file and, when present:

1. Reads baseline voiceover from the **companion**, keyed by
   `for_slide` metadata and mapped to the slide file's `slide_id`
   cells.
2. Runs the merge pipeline as today.
3. Writes merged voiceover back **to the companion**, not the slide
   file.
4. Leaves the slide file's structure untouched.

Detection is mechanical: `companion_path(slides_path)` (already in
`src/clm/slides/voiceover_tools.py`) returns a stable derivation of
the companion name; if that file exists, companion mode is active.
A `--companion/--no-companion` flag overrides auto-detection for
edge cases.

### Slide-id alignment

Extraction auto-generates `slide_id` attributes on slide cells that
lack them. Sync's companion mode **requires** that every slide the
merge touches has a stable `slide_id`. Two options:

A. **Error if ids missing.** Tells the user to run
   `clm extract-voiceover` (or the `normalize` command) first.
B. **Auto-generate on the fly.** Mirrors extraction's behavior.

Recommend **A** — companion mode is an authoring mode; silent
generation of ids that then get written back to the slide file is
surprising. The error message includes the exact command to fix it.

### Language handling

Companions carry both `lang="de"` and `lang="en"` voiceover cells,
each tagged `for_slide="<slide_id>"`. Sync reads only the
cells matching `--lang` (same rule as the slide-file case) and writes
back to the same cells. Propagation (Item 2) applies unchanged — it
operates on `(source_cell, target_cell)` pairs regardless of whether
those cells live in the slide file or the companion.

### Dry-run output

Unified diff scoped to the companion file. The slide file itself is
never modified in companion mode, so it appears nowhere in the diff.

### Edge cases

- **Companion file exists but is empty or lacks cells for the merged
  slide ids.** Insert fresh voiceover cells into the companion (same
  behavior as an empty baseline today).
- **Companion file path conflicts with an existing non-voiceover
  file.** `companion_path` is deterministic; if a name collision
  exists, error out rather than overwriting.
- **Mix of inline and companion voiceover.** Uncommon. Recommend
  rule: if a companion exists, all voiceover lives there; any inline
  voiceover cells found in the slide file are a lint-level warning
  (surface in `--dry-run`, do not auto-move).

### Out of scope

- Creating a companion file on the fly when none exists. Use
  `clm extract-voiceover` explicitly.
- Auto-deleting the companion when all cells are removed. Same.

### Tests

- `test_sync_writes_to_companion_when_present`.
- `test_sync_requires_slide_ids_in_companion_mode`.
- `test_sync_dry_run_diff_scoped_to_companion`.
- `test_sync_no_companion_flag_forces_inline_mode`.
- `test_sync_companion_mode_preserves_slide_file` — byte-exact file
  check after merge.
- Propagation + companion interaction (one test covering the
  combined flow).

### Effort estimate

Small to medium. Extract/inline infrastructure already exists; the
changes are in `sync`'s baseline-read and writer dispatch. No new
LLM logic.

---

## Suggested implementation order

1. **Item 1 (glob expansion).** Smallest, usability-visible, no
   design decisions. Ship first.
2. **Item 3 (companion file merge).** Unblocks the authoring
   workflow where voiceover is already extracted. Low design risk
   — infrastructure exists.
3. **Item 2 (cross-language propagation).** Highest value and
   highest effort; land last so the merge path is stable and the
   prompt library has settled.

Each item is independently shippable and reversible. No item
introduces a new hard dependency or breaks existing CLI syntax.

---

## Deferred items

### Verbatim + merge semantics (deferred 2026-04-21)

Today `--mode verbatim` combined with merge errors out, because
verbatim has no polish step and therefore no noise filter; appending
raw transcript onto a baseline would splice greetings, self-
corrections, and code-typing narration into the voiceover.

Options considered during round 2 drafting:

- **A. Keep the error.** Status quo.
- **B. "Append without filter" merge** — raw concat; risky.
- **C. "Filter-only merge"** — stripped-down noise-filter-only LLM
  call, preserves exact wording of kept spans.
- **D. "Verbatim sidecar"** — write verbatim to a separate cell type
  rather than the voiceover cell.

**Decision: ship A (keep the error) and defer the rest.** Standalone
value is low — the pragmatic workaround (`--overwrite` + verbatim, or
`--mode polished` as the default) already covers the main cases —
and a useful verbatim-merge may be subsumed by future features
(e.g., a verbatim sidecar cell type is closer to a cell-vocabulary
change than a sync-mode change). Revisit if real authoring pressure
appears.

---

## Out of scope for this round

Deferred to a future proposal if needed:

- VS Code slide-mode footer OCR for slide-number validation.
- Per-slide confidence reporting and TUI review mode.
- Batch processing all videos for a course in one invocation.
- Incremental updates (re-process only slides whose part changed).
- Alternative ASR backends (OpenAI Whisper API, whisper.cpp).
- Content-based cross-validation between transcript and slide text.
