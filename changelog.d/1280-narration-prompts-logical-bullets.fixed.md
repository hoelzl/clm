- **Narration reaches the model as logical bullets.** The existing notes and
  voiceover that `clm slides polish` (`task` and `autopilot`), `clm voiceover sync`
  and `clm harvest autopilot` hand to the LLM used to be one run-on paragraph per
  cell: every line's comment prefix was stripped, all lines were joined with a space,
  and markdown (backticks, bold) was removed. They are now one line per bullet, child
  bullets indented two spaces under their parent and wrapped continuation lines
  joined, with markdown kept verbatim — the same bullet model the writers use since
  #1162, which re-wrap the answer. Companion baselines get the same form, and a `//`
  (C-family) companion file's baselines are now read at all (it used to be parsed
  with the `#` token and yielded no baseline). The prompt text changes once; there is
  no LLM response cache keyed on it (#1280).
