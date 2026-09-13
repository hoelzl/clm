# C++ IDE Export (#928) — Handover

**Created**: 2026-09-12 | **Updated**: 2026-09-13 | **Status**: Phases 0–4
merged (#932–#937, CppCourses #129); owner-review follow-ups #938 (title
slide) and #939 (naming/HTML) merged, #940 (SHOW macro + `clm slides
cpp-show`) and CppCourses rewrite PR open
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
- `foo_workshop_N.cpp` — one file per workshop block (skeleton in code-along,
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

### D10 — A workshop *block* is the file unit; lecture definitions need `global`

The canonical detector (`find_workshop_ranges`) opens a new range at every
`workshop` tag or `workshop-…` slide_id, so a workshop whose tasks are
`workshop-task-1`, `workshop-task-2`… sub-slides is several ranges. For
blanking that is invisible; for the export it would put each task in its
own program although the tasks build on each other. The Phase 3 corpus
scan: 121 decks with ranges, 20 with more than one, **all 20 a single run
of back-to-back ranges** (no deck has two workshops separated by lecture
content). So the emitter merges adjacent ranges into one block and writes
one file per block; an `end-workshop` closer or lecture cells between two
openers keep them separate.

Cross-file visibility stays as D8 says: lecture definitions live in
`foo.cpp`, and a workshop that needs one gets the cell tagged `global`. The
corpus has 8 decks whose workshop uses an untagged lecture definition
(`array_basics`: `print_array`; `pointers_to_struct`, `reference_args`,
`overloading`: `Point`/`print`/`Point3d`; `lifetime_observer`: `Obs`;
`std_library_overview`: `numbers`; `my_vector_stl`: `MyVector`;
`variables`: `i`) — they compiled as one translation unit before and fail the
Completed gate now until tagged. The export reports them as a build warning
(`cpp_export_workshop_scope`, Completed view, once per deck) so the tagging
pass is mechanical. The alternative — auto-hoisting referenced lecture
definitions into the header, D2-style — was **not** implemented because D8
explicitly keeps definitions next to their narrative; it is a one-function
change in `_DeckEmitter` if the owner prefers zero tags over narrative
locality. `using namespace` directives of the lecture part *are* hoisted into
the header (environment, like includes — `string_views` needed
`std::literals` in its workshop).

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

### Phase 1 — Section functions in a single file [DONE — PR #933 + #934, merged 2026-09-12]

Implemented on `claude/issue-928-phase1-sections`; #934 moved the display
helper into the vendored header `clm/display.hpp`. Original scope:

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

### Phase 2 — Code-along skeleton [DONE — PR #935, merged 2026-09-13]

Branch `claude/issue-928-phase2-code-along`. Planned scope: bodies = keep
cells in order + `// TODO: <heading>` at each blank position; deck-global
name scan (D5) comments out a `keep` cell that references a blank-defined
name behind "depends on code you'll type above — uncomment after"; blanked
*definitions* leave a TODO at namespace scope. What the corpus forced on
top of that (see Current Status): **solution cells the view drops**
(`completed`/`alt`) must count as missing definitions — eight decks pair a
kept `start` stub with a dropped `completed` twin and every kept cell after
depends on the twin — plus latest-definition-wins ordering, namespace
recursion, member access and operator tracking. Acceptance: no code-along
or partial deck fails MSVC `/Zs` that the Completed view passes (met:
0 / 357); `TestEmitCodeAlongTodos` rewritten.

### Phase 3 — Multi-file output: header, workshop files, per-module CMake [DONE — PR #936, merged 2026-09-13]

Branch `claude/issue-928-phase3-multifile`. `foo.hpp` (D8) and
`foo_workshop_N.cpp` per **workshop block** (D6, see D10 below); worker
output contract grew from one file to a file set for the code format
(*companion files*); `cmake_export.py` emits one target per deck plus one per
workshop, grouped per module via `add_subdirectory`; workshop targets are
gate-able. Acceptance: CppCourses compile gate includes workshop targets
(automatic — the gate builds the kind root, which now adds every module);
a student can open one module directory as a CMake project. What the
corpus forced on top (Current Status): merging back-to-back ranges, hoisting
lecture `using` directives into the header, and a build warning naming the
lecture definitions a workshop uses without a `global` tag.

### Phase 4 — Verification tooling and rollout [IN PROGRESS — clm PR + CppCourses PR open]

Branch `claude/issue-928-phase4-verification` (clm) and
`claude/issue-928-global-tags` (CppCourses). Done: the D10 tagging pass
(24 cells in 16 files, both languages), the code-along kind added to the
CppCourses compile gate, the opt-in differential check
(`tools/diff_deck_output.py` + `execute_deck_kernel.py --json`), five
emitter fixes and one cache-key fix the verification surfaced (see Current
Status). `SHOW`-in-notebooks stays #930. Open: the owner review (item 4 of
Next Steps) — not something a session can fake.

## 4. Current Status

- **Done**: design evaluation posted on #928 (2026-09-12); owner decisions
  D1–D3, D9 recorded above; scan script in CppCourses
  `tools/scan_section_export.py` (PR #128, branch
  `claude/scan-section-export` — not merged there yet); Phase 0 merged (PR
  #932); `SHOW` split off as #930; this handover merged (PR #931); Phase 1
  merged (PR #933, header follow-up PR #934).
- **Owner review follow-ups (2026-09-13)**, after the owner reviewed the
  Phase 4 output in an IDE and the HTML build:
  - The kernel image (`1.22.1`, `full`, and the interactive image the
    owner tried) prints **nothing** for a cell not terminated by `;`:
    the new xeus-cpp dropped xeus-cling's value printing, and a bare
    `f()` loses even its `std::cout` output. Decision: decks always
    terminate with `;` and use a display macro; the export's
    `CLM_DISPLAY` becomes the notebook's macro too (header shipped in
    the worker image; `SHOW` alias). Done in PR #940: `SHOW(...)` alias in
    `clm/display.hpp`, `docker/notebook/Dockerfile` copies the header to
    `/opt/conda/include/clm` (probed by mounting it into the `1.22.1`
    image: `SHOW(x);` → `x = 42`, void call runs, unstreamable →
    placeholder), `src/clm/slides/cpp_show.py` + `clm slides cpp-show`
    (lossless `raw_cells` rewrite; classifier `expr_display` or `call_stmt`
    without `;`/`}`; matches anchored at line boundaries — a bare `arg`
    was first found inside `int arg{1};`; multi-line block comments stay
    in front; backtick = prose → reported, not wrapped; `global` cells
    reported, exit 1). CppCourses rewrite PR: 1458 displays in 156 decks;
    left alone: two `!true` shell-escape cells, one prose paragraph in
    `invoice_v6`, the disabled `adventure_v1_editscript` topic (CMake
    text in code cells). With `SHOW` in the decks the export needs no
    `CLM_DISPLAY` wrapping and the differential check compares values.
  - PR #938: the `header*` macros render `# Title` + author for the code
    format (the Python macros never removed the title either — only the
    logo is gated on notebook/HTML).
  - Naming PR (`claude/issue-928-section-naming`): section functions are
    `slide_<fragment>` (`SECTION_PREFIX`); the fragment is the opener's
    new `section_name="…"` attribute, else the folded `slide_id`, else
    the section number. `identifier_from_slide_id` lost its keyword /
    leading-digit special cases (the prefix makes them moot); a deck
    defining a `slide_…` name still gets `_section`. `section_name` is
    snapshotted with `slide_id`, folded the same way, feeds the banner
    when there is no heading, and is stripped from every output
    (`_strip_internal_cell_metadata`). `html_to_markdown` approximates
    the HTML of markdown cells in `_comment_block` and `_first_heading`
    (img/a/h1-6/b/i/tt/code/strike/li/ul/table/br; div/span/p dropped;
    code spans and fences untouched). Owner decisions: names come from
    `slide_id`, and bad ones (`the_type_right_side_must`) are fixed by
    authoring a `section_name`, not by an emitter heuristic; split
    variable groups (`i1b`/`i2b`) are an authoring rule (tag the run
    `global`), explicitly **not** a tool heuristic — do not add one.
- **Phase 4 implemented** (2026-09-13), two PRs:
  - **CppCourses** (`claude/issue-928-global-tags`): `global` on the lecture
    cells the 8 D10 workshops use (`variables`: `int i{1};`; `array_basics`:
    `print_array`; `pointers_to_struct` / `reference_args`: `Point` + `print`;
    `overloading`: `Point` + `Point3d`; `lifetime_observer`: `Obs` **and**
    the `int Obs::next_id{0};` cell — a static member definition the name
    scan cannot see, needed at link time; `std_library_overview`:
    `numbers`; `my_vector_stl`: the `MyVector` template) in `.en` and `.de`;
    `struct Vec` in `more_initialization` closed with `};` (deck defect);
    `code-export-compile.yml` builds `Cpp/Completed` **and**
    `Cpp/Code-Along` kind roots; `tools/execute_deck_kernel.py --json`
    writes a structured transcript; new `tools/diff_deck_output.py`
    (manifest → exported file → CMake target → executable; kernel stdout
    concatenated as one stream, displays spliced as `=> value` lines,
    banners cut out, `CLM_DISPLAY` labels consumed in source order,
    workshop programs appended; `--streams-only`, `--strict`); AGENTS.md
    bullet. The gate had **not run** against Phase 1–3 clm before this
    (last run 2026-09-12 12:38 UTC, PR #933 merged 20:50): merge the
    CppCourses PR **after** the clm PR, or `good_tests` / `observer` fail
    on the stray-`;` defect below.
  - **clm** (`claude/issue-928-phase4-verification`): `_terminate` leaves
    preprocessor lines alone (`#define private public;` put the `;` in
    the macro, `#endif;` is ill-formed — `good_tests`, `program_structure`,
    `observer`); a `constinit` variable is always promoted
    (`_CONSTINIT_RE` in `_decide_promotions`; a local is ill-formed —
    `compile_time`); `new Vec(1)` classifies as an expression instead of a
    variable `Vec` of type `new` (`more_initialization`); `thread_local`
    joined `_SPECIFIERS`. **Cache key**: `NotebookPayload.content_hash`
    covers the output stem for C++ code payloads
    (`_output_stem_key`) — the result cache is keyed by input file, and a
    second spec exporting `functions` as `02 Functions.cpp` replayed the
    cpp-for-programmers text (`#include "07 Functions.hpp"`) and
    companions `07 Functions.hpp` / `07 Functions_workshop_1.cpp` next to
    it; the manifest and CMake then saw no companions at all. Found by the
    differential check, fixed without a schema bump (only C++ code keys
    change). `commands.md` (constinit sentence), `caching.md` (§2 bullet),
    `changelog.d/928-phase4-verification.fixed.md`.
- **Corpus run (Phase 4, 357 `.en.cpp` decks, three views, MSVC `cl /Zs`,
  356 `###` markers per log)**: **0 new failures in any view**. Completed 6
  (Phase 3: 19) — the 13 fixed are the 8 D10 decks plus the 5 emitter-defect
  decks; the 6 left (`*_disabled` ×5, module-root `ws_100_employee`) are in
  no course spec, so `clm: no-compile` was not needed anywhere. Code-along 4
  (9), Partial 6 (14): same out-of-spec set. Scope warnings: only the two
  advisory false positives. **Real gate, locally** (`clm build
  cpp-for-programmers --no-html --no-diagrams`, then `cmake -G Ninja` +
  `cmake --build` under `vcvars64`): Completed-en and Code-Along-en both
  green, 146 targets each (link level, not just `/Zs`).
- **Differential check, real transcript** (Docker
  `mhoelzl/clm-notebook-processor:1.22.1`, four review decks, 104 cells, 0
  kernel errors; scratch spec with `variables_core`, `functions`,
  `const_constexpr`, `array_basics`): `--streams-only` matches 3 of 4. The
  one diff is a **kernel-image finding, not an export defect**: in the
  `1.22.1` and `full` images a bare-expression cell publishes *nothing* —
  no `execute_result`/`display_data`, and not even the `std::cout` output
  of a bare `print_prod(1, 10)` (nbconvert shows the same: `x` and `pp()`
  cells have empty outputs, `std::cout << x << "\n";` streams). So the
  transcript carries no display values, `const_constexpr` cell 20's
  `prod(1, 10) = 3628800` exists only in the program, and every HTML
  built with that image lacks those outputs. `:latest` was not probed
  (not pulled here). Deck fix if intended: `print_prod(1, 10);`.
- **Tests**: `TestEmitClassifierRegressions` (+5: preprocessor lines,
  constinit promoted, blanked constinit TODO at namespace scope, `new`
  display, `thread_local`), a compile smoke test for the three,
  `test_cpp_code_content_hash_depends_on_output_stem` /
  `test_other_outputs_content_hash_ignores_output_name`.
- **Phase 3 implemented** on `claude/issue-928-phase3-multifile` (worktree
  `.claude/worktrees/issue-928-cpp-ide-export`):
  - `src/clm/core/cpp_export_files.py` (new): the naming rule
    (`header_file_name`, `workshop_file_name`, `workshop_ordinal`,
    `companion_output_files`) shared by emitter, manifest and CMake.
  - `cpp_code_emitter.py`: `emit_cpp_deck(cells, *, stem, blanks_code_cells,
    workshop_ranges)` returns `CppDeckExport(main, header, workshops,
    workshop_lecture_uses)`. Sections split at workshop-block boundaries
    and carry a `file` id; `_later_code`/`_namespace_texts` are per file
    (promotion never crosses files); the dangling scan is untouched
    (deck-global). Lecture `global` cells and `using` directives go to
    `header_stream`; the header exists iff the deck has a block or a
    lecture `global` cell, and then carries `#pragma once` + every include
    (banner/display ones too) + that stream. `defines_main` is per file.
    `merge_adjacent_workshop_ranges` (public) folds ranges into blocks;
    `_workshop_lecture_uses` scans pre-blank workshop code for lecture
    names (`_references`, minus names `_declared_locally` — a same-line
    type+name heuristic).
  - `notebook_processor.py`: the snapshot is `_CppSnapshotCell` (with the
    source index) plus `_cpp_export_ranges` computed on the **full** cell
    list (an `end-workshop` closer may sit on a dropped cell); blocks are
    merged, then `_translate_ranges` maps them onto the snapshot. The stem
    is `PurePath(payload.output_file).stem`. `get_companion_outputs()`
    mirrors `get_warnings()`. The scope warning is added for
    `CompletedOutput` only.
  - Worker/host contract: the worker writes companions next to the output
    (LF), `set_job_companion_files` puts their names into the job result
    JSON (`companion_files`) and into the job-cache metadata;
    `NotebookResult.companion_files` (+ `companion_bytes()`, tolerant of
    pre-field pickles) carries them in the result cache;
    `CACHE_HASH_SCHEMA_VERSION` → 5. `sqlite_backend.py`: the job-cache
    probe requires every companion on disk; the jobcache-hit path
    registers them via the new read-only `JobQueue.peek_cache_metadata`;
    the DB replay writes and registers them (`_replay_companion_files`);
    completion registers them from the job result
    (`_job_result_data`, `_register_on_disk_outputs`);
    `_prepare_result_for_cache` reads them back. `_companion_paths` drops
    any name with a path separator.
  - `provenance_manifest.py`: companions found on disk next to a `.cpp`
    code output are enumerated with that output's record (the release
    sync copies by manifest). `cmake_export.py`: `generate_cmake_files`
    returns the kind-root file (toolchain + `add_subdirectory` per module)
    and one standalone project per module (`if(NOT
    CLM_CODE_EXPORT_CONFIGURED)` toolchain block, `../include`); target
    names are assigned project-wide, workshop targets are
    `<deck target>_workshop_N`, `EXCLUDE_FROM_ALL` covers a no-compile
    deck's workshops. The CppCourses gate builds the kind root, so
    workshop targets join it without a workflow change.
  - Docs: `commands.md` C++ section (file set, blocks, CMake layout),
    `slide-format.md` `global` row, `changelog.d/928-multi-file-export.added.md`.
- **Corpus run (357 `.en.cpp` decks, three views, MSVC 2022 `cl /Zs`)**:
  356 emitted per view (`slides_header` fails template expansion in the
  harness only — its `add.h` Jinja include). 120 headers, 121 workshop
  files per view, file sets identical across views. Failing topics
  (Phase 2 baseline in parentheses): Completed 19 (13) — the 8 D10 decks
  are the only new ones, everything else is the kernel-only baseline;
  code-along 9 (8) — new: `pointers_to_struct`, `reference_args`,
  `my_vector_stl` (their *kept* workshop cells use the lecture type; in
  the other D10 decks the using cells are blanked); partial 14 (12) —
  new: those three plus `variables`. Before merging adjacent ranges the
  same run had 15 new Completed failures (`workshop-task-2` files not
  seeing task 1) and `string_views` failed on `"…"sv` until the lecture
  `using namespace std::literals` moved to the header. The scope warning
  lists the 8 decks plus two advisory false positives
  (`templates_and_strategy`: a template parameter named like a lecture
  class; `command`: a member function `Undo`).
- **Tests**: `TestMultiFileExport` (+ compile smoke test building
  `deck.cpp` and a workshop file against the header), processor tests
  (companion naming, full-list ranges, scope warning),
  `tests/infrastructure/backends/test_companion_outputs.py` (all four
  host paths + path-component rejection), worker tests (companions
  written/reported/cached), `NotebookResult` tests, `TestGenerateCmakeFiles`,
  a manifest companion test.
- **Phase 2 implemented** on `claude/issue-928-phase2-code-along` (worktree
  `.claude/worktrees/issue-928-cpp-ide-export`):
  - `cpp_code_emitter.py`: blanked cells are classified from
    `original_source`; the TODO goes where the code would have gone
    (`_todo_target`: namespace scope for a `global` cell or any hoisted
    item / include / promoted variable, else the body) and reads
    `// TODO: define <names>` (classifier names, one marker per cell) or
    `// TODO: <heading>`. `_is_blanked` is exact when a snapshot exists
    (an originally empty cell is never a TODO — the Partial gotcha); the
    `blanks_code_cells` flag only decides for snapshot-less callers.
    `_find_dangling(promoted)` runs after promotion as a deck-order state
    machine, latest definition wins: names from blanked, **excluded** or
    dangling cells are *missing*; names from emitted cells are available
    deck-wide (namespace scope) or per section (local var); a kept cell
    that `_uses` a missing name outside its own definitions, or accesses a
    missing member (`p.f`, `T::f`), is dangling and rendered by
    `_CellWriter` commented out behind `DANGLING_NOTE` (once per stream it
    touches); its own names become missing (transitive closure). Namespace
    blocks are recursed (`_entity_items`); member definitions never
    provide their class; a missing operator overload marks its
    deck-defined operand types missing (`_operator_operands`) — broader
    than needed but the only name-based handle on `a * b`. A dangling
    `main` is commented out and a `main` generated. `_references` now
    skips member accesses (`p.x`, `p->x`); `_uses` additionally accepts
    `ns::x`.
  - `CppCell.excluded`: solution cells the view drops. Never emitted,
    never open a section, but classified for the dangling scan, for
    promotion (`_later_code` / `_namespace_texts` — parity with Completed,
    which contains them) and for section-name reservation.
  - `output_spec.py`: `SOLUTION_ONLY_TAGS = {"alt", "completed"}` (= the
    CodeAlong delete set minus Completed's). `notebook_processor.py`: the
    snapshot is a 5-tuple with an `excluded` flag; it walks `source_cells`
    and keeps included cells plus solution-only code cells of the build's
    language (`_is_solution_only_code_cell`); `_create_cpp_code_export`
    re-zips by consuming a processed cell per non-excluded entry. Dropped
    `start` cells (Partial pre-workshop, Completed) are *not* snapshotted:
    a dropped stub must not make the kept solution's name look missing.
  - Docs: the "Code-along and partial outputs" bullet in `commands.md`;
    `changelog.d/928-code-along-skeleton.added.md`.
- **Corpus run (357 `.en.cpp` decks, all three views, MSVC 2022
  `cl /std:c++20 /Zs`, scratch script)**: 0 emit errors. Completed fails
  13 decks (baseline, untouched by this phase: `slides_header`,
  `program_structure`, `more_initialization`, `compile_time`,
  `good_tests`, the `*_disabled` topics, `observer`, `builder`,
  `ws_100_employee` — kernel-only constructs; candidates for
  `clm: no-compile` or Phase 1 follow-ups). **Code-along: 8 failing, all in
  the baseline; Partial: 12, all in the baseline.** Before the
  excluded-cell handling code-along had 8 *extra* failures, every one a
  `start`/`completed` pair (`member_functions`, `copy_and_call`,
  `unique_ptr`, `destruction`, `slicing`, `names_part2`,
  `class_templates` — a dropped partial specialization — and
  `operator_overloading`: blanked `operator*` + a class inside a
  namespace). Code-along: 1,981 TODOs (506 `define`, 384 at namespace
  scope), 136 dangling cells in 26 decks (D5's scan said 12 — it ignored
  dropped solution cells and transitivity). Partial: 443 TODOs, 32
  dangling cells in 8 decks.
- **Tests**: `TestEmitCodeAlongTodos`, `TestDanglingKeepCells`,
  `TestExcludedCells`, two compile smoke tests (skip without g++ — the
  dangling one was checked with MSVC by hand), processor tests
  (code-along skeleton, solution cells missing, Partial pre-workshop
  `start`, Partial workshop range).
- **Phase 1 (for reference)** landed on `claude/issue-928-phase1-sections`:
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

## 5. Next Steps (Phase 4 close-out)

0. **Display-macro rollout**: merge #940, then the CppCourses rewrite PR
   (its build needs the header in the worker image — until the image is
   rebuilt from #940's Dockerfile, a Docker-mode build of the rewritten
   decks fails on `#include <clm/display.hpp>`; `:latest` is built by
   CI on master). Direct-mode kernels (`clm provision kernel-env`) do not
   get the header — add an include path there if Direct mode is ever used
   for C++. A validator rule for `SHOW` in `global` cells is still open.
1. **Merge order**: clm PR first (emitter + cache fixes), then the
   CppCourses PR — its master push triggers `code-export-compile.yml`,
   which installs clm from git master. Watch that run: it is the first
   gate run on Phase 1–4 output, now with Code-Along included.
2. **Owner review** of `variables_core`, `functions`, `const_constexpr`
   and `array_basics` (workshop deck) as a student would — the open Phase
   1 acceptance item. Ready-made material: a scratch spec with those four
   topics (`course-specs/review-928.xml`, untracked — one section, four
   `<topic>` lines, `<prog-lang>cpp</prog-lang>`), `clm build ... --no-html
   --no-diagrams`, open `.../Slides/Cpp/Completed` (or `Code-Along`) as a
   CMake project. Ask for the review; don't fake it.
3. **Kernel image**: decide what to do about bare-expression cells
   producing no output in the `1.22.1`/`full` images (probe recipe in §7).
   If `:latest` behaves the same, the HTML output of every C++ course is
   missing its displays and #930 (`SHOW`) becomes urgent; if not, pin the
   image the differential check uses. Either way `const_constexpr` cell
   20 should read `print_prod(1, 10);`.
4. **Partial kind**: it compiles corpus-wide outside the out-of-spec set;
   gating it costs one more glob in the workflow if wanted.
5. **`variables` deck structure**: its mini-workshop range runs to EOF
   (no `end-workshop`), so the workshop file carries the rest of the
   lecture; `global` on `int i{1};` makes it compile, an `end-workshop`
   closer after `display-auto-w2` would restore the intended split but
   changes the Partial view — owner call.

Phase 3 leftovers worth a look while there: (a) auto-hoisting referenced
lecture definitions into the header instead of `global` tags (D10 — owner
call); (b) `preproc_other` items (`#define`) stay in the lecture file, so a
workshop using a lecture macro fails like a definition — hoist with the
using-directives if it shows up; (c) the scope warning is advisory:
template parameters and member functions named like a lecture entity
(`Strategy`, `Undo`) produce false positives, a local declared on a
different line than its type is a false negative; (d) Phase 2's leftovers
(13 baseline decks, operator rule breadth, `global:NAME`, two `main`s after
uncommenting) are untouched.

Gotchas carried forward: the emitter decides blanking from the snapshot,
never from `blanks_code_cells`, when a snapshot exists; dropped `start`
cells are never snapshotted; workshop ranges must come from the full cell
list and be merged **before** translation (a range that is empty in one
view must not split a block); the companion file set must be identical
across views (it is a function of the source cells only) or the manifest,
CMake and cache paths disagree per kind; `_companion_paths` is the only
place that validates names coming from the DB — keep it that way.

## 6. Key Files & Architecture

| File | Role |
|---|---|
| `src/clm/workers/notebook/cpp_code_analysis.py` | Heuristic top-level item classifier (spans, categories, names). Phase 0 added `_is_digit_separator`, `_skip_requires_clause`. |
| `src/clm/workers/notebook/cpp_code_emitter.py` | Structured-cell emitter (`CppCell`, `emit_cpp_deck`): sections, promotion, TODO placement, dangling scan (`_find_dangling`), `_CellWriter`. |
| `src/clm/workers/notebook/notebook_processor.py` | `_create_cpp_code_export` (call site), `_process_notebook_node` (snapshot incl. excluded solution cells) / `_process_code_cell` (filtering, blanking, metadata strip). |
| `src/clm/workers/notebook/output_spec.py` | Output kinds; which tags delete/blank cells per view; `SOLUTION_ONLY_TAGS`; `find_workshop_ranges` adapter. |
| `src/clm/core/workshop_scope.py` | Canonical workshop range detector; the emitter merges adjacent ranges into blocks (D10). |
| `src/clm/core/cpp_export_files.py` | File-set naming rule (`<stem>.hpp`, `<stem>_workshop_N.cpp`) and on-disk companion discovery. |
| `src/clm/core/messaging/notebook_classes.py` | `NotebookResult.companion_files`; `CACHE_HASH_SCHEMA_VERSION`. |
| `src/clm/infrastructure/backends/sqlite_backend.py` | Host side of the companion contract: jobcache probe/hit, DB replay, completion registration, result-cache prepare. |
| `src/clm/workers/notebook/notebook_worker.py`, `src/clm/infrastructure/workers/worker_base.py` | Worker side: writes companions, reports `companion_files` in the job result JSON and job-cache metadata. |
| `src/clm/core/provenance_manifest.py` | Enumerates companions next to each `.cpp` code output. |
| `src/clm/core/tags.py` | Tag registry (`global` lands here). |
| `src/clm/core/cmake_export.py` | Generated CMake projects: kind root + one per module, deck and workshop targets, `clm: no-compile`, vendored support headers. |
| `src/clm/cli/info_topics/slide-format.md`, `commands.md` | Version-accurate docs downstream agents rely on; the C++ export is not documented there yet. |
| `tests/workers/notebook/test_cpp_code_emitter.py` | Classifier + emitter + compile-smoke tests. |
| CppCourses `tools/scan_section_export.py` | Corpus scan (`--mode slide|heading|both`); rerun after any grouping/promotion rule change. |
| CppCourses `tools/execute_deck_kernel.py`, `tools/diff_deck_output.py` | Kernel transcript (`--json`) and the opt-in kernel-vs-program differential check (Phase 4). |
| CppCourses `.github/workflows/code-export-compile.yml` | The compile gate: builds every spec, then the `Completed` and `Code-Along` kind roots. |

Flow: `process_notebook` → `process_notebook_for_spec` →
`_process_notebook_node` (filter, blank, strip; snapshot + full-list
workshop ranges) → `create_contents` (same processor instance; branches on
`format == "code" and prog_lang == "cpp"`) → `_create_cpp_code_export` →
emitter → the lecture file is returned as the result string, the header and
workshop files sit in `get_companion_outputs()` → the worker writes all of
them next to each other and names the companions in the job result JSON
and the job-cache metadata → the host registers them (sweep), stores them
in `NotebookResult.companion_files` (result cache) and replays them on a
hit → manifest + `cmake_export` post-build find them on disk by the naming
rule.

## 7. Testing Approach

- Unit: `.venv/Scripts/python.exe -m pytest tests/workers/notebook/test_cpp_code_emitter.py tests/slides/test_validator_code_export.py -n 4`.
- Compile smoke tests run wherever `g++`/`clang++` is on PATH (CI ubuntu; not
  this Windows box). Every emitter feature needs one.
- Corpus: `python tools/scan_section_export.py --mode both` in CppCourses (from
  the clm venv) before and after rule changes.
- End-to-end: build CppCourses with the `Cpp` code output and run its compile
  gate (windows-latest MinGW today — note #922's finding that MinGW accepted
  namespace-scope statements g++/MSVC reject; consider adding a Linux g++ job).
- Corpus compile check used in Phases 2–3 (no `g++` on this box): a
  scratch script emits every `.en.cpp` deck through `NotebookProcessor` for
  the three specs into `D:/tmp/clm-corpus-928-p3/<kind>/<module>__<topic>/`
  (main + companions side by side, one `files.rsp` per topic listing its
  `.cpp` files) and runs one `cl /nologo /std:c++20 /EHsc /utf-8 /Zs /w`
  per topic (with `/I` for the vendored include dir, the output dir, the
  topic and the module dir) from a generated `compile.bat` per kind that
  calls `vcvars64.bat` once and echoes `### <topic>` before each `cl`; a
  report script groups `error C` lines by topic and diffs against the
  Phase 2 logs in `D:/tmp/clm-corpus-928/`. Run the `.bat` files through
  the PowerShell tool — the worktree guard refuses `cmd //c`. Rebuild from
  this description if needed.
- Phase 4 real-gate check (link level): `clm build <spec> -o D:/tmp/... --no-html
  --no-diagrams --workers direct --no-progress` from the CppCourses root with
  the worktree's `clm.exe`, then a `.bat` that calls `vcvars64.bat` and runs
  `cmake -S <kind root> -B <kind root>/.build -G Ninja -DCMAKE_BUILD_TYPE=Release`
  + `cmake --build ... --parallel` per kind root (both on PATH here).
- Kernel transcript: stage the decks under their topic-dir names (the tool
  derives the topic id from the parent dir) plus `execute_deck_kernel.py`
  in `D:/tmp/clm-kernel-928/`, then from PowerShell (MSYS mangles `/work`)
  `docker run --rm -v "D:/tmp/clm-kernel-928:/work"
  mhoelzl/clm-notebook-processor:1.22.1 bash -c "cd /work && python3
  execute_deck_kernel.py --json transcript.json <topic_dir>/<deck>.en.cpp ..."`;
  Docker Desktop may need starting first. A 6-cell probe through
  `jupyter_client` / `nbconvert --execute` shows what the image publishes.
- Phase 4 corpus logs: `D:/tmp/clm-corpus-928-p4/` (Phase 3 baseline in
  `-p3/`), naming PR run in `-p5/`; gate builds in `D:/tmp/clm-gate-928/`.

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
- Phase 2 (2026-09-13): the first corpus run had every extra code-along
  failure caused by dropped `completed` cells, which the emitter never saw
  — D5's "12 decks" was computed on blanked cells only. Snapshot what the
  view *drops* as well as what it blanks. The worktree guard also refuses
  `for` loops, `comm`, and heredoc-in-pipeline compounds — put multi-step
  work in a scratch `.py` and run it with one plain command.
- Phase 3 (2026-09-13): the first corpus run put 7 files out of one workshop
  (`workshop-task-N` sub-slides) — check *how* the canonical detector's
  ranges relate to each other before building on them. The "no longer
  failing" list of a compile diff needs a sanity check: two of the entries
  were a harness artifact (a deck that failed template expansion, a deck
  whose output dir was renamed). A `_workshop_` substring count over deck
  names is wrong when a deck is *called* `..._workshop.cpp`.
- Phase 4 (2026-09-13): the corpus harness (`NotebookProcessor` per deck,
  no DB) cannot see cache defects — the stale-companion bug only showed up
  in a real `clm build` of a second spec on the same DB. Keep one real
  build in every verification round. The Bash tool collapses `\\` in
  heredocs, so a Python patch script with `"\n"` in its anchors silently
  mismatches: write patch scripts with the Write tool, then run them. The
  exported deck names are the spec's display names (`02 Functions.cpp`),
  not the source stems — anything mapping source → output goes through
  the provenance manifest's `topic_id`.
