# CLM `validate_slides` — voiceover_gap false positive on bilingual slides

**Tool:** `clm validate-slides --review` (also surfaces via the
`mcp__clm__validate_slides` MCP tool)
**Check:** `voiceover_gap` (part of the `--review` / review-mode checks)
**Severity:** False positive — 30+ noisy findings per bilingual video file,
which drowns out real issues and erodes trust in the tool.

## Observed behavior

On a standard bilingual video slide file (`slides_NNNv_*.py`) structured
like this:

```python
# %% [markdown] lang="de" tags=["slide"]
# German slide content

# %% [markdown] lang="en" tags=["slide"]
# English slide content

# %% [markdown] lang="de" tags=["voiceover"]
# German voiceover text

# %% [markdown] lang="en" tags=["voiceover"]
# English voiceover text
```

`validate_slides --review` reports a `voiceover_gap` finding on the German
slide cell, with a message along the lines of "markdown cell is not
followed by a voiceover cell".

The German slide cell **is** covered by a voiceover cell — it's just not
*immediately* followed by one, because the matching English slide cell
comes first.

## Root cause (inferred)

The check appears to scan cells linearly and expect the pattern
`[content cell][voiceover cell]`, treating any other cell immediately after
a content cell as a voiceover gap. It doesn't model the fact that
bilingual slide files interleave DE and EN cells, and that the canonical
pattern for one "slide" is actually four cells:

```
[de content] [en content] [de voiceover] [en voiceover]
```

The check never sees a voiceover cell directly after the DE content cell,
so it flags a gap.

## What the check should do

Treat a bilingual "slide unit" as the DE and EN cells with the same tag
plus their DE and EN voiceover counterparts, and check for voiceover
coverage at the unit level, not the cell level.

Concrete proposal — pair up cells before checking:

1. **Group adjacent language cells**. Walk the cell stream and fold every
   `lang="de"` + next `lang="en"` pair (with the same tag set) into a
   single logical unit. Cells without a `lang` attribute form their own
   unit.
2. **Then apply the voiceover_gap check at the unit level**. A content
   unit is "covered" if it is followed by a voiceover unit (DE+EN pair, or
   a single voiceover cell without `lang`) before any further content unit
   starts.
3. **Optionally tolerate a "buffer" of adjacent content cells** that share
   voiceover — e.g. two markdown cells followed by one DE+EN voiceover pair
   should be allowed if the authoring rules permit it. (The current
   authoring rule is "voiceover after each *nontrivial* code cell + after
   each markdown content cell", so adjacent short markdown cells sharing a
   voiceover is sometimes legitimate.)

## Minimal reproduction

Any file in the AZAV ML course works, but `slides_010v_what_are_llms.py`
and `slides_010v_prompt_engineering.py` both produce 30+ voiceover_gap
findings despite having voiceover cells covering essentially every slide.
Compare:

- The human-readable structure: every slide has DE content + EN content +
  DE voiceover + EN voiceover.
- The tool output: most DE content cells are flagged as missing voiceover.

## Why this matters

Voiceover coverage is a review check the course authors genuinely rely on
— it's one of the main ways to catch forgotten voiceover before a video
recording session. When the check is noisy, authors either:

1. Tune it out entirely, missing real gaps when they exist.
2. Add workaround cells (e.g. a single `lang`-less voiceover cell) that
   silence the check but hurt the DE/EN symmetry the rest of the course
   enforces.

Neither is good. Fixing the check restores confidence in the tool and lets
it catch real issues.

## Related notes

- The existing `clm normalize-slides` tool already knows about the
  DE-then-EN cell interleaving convention (it can reorder cells into this
  pattern). The same pairing logic should be reusable for the check.
- If a cell-level view is useful for other checks, it may be worth
  introducing a shared "unit view" helper that `normalize_slides`,
  `validate_slides`, and `get_language_view` can all use — rather than
  each re-implementing the pairing.
- The `suggest_sync` tool is a good place to look: it already reasons
  about DE/EN pairs, though from a diff perspective rather than a cell
  ordering one.

## Quick test for a fix

Run `clm validate-slides --review` on
`slides/module_550_ml_azav/topic_020_what_are_llms/slides_010v_what_are_llms.py`
before and after the fix. Expected: the number of `voiceover_gap` findings
drops from 30+ to 0 (assuming no real gaps; at the time of writing the
file was human-reviewed and no real gaps remain).
