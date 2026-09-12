# C++ IDE Export (#928) — Handover

**Created**: 2026-09-12 | **Updated**: 2026-09-12 (evening) | **Status**: Phase 0
merged (#932), Phase 1 in PR, Phase 2 next
| **Issue**: https://github.com/hoelzl/clm/issues/928 (design + owner decisions in
the 2026-09-12 evaluation comment) | **Predecessor**: #333 (current export)

---

## 1. Feature Overview

**Student-facing C++ export: per-deck IDE projects.** The current `format="code"`
export for `prog_lang="cpp"` (#333) is a compile-gate artifact: every code cell
becomes a `void slide_NN()` function, bare expressions are wrapped in a
`CLM_DISPLAY` shim, and the markdown narrative is dropped. It compiles but does
not teach. #928 turns it into study material a student can open in CLion / VS /
VS Code and *run and repeat* the slide examples from:

- `foo.cpp` — markdown cells as `//` comment blocks, one **section function**
  per slide, a generated `main()` calling sections in order;
- `foo.hpp` — hoisted includes plus `global`-tagged cells, so workshop files are
  standalone;
- `foo_workshop_N.cpp` — one file per workshop range (skeleton in code-along,
  solution in completed);
- labeled display output (`i1 = 10`), a compilable code-along skeleton with
  `// TODO` markers, and per-module CMake projects.

Why it matters: xeus-cpp notebooks are measurably less stable than Python ones,
some students prefer an IDE, and trainers want to mix notebook and IDE work
when re-recording the C++ courses. Same single source, no hand-curated copies.

Related: #921 / #922 (classifier gaps, fixed in Phase 0), #732 (workshop range
detector unification), CppCourses PR #128 (`tools/scan_section_export.py`, the
corpus scan behind every number below).

## 2. Design Decisions

All decisions below were confirmed by the owner on 2026-09-12 after the corpus
evaluation posted on #928. Numbers come from `tools/scan_section_export.py` in
CppCourses (385 decks, one language per stem, checkpoints excluded).

### D1 — Section boundaries: `slide` **and** `subslide`

| Boundary rule | Decks with ≤1 code section | Sections / deck (median / mean) |
|---|---|---|
| `slide` only (issue's recommendation) | 263 / 385 | 1 / 1.6 |
| `slide` + heading-carrying `subslide` | 199 / 385 | 1 / 2.5 |
| **`slide` + `subslide`** (chosen) | 192 / 385 | 2 / 5.6 |

178 decks carry no `slide` tag at all, 111 exactly one. Slide-only grouping is
vacuous for two thirds of the corpus until a retagging pass nobody budgeted.
Code cells carrying `slide`/`subslide` (1,206 in the corpus) open a section
too — their code belongs to the section they open. Cells before the first
boundary form a leading section.

### D2 — Locals by default, **auto-promoted** to namespace scope, `global` as override

The issue proposed "explicit `global` tag needed in one situation". The corpus
says otherwise under D1's boundaries: **238 variables in 90 decks** are
referenced from a *later* section, plus up to 34 (18 decks) referenced from a
function/class definition hoisted out of their own section (a second situation
the issue omitted). Rejected alternatives:

- *tag-only* — ~240 tags across ~90 decks during the re-recording; every miss
  is a compile-gate failure naming the deck.
- *namespace scope by default (status quo)* — zero burden, but
  `int i1{10};` between functions is not the study material we want. This
  remains the **fallback** if auto-promotion misbehaves in practice.

Chosen: the emitter runs a word-boundary name scan (on comment/string-stripped
text) over every `var_decl`; a variable referenced by a later section, or by
any hoisted definition, is emitted at namespace scope in `foo.cpp` at its
source position. `global` (new code-cell tag) forces namespace scope **in the
header**. False positives (name in a string that survived stripping, shadowed
name) cost only locality; false negatives hit the compile gate. Not coupled to
`keep`: variant selection and scope are orthogonal.

### D3 — Labeled output via `CLM_DISPLAY`; `SHOW` deferred

`CLM_DISPLAY(...)` already receives the expression tokens; stringizing
`#__VA_ARGS__` prints `i1 = 10` in the export with zero deck changes. The
macro lives in the vendored support header `clm/display.hpp`
(`src/clm/data/cpp_export/include/`, copied by `cmake_export` like the xcpp
shim) so the deck only carries `#include <clm/display.hpp>` — owner request
after reviewing the first output. The
issue's `SHOW` macro needs a header the *kernel* can see: no hidden-but-executed
cell mechanism exists (`del` cells are dropped before execution; the kernel runs
in a temp dir with only the topic's siblings copied in). That is a notebook-UX
feature and is tracked as #930. Trap to keep: a capture-default lambda is
ill-formed at namespace scope, so a display expression inside a `global` cell
must be rejected (validate) or the wrapper must not be used there.

### D4 — Structured emitter input carrying the *unblanked* source

`_process_code_cell` blanks non-`keep` sources
(`src/clm/workers/notebook/notebook_processor.py`, `cell["source"] = ""`) and
`_strip_internal_cell_metadata` removes `slide_id` before the emitter is called
from `_create_cpp_code_export`. The emitter therefore cannot run the code-along
name scan, cannot place a TODO marker (header vs. section), and cannot name
sections. The new emitter takes a sequence of cell records: `cell_type`,
`source` (as the view wants it), `original_source` (pre-blank), `blanked`,
`tags`, `slide_id`, `heading` (first markdown heading of the section). See
Next Steps for where to capture these.

### D5 — Code-along name scan is deck-global

12 decks have a `keep` cell referencing a name defined in an earlier blanked
cell; only 3 are same-section. The issue's census (174/147/30) matches the scan
under slide+subslide sectioning (199/155/31) — it was computed with subslide
boundaries. A same-section restriction would ship 9 non-compiling decks.

### D6 — Workshop-range definitions live in the workshop file, never the header

Otherwise the Completed workshop file plus `foo.hpp` define them twice. All 121
workshop ranges in the corpus run to EOF today, so nothing after a workshop
references its definitions — but the rule must hold structurally.

### D7 — Function names from `slide_id`, banners from the heading

`slide_id` is present on all but 19 slide-start cells, is language-invariant
(DE and EN exports get identical names), and sanitizes to an identifier
(`brace-initialization` → `brace_initialization`). Headings collide and differ
per language, so they feed only the banner line
(`std::cout << "== Brace initialization ==\n";`). Collisions get a numeric
suffix; a section without slide_id falls back to `section_NN`.

### D8 — What goes where

- `foo.cpp`, in source order: for each section, its namespace-scope items
  (definitions, auto-hoisted categories, auto-promoted variables) followed by
  `void <section>() { ... }` holding statements, locals, and display calls.
  Markdown that opens a section becomes the comment block above the function;
  markdown inside a section becomes a comment at its position in the body.
  Generated `main()` unless the deck defines one (21 decks do).
- `foo.hpp`: hoisted, deduped includes + `global`-tagged cells, source order.
  The `CLM_DISPLAY` helper moves here when the header exists.
- `foo_workshop_N.cpp`: `#include "foo.hpp"`, the range's cells with the same
  section rules, its own `main()` always generated.
- Definitions stay in `foo.cpp` next to their narrative rather than migrating
  to the header; a workshop that needs a lecture definition tags it `global`.

### D9 — Phasing and delivery

Phased PRs, each fresh off `origin/master`, each with a handover update. Phase
0 (classifier fixes) first so the compile gate on the new output is
meaningful. Multi-file output (header + workshop files) is one phase because
both need the same worker output-contract change.

## 3. Phase Breakdown

### Phase 0 — Classifier fixes #921 / #922 [DONE — PR #932, merged 2026-09-12]

Branch `claude/issue-921-922-cpp-classifier`, commit `cdeba445`.
`cpp_code_analysis.py`: `_is_digit_separator` (a `'` between two
alphanumerics of a run starting with a digit is a separator, not a char-literal
quote) used by both `strip_comments_and_strings` and
`mask_comments_and_strings`; `_skip_requires_clause` drops
`requires <constraint>` after a template head before classification.
Acceptance: `TestDigitSeparators`, `TestRequiresClause`,
`TestEmitClassifierRegressions`, two new compile tests; CI green; auto-merge.

### Phase 1 — Section functions in a single file [IN PROGRESS — PR open]

Implemented on `claude/issue-928-phase1-sections` (see Current Status for
what landed and what the corpus run showed). Original scope:

Accomplishes: new emitter entry point taking structured cells (D4); sections
per D1; markdown comments (D8); labeled `CLM_DISPLAY` (D3); auto-promotion +
`global` tag (D2) emitted at namespace scope in `foo.cpp` (header comes in
Phase 3); function names/banners (D7); deck-defined `main` handling. Completed
and Trainer/Recording code outputs only — code-along keeps today's TODO-stub
behaviour until Phase 2.

Files: `src/clm/workers/notebook/cpp_code_emitter.py` (new
`emit_cpp_deck(cells, ...)`; keep `emit_cpp_translation_unit` as a thin
adapter or delete it with its tests), `notebook_processor.py`
(`_create_cpp_code_export` + capture of the structured cells, see Next Steps),
`src/clm/core/tags.py` (`global` in `CODE_CONTENT_TAGS`? No — a new
`SCOPE_TAGS` set added to `EXPECTED_CODE_TAGS`; `global:NAME` needs a prefix
rule in `get_invalid_code_tags`), validator (`global` on markdown → error),
`src/clm/cli/info_topics/slide-format.md` (tag table row + a "C++ code export"
subsection), `src/clm/cli/info_topics/commands.md` (the export is currently
**undocumented** in the info topics — add a section under `clm build` describing
the per-deck layout, `clm: no-compile`, and the generated CMake project),
`changelog.d/928-section-functions.added.md`.

Acceptance: emitter tests for every D-item above; a representative deck
compiles (CI has g++); CppCourses `Cpp/Completed` export of the three M1 decks
named in the issue (`variables_core`, `functions`, `const_constexpr`) reviewed
by the owner as a student would; existing compile gate in CppCourses stays
green.

### Phase 2 — Code-along skeleton [TODO]

Bodies = keep cells in order + `// TODO: <heading of the enclosing section>`
at each blank position; deck-global name scan (D5) comments out a `keep` cell
that references a blank-defined name, with the note "depends on code you'll
type above — uncomment after". Blanked *definitions* leave a TODO at namespace
scope. Files: emitter, `notebook_processor.py` (CodeAlong and Partial specs),
tests, info topic. Acceptance: the 12 dangling decks from the scan produce
compiling code-along output; `TestEmitCodeAlongTodos` rewritten.

### Phase 3 — Multi-file output: header, workshop files, per-module CMake [TODO]

`foo.hpp` (D8) and `foo_workshop_N.cpp` per `find_workshop_ranges` (D6);
worker output contract grows from one file to a file set for the code format;
`cmake_export.py` emits one target per deck plus one per workshop, grouped per
module via `add_subdirectory`; code-along workshop targets become gate-able.
Acceptance: CppCourses compile gate includes workshop targets; a student can
open one module directory as a CMake project.

### Phase 4 — Verification tooling and rollout [TODO]

Opt-in differential check (kernel output vs. compiled-executable output per
deck; kernel side is `tools/execute_deck_kernel.py` in CppCourses); wire
code-along workshop targets into the CppCourses CI gate; `SHOW`-in-notebooks is #930.

## 4. Current Status

- **Done**: design evaluation posted on #928 (2026-09-12); owner decisions
  D1–D3, D9 recorded above; scan script in CppCourses
  `tools/scan_section_export.py` (PR #128); Phase 0 merged (PR #932); `SHOW`
  split off as #930; this handover merged (PR #931).
- **Phase 1 implemented** on `claude/issue-928-phase1-sections` (worktree
  `.claude/worktrees/issue-928-cpp-ide-export`):
  - `cpp_code_emitter.py` rewritten: `CppCell` (attrs, frozen) +
    `emit_cpp_deck(cells, *, blanks_code_cells)`; `emit_cpp_translation_unit`
    is gone. Two passes: classify every cell and decide promotions
    (`_decide_promotions`: later-section reference, namespace-scope-item
    reference, then a same-section transitive closure), then emit per
    section into a namespace-scope stream and a body stream (`_Stream`
    renders items of one cell tight, cells blank-line separated, a comment
    glued to the code after it). Markdown goes to the namespace stream until
    the body has code, then into the body. A section with an empty body
    emits no function (definitions/comments only). Section names:
    `identifier_from_slide_id` + `_section` suffix when the name is one the
    deck defines (code-derived slide_ids make that common: `void include()`
    showed up in `topic_160_functions`), numeric suffix on duplicates,
    `section_NN` fallback. Banner = first markdown heading, else humanized
    slide_id, else the name. `CLM_DISPLAY` prints `label = value`.
  - `notebook_processor.py`: `_process_notebook_node` snapshots
    `(cell_type, tags, slide_id, pre-blank source)` of the included cells
    into `self._cpp_export_cells` before blanking/stripping (only for
    `format == "code"` and `prog_lang == "cpp"`); `_create_cpp_code_export`
    zips it with the processed cells (length-checked) and falls back to the
    bare cells when there is no snapshot (a caller that skips processing).
  - `cpp_code_analysis.py`: `int i2(20);` / `std::string s("hi");` — a
    parenthesized initializer starting with a literal — now classifies as
    `var_decl` instead of `fn_decl` (`_PAREN_INIT_LITERAL_RE`);
    `int i(value);` stays `fn_decl` (most vexing parse, either scope compiles).
  - `tags.py`: `SCOPE_TAGS = {"global"}` joined `EXPECTED_CODE_TAGS`
    (validator and unknown-tag warnings pick it up automatically).
  - Info topics: `commands.md` gained "C++ code export" under `clm build`
    (the export was undocumented there before); `slide-format.md` gained the
    `global` row. Changelog fragment `changelog.d/928-section-functions.added.md`.
- **Corpus run (357 `.en.cpp` decks, scratch script)**: 0 errors; 4,035
  sections, 1,363 functions, 274 of 916 top-level variables promoted, 739
  labeled displays, 2 deck-defined `main`s. The three M1 review decks
  (`variables_core`, `functions`, `const_constexpr`) pass MSVC 2022
  `cl /std:c++20 /Zs` in both Completed and code-along form. **Owner review
  of those three as a student would is the open acceptance item.**
- **Tests**: `test_cpp_code_emitter.py` rewritten for the new API (sections,
  naming, banners, markdown placement, promotion incl. comment/string/partial
  identifier negatives and the blanked-later-cell case, `global`, TODOs,
  main, labeled display, paren-init, #921/#922, compile smoke tests);
  processor tests cover the snapshot path, slide_id-named sections, no
  metadata leak, and the code-along TODO. 240 passed in those two files;
  `tests/slides tests/core tests/workers/notebook tests/cli` 5,945 passed.
- **Open / deferred**: `SHOW` in `global` cells is ill-formed and not yet
  diagnosed by validate; `int i(value);` paren-init stays namespace-scope;
  markdown-only trailing sections read fine but a section whose opener is a
  code cell takes its heading from a *following* markdown heading if any
  (rare, cosmetic).

## 5. Next Steps (Phase 2 — code-along skeleton)

Start on a **fresh branch off `origin/master`** once the Phase 1 PR has
merged (`git fetch origin && git switch -C worktree-issue-928-cpp-ide-export
origin/master && git switch -c claude/issue-928-phase2-code-along`) — never
switch a worktree to literal `master`. Everything Phase 2 needs is already
in the emitter's input: `CppCell.original_source` carries the blanked text.

1. **Blanked definitions.** Today every blanked cell leaves `// TODO` in the
   section body. Classify `original_source` instead: items that would have
   gone to namespace scope (definitions, `global` cells) leave their TODO in
   the namespace stream at the cell's position, statements in the body. One
   TODO per cell, not per item.
2. **TODO text.** `// TODO: <section heading>` — or, when the blanked cell
   defined named entities, `// TODO: define <names>` so the student knows what
   the video types here (names come from the classifier; `global:NAME` is the
   escape hatch for shapes it cannot name — add the prefix rule to
   `get_invalid_code_tags` / the validator then).
3. **Dangling keep cells (D5).** Deck-global scan: collect the names every
   blanked cell defines (all sections); a `keep` cell whose comment-stripped
   text references one is emitted commented out, preceded by
   `// depends on code you'll type above — uncomment after`. 12 corpus decks
   hit this; `tools/scan_section_export.py --mode both` lists the count.
4. **Promotion under blanking.** The scan already reads `pre_blank_source`,
   so a variable a blanked later cell uses is promoted; keep that (test
   `test_reference_in_a_blanked_later_cell_promotes`).
5. **Tests**: rewrite `TestEmitCodeAlongTodos`, add the dangling-keep cases
   and a compile smoke test for a deck with a blanked definition + kept
   caller; processor test with `CodeAlongOutput` and `PartialOutput`.
6. **Docs**: the "Code-along outputs" bullet in `commands.md`'s "C++ code
   export" section; changelog fragment.

Gotchas: `PartialOutput.blanks_code_cells` is `True` while only in-range
cells are blank — the emitter must not treat empty pre-workshop cells as
TODOs (it only does when `original_source` is non-empty or the spec blanks;
keep the `original_source` check first). The MSVC gate in CppCourses runs
MinGW (#922 showed it accepts things g++/MSVC reject); the local VS 2022
`cl /Zs` check used here is the stricter one.

## 6. Key Files & Architecture

| File | Role |
|---|---|
| `src/clm/workers/notebook/cpp_code_analysis.py` | Heuristic top-level item classifier (spans, categories, names). Phase 0 added `_is_digit_separator`, `_skip_requires_clause`. |
| `src/clm/workers/notebook/cpp_code_emitter.py` | Emits the translation unit from cell sources (#333). Phase 1 replaces its entry point with the structured-cell emitter. |
| `src/clm/workers/notebook/notebook_processor.py` | `_create_cpp_code_export` (call site), `_process_notebook_node` / `_process_code_cell` (filtering, blanking, metadata strip). |
| `src/clm/workers/notebook/output_spec.py` | Output kinds; which tags delete/blank cells per view; `find_workshop_ranges` adapter. |
| `src/clm/core/workshop_scope.py` | Canonical workshop range detector (Phase 3). |
| `src/clm/core/tags.py` | Tag registry (`global` lands here). |
| `src/clm/core/cmake_export.py` | Generated `CMakeLists.txt`, `clm: no-compile`, vendored support headers (Phase 3). |
| `src/clm/cli/info_topics/slide-format.md`, `commands.md` | Version-accurate docs downstream agents rely on; the C++ export is not documented there yet. |
| `tests/workers/notebook/test_cpp_code_emitter.py` | Classifier + emitter + compile-smoke tests. |
| CppCourses `tools/scan_section_export.py` | Corpus scan (`--mode slide|heading|both`); rerun after any grouping/promotion rule change. |

Flow: `process_notebook` → `process_notebook_for_spec` →
`_process_notebook_node` (filter, blank, strip) → `create_contents` (same
processor instance; branches on `format == "code" and prog_lang == "cpp"`) →
`_create_cpp_code_export` → emitter → **one string** returned as
`NotebookResult.result: str` (`src/clm/core/messaging/notebook_classes.py`,
a Pydantic message crossing the worker/CLI boundary) → written by the worker
→ `cmake_export` post-build. Phase 3 must extend `NotebookResult` with a
sibling-file map (published path → text) and teach the writer to emit them;
that is the only place the "one string" assumption lives.

## 7. Testing Approach

- Unit: `.venv/Scripts/python.exe -m pytest tests/workers/notebook/test_cpp_code_emitter.py tests/slides/test_validator_code_export.py -n 4`.
- Compile smoke tests run wherever `g++`/`clang++` is on PATH (CI ubuntu; not
  this Windows box). Every emitter feature needs one.
- Corpus: `python tools/scan_section_export.py --mode both` in CppCourses (from
  the clm venv) before and after rule changes.
- End-to-end: build CppCourses with the `Cpp` code output and run its compile
  gate (windows-latest MinGW today — note #922's finding that MinGW accepted
  namespace-scope statements g++/MSVC reject; consider adding a Linux g++ job).
- Still needs tests: everything in Phases 1–4.

## 8. Session Notes

- Owner picked the recommended option on all four questions (boundaries,
  auto-promotion, decoupled `SHOW`, phased PRs) on 2026-09-12.
- Repo rules that bit or nearly bit this session: the worktree guard refuses
  compound git commands with env prefixes or `-C` — run plain `git` commands
  one per call; the pre-push fast suite can exceed 10 minutes on this box;
  never edit files while a push is running; follow-up PRs go on a new branch
  off `origin/master`, not stacked.
- The `.ipynb_checkpoints` directories under `slides/` inflate corpus counts
  (442 → 385 decks); the scan excludes them.
