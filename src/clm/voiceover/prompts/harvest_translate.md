# Harvest translation task

You are translating a slide's curated voiceover to its twin language side.
The inputs give you the source-side voiceover (the recorded language,
already curated) and the target side's existing voiceover, if any.

Rules:

1. **Translate the source bullets faithfully**, one target bullet per source
   bullet, preserving order and inline markdown formatting.
2. **Preserve target bullets that already say the same thing** — prefer
   minimally editing an existing good translation over re-translating from
   scratch; keep established terminology consistent with the target deck.
3. **No content changes in flight**: do not add, drop, or reorder points
   relative to the source side. Curation happened in the `curate` task; this
   task is translation only.
4. Stay idiomatic in the target language; technical terms follow the deck's
   existing conventions.

5. **Multiple narrative cells.** `inputs.baseline` lists every narrative
   cell of the slide (document order) with its `member` handle and both
   language sides. Translate each source cell in its own `updates` entry
   addressed at that `member`; skip cells whose target side is already a
   faithful translation.

Answer with the JSON shape given in `answer_schema`, providing bullets for
the **target** language side only, echoing `item`, `kind`,
`baseline_fingerprints`, and `video_fingerprint` from this task document.
Then submit it with `clm harvest accept DECK --answer FILE [--record]`.
