# Proposal: Expand `clm slides assign-ids` Extraction

**Status:** Design — ready for phased implementation
**Tracking issue:** [hoelzl/clm#89](https://github.com/hoelzl/clm/issues/89) —
*feat(slides): expand assign-ids extraction to clear ~95% of hard refusals
(prose lines + code identifiers + LLM fallback)*

**Scope:** Four additive extensions to the slug-extraction pipeline behind
`clm slides assign-ids` (shipped in PR #79). Three extractor additions and
one LLM-flow change. All work lives in `src/clm/slides/` and
`tests/slides/`. The corpus that motivates the work — and the consumer of
the eventual release — is `slides/module_550_ml_azav/` in
[`hoelzl/PythonCourses`](https://github.com/hoelzl/PythonCourses); see
that repo's `docs/handover-slide-format-redesign-course.md` Phase B for
the rollout side.

**Prerequisite reading:**
- `PythonCourses/docs/handover-slide-format-redesign-clm.md` §2.3 + Phase 2
  (the existing `assign-ids` design — three-category policy, EN-derived
  paired slug, preserve marker, voiceover inheritance). The doc was
  authored against this CLM repo's implementation but lives in the course
  repo as part of the paired slide-format-redesign tracking.
- §2.5 of this proposal contains a correction to a claim in the issue body
  that is *not* a real bug. Read it before touching the `_handle_slide`
  `EXTRACTABLE` branch.

## 1. Feature Overview

**Feature:** Extend the slug-extraction logic behind `clm slides assign-ids`
so that two cell categories that currently hard-refuse start producing
useful proposals, plus close a logic gap that makes `--llm-suggest` a
no-op on the largest single category of refusals.

**Problem the issue surfaces:** Running the documented "bulk-accept"
combination

```bash
clm slides assign-ids --dry-run --llm-suggest --accept-content-derived \
    slides/module_550_ml_azav/
```

against the 109-file AZAV ML corpus yields:

```
109 file(s) visited, 0 modified, 0 assigned, 115 soft refusal(s),
                                              292 hard refusal(s).
```

The 292 hard refusals split cleanly into two patterns:

| Pattern | Count | Root cause in the current code |
|---|---|---|
| Markdown subslide whose only content is a prose intro line (no `##` heading, no `- ` bullet, no `**bold**`, no `<img alt>`) | 109 | `clm.slides.headingless.classify` only matches markdown structural cues. A bare prose line like `# Test with two turns -- does the bot remember?` falls through to `NON_EXTRACTABLE`. |
| Code cell carrying `tags=["slide"]` or `tags=["subslide"]` (imports, assignments, defs, function calls) | 183 | `_classify_for_assignment` returns `"slide"` for any `is_slide_start` cell — markdown *or* code. `_handle_slide` then calls `classify(cell.body)` which scans for markdown patterns only and never finds any in Python source. |

The 115 soft refusals are not independent failures — they're *paired
siblings* of cells in the 292 hard-refusal set. When `_handle_slide`
decides the slug-source cell (typically the EN sibling) cannot produce
a slug, it stores `group_slug[idx] = None`, and the paired DE cell
(which on its own might be extractable) mirrors the refusal as a soft
warning ("sibling cell refused; both cells in the pair need a manual
id"). So 292 hard root causes generate 407 visible warnings.

**Impact of fixing this:** PythonCourses Phase B (slide_id rollout
across ML AZAV, commit `109fc63e` on the
`worktree-sprightly-sprouting-steele` branch of PythonCourses) lists 407 missing-ID
warnings as a follow-up gap; closing that gap unblocks the slide-format
redesign's downstream phases (split-source parity check, coverage
walker) that currently treat every missing `slide_id` as a structural
defect.

**Why algorithmic extraction rather than just LLM:** Most of the 292
hard refusals have an obvious local slug source (the prose line's text,
or the code cell's assignment target / called function / import).
Slug-from-local-content survives content edits the same way
heading-derived slugs do, doesn't need a running Ollama instance, and
doesn't depend on the LLM cache being warmed. The LLM path stays as a
universal fallback for the residual cases.

## 2. Design Decisions

### 2.1 Add extractors, don't reshape the orchestrator

The current `assign_ids_for_text` flow is solid: it handles preserve
markers, paired-DE/EN slug sharing via the `pairs`/`group_slug` cache,
the title-macro special case, narrative inheritance, and idempotency.
Two new extractors slot into `clm.slides.headingless.classify` without
touching `assign_ids.py`'s control flow except for one branch
(NON_EXTRACTABLE → LLM fallback).

**Rationale:** Phase 2 already has 30 passing tests for the
orchestrator's three-category contract. Re-targeting that contract
risks reopening decisions that were settled in PR #79. Reclassifying
cells from `NON_EXTRACTABLE` to `EXTRACTABLE` is a pure addition: cells
that previously succeeded continue to succeed unchanged; cells that
previously hard-refused move into a category that already has
opt-in/refusal semantics.

**Alternative considered & rejected:** Move all extraction
responsibility into `assign_ids.py` itself and demote `headingless.py`
to a flat utility module. Rejected because `headingless.py` is also
consumed by future Phase work (per the slide-format-redesign handover
§2.3 it's the canonical extraction surface) and a clean separation
keeps the validator and coverage walker decoupled from the assignment
algorithm.

### 2.2 First-prose-line extractor (Priority 1)

Add a new branch in `classify()` that fires when no bullet, bold,
numbered, or img-alt extractor matched. It returns the first non-empty
line of the cell after stripping the Jupytext `# ` comment prefix, any
leading markdown noise, and trailing terminal punctuation (`:`, `.`,
`?`, `!`). The line is classified as `EXTRACTABLE` with `source =
"prose"` (or `"first_line"` — pick one and stick with it; the value is
recorded on `AssignedId.source` as `content:<source>` so authors can
later identify which extractor produced a given slug).

**Why this is safe:** The existing `slugify` + length-cap + collision
suffix machinery in `clm.slides.slug` handles long prose lines
(truncated at word boundary) and duplicates (`example`, `example-2`,
...). The pattern is identical to how the bullet extractor handles
"Erste Anfrage" and similar phrases — there's nothing new about
collisions or length, only the input shape.

**Why this slug is good enough:** A slug derived from the cell's own
intro line is strictly more meaningful than a positional fallback
(`slide-xyz-cell-N`) and matches what an author would naturally type.
It anchors on intent (what the cell announces) and survives edits to
the *code* below the intro, which is the common churn surface in
demo-heavy slide decks.

**Why this is conservative:** The first-prose-line extractor runs
*last* among the markdown extractors. Existing matches for headings,
bullets, bold lines, and image-alt text continue to win.

### 2.3 Code-cell identifier extractor (Priority 2)

Add a new module — `clm/slides/code_cell_extract.py` is the cleanest
home — that takes the cell body, parses it via `ast.parse`, and walks
the top-level statements. It returns the first match in this
precedence order:

1. `class Foo(...)` → `class-foo`
2. `def func(...)` → `function-func`
3. Top-level assignment `target = ...` → slug of the target name
4. `import x[, y, ...]` / `from m import x[, y, ...]` → composite slug
   capped at MAX_SLUG_LENGTH (e.g. `import-requests-trafilatura-ftfy`)
5. First call expression `obj.method(...)` → `obj-method` or `method`
   when the callee is a bare name

`assign_ids._handle_slide` should detect `meta.cell_type == "code"` and
route through the code extractor *before* invoking
`headingless.classify`. Mark the result as `EXTRACTABLE` with a
distinct `source = "code"` (or `"code:assign"`, `"code:def"`, etc.) so
the refusal/assignment report can show which strategy produced the
slug.

**Why ast, not regex:** Python source has enough structural variation
(decorators, multi-line function signatures, type hints, async defs,
walrus assignments) that regex shortcuts break on real demo code.
`ast.parse` is reliable; failures fall back to `NON_EXTRACTABLE`
without raising.

**Risk note:** Some cells contain code that doesn't parse as standalone
modules (deliberate `# !pip install ...` shell escapes, half-finished
"replace this with your name" stubs, magic-only cells). `ast.parse`
will `SyntaxError` on those; catch it and return `NON_EXTRACTABLE`.
**Do not** let the syntax error bubble — `assign_ids_for_text` is
called over entire directories and one unparsable cell must not abort
the run.

**Routing nuance:** Code cells *with* a `keep` tag still classify as
`is_slide_start=True` when paired with `slide`/`subslide`. Don't
special-case `keep`; the assignment policy is "any slide/subslide cell
gets an id", and `keep` only affects build output, not metadata.

### 2.4 Sibling-pair asymmetry fix (Priority 3)

When one cell of a DE/EN pair is `EXTRACTABLE` and the other is
`NON_EXTRACTABLE`, currently the entire pair refuses because
`_handle_slide` walks the EN sibling (the slug source) and bails on
NON_EXTRACTABLE. After Priorities 1+2 most pairs will have both halves
extractable, so this matters less — but the residual case still needs
handling.

**Resolution:** If the slug-source cell classifies as
`NON_EXTRACTABLE` but the *other* sibling is `EXTRACTABLE`, fall back
to deriving the slug from the extractable sibling. Update the
`group_slug` cache to the resulting slug so both cells inherit it.

**Why deviate from "EN-derived" here:** The EN-derived rule is a
quality preference, not a correctness invariant. When the EN cell has
literally nothing to slug from, a DE-derived slug (transliterated) is
strictly better than a refusal. The slug is still ASCII (transliteration
handles ö/ä/ü/ß) and uniqueness is enforced by the existing collision
mechanism.

**Implementation locus:** `_handle_slide` in `assign_ids.py` around the
`extraction.category` branch (lines ~452-492). Add a helper that, given
both cells of a group, picks the extractable one as slug source.

### 2.5 LLM fallback on hard refusals (Priority 4)

The issue body asserts there are *two* logic gaps in `--llm-suggest`.
Only one is real:

**Gap that IS real:** Step 8 (NON_EXTRACTABLE) returns a hard refusal
unconditionally. `--llm-suggest` is never consulted. This means the
entire 292-cell hard-refusal set is invisible to the LLM no matter
how the flags are combined.

**Gap that is NOT real (despite the issue body):** The issue claims
"`--llm-suggest` is short-circuited by `--accept-content-derived`" via
an `else if` chain. The current code does *not* have that structure
— `_handle_slide` at lines 457-472 explicitly tries the LLM **first**
on EXTRACTABLE cells and falls back to content-derived only if the
LLM call returned nothing. The `write = True` condition at line 497
covers both paths (`accept_content_derived or source == "llm"`).
Don't "fix" this; it already works. The docstring summary that the
issue quotes (steps 7–8) is from `handover-slide-format-redesign-clm.md`
§Phase 2 and lags the implementation — the doc should be updated, the
code shouldn't.

**The empirical "zero new assignments" observation in the issue**
likely traces to one of two causes:
1. Ollama wasn't actually reachable during the test run, so the CLI
   wrapper (`commands/assign_ids.py` lines 146-153) set `suggester =
   None` and silently downgraded `options.llm_suggest = False`. Verify
   first by re-running with Ollama confirmed up.
2. Every refusal in the corpus is NON_EXTRACTABLE, so the LLM is never
   tried (this is the real gap).

Cause #2 is by far the more likely; the breakdown in the issue (292
hard / 115 paired-soft / 0 standalone-soft) is consistent with no
purely-EXTRACTABLE cells existing in the corpus.

**Implementation:** In the NON_EXTRACTABLE branch of `_handle_slide`,
*before* appending the hard-refusal record, call
`_try_llm_suggestion(slug_source, options, ...)`. If it returns a
title, slug it, run through `resolve_collision`, mark
`source = "llm"`, and follow the same write path as for an
LLM-on-extractable result. If it returns `None`, fall through to the
existing hard refusal. The cache key (`content_hash, prompt_version,
lang`) already covers code-cell content as well as markdown content,
so no cache schema change is needed.

**Acceptance after Priority 4:** with `--llm-suggest
--accept-content-derived` on the corpus, hard refusals trend to zero
(modulo cells where the LLM truly cannot infer a meaningful title,
which is a fail-soft return value, not an error).

### 2.6 Backward compatibility

- Cells that previously produced a slug continue to produce the *same*
  slug — extractor precedence is preserved (heading → bullet →
  numbered → bold → img_alt → prose; code-cell extractor only fires
  when `cell_type == "code"`).
- `--force` semantics are unchanged: it regenerates only when the
  algorithm can produce a proposal, otherwise the existing id is left
  intact (the "baseline rule" from §2.3 of the slide-format-redesign
  handover).
- Preserve marker (`!`) is unchanged.
- Priority 4 only fires when `--llm-suggest` is explicitly passed;
  default behavior on hard refusals (refuse) is preserved.

## 3. Phase Breakdown

### Phase 1: First-prose-line extractor — [TODO]

Add the prose-line branch to `classify()` in `headingless.py`.

**Files to touch:**
- `src/clm/slides/headingless.py` — new private helper
  `_extract_first_prose_line(lines: list[str]) -> str | None`, wired
  into `classify()` after the img_alt branch and before
  `NON_EXTRACTABLE`. Strip the `# ` comment prefix, strip leading
  markdown noise (residual `*`, `_`, backticks), strip trailing
  terminal punctuation. Reject lines that are entirely punctuation
  after cleanup.
- `tests/slides/test_headingless.py` — new `TestProseLineExtraction`
  class covering: short prose line, long prose line (slugify trims),
  trailing colon stripped, German with umlauts (transliterated by
  slugify), purely-punctuation line still refuses.

**Acceptance:**
- `classify()` returns `Extraction(EXTRACTABLE, "Test with two turns",
  source="prose")` for `"#\n# Test with two turns -- does the bot
  remember?"`.
- `classify()` still returns `NON_EXTRACTABLE` for an empty cell, a
  pure `<img>` without alt, and `"#\n#  \n"`.
- All existing 14 tests in `test_headingless.py` pass unchanged.
- A repro of the issue's hard-refusal sample (run via
  `assign_ids_for_text`) confirms the prose extractor fires on the
  cited subslides at `slides_010_chatbot_decorators.py:904`,
  `slides_010_text_cleanup.py:728`, etc.

**Source citation:** Real corpus sample to use in tests is at
`slides/module_550_ml_azav/topic_039_chatbot_decorators_deep_dive/slides_010_chatbot_decorators.py:904`
("Test mit zwei Runden -- erinnert sich der Bot?" /
"Test with two turns -- does the bot remember?") — verified to be a
prose subslide with no bullet/bold/img-alt.

### Phase 2: Code-cell identifier extractor — [TODO]

New module + integration into `_handle_slide`.

**Files to touch:**
- `src/clm/slides/code_cell_extract.py` (new) — `extract_from_code(source: str) -> Extraction | None`.
  AST-walk producing the priority order described in §2.3. Returns
  `None` on `SyntaxError` and on cells whose top-level statements
  contain nothing slug-worthy (e.g., bare expressions like
  `response.choices[0].message.content` — fall through to method-call
  rule 5, which produces something usable). Defer to
  `_extract_first_prose_line` only if the code extractor is later
  wired to inspect comments — but in the first cut, do not look at
  comments, only at AST nodes.
- `src/clm/slides/assign_ids.py` — in `_handle_slide`, after computing
  `extraction = classify(slug_source.body)`, if
  `extraction.category == NON_EXTRACTABLE` *and*
  `slug_source.metadata.cell_type == "code"`, try
  `extract_from_code(slug_source.body)` and treat a non-None return as
  the new `extraction`.
- `tests/slides/test_code_cell_extract.py` (new) — coverage matching
  the five-tier precedence: class def, function def, assignment,
  import (single + comma-separated), method call.
- `tests/slides/test_assign_ids.py` — new `TestCodeCellSlideStart`
  class with an integration test asserting that a `tags=["subslide"]`
  code cell containing `import requests, trafilatura, ftfy` gets an
  id derived from the imports.

**Acceptance:**
- `extract_from_code("import requests\nimport trafilatura\n")` →
  `Extraction(EXTRACTABLE, "import requests trafilatura", source="code:import")`.
- `extract_from_code("class HistoryChatbot(BaseChatbot):\n    pass\n")` →
  `class-historychatbot` or `class-history-chatbot` (depends on slugify
  tokenization — verify against the existing slug test fixtures).
- `extract_from_code("response = client.chat.completions.create(...)")`
  → assignment-target wins → `response`.
- `extract_from_code("# !pip install foo")` (just a magic) → `None`.
- Running `assign_ids_for_text` on a `slides_010_text_cleanup.py`
  excerpt produces a slug for the import block at line 411.

### Phase 3: Sibling-pair asymmetry fix — [TODO]

Make `_handle_slide` tolerate a NON_EXTRACTABLE slug source if the
sibling is EXTRACTABLE.

**Files to touch:**
- `src/clm/slides/assign_ids.py` — locate the
  `extraction.category == NON_EXTRACTABLE` branch in `_handle_slide`
  (~line 474). Before refusing, if the cell is paired (i.e.
  `slug_source_idx != idx`), classify the *other* sibling. If that
  classifies as `HEADED` or `EXTRACTABLE`, slug from it and write the
  paired group; mark `source = "content:sibling-<original-source>"`.
- `tests/slides/test_assign_ids.py` — new test under
  `TestExtractableSlides`: a paired DE markdown subslide with a prose
  line and an EN code subslide with no prose → both get the
  prose-derived slug.

**Acceptance:**
- The DE-prose / EN-code pair scenario produces one slug shared by
  both cells.
- A DE-code / EN-code pair where neither extracts continues to hard
  refuse (no behavior change).

### Phase 4: LLM fallback on hard refusals — [TODO]

Hook `_try_llm_suggestion` into the NON_EXTRACTABLE branch.

**Files to touch:**
- `src/clm/slides/assign_ids.py` — in `_handle_slide`'s
  NON_EXTRACTABLE branch, before recording the hard refusal, call
  `_try_llm_suggestion(slug_source, options, file_str, result)`. If a
  title comes back, slug it, run through `resolve_collision`, set
  `source = "llm"`, follow the normal write path. If it returns
  `None`, fall through to the existing hard-refusal record.
- `tests/slides/test_assign_ids.py` — extend `TestLLMSuggest`:
  - `test_llm_fires_on_hard_refusal`: empty/markdown-only-image cell
    with a `StaticTitleSuggester` configured to return a title →
    assignment with `source == "llm"`.
  - `test_llm_silent_on_hard_refusal_when_flag_off`: same input
    without `--llm-suggest` → hard refusal as today.
  - `test_llm_caches_hard_refusal_results`: re-run uses the cache.

**Acceptance:**
- With `--llm-suggest --accept-content-derived` on the AZAV ML
  corpus and Ollama up, the hard-refusal count drops to near zero
  (residual = cells where the LLM legitimately can't infer anything,
  which produce empty titles → hard refusal).
- Without `--llm-suggest`, behavior is byte-identical to today.

### Phase 5: Corpus rerun + documentation updates — [TODO]

After Phases 1-4 land in CLM:

- Bump the CLM pin in PythonCourses (the worktree branch uses CLM
  `f57993d5`; bump to whatever version ships #89).
- Re-run `clm slides assign-ids --accept-content-derived
  slides/module_550_ml_azav/` (no `--llm-suggest` — algorithmic
  extraction alone should clear ~80%+).
- Optionally run a second pass with `--llm-suggest` to mop up
  residuals; verify Ollama is reachable first.
- Commit the resulting slide_id assignments in one commit per logical
  group (mirror the PythonCourses Phase B style — `109fc63e` is the
  reference).
- Update `docs/handover-slide-format-redesign-clm.md` step 7-8
  pseudocode to reflect the LLM-first-on-EXTRACTABLE structure (the
  stale paraphrase that prompted §2.5 of this doc).

**Acceptance:**
- Final `assign-ids` warning count on `slides/module_550_ml_azav/`
  drops from 407 to <10 (algorithmic only) or 0 (with LLM).
- The Phase B follow-up entry in `handover-slide-format-redesign-course.md`
  can be retired.

## 4. Current Status

**Not started.** Issue filed 2026-05-19, no PR yet. All four
priorities described in the issue body remain TODO.

**Reproduction confirmed in this session:**
- `slides/module_550_ml_azav/topic_039_chatbot_decorators_deep_dive/slides_010_chatbot_decorators.py:904`
  is exactly the prose-subslide pattern from Priority 1.
- `slides/module_550_ml_azav/topic_076_text_processing_azav/slides_010_text_cleanup.py:411`
  is exactly the code-subslide pattern from Priority 2 (`tags=["keep",
  "subslide"]` on a cell containing `import requests / import
  trafilatura / import ftfy / from cleantext import clean`).

**Code review of the current implementation revealed one stale claim
in the issue body:** Priority 4 contains two sub-fixes, but only one
("LLM never fires on hard refusals") is a real gap. The other ("LLM
short-circuited by accept-content-derived") is based on a docstring
paraphrase in `handover-slide-format-redesign-clm.md` that lags the
shipped code. See §2.5 above. Action: implement only the
hard-refusal-fallback half of Priority 4; update the stale handover
prose as part of Phase 5.

**Blockers:** None. PR #79 (Phase 2 baseline) and PR #80 (Phase 3
validator) are merged, so the architectural ground is settled. The
work is purely additive to existing extractors.

**Test state:** Existing 30 `test_assign_ids.py` tests + 14
`test_headingless.py` tests pass on `master` at `933da17`. New tests
described per phase above; no existing tests should require changes.

## 5. Next Steps

Start with **Phase 1** — it's the highest-impact change (~218 of 407
warnings) and the lowest risk (single function in a single module).
A reasonable session-sized increment:

1. Read `src/clm/slides/headingless.py` end-to-end (152 lines).
2. Add `_extract_first_prose_line` and wire it into `classify()`.
3. Add tests to `test_headingless.py`.
4. Run `.venv/Scripts/python.exe -m pytest tests/slides/test_headingless.py
   tests/slides/test_assign_ids.py -q`.
5. Run the AZAV ML repro from the issue to confirm warning count
   dropped. `pip install -e .` this CLM tree into the PythonCourses `.venv`
   (`C:/Users/tc/Programming/Python/Courses/Own/PythonCourses/.venv`) for
   an end-to-end check against `slides/module_550_ml_azav/`.

**Setup needed:**
- Currently on `master` at commit `933da17`. The issue references
  baseline `f57993d5` (CLM 1.5.0 post-PR-#83); both points have the
  same assign-ids surface.
- Branch off `master` per the project's PR convention.

**Gotchas:**
1. **DE/EN paired-slug sharing relies on `pairs.get(idx, idx)` always
   pointing at the EN sibling.** Don't change this — the prose
   extractor must run on the EN cell when paired, otherwise you'd
   slug from DE text (which then transliterates) and break the
   EN-derived contract. The dispatch already calls `classify` on
   `slug_source.body`, so as long as the new prose extractor is *inside*
   `classify`, the EN-source semantics are inherited automatically.
2. **`group_slug` caching short-circuits the second cell of a pair.**
   When testing, exercise both `assign_ids_for_text` on a paired DE/EN
   input *and* on the EN cell alone. The cached path
   (`_maybe_write_cached`) doesn't re-call `classify`, so a bug in
   the new extractor won't surface on the paired-second-cell visit.
3. **The PostToolUse hook runs `clm validate-slides --quick` after
   every slide-file Write/Edit** (this project's CLAUDE.md), so test
   fixtures should live in `tests/` not in `slides/`. Don't write
   test data to `slides/module_*/` accidentally.
4. **Don't run `--llm-suggest` from CI / pre-commit.** It calls
   Ollama; the cache + `is_available` check + fail-soft path are
   meant for interactive use. The new Priority 4 hook on hard
   refusals must continue to be fail-soft: if Ollama is unreachable,
   it must return None silently, same as the existing path on
   EXTRACTABLE cells.
5. **The `cell_text_for_llm` helper assumes markdown** (strips the
   `# ` comment prefix). For code cells fed to the LLM via Priority
   4, the prefix isn't there. Either bypass the prefix-stripping when
   `cell_type == "code"`, or generalize the helper. Test against
   both.

## 6. Key Files & Architecture

**CLM repo (this repo):**

| File | Role after this work |
|---|---|
| `src/clm/slides/headingless.py` | Add prose-line extractor + new `Category` source tag. Currently 152 lines; +30-50 lines expected. |
| `src/clm/slides/code_cell_extract.py` *(new)* | AST-walking extractor for code cells. 80-120 lines expected. |
| `src/clm/slides/assign_ids.py` | `_handle_slide` gains: (a) code-cell routing when `cell_type == "code"` (Phase 2), (b) sibling-asymmetry fallback (Phase 3), (c) LLM call before NON_EXTRACTABLE hard-refusal (Phase 4). 700 lines today; +50-80 lines expected. |
| `src/clm/cli/commands/assign_ids.py` | No changes; flag surface stays identical. |
| `src/clm/slides/slug.py` | No changes; existing `slugify`, `resolve_collision`, length-cap handle all new inputs. |
| `src/clm/slides/pairing.py` | No changes; `build_slide_pairs` semantics unchanged. |
| `tests/slides/test_headingless.py` | Add `TestProseLineExtraction`. |
| `tests/slides/test_code_cell_extract.py` *(new)* | Five-tier precedence + ast.SyntaxError handling. |
| `tests/slides/test_assign_ids.py` | Add `TestCodeCellSlideStart`, `TestSiblingAsymmetry`, `TestLLMSuggestOnHardRefusal`. |

**PythonCourses repo (`C:/Users/tc/Programming/Python/Courses/Own/PythonCourses/`):**

| File | Role |
|---|---|
| `slides/module_550_ml_azav/**/*.py` | Receives the new slide_id assignments once Phase 5 ships. No source changes during Phases 1-4. |
| `docs/handover-slide-format-redesign-clm.md` | Update Phase 2 step 7-8 paraphrase during Phase 5 (see §2.5 above). |
| `docs/handover-slide-format-redesign-course.md` | Phase B's "follow-up: 407 missing-ID warnings" entry retires here when Phase 5 completes; a pointer from Phase B to this proposal lives at the same place. |

**Entry-point map:**

```
clm slides assign-ids <path>
  -> src/clm/cli/commands/assign_ids.py::assign_ids_cmd
    -> src/clm/slides/assign_ids.py::assign_ids_in_{file,directory}
      -> assign_ids_for_text
        -> _split_cells
        -> _classify_for_assignment            # routes cell to slide/narrative/skip
        -> _handle_slide
          -> classify(body)                    # <-- Phase 1 prose extractor lives here
            -> _extract_first_prose_line       # NEW
          -> extract_from_code(body)           # <-- Phase 2 (called from _handle_slide
                                               #     when cell_type == "code")
          -> _try_llm_suggestion (existing on EXTRACTABLE; <-- Phase 4 wires it on NON_EXTRACTABLE too)
        -> _handle_narrative                   # unchanged
```

**Conventions to preserve:**
- Every extractor returns a `clm.slides.headingless.Extraction` with
  category + text + source. Don't invent new return types.
- `source` strings end up in `AssignedId.source` (visible to the
  user in the CLI report); pick names that read well in
  `slides_xyz.py:418 -> slide_id="response" (source=code:assign)`.
- Slugification happens *outside* the extractor, in
  `_proposed_slug_from_extraction`. Extractors return *titles*, not
  slugs.
- `group_slug[idx]` is the synchronization point for DE/EN pair
  sharing. Any new path that mutates `group_slug` must do so
  consistently with both write and refusal branches.

## 7. Testing Approach

**Run order during development (CLM repo):**

```bash
# Fast feedback while editing — under 5 seconds:
.venv/Scripts/python.exe -m pytest tests/slides/test_headingless.py -q
.venv/Scripts/python.exe -m pytest tests/slides/test_code_cell_extract.py -q
.venv/Scripts/python.exe -m pytest tests/slides/test_assign_ids.py -q

# Phase boundary — full slides suite:
.venv/Scripts/python.exe -m pytest tests/slides -q

# Pre-PR — full fast suite:
.venv/Scripts/python.exe -m pytest -q
```

**Integration smoke against real corpus (run from a PythonCourses worktree with this CLM installed in its `.venv`):**

```bash
# Algorithmic-only — should drop hard refusals dramatically after Phases 1-2:
.venv/Scripts/python.exe -m clm slides assign-ids \
    --dry-run --accept-content-derived \
    slides/module_550_ml_azav/

# Full bulk-accept — should drop to ~0 after Phase 4 with Ollama up:
.venv/Scripts/python.exe -m clm slides assign-ids \
    --dry-run --accept-content-derived --llm-suggest \
    slides/module_550_ml_azav/
```

**What to assert per phase:**
- Phase 1: total hard refusals drops by ≥100 (target: 109).
- Phase 2: total hard refusals drops by another ≥150 (target: 183).
- Phase 3: residual soft refusals drop to single digits (the
  cross-category pairs).
- Phase 4: with LLM up, total refusals → 0.

**Tests not to touch:**
- `tests/slides/test_pairing.py` — pairing semantics don't change.
- `tests/slides/test_validator.py` — validator is consumer-side; it
  doesn't care how the slug was derived.
- `tests/slides/test_assign_ids.py::TestHeadedSlides` — heading-first
  precedence is preserved; these must continue to pass byte-identical.

**Manual verification:**
Re-run on a previously-clean file (`slides_015_langsmith_tracing.py`
per the slide-format-redesign handover §Phase 2 acceptance criteria)
and confirm no slide_id changes — i.e., backward compatibility on
already-headed cells.

## 8. Session Notes

- Builds and end-to-end development typically run under the
  PythonCourses `.venv`, with this CLM tree editable-installed via
  `pip install -e .` from the PythonCourses venv. A single CLM-side
  `pytest`-pass immediately reflects in `slides/module_550_ml_azav/`
  runs via that editable install.
- The issue body's quantitative impact estimate (Priority 1 clears
  218 warnings, Priority 2 clears 165, etc.) was computed against the
  current corpus snapshot at PythonCourses commit `109fc63e`. Don't
  rely on these numbers post-#89 land — the corpus will have moved
  on. The shape of the breakdown (Priority 1 ≫ Priority 2 ≫ rest) is
  what to verify.
- The CLM master branch has moved ahead of the commit cited in the
  issue (`f57993d5` → `933da17` as of this writing) but the
  assign-ids surface area is unchanged. Branch from current master.
- The issue's suggested filing strategy ("one issue with all four
  priorities, recommended") matches the way this proposal is
  structured. Splitting into separate PRs is fine — recommended split
  is Phases 1+2 as one PR (pure additions to extractors), Phase 3 as
  a small follow-up, Phase 4 as a third PR (it's the only one with
  semantic changes to `--llm-suggest` behavior). Phase 5 (corpus
  rerun) is PythonCourses-side and happens after the CLM release.
