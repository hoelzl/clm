# Harvest curation task

You are curating spoken narration from a recorded video into a slide's
voiceover. The inputs give you the slide's existing voiceover baseline (both
language sides), the transcript passages the aligner assigned to this slide,
and the slide content for context. Produce the slide's new voiceover as an
ordered bullet list in the recorded language.

Rules (these replace the old embedded-model merge — apply them yourself):

1. **Preserve every substantive baseline bullet.** The existing voiceover is
   curated content; never silently drop a baseline point. The only permitted
   baseline edit is rewriting a bullet the transcript *factually
   contradicts* — not a stylistic preference.
2. **Integrate new substantive transcript content** at its natural narrative
   position among the baseline bullets, not appended as an afterthought.
3. **Filter transcript noise aggressively.** Drop greetings and sign-offs,
   self-corrections, remarks about the recording environment or tooling,
   operator asides ("let me scroll down"), live-coding dictation of code the
   slide already shows, and off-topic tangents. Every dropped passage goes
   into the answer's `dropped` list — that is the audit trail.
4. **No hallucination.** Add nothing the transcript or baseline does not
   support.
5. **Bullet form**: one thought per bullet, markdown inline formatting
   allowed within a bullet, no nested block structure. Stay in the recorded
   language (translation is a separate `translate` task — but you MAY
   answer bilingually if you also translate every bullet for the twin side).
6. **`revisited_segments`** are passages spoken when the presenter navigated
   *back* to this slide after having moved past it (aligner backtracking
   groups, in order of return). They often revisit or correct earlier
   statements — weigh them accordingly; a later revisit wins over an earlier
   statement it contradicts.

Answer with the JSON shape given in `answer_schema`, echoing `item`, `kind`,
`baseline_fingerprint`, and `video_fingerprint` from this task document.
Then submit it with `clm harvest accept DECK --answer FILE` (add `--record`
to bank the write into the sync ledger).
