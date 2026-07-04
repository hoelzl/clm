# Voiceover Text Layer

`clm voiceover` manages the **written narration** of a slide deck: voiceover
cells and their companion `voiceover_*.<ext>` files. Since the harvest
cutover the group holds only the text-layer verbs `extract`, `inline`, and
`inline-notes`.

> Harvesting narration **from video recordings** (transcription, alignment,
> backfill, the one-shot pipeline) moved to `clm harvest` — see
> [Harvesting narration from video recordings](harvest.md) and
> `clm info migration` for the rename table.

## Extract voiceover to a companion file

`clm voiceover extract FILE` moves the deck's `voiceover`-tagged cells into a
companion `voiceover_*.<ext>` file, linked via `slide_id`/`for_slide`
metadata (id-less content cells get auto-generated IDs first). On a split
half (`<deck>.de.py` / `<deck>.en.py`) whose twin exists, both companions are
extracted in one atomic op by default; pass `--single` to extract only
`FILE`'s own companion. An existing companion is never overwritten without
`--force`.

```bash
clm voiceover extract slides_intro.py                 # bilingual: single companion
clm voiceover extract slides_intro.de.py              # split half: auto-pairs both companions
clm voiceover extract slides_intro.de.py --layout subdir   # write into voiceover/
clm voiceover extract slides_intro.py --dry-run
```

## Inline voiceover back into the deck

`clm voiceover inline FILE` merges the companion's cells back into the slide
file, restoring each voiceover to its exact original position via its
`vo_anchor`. The companion is deleted only when every cell is placed;
unmatched cells are kept in the companion and the command exits non-zero.

```bash
clm voiceover inline slides_intro.py
clm voiceover inline slides_intro.py --dry-run    # per-cell placement report
```

## Migrate notes out of companions

`clm voiceover inline-notes PATH` moves **speaker-notes** cells from
companions back inline into their decks, leaving the `voiceover` cells in the
companion — use it to convert pre-1.14 companions into pure narration files.
`PATH` may be a single slide file or a directory (a whole course migrates in
one command).

```bash
clm voiceover inline-notes slides/topic/slides_intro.py --dry-run
clm voiceover inline-notes slides            # migrate a whole course
```

See `clm info commands` for the full option tables of all three verbs.

## Companion file placement

As of CLM 1.14, `clm voiceover extract` moves **only `voiceover`-tagged cells**
into the companion; `notes` (speaker-notes) cells stay inline in the deck, so a
`voiceover_*` companion is a pure narration file. Notes still reach the
trainer/recording outputs from their inline position. Pass `--include-notes` to
also extract notes (the pre-1.14 behavior).

Voiceover companion files (`voiceover_*.py`) can live either in a `voiceover/`
subdirectory next to the topic or as siblings of the slide file. As of CLM 1.14
a *new* companion is written into the `voiceover/` subdirectory by default —
**unless** that deck already has a sibling companion, which is kept a sibling so
a deck is never split across layouts. Override the location for new companions
with `--layout subdir|sibling` on `clm voiceover extract` (or
`clm harvest autopilot`), or set a course-wide default via the
`CLM_SIDECAR_LAYOUT` environment variable or the `[tool.clm] sidecar-layout`
setting in `pyproject.toml`. To reorganize existing companions in bulk, use
`clm slides tidy PATH --layout subdir` (or `--layout sibling` to flatten them
back).
