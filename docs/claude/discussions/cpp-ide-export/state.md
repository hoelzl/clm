---
status: active
owner: maintainers
updated: 2026-09-14
review-by: 2027-03-14
---

# State: C++ IDE export (#928) — Phase 4 and the owner review

Per-deck IDE study material generated from the C++ decks (`format="code"`,
`prog_lang="cpp"`): `<deck>.cpp` with one section function per slide,
`<deck>.hpp`, one `<deck>_workshop_N.cpp` per workshop block, per-module
CMake projects. Task state (decisions D1–D10, phases, landmines, next steps)
lives in `docs/claude/handovers/cpp-ide-export-handover.md`; this file
carries the conversation. Session S1 (2026-09-13) covered Phase 4, the
owner's review of the generated output, and the follow-ups it triggered; the
same session also produced the discussion stack itself (thread
`agent-discussion-continuity`).

## Settled (owner + agent, S1)

- **The kernel prints nothing for a cell not terminated by `;`.** The owner
  confirmed the agent's probe finding in the interactive image and the HTML
  build: xeus-cpp dropped the bare-expression display xeus-cling had, and a
  bare `f()` loses even its `std::cout` output, while `std::cout << x`
  works with or without `;`. Consequence the owner drew: every cell ends
  with `;`, no "semicolon here, not there" rule, and values are shown
  through one macro. Landed as `SHOW(expr);` (alias of `CLM_DISPLAY` in
  `clm/display.hpp`, installed into the worker image, `clm slides cpp-show`
  rewrite, CppCourses #130: 1458 displays in 156 decks). This also closed
  #930's premise.
- **Section function names come from `slide_id`** (D7), so the odd names
  (`the_type_right_side_must`) are code-derived ids from `assign-ids`, not
  emitter output. Owner decision: an optional `section_name="…"` cell
  attribute the emitter prefers, falling back to `slide_id` so no deck has
  to be rewritten; never rename ids (they are sync identity). Landed in
  #939.
- **Split variable groups (`i1b`/`i2b`) are an authoring problem.** The
  owner explicitly rejected a tool heuristic ("these kinds of complex
  mechanisms are what gets forgotten over time and then lead to surprising
  results"): tag the run `global`, or redefine with a new name. No emitter
  rule was added — keep it that way.
- **`slide_` prefix** on every section function so slide functions are
  told apart from the deck's own; a leading underscore is reserved,
  `cell_` would mislead (a section holds several cells). Landed in #939.
- **Plain Markdown title** for the code export instead of the HTML title
  slide (#938). The owner's recollection that the Python templates already
  removed it was only partly right: they gate the logo, and Python code
  exports drop markdown cells entirely.
- **HTML in markdown cells is approximated as Markdown** in the export's
  `//` comments (#939); the corpus vocabulary is small.
- **Verification is real, not proxy**: the compile gate builds Completed
  and Code-Along for all ten courses; the differential check
  (`tools/diff_deck_output.py` in CppCourses) matches the four review decks
  against the kernel transcript with values, workshops included.

## Grounding facts established in S1

- xeus-cpp 0.8 (`1.22.1` and `full` images) crashes on the second
  instantiation of any lambda-based display wrapper once new globals were
  defined in between; a value-argument or overloaded-comma wrapper does not.
  `display.hpp` uses the comma operator now; the probe scripts are in
  `D:/tmp/clm-kernel-928/probe4-6.py` on the maintainer's machine.
- The result cache was keyed by input file only while the C++ export embeds
  its output stem: a second spec exporting a deck under another display name
  replayed the first spec's companions. Fixed in #937 (`content_hash`
  covers the stem for C++ code payloads). The corpus harness (no DB) cannot
  see such defects; one real `clm build` per verification round is needed.
- Exported deck names are the spec's display names, not the source stems;
  the provenance manifest's `topic_id` is the bridge.
- The eight D10 decks needed `global` on exactly the cells the warning
  named plus `int Obs::next_id{0};` (a static member definition the name
  scan cannot report — link-level only).

## Open

- **Owner review as a student** of `variables_core`, `functions`,
  `const_constexpr` and `array_basics` in an IDE — the Phase 1 acceptance
  item is still open. The scratch spec `course-specs/review-928.xml`
  (untracked, CppCourses) builds exactly those four decks.
- Direct-mode kernels never get the header (`clm/display.hpp`); the
  published `latest` image (1.28.0, 2026-09-14) has it.
- The `variables` deck's mini-workshop range runs to EOF, so its workshop
  file carries the rest of the lecture; `global` on `int i{1};` makes it
  compile, an `end-workshop` closer would restore the split but changes the
  Partial view — owner call.
- A validator rule for `SHOW` inside a `global` cell (ill-formed at
  namespace scope) is unimplemented; `cpp-show` only reports such cells.
- Left untouched by the rewrite on purpose: two `!true` shell-escape cells,
  one prose paragraph inside a code cell of `invoice_v6` (deck defect), the
  disabled `adventure_v1_editscript` topic (CMake text in code cells).

## Deferred / revisit conditions

- LLM-assisted naming of sections: the owner floated running an LLM over
  slide ids; with `section_name` the target is an authoring pass, not a
  tool feature. Revisit when the re-recording reaches decks whose ids read
  badly.
- Partial kind in the CppCourses gate: compiles corpus-wide outside the
  out-of-spec set; one more glob if wanted.
- D10 auto-hoisting of lecture definitions into the header instead of
  `global` tags: still the owner's call; the tagging pass made it moot for
  the current corpus.

## Known weak points

- The differential check's loose value comparison (`<unprintable value>`
  matches anything, quotes stripped) is deliberately permissive; `--strict`
  exists but has not been exercised on real decks.
- The corpus harness emits every `.en.cpp` under `slides/`, including
  `_disabled` topics and module-root decks no spec includes; its six
  remaining failures are those.

## Next conversational boundary

The owner's student-view review of the four decks. Everything else the
session touched is landed (#937–#941, CppCourses #129/#130, gate green).
Work done after the last owner exchange in S1: the discussion stack (#941)
and this save.
