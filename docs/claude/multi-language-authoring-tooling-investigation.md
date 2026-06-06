# Extending CLM Authoring Tooling from Python-Only to All Programming Languages

**Investigation report — comment-token / extension decoupling of `clm.slides`, `clm.voiceover`, and the shared percent-format parsers**

---

## 1. Executive summary

CLM's **build pipeline** is already multi-language: it derives comment style, jinja prefix, jupytext format, and template directory from the spec's `<prog-lang>` per topic, so C#/C++/Java/TypeScript/Rust courses build today (verified: `prog_lang_utils.py` carries one config per language; `jinja_prefix_for` returns `"// j2"` for the `//`-family and `"# j2"` for python/rust). The **authoring tooling** built on top of it — split/unify, sync, assign-ids, normalize, validate, voiceover extract/inline/**build-merge**, full-deck translate/bootstrap, coverage, slug — was written and tested **exclusively for Python `.py` files** and hardcodes the Python line-comment token `#` and the `.py` extension throughout. The single dominant failure is structural and silent: the two hand-kept copies of the cell-boundary detector (`slide_parser._is_cell_boundary` at `slide_parser.py:326-328` and `raw_cells.is_cell_boundary` at `raw_cells.py:27-29`) only recognize `# %%` / `# j2 ` / `# {{ `, so a `// %%` C#/C++ file parses as **zero cells** — the whole file collapses into the preamble and every downstream tool no-ops **without raising an error**, the most dangerous possible failure mode.

The fix is a small abstraction — a single "line-comment token" derived from the file extension — threaded through one parser foundation, after which the higher subsystems mostly fall in line. **However, the parser abstraction is necessary but not sufficient on the real corpus.** Every production C#/C++ deck wraps its `// j2 … import header` / `// {{ header(...) }}` lines as *body lines inside* a `// %% [markdown] tags=["slide"]` cell, whereas the Python convention (and `split.py`) treat the `# j2` / `# {{ }}` lines as *standalone cells with no `%%` wrapper* — verified directly against both corpora (`§9.0`). A naive "make `// j2` / `// {{` boundaries" token fix would *shatter* the real header cell into three cells, including an orphan (sometimes `lang=`-tagged) empty `%% [markdown]` wrapper with no Python analog. **This structural divergence is the single largest soundness gap and must be resolved before, or together with, the token work.**

**Headline effort: ~3-4 focused weeks of engineering** (revised up from the draft's ~2-3 weeks to absorb the j2-in-`%%`-cell structural decision, the build-merge parse-site fix, and the `translate_deck` engine), front-loaded into a single parser-foundation phase plus the structural decision, plus one **build-side prerequisite that is independent of all the Python code** — adding `header_de`/`header_en` macros to the four non-Python templates, without which a split C#/C++ deck cannot build.

---

## 2. The root abstraction

### The two candidate strategies

- **(A) Derive the comment token from the file extension** via a resolver in `prog_lang_utils.py`. Works at any site that owns a `Path`.
- **(B) Thread an explicit `prog_lang` / comment-token parameter** through the call sites. Works at the pure-string core functions that only receive `text: str`.

### Recommendation: a hybrid — **A at the file-reading seams, B through the pure-string core** — anchored on a single new resolver, plus a structural-parsing decision (§2.5) the token swap cannot make on its own.

Neither strategy alone is sufficient, and every investigator converged on the same split independently:

- The **pure-string core** (`parse_cells`, `parse_cell_header`, `split_cells`, `is_cell_boundary`, `split_text`, `unify_texts`, the headingless regexes, the body strippers) receives **text, not a Path** — it physically cannot derive a token from an extension, so it must take an explicit `comment_token` parameter (**Strategy B**), defaulting to `"#"` for backward compatibility.
- The **file-reading boundaries** (`parse_slides`, `split_in_file`, `validate_file`, `normalize_file`, every CLI/MCP entrypoint, every `path.read_text()` call site) own a `Path`, so they should **resolve the token from the extension** (**Strategy A**) and thread it inward.

This keeps the resolution in exactly one place per call chain and keeps the core unit-testable in isolation.

### The canonical resolver

Half the chain already exists in `path_utils.py` and is the working pattern the build side uses (`notebook_file.py:376`):

```
EXTENSION_TO_PROG_LANG   (path_utils.py:145)   # ".cs" -> "csharp", ".rs" -> "rust", ...
path_to_prog_lang(path)  (path_utils.py:487)
extension_to_prog_lang   (path_utils.py:491)
SUPPORTED_PROG_LANG_EXTENSIONS (path_utils.py:131)  # .c/.cpp/.cs/.java/.md/.py/.rs/.rust/.ts
```

The **missing primitive** is the prog_lang → bare-comment-token half. Today `jinja_prefix_for(prog_lang)` (`prog_lang_utils.py:140`) returns `"// j2"` / `"# j2"` but **no function returns the bare `#` / `//`** (verified: no `comment_token_for`, no reverse map in `prog_lang_utils.py`). Add it as the single source of truth, keyed on `prog_lang` (NOT extension family), so Rust groups with Python:

```python
# prog_lang_utils.py — new, the single source of truth
def line_comment_for(prog_lang: str) -> str:
    # "#" for python/rust, "//" for cpp/csharp/java/typescript
    return jinja_prefix_for(prog_lang).removesuffix("j2").strip()  # or a dedicated field

def boundary_prefixes(prog_lang: str) -> tuple[str, str, str]:
    tok = line_comment_for(prog_lang)
    return (f"{tok} %%", f"{tok} j2 ", f"{tok} {{{{ ")   # note brace-escaping
```

**Why keyed on `prog_lang`, not extension family:** Rust uses `#` (its `jinja_prefix` is `"# j2"`, verified `prog_lang_utils.py:73`) but extension `.rs`; the `//`-family is `cpp/csharp/java/typescript`. So the token is **binary** — `#` for `{python, rust}`, `//` for `{cpp, csharp, java, typescript}` — but it must be decided from the resolved language, never from a naive "is the extension `.py`?" test.

**Resolver crash on `.c` (gate it).** `EXTENSION_TO_PROG_LANG[".c"] -> "c"` (`path_utils.py:146`) and `.c` is in `SUPPORTED_PROG_LANG_EXTENSIONS` (`path_utils.py:133`), so `is_slides_file` accepts a `.c` path — but `config.prog_lang` (`prog_lang_utils.py:113-120`) has **no `"c"` key**. Therefore `line_comment_for("c")` → `jinja_prefix_for("c")` raises `ValueError`. Any authoring resolver must either add a `"c"` config entry or **exclude `.c` from the authoring extension set** (recommended — scope the delivery to `.py/.cs/.cpp/.java/.ts`; see §9).

**Eliminate the drift risk at the same time.** `_is_cell_boundary` (`slide_parser.py:326-328`) and `is_cell_boundary` (`raw_cells.py:27-29`) are **two hand-maintained byte-identical copies today** (verified — they are character-for-character the same three `startswith` checks). Adding a token parameter to two copies doubles the drift surface. **Collapse both onto the single `boundary_prefixes()` helper** (or a new `clm.notebooks.percent_format` module both import) so the predicate can never diverge.

### 2.5. The structural divergence the token swap cannot fix (real-corpus blocker)

**Verified directly against both production corpora:**

- **Python** (`PythonCourses/.../slides_welcome_to_clean_code.py:1-2`): the header is two **standalone cells** at the top of the file —
  ```
  # j2 from 'macros.j2' import header
  # {{ header("Herzlich Willkommen", "Welcome") }}
  ```
  with **no `# %%` wrapper**. They become their own cells precisely because `_is_cell_boundary` treats `# j2 ` and `# {{ ` as boundary-starting lines.

- **C#** (`CSharpCourses/.../slides_xunit_fixtures*.cs:2-4`, representative of every production C#/C++ deck inspected): the same content is **body lines inside one `%% [markdown]` cell** —
  ```
  // %% [markdown] lang="de" tags=["slide"]
  // j2 from 'macros.j2' import header
  // {{ header("xUnit Fixtures", "xUnit Fixtures") }}
  ```

So if `boundary_prefixes()` makes `// j2 ` and `// {{ ` boundary lines (mirroring the Python predicate), the **real C# header cell shatters into three cells**: an orphan empty `// %% [markdown] lang="de" tags=["slide"]` wrapper (note it is even `lang="de"`-tagged in some decks, e.g. CppCourses `welcome_brief.cpp:2`), a `// j2` cell, and a `// {{ header }}` cell. `split.py`'s `_is_bilingual_header_import_cell`/`_is_bilingual_header_cell` inspect `cell.lines[0]` of a **standalone** cell, so they no longer see the import/header as standalone — the orphan `%%` wrapper has no Python analog and the round-trip/unify path never anticipated it.

**This is why the draft's S-2 claim that the header rewrite "works once the parser sets `is_j2` correctly" is false on the real corpus.** The regex-level statement (`HEADER_MACRO_RE`/`is_title_macro_cell` are comment-prefix-agnostic, they match inside `{{ }}`) is true *in isolation*, but it is unreachable because the cell structure differs from the fixtures.

**Decision required (Phase 1, before committing to token-only):** either
- **(a)** re-author the ~850+ real decks to drop the `%%` wrapper around the j2 lines (large authoring churn, brings them to the Python shape), **or**
- **(b)** teach the parser that `j2` / `{{` lines appearing *inside an already-open `%%` cell* are **body lines, not boundaries** (a deeper parser change than a token swap — and one that also alters Python parsing, where today the bare leading j2 lines are standalone). Option (b) is the smaller-blast-radius path for the corpus but must be validated to not regress Python decks (whose j2 lines appear *before* any `%%` cell opens — so "inside an open cell" is the discriminator that keeps Python unchanged).

Recommend prototyping (b) against both real decks in Phase 1 and choosing before any split/unify work begins.

### The round-trip / byte-identical invariant (`raw_cells.py`)

`raw_cells.reconstruct` and the `RawCell` dataclass operate on **verbatim line lists** with no knowledge of the comment token — the invariant `text == reconstruct(*split_cells(text))` holds for any language automatically. **Only the boundary-detection front door (`is_cell_boundary`) needs the token.** This means the lossless layer that `assign_ids`/`normalizer`/`split`/`sync_writeback`/`merge_voiceover_text` depend on stays correct by construction; the danger is solely that *zero or wrong-count boundaries are detected*, which produces a silent no-op (round-trip still trivially holds because the whole file round-trips as preamble) — or, with the structural divergence of §2.5, a silent *over*-split. The fix is purely additive and cannot break existing Python round-trips when the default token stays `"#"`.

**One pre-existing bug to decide on (see §9):** the body strippers use `line.lstrip("# ")` (`slide_parser.py:336,357,368`), which is **character-class** stripping, not prefix stripping — it over-strips even for Python (`"## H2"` → `"H2"`, verified). Generalizing to a token is the moment to decide whether to fix this to a single-prefix removal or preserve it bug-for-bug for golden-file stability.

---

## 3. Coupling inventory by subsystem

Deduplicated across all 8 investigators and both adversarial critiques. **The parser foundation (P-1, P-2, P-3, P-4) is cited by every subsystem** — it is counted once here and is the critical path. Severity: **blocker** = silent data loss / corruption / total no-op; **major** = degraded or wrong-but-recoverable; **minor** = cosmetic/docs.

### 3a. Parser foundation (shared substrate — fix once, unblocks everything)

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **P-0** | (real-corpus structure, §2.5) | Python j2 lines are standalone cells; real C#/C++ j2 lines are **body lines inside a `%%` cell** | Decide re-author vs. body-aware-j2-in-open-cell parser; **NOT a token swap** | **blocker** |
| **P-1** | `notebooks/slide_parser.py:326-328` `_is_cell_boundary` | `# %%`/`# j2 `/`# {{ ` hardcoded | B: token param from `boundary_prefixes()` | **blocker** |
| **P-2** | `slides/raw_cells.py:27-29` `is_cell_boundary` (+`split_cells:53-92`) | 2nd identical copy of P-1 (lossless layer) | B: same token param; **collapse onto shared helper** | **blocker** |
| **P-3** | `notebooks/slide_parser.py:142` `parse_cell_header` `is_j2` | `# j2 `/`# {{ ` → mis-classifies `// {{ header }}` as non-j2 | B: token param. Metadata regexes 153-168 need **no change** | **blocker** |
| **P-4** | `notebooks/slide_parser.py:336,357,368` `_strip_markdown`/`_strip_code_comments`/`_extract_title` | `lstrip("# ")` no-ops on `// `; leaves `//` in voiceover/OCR/title text (and over-strips `"## H2"` even for Python) | B: token-aware prefix strip (also fixes pre-existing over-strip) | **blocker** |
| **P-5** | `notebooks/slide_parser.py:224-244` `parse_slides` | Reads `path.read_text()`, never inspects `path.suffix` | **A here**: derive token from suffix, bridge into B core | **blocker** |
| **P-6** | `workers/notebook/utils/prog_lang_utils.py:140` | No bare-token accessor, no `boundary_prefixes` helper; `config.prog_lang` has no `"c"` key | **Add `line_comment_for` + `boundary_prefixes`** (enabling change); gate `.c` out | **major** |

### 3b. Write-side of the format core

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **W-1** | `notebooks/slide_writer.py:33-44` `format_narrative_cell` (+`update_narrative:74`) | Emits `# %%` headers + `#`-prefixed bodies; `parse_cells` finds nothing | A/B: token into `format_narrative_cell`/`update_narrative` (caller has Path) | **blocker** |
| **W-2** | `slides/voiceover_tools.py:1344-1356` `_format_companion_cell_body` | `["#"]` / `f"# {…}"` body prefix | B: token from companion extension | **major** |
| **W-3** | `slides/voiceover_tools.py:1397` `render_companion_update` new-cell header | `# %%` header → unparseable, new VO silently lost | A/B: build header from token | **major** |

### 3c. Split / unify

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **S-1** | `slides/split.py:391` `_split_target`, `:730` `_strip_lang_suffix` | `if source.suffix != ".py": raise`; rebuilds `.{lang}.py` | B: use `source.suffix`; **reuse pairing.py helpers** instead of re-deriving | **blocker** |
| **S-2** | `slides/split.py:135-145` header/import regexes; specifically `_HEADER_IMPORT_RE`/`_HEADER_DE_IMPORT_RE`/`_HEADER_EN_IMPORT_RE` at **`:143-145`** hardcode `^#\s*j2\s+` | `.match()` fails on a `// j2 … import header` line → C# import **never** rewritten to `header_de`/`header_en` | A: build regexes from `re.escape(token)`; thread token into `split_text`/`unify_texts`. **Blocked on P-0** — orphan `%%` wrapper must be resolved first | **blocker** |
| **S-3** | `slides/voiceover_tools.py:181-199` `companion_name` (**2** `.py`-literal return sites: `:197`, `:199`) | Always `voiceover_*.py` — ROOT of all companion-naming bugs | A: `f"voiceover_{…}{slide_path.suffix}"` — fixes `companion_path`/`expected_companion`/`resolve_companion`/`companion_locations` at once | **blocker** |
| **S-4** | `cli/commands/split.py:43-61` help/`_to_dict` | Docstrings say `.de.py`/`.en.py` only | Cosmetic: `<basename>.de.<ext>` once library is generic | **minor** |

### 3d. Sync engine, full-deck translate & assign-ids

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **Y-1** | `slides/sync.py:223`, `sync_split_pair`; `assign_ids.py:807` (via P-1/P-2) | All sync/assign-ids read through the coupled parser → empty deck → **silent in-sync / 0 ids minted** | Inherits parser fix; thread prog_lang from the two `Path`s | **blocker** |
| **Y-1b** | `slides/translate_deck.py:133` `split_cells(source_text)`; `:144,:150,:239,:258,:273` `_HEADER_MACRO_RE`/`_HEADER_IMPORT_RE` (imported from `split.py`, `#`-anchored) | The whole `clm slides translate` / `bootstrap` (PR #234) full-deck engine: zero cells on `.cs` → **silent no-op**; macro rewrite never fires | Inherits P-1/P-2 token fix + S-2 regexes; thread prog_lang from the source `Path`. Docstrings/paths `:5-6,:120-121` assume `*.<lang>.py` | **blocker** |
| **Y-2** | `slides/sync_plan.py:635-641` `_lang_for_path` | `name.endswith(".de.py")`/`.en.py` → C# baseline never found, drops to cold-start | B (mechanical): use `pairing.split_lang_tag(path)` (already generic) | **major** |
| **Y-3** | `slides/sync_apply.py:1733-1746` `_build_cell` | Fabricates `# %%` header for id-less ADD → inserts `#`-cell into a `//` file → **corrupts deck** | A/B: build header from token; or reuse source header like `build_twin_cell` | **major** |
| **Y-3b** | `slides/sync_apply.py:1708-1722` `_slide_heading_from_body` | Strips `# `/`#` (`raw[2:]`/`raw[1:]`) to recover a slide heading for slug minting → wrong/empty heading on `//` file, weakening id/slug minting during sync ADD | B: token-aware prefix strip (distinct function from Y-3) | **major** |
| **Y-4** | `slides/sync_writeback.py:228-238` `swap_lang` fallback | `header.replace("# %%", …)` → no-op on `// %%` → twin cell mis-tagged (no `lang=`) | A/B: replace `# %%` literal with `f"{token} %%"` | **major** |
| **Y-5** | `slides/sync_translate.py:165-190` `_SYSTEM_PROMPT`/`_CODE_SYSTEM_PROMPT` | Prompts bake in "preserve `# ` prefixes" and "return runnable **Python**" | B: thread prog_lang; `{comment_prefix}` + `{prog_lang_name}` placeholders from `language_info` | **major** |
| **Y-6** | `slides/sync_code.py`, `sync_direction.py:180`, `sync.py` v1 paths | Read via parser; structural logic is language-neutral | Inherits parser fix; thread token at the 2 `Path` sites | **minor** |
| **Y-7** | `cli/commands/slides_sync.py:119,126-128,189-191` | Help/error text says `.de.py`; never passes prog_lang to engine | B at seam: `build_sync_plan`/`apply_plan` derive prog_lang from `de_path.suffix` internally | **minor** |

### 3e. Voiceover extract / inline / companion merge (authoring + build-time)

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **V-1** | (P-1/P-2/S-3 inherited) | Extract/inline/merge all funnel through coupled parser + `companion_name` | Inherits P-1/P-2/S-3 | **blocker** |
| **V-1b** | `slides/voiceover_tools.py:851-852` `merge_voiceover_text` → `_split_raw_cells` (= P-2's `raw_cells.split_cells`) | **Build-time host-side VO merge** (invoked from `process_notebook.py:336/339`). On a `.cs` slide+companion both split to **zero cells** → `if not companion_cells: return slide_text, []` (`:854-855`) → **voiceover silently dropped at build with no error**. The merge *body* is structural (verbatim `RawCell` splice + reconstruct), so only the boundary gate is coupled. **S-3 is NOT sufficient** — S-3 only fixes the companion *filename* so it is found; the merge still needs P-2's token threaded through `merge_voiceover_text` | B: thread token into `merge_voiceover_text` → `_split_raw_cells`; resolve from `prog_lang` at the `process_notebook` call site | **blocker** |
| **V-2** | `voiceover/backfill.py:57` `extract_slide_file_at_rev` | Scratch export named `…-{rev}.py` → wrong-token re-parse | A: use `slide_path.suffix` | **major** |
| **V-3** | `cli/commands/voiceover.py:185,241-245,2397,2594` | `--companion` help + synced output names hardcode `.py` | A: `slide_file.suffix` for outputs; reword help | **minor** |
| **V-4** | `voiceover/training_export.py:158` | Docstring "the `.py` slide file"; logic agnostic | Docstring only | **minor** |

### 3f. Sidecar / cassette layout + output suppression

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **D-1** | `infrastructure/utils/path_utils.py:110` `SKIP_OUTPUT_FILE_PATTERNS` (`voiceover_.*\.py$`), `:120` `SKIP_OUTPUT_FILE_GLOBS` (`voiceover_*.py`) | Companion suppressed from output/kernel payload **only if `voiceover_*.py`** | A: broaden regex + one glob per ext from `PROG_LANG_TO_EXTENSION` — **a `.cs` companion leaks to public/speaker output *and* the kernel `other_files` payload today** | **blocker** |
| **D-2** | `slides/tidy.py:42` `_VOICEOVER_RE` (`plan_tidy:152`) | Relocates companion only if `.py` → `.cs` companion invisible to tidy | A: broaden regex; share constant | **major** |

### 3g. Normalize / validate / coverage / headingless / language-view / code-extract

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **N-1** | `slides/normalizer.py:495-538,661-683` `_heading_level`/`_bullet_count`/`_apply_cell_spacing`/`_code_names` | Markdown body is `# `; cell_spacing **inserts a literal `#` line** into a `//` cell → **corrupts C# file** | B: token from `path.suffix`; insert `token` not `#`; `_code_names` per-lang or skip | **blocker** |
| **N-2** | `slides/validator.py:140,196-198,403-425,1391,1576-1587` | `_check_format` requires `# %%` (`:140 header.startswith("# %%")`) → **false error** on every `// %%`; `_is_blank_comment`==`'#'` (`:198 line.strip()=="#"`) → spurious warning on every C# md cell; normalize then "fixes" by inserting `#` | B: token into `_check_format`/`_is_blank_comment`/`_extract_markdown_heading`/`_is_workshop_heading_cell` | **blocker** |
| **N-3** | `slides/headingless.py:53-62,111,191-194` | Regexes anchored `^#…`; prose gate `startswith("# ")` → **every C# slide hard-refuses id assignment** (kills assign-ids/normalize/course-gate for non-Python) | B: build regexes from `re.escape(token)`; thread token | **blocker** |
| **N-3b** | `slides/coverage.py:266-267` `_BULLET_RE`/`_NUMBERED_RE` (`^#\s+[-*]` / `^#\s+\d+\.`), `:303-308` `_narrative_text` (`raw_line.startswith("# ")` → `raw_line[2:]`, else `[1:]`) | **Hardcodes the Python `#` prefix on slide content** — on a `.cs` deck `extract_bullets`/`_narrative_text` see zero bullets and `//`-prefixed noise leaks into the LLM coverage prompt. **Distinct file from N-4** (`lang_coverage.py`); the draft wrongly listed `coverage.py` as agnostic | B: token from `path.suffix`; build bullet regexes from `re.escape(token)` | **major** |
| **N-4** | `slides/lang_coverage.py:266-267,303-308` | Bullets `^#\s+[-*]`; narrative strip `# ` → coverage judge sees zero bullets, `//` leaks into prompt | B: token from `path.suffix` | **major** |
| **N-5** | `slides/language_tools.py:83,102` `get_language_view` | Boundary scan `# %%`; injects `# [original line N]` → **uncompilable C# view** | A+B: token for scan + annotation | **major** |
| **N-6** | `slides/code_cell_extract.py:39-61` `extract_from_code` | `ast.parse` is **Python-syntax-only** → `SyntaxError` on C#/C++ → code-cell slug minting never works | B: gate on prog_lang; regex fallback for `//`-family or accept graceful `None` (more refusals) | **minor** |
| **N-7** | `slides/validation_summary.py:36-56` | Buckets message substrings; inherits `.py` phrasing from validator | None structural; de-Pythonize upstream messages | **minor** |

### 3h. CLI surface & discovery

| ID | File:line | Assumption | Fix | Sev |
|---|---|---|---|---|
| **C-1** | `core/topic_resolver.py:277` `find_slide_files_recursive` | Fallback walk `rglob("*.py")` restricts the glob **before** the `is_slides_file` filter (which already accepts all `SUPPORTED_PROG_LANG_EXTENSIONS`) → **`assign-ids slides/`, `normalize slides/`, `validate --shipping-only` find ZERO `.cs` decks in nested module dirs** | `rglob("*")` + `is_slides_file` filter (mirror `pairing.find_split_slide_files_recursive`) | **blocker** |
| **C-2** | `cli/commands/voiceover.py:317,344,888,1362,1370` | Every VO subcommand calls `parse_slides(slides, lang)` with no prog_lang | B: derive `path_to_prog_lang(slides)`, pass into `parse_slides` | **blocker** |
| **C-3** | `cli/commands/validate.py:45-59` `_infer_kind` | Only `suffix == ".py"` → "slides"; `.cs` → "ambiguous" error | A: `path.suffix in SUPPORTED_PROG_LANG_EXTENSIONS` / `is_slides_file` | **major** |
| **C-4** | `mcp/tools.py:395,471,624-671` | MCP wrappers forward path, never resolve prog_lang → same wrong parse as CLI | A: push extension→prog_lang resolution **into the shared slide functions** so CLI+MCP get it free | **major** |
| **C-5** | `cli/commands/summarize.py:103-110` `_extract_content` dispatch + `:143-164` `_extract_from_py` internal parser (`:154 line.startswith("# %%") or line.startswith("# +")` — note the `# +` light-format boundary, the only mention of it in this report — and `#`-body strip) | `.cs` extracts nothing; even after broadening the dispatch, the `_extract_from_py` body still can't parse `// %%` cells | A: broaden dispatch to `SUPPORTED_PROG_LANG_EXTENSIONS`; B: token the boundary scan + `# +` light-format prefix in `_extract_from_py` | **minor** |
| **C-6** | `slides_translate.py`, `unify.py:3-4,36`, `suggest_sync.py:40` help text | Universally `.de.py`/`.en.py` wording | Cosmetic: `<deck>.de.<ext>` | **minor** |

### 3i. Build-side prerequisite (NOT Python code — independent blocker)

| ID | File | Assumption | Fix | Sev |
|---|---|---|---|---|
| **T-1** | `workers/notebook/templates_csharp/macros.j2`, `templates_cpp/`, `templates_java/`, `templates_typescript/` | `header_de`/`header_en` macros exist **only** in `templates_python/macros.j2:37,56` (verified by reading all macro files — the four `//`-family templates carry only the bilingual `header`); split rewrites `header(de,en)` → `header_de`/`header_en` | **Add `header_de`/`header_en` to all four non-Python templates** (mirroring the single-language halves of their existing bilingual `header`, emitting the `//` prefix). Without this a split `.de.cs` deck **cannot build** — `header_de is undefined` | **blocker** |

---

## 4. Subsystems already language-agnostic (no change needed)

Proven by the investigators and both critiques against the real `.cs`/`.cpp` corpora and the code. **Note: three subsystems the draft listed here — `coverage.py`, the build-time `merge_voiceover_text` path, and `translate_deck.py` — have been *moved out* to §3 (N-3b, V-1b, Y-1b respectively) because they are in fact coupled.**

- **Path-level split-pair derivation — `slides/pairing.py`** (entire module). `split_lang_tag`, `_split_family` (docstring `:179` explicitly: "a `.de.py` and a `.en.cpp` are different families"), `order_split_pair`, `derive_split_twin`, `derive_split_pair_from_stem`, `find_split_slide_files_recursive` (globs `*` + filters), `iter_split_pairs` all operate on `path.suffix` generically and gate on `SUPPORTED_PROG_LANG_EXTENSIONS` (which already contains `.cs/.cpp/.java/.ts/.rs`). **`split.py` should reuse these instead of its own `.py`-literal `_split_target`/`_strip_lang_suffix`.** `HEADER_MACRO_RE`/`is_title_macro_cell` match the macro *inside* `{{ }}`, so they are comment-prefix-agnostic at the regex level (but see P-0 §2.5 — they are still gated on the j2 lines being parsed into the right cells, which the real corpus does *not* guarantee).
- **The metadata grammar after `%%`** — `lang=`, `tags=[…]`, `slide_id=`, `for_slide=`, `vo_anchor=` (`slide_parser.py:153-168`; `voiceover_tools._FOR_SLIDE_RE`/`_VO_ANCHOR_RE`). Verified byte-identical between Python and the real C# header `// %% [markdown] lang="de" tags=["subslide"]`. `re.search`-anywhere, so already works on `//` headers.
- **`slides/tags.py`** — pure tag-name frozensets; zero coupling.
- **`raw_cells.reconstruct` + the `RawCell` dataclass** — verbatim line lists; round-trip holds for any language.
- **`voiceover/merge.py`** — confirmed clean: no `#`-parsing; consumes structured objects only.
- **The slide_id / reconciliation / watermark / move-detection / cold-start mint-adopt / correspondence-verify / id-migration algorithms** (`sync_plan`, `sync_apply`, `sync_recover`, `sync_code`) — language-neutral; operate on parsed metadata, become correct the moment cells parse. (The two `#`-coupled *helpers* inside `sync_apply.py` — `_build_cell` and `_slide_heading_from_body` — are tracked as Y-3 and Y-3b.)
- **Slug minting** (`slides/slug.py`) — DE/EN transliteration acts on the **natural-language title**, orthogonal to programming language. CLM is DE/EN bilingual across all prog-langs; no change.
- **File discovery** (`core/topic_resolver.find_slide_files`, `build_topic_map`, `find_slide_units`), `deck_scope.py`, `workshop_scope.py`, `slug_quality.py`, `refusal_report.py`, `validation_summary.py`, `course_gate.py`, `search.py`, `spec_validator.py` — all path/glob/metadata-based; the **only** inherited weakness is the `rglob("*.py")` fallback (C-1).
- **The voiceover ML submodule** (`voiceover/matcher.py`, `aligner.py`, `identify.py`, `compare.py`, `slide_matcher.py`, `rev_scorer.py`, `narrative_commits.py`, `keyframes.py`, `timeline.py`) — consumes parsed objects; `.py` appears only in docstrings.
- **Sidecar/cassette resolution** (`notebook_file.py` cassette resolver + `SKIP_DIRS` sets, `sidecar_layout.py`, `tidy.py` cassette regexes, `companion_voiceover_path`, `resolve_companion`/`companion_path`/`companion_locations`/`expected_companion`) — dir-name/stem/yaml based; all correct **once `companion_name` returns the right extension (S-3)**.
- **The build resolver chain itself** — `EXTENSION_TO_PROG_LANG`/`path_to_prog_lang`/`prog_lang_to_extension` (`path_utils.py:145,487,495`) + `jinja_prefix_for` already power the build; the authoring side should mirror this exact chain rather than invent a parallel one.

---

## 5. Phased implementation plan

The critical path is **Phase 0 → Phase 1 (incl. the P-0 structural decision) → Phase 2**; everything else parallelizes once Phase 1 lands. **Phase 0 (templates) has no Python dependency and can start immediately, in parallel.**

### Phase 0 — Build-side template prerequisite *(parallel, no dependency)* — **M**
- **Files:** `templates_csharp/macros.j2`, `templates_cpp/macros.j2`, `templates_java/macros.j2`, `templates_typescript/macros.j2`.
- **Change:** add `header_de`/`header_en` macros mirroring `templates_python/macros.j2:37,56`, emitting the `//` prefix (T-1). Without this, Phase 2's split output cannot build.
- **Gate:** build one synthetic split `.de.cs` deck end-to-end.

### Phase 1 — Parser foundation + structural decision *(CRITICAL PATH — everything depends on this)* — **L-XL**
- **Files:** `prog_lang_utils.py` (P-6: add `line_comment_for` + `boundary_prefixes`; gate `.c`); `slide_parser.py` (P-1,P-3,P-4,P-5); `raw_cells.py` (P-2); `slide_writer.py` (W-1).
- **Change:** introduce the resolver; **collapse the two boundary copies into one shared helper**; add `comment_token` param (default `"#"`) to `parse_cells`/`parse_cell_header`/`split_cells`/`is_cell_boundary`/strip helpers; `parse_slides` resolves token from `path.suffix` (Strategy-A seam).
- **P-0 decision (do FIRST in this phase):** resolve the j2-in-`%%`-cell structural divergence (§2.5) against both real corpora — re-author vs. body-aware-j2 parsing — and prototype the chosen path. Nothing in Phase 2 is sound until this is settled.
- **Decide the `lstrip("# ")` over-strip question here (§9).**
- **Why first:** P-0…P-4 are cited as blockers by **all 8** investigators and both critiques. Nothing downstream is observable until this lands.

### Phase 2 — Split / unify + full-deck translate + companion naming *(depends on P0 + P1 incl. P-0)* — **L**
- **Files:** `split.py` (S-1,S-2 — reuse `pairing.py` helpers; pin the `_HEADER_IMPORT_RE` triplet at `:143-145`), `translate_deck.py` (Y-1b — the `clm slides translate`/`bootstrap` engine), `voiceover_tools.companion_name` (S-3 — the `.py`→`suffix` fix at the 2 return sites that cascades to all companion helpers).
- **Gate:** round-trip `split`→`unify` on a real bilingual `.cs`/`.cpp` deck (must NOT produce an orphan `%%` header cell — validates the P-0 choice), then build the split halves (requires Phase 0); run `clm slides translate` on a real C# deck and confirm non-zero cell output.

### Phase 3 — Sync, assign-ids, normalize, validate, coverage *(depends on P1; parallelizable internally)* — **L**
- **Files:** `sync_plan.py` (Y-2), `sync_apply.py` (Y-3, Y-3b), `sync_writeback.py` (Y-4), `sync_translate.py` (Y-5), `assign_ids.py` (Y-1 inherited), `normalizer.py` (N-1), `validator.py` (N-2), `headingless.py` (N-3), `coverage.py` (N-3b), `lang_coverage.py` (N-4), `language_tools.py` (N-5), `code_cell_extract.py` (N-6).
- **Critical sub-items:** N-1, N-2, and N-3 are corruption/false-error/total-refusal blockers; do these first within the phase.

### Phase 4 — Voiceover write paths + build-merge + sidecar suppression *(depends on P1+P2)* — **M-L**
- **Files:** `voiceover_tools.py` (W-2, W-3, **V-1b `merge_voiceover_text`/`_split_raw_cells`** — thread token so the build-time merge stops silently dropping `.cs` voiceover), `backfill.py` (V-2), `path_utils.py` `SKIP_OUTPUT_*` (D-1 — **leaks to output today**), `tidy.py` (D-2), `process_notebook.py:336/339` (resolve token from `self.prog_lang` at the call site and pass into `merge_voiceover_text`).

### Phase 5 — CLI / MCP / discovery wiring *(depends on P1)* — **M**
- **Files:** `topic_resolver.py:277` (C-1 blocker), `cli/commands/voiceover.py` (C-2), `validate.py` (C-3), `mcp/tools.py` (C-4 — prefer pushing resolution into shared functions), `summarize.py` (C-5 — dispatch **and** `_extract_from_py`), plus help-text cleanup (S-4,V-3,C-6).

### Phase 6 — Tests & docs *(depends on all)* — **L**
- See §6 and §7. The shared test-builder refactor (§6) is a prerequisite for cheap parametrization and is itself **M-L**.

---

## 6. Test strategy

**Parametrize existing Python tests by comment token — do NOT maintain parallel byte-divergent C# corpora.** The single source of truth already exists and is already tested: `tests/workers/notebook/utils/test_prog_lang_utils.py` covers `suffix_for`/`jinja_prefix_for` for all six languages, so a fixture can derive `(token, ext)` as `jinja_prefix_for(lang).removesuffix("j2").strip()` + `suffix_for(lang)`.

Concrete plan, in order:
1. **Prerequisite refactor — one shared builder module** `tests/slides/_deck_builders.py` parametrized by `(prog_lang)`. Today there is **no `conftest.py` in `tests/slides/` and no shared builder** — `_slide_pair`/`_voiceover_pair`/`_slide`/`_vo`/`_aux`/`_code_shared`/`HEADER_PREAMBLE` are copy-pasted across ~15 files (e.g. `test_split.py:48-73`, `test_sync_code_cells.py:43-58`), each baking in `# %%`. Consolidate so one rewrite covers all.
2. **Add a `[("python",".py"),("csharp",".cs"),("cpp",".cpp")]` parametrize axis** over the highest-leverage suites:
   - `tests/notebooks/test_slide_parser.py` (`TestParseCellHeader` — **the foundation contract**; pin `parse_cell_header("// %% [markdown] lang=\"de\" tags=[\"slide\"]")` yields identical `CellMetadata`).
   - **A dedicated P-0 structural test**: feed the *real* C# header shape (`// %% [markdown] tags=["slide"]` → `// j2 …` → `// {{ header }}` as body lines) and assert it parses as **one** title cell, not three — this is the regression guard for the §2.5 decision, and the Python equivalent (standalone j2 lines) must keep parsing as before.
   - `test_split.py` round-trip + the Hypothesis `_bilingual_deck` generator (the trust foundation — currently `#`-only).
   - `test_voiceover_tools.py` extract/inline/companion round-trip (166 `# %%` literals today) **plus a `merge_voiceover_text` case** asserting a `.cs` slide+companion merge is non-empty (V-1b regression guard).
   - `test_validator.py` — **specifically the 1.8 cell_spacing "blank comment line" rule must accept a bare `//` lead-in**, since that is the exact gate the 558 bilingual decks must pass.
   - `test_sync_*.py` role/code-propagation; `test_translate_*` full-deck translate on a `.cs` source (Y-1b guard).
3. **Generate on-disk fixtures at test time** from the builder (replacing static `tests/slides/fixtures/well_formed.py` etc.) so one source yields both `#` and `//` variants without drift.
4. **Real-repo round-trip, skip-when-absent**, mirroring the existing PythonCourses fixture idiom in `test_split.py:237-244` — point one case at a CSharpCourses `.cs` deck and one at a CppCourses `.cpp` deck. **Include a fixture variant carrying the leading `// -*- coding: utf-8 -*-` line** that 100% of real C#/C++ decks have but the Python fixtures lack — confirm the parser preserves it as a non-cell preamble line.
5. **Coordinate the helper signature with the production strategy:** if the core lands on explicit `comment_token` params (Strategy B), the parser-level tests must pass that arg too.

`test_pairing.py:258-260,428-431` and `test_translate_bootstrap.py:427-430` **already** prove the path layer with `.cpp` (via `_touch`'d empty files); extend that idiom to the content-bearing surfaces.

---

## 7. Docs / info-topic updates

Per the CLAUDE.md **Info Topics Maintenance Rule**, downstream course-repo agents rely on these being version-accurate; stale topics will make them emit `#`-prefixed cells into `//` files.

| File | Lines | Fix |
|---|---|---|
| `src/clm/cli/info_topics/migration.md` | `:138` ("must start with a blank comment line (#)"), `:45-54,100-107,231` | State the token varies (`#` python/rust, `//` C#/C++/Java/TS); add a "Non-Python courses" note to the slide_id rollout (the 558-deck migration) |
| `src/clm/cli/info_topics/spec-files.md` | `:650-664` ("Core source: `slides_*.py`", `voiceover_*.py`) | Generalize to `slides_*.<ext>` / `voiceover_*.<ext>`; **internally inconsistent today** with `:48` which already lists all prog-langs |
| `src/clm/cli/info_topics/commands.md` | `:190-217` (split `.de.py`), `:597` (`--kind` infers only `.py`) | Make split-build examples `.de.<ext>`; fix `--kind` to `.py/.cs/.cpp/… or directory`. Precedent already exists at `:224,469` |
| `docs/user-guide/voiceover.md` | `:4,63,217` + all CLI examples | Add one sentence: companions adopt the deck's extension + comment token; show a `.cs` example |
| `docs/user-guide/configuration.md` | `:183,197,201` | Keep companion-naming wording extension-neutral (mostly already agnostic) |

---

## 8. Course-repo migration (real-world target state)

Both target repos are **fully greenfield** for authoring features (verified by the tests-investigator against the live repos):

| Repo | Decks | Bilingual | slide_id | Split companions | VO companions | VO tags |
|---|---|---|---|---|---|---|
| **CSharpCourses** | 311 `.cs` | 248 | **0** | **0** | **0** | uses `notes`, not `voiceover` |
| **CppCourses** | 406 `.cpp` | 310 | **0** | **0** | **0** | same |

Specs already declare the language correctly (`clean-code-csharp.xml:6` `<prog-lang>csharp</prog-lang>`; all 17 CppCourses specs `<prog-lang>cpp</prog-lang>`), so the build works today. The format is structurally identical to Python percent format with `//` for `#`, plus a leading `// -*- coding: utf-8 -*-` line **and the j2 header wrapped in a `// %% [markdown] tags=["slide"]` cell** (the P-0 divergence of §2.5 — present in 100% of inspected decks and the reason the structural decision precedes the migration).

**Migration sequence (after the CLM work ships):**
1. **`clm slides assign-ids` across 558 bilingual decks** — *required*, because 1.8 escalates missing `slide_id` to a hard validator **error**. This is the load-bearing step and the reason N-3 (headingless) and N-6 (code-cell extract) matter: any deck whose slides hard-refuse id assignment blocks the gate. Expect the bulk to mint from headings; code-only slides may need `--report-refusals` triage (N-6 means C#/C++ code-cell slug minting is weaker than Python until a regex fallback lands).
2. **Split / voiceover are net-new** — no existing on-disk corpus to round-trip against beyond synthetic fixtures and the bilingual-only real files, so there is no regression risk, only forward enablement. (But the first real split *will* exercise P-0 — the orphan-`%%`-wrapper guard is what makes this safe.)

**Effort:** the assign-ids pass is largely automated (hours of compute + refusal triage); split/voiceover adoption is per-course authoring work the maintainer drives at their own pace.

---

## 9. Risks, gotchas & open questions

0. **The j2-in-`%%`-cell structural divergence is the top soundness risk** (P-0, §2.5). Verified directly: real C#/C++ decks wrap `// j2`/`// {{ header }}` as **body lines inside** a `// %% [markdown] tags=["slide"]` cell, while Python puts the `# j2`/`# {{ }}` lines as **standalone cells with no `%%` wrapper**. A token-only fix that makes `// j2`/`// {{` boundaries shatters the real header cell into three cells incl. an orphan (sometimes `lang=`-tagged) empty `%% [markdown]` wrapper — corrupting split/unify. **Mitigation:** resolve in Phase 1 before any split work — prefer teaching the parser that j2/`{{` lines *inside an already-open `%%` cell* are body lines (Python keeps working because its j2 lines appear *before* any cell opens), validated against both real corpora.

1. **Silent no-op is the default failure** (P-1/P-2). On any non-`#` file the current code parses zero cells and **reports success** — validate says "clean," assign-ids mints 0 ids, sync says "in sync," normalize writes nothing, **the build-time `merge_voiceover_text` drops voiceover (V-1b), and `clm slides translate`/`bootstrap` no-ops (Y-1b)**. Any partial fix that leaves one parser copy un-tokenized reintroduces this. **Mitigation:** collapse to one shared boundary helper in Phase 1; add an explicit test that a `//` deck yields >0 cells, and that the build merge of a `.cs` slide+companion is non-empty.

2. **Active corruption paths** (N-1, N-2, Y-3, W-1, W-3). `normalizer._apply_cell_spacing` **inserts a literal `#` line** into a `//` markdown cell; `validator` raises a false format error then "fixes" by inserting `#`; `sync_apply._build_cell` fabricates a `# %%` header inside a `//` file; the voiceover write paths emit `#` cells. These don't no-op — they **write a malformed mixed-comment file** that the next (fixed) parser mis-parses, merging cells. Prioritize these within their phases.

3. **`header_de`/`header_en` build blocker** (T-1). Verified: these macros exist **only** in `templates_python/macros.j2:37,56`. A split C#/C++ deck is unbuildable without Phase 0. The alternative (stop renaming the macro, route `header(de,en)` structurally by language) sacrifices the byte-identical round-trip guarantee — **adding the four macros is the cleaner, smaller-blast-radius path.**

4. **Output leak** (D-1). A `.cs` voiceover companion is **not** suppressed by `SKIP_OUTPUT_FILE_PATTERNS`/`SKIP_OUTPUT_FILE_GLOBS` today, so it would leak into public/speaker output *and* the kernel `other_files` payload. This is a data-exposure bug the moment a non-Python companion exists; the glob has no alternation, so it needs one entry per extension.

5. **`.c` → `"c"` dead config key.** `EXTENSION_TO_PROG_LANG[".c"] -> "c"` and `.c ∈ SUPPORTED_PROG_LANG_EXTENSIONS` (so `is_slides_file` accepts it), but `config.prog_lang` has no `"c"` entry — `line_comment_for("c")` / `jinja_prefix_for("c")` raise `ValueError`, crashing the resolver on any `.c` slide. **Mitigation:** exclude `.c` from the authoring extension set (or add a `"c"` config); scope the initial delivery to `.py/.cs/.cpp/.java/.ts`.

6. **Rust / `.md` edge.** Rust uses `#` (correct under a prog_lang-keyed resolver) but `jupytext_format: "md"` (not `py:percent`) and `EXTENSION_TO_PROG_LANG[".md"] -> "python"`. **A Rust/markdown deck may not be percent-format at all** — the `# %%` boundary abstraction may not apply. **Open question:** confirm with a real Rust sample whether Rust authoring flows through these parsers before claiming "`#`-languages uniformly covered." Recommend scoping the initial delivery to `.py/.cs/.cpp/.java/.ts` and deferring Rust/`.md`.

7. **The `lstrip("# ")` over-strip** (`slide_parser.py:336,357,368`). Character-class strip, not prefix strip — already wrong for Python (`"## H2"` → `"H2"`, verified). Generalizing forces a decision: fix to single-prefix removal (cleaner, but may change existing Python golden output) vs. preserve bug-for-bug (safe for golden files, carries the bug into the token version). **Recommend fixing with a golden-file regeneration in the same PR.**

8. **Code-cell bodies carry no comment prefix** in `//`-family files (`1 == 2` bare), while markdown bodies do (`// ## …`). The body strippers must apply the token **only to markdown cells** — audit each caller's `cell_type` gating during Phase 3 so a stripper never runs `lstrip("// ")` over bare code lines.

9. **Default-`"#"` vs required token.** `parse_cells`/`split_cells` have ~40 call sites across voiceover/recordings/notebook-worker. A defaulted optional param (`"#"`) keeps every existing Python caller working unchanged (additive, no flag-day) but risks a forgotten call site silently defaulting to `#` on a `.cs` path. **Recommend defaulted param + a lint/audit pass** confirming every `Path`-bearing call site resolves the token.

10. **Build-merge ordering** (V-1b). In `process_notebook.py`, `merge_voiceover_text(data, companion_text)` is called at `:339` — **before** the payload (carrying `prog_lang`) is constructed at `:364`. The language is available as the object attribute `self.prog_lang` (`:102`), so threading is feasible, but resolve from the object attribute, **not** from a payload object that does not yet exist at merge time.

11. **Import direction.** The canonical resolver lives in `prog_lang_utils.py` under `clm.workers.notebook.utils`; importing it from `clm.slides`/`clm.cli` couples authoring to the workers package. The build side already crosses this freely, so it is acceptable, but consider whether the bare-token accessor should be promoted to `path_utils.py` (neutral core) to keep the dependency clean.

**Verification note:** every line citation in §2-§3 against `prog_lang_utils.py`, `slide_parser.py`, `raw_cells.py`, `path_utils.py`, `coverage.py`, `voiceover_tools.py`, `translate_deck.py`, `summarize.py`, and `sync_apply.py` was independently confirmed in this investigation, as was the §2.5 structural divergence (read directly from production `slides_xunit_fixtures*.cs:2-4` and `slides_welcome_to_clean_code.py:1-2`). The parser-foundation couplings P-1…P-4 were reported identically by all 8 investigators and both critiques, which is strong corroboration. Four draft entries were corrected against the code: `coverage.py` is **not** agnostic (now N-3b); the build-time `merge_voiceover_text` path is a **breaking parse site**, not a safe seam, and is **not** made agnostic by S-3 (now V-1b); `translate_deck.py` is a **full-engine blocker** (now Y-1b); and `companion_name` has **two** `.py`-literal return sites, not three (S-3 corrected).

---

## 10. Header-convention decision — resolving the §2.5 / P-0 blocker by reformatting the decks

> Added after a follow-up investigation + validated dry-run. This section **decides** the open P-0 question (§2.5): rather than teach clm's parser two header shapes forever, we **normalize the C#/C++/Java/TS decks to the Python "header-line-less" convention**. This removes the structural-divergence blocker entirely. It does **not** remove the comment-token work (Problem A below) — that is still required.

### 10.1. Two separate problems (do not conflate)

- **Problem A — the comment token (`#` vs `//`).** Parsers/strippers/regexes hardcode `#`. Reformatting decks does **not** fix this; a header-line-less C# deck still uses `// %%` / `// j2` / `// {{`. The P-1…P-4 work stands regardless.
- **Problem B — the header structure** (authored `// %%` wrapper around the j2 header call vs standalone j2 cells). *This* is P-0/§2.5. This section resolves Problem B by reformatting the decks.

### 10.2. Mechanism confirmed

The Python `header` macro emits its own leading `%% [markdown] lang="de" tags=["slide"]` boundary (`templates_python/macros.j2:2`), so the source needs no wrapper and the title group is built `lang=None` (`slide_parser.py:271`) — the source is language-neutral, so pairing/sync never see an unpaired `lang="de"` title. The four `//`-family `header` macros do **not** emit that boundary, so their decks must wrap the call in an authored `// %%` cell. That single macro difference *is* the divergence. Confirmed deliberate since the first bilingual commit `8f8e51d7` (2026-02-11).

### 10.3. Corpus reality (precise, checkpoints excluded)

| Header wrapper | Python (555) | C# (131) | C++ (302) |
|---|---|---|---|
| Header-line-less (no wrapper) | **536 (97%)** | 2 | 1 |
| `lang="de"` wrapper | — | ~0 (real decks) | **291 (96%)** |
| Neutral `tags=["slide"]` wrapper | — | **123 (94%)** | 1 |
| Outliers (clang-format / code-cell) | ~19 | 6 | 9 |
| **Split / voiceover companions** | some | **0** | **0** |

C++ overwhelmingly uses a `lang="de"` wrapper (the literal unpaired-DE-title case); C# overwhelmingly uses a **neutral** wrapper holding German title content — which `is_cell_included_for_language` (`jupyter_utils.py:131`: no-lang ⇒ kept for *all* languages) leaks into the **English** build. Both repos are greenfield for split/voiceover.

### 10.4. The decision: normalize `//`-family decks to header-line-less

Chosen over (a) dual-shape parser in clm — permanent complexity that also perturbs Python parsing — and (b) switching Python decks to the wrapped form — which *reintroduces* the unpaired-`lang="de"` problem the format was built to avoid and churns 555 working decks. The greenfield state (no split/voiceover yet) makes "reformat now" far cheaper than baking dual-convention parsing in forever. The change is two coordinated halves that **must land together** (applying one without the other shatters/doubles the title cell):

1. **clm side (Phase 0 — DONE):** the four `header` macros now emit the leading `%% [markdown] lang="de"` boundary and gain `header_de`/`header_en` (also the T-1 split prerequisite) — applied to the live `src/clm/workers/notebook/templates_{cpp,csharp,java,typescript}/macros.j2` (+1 boundary line in `header`, +the two sibling macros; +40 lines each, 0 deletions). Locked by `tests/workers/notebook/test_header_macros_clike.py`. The `.c`→`cpp` resolver bug (§9 #5) is fixed in `path_utils.py` in the same change.
2. **Course-repo side:** strip the `// %%` wrapper line from each deck. Tool: `scripts/reformat_header_convention.py <slides-dir> [--apply]` (dry-run default; classifies and skips outliers).

### 10.5. Validated build-output impact (dry-run against the real repos)

The validation harness renders every bilingual deck two ways through CLM's exact jinja settings — `(original deck + original macro)` vs `(reformatted deck + new macro)` — and diffs the **jupytext cells** (the layer that determines every downstream output), across the html / notebook / code globals. (The harness has been distilled into the committed, corpus-wide invariant checker `scripts/verify_header_reformat.py`, which reformats every deck, builds it with the current macro, and asserts **exactly one title slide per language** — it passes on all 560 decks: cpp 302, csharp 131, java 78, typescript 49, 0 outliers, 0 violations.)

| | C++ (302 header decks) | C# (131 header decks) |
|---|---|---|
| **Preserved — byte-identical cells** | **291 (96%)** | 0 |
| **Corrected — neutral→`de` (EN-title fix)** | 1 | **123 (94%)** |
| **Unexpected diffs** | **0** | **0** |
| Render errors | 0 | 0 |
| Outliers (skipped, manual) | 9 | 6 |

**Key result — the two repos behave differently and both are correct:**
- **C++ reformat is output-*preserving*** (the wrapper was already `lang="de"`; the macro now emits the same boundary) → a safe mechanical no-op for 96%, verified byte-identical.
- **C# reformat is output-*correcting*** → the 123 neutral wrappers had their German title content leaking into the **English** build; tagging the title `lang="de"` fixes it. Output changes **intentionally** — the EN title slides should be eyeballed once, but every change is the fix, and **zero** changes were unexplained.

### 10.6. The outlier class (15 decks, homogeneous, manual)

All 9 C++ + 5 of 6 C# outliers are the **same pattern**: the title wrapper cell also contains `<!-- clang-format off -->` … `<!-- clang-format on -->` HTML comments *before/around* the `header()` call. Removing the wrapper would orphan that pre-header content into a spurious leading cell (the off-by-one the dry-run caught), so the script **refuses** them. They cluster in `module_360_solid_grasp` + a few others and need a one-time manual edit (drop the clang-format comments around the title — they do nothing in a markdown cell — or hand-convert). The remaining C# outlier is a bare `// %%` *code-cell* wrapper (one kata deck). `.ipynb_checkpoints` artifacts (57 C++ / many C#) are excluded entirely — they should not be in the repos or built.

### 10.7. Migration recipe (sequenced)

1. Land the four macro prototypes into `templates_{cpp,csharp,java,typescript}/macros.j2` **and** update the `tests/workers/notebook/test_header_macros.py` golden expectations in the **same** clm PR (the macro output changes for C#-neutral decks by design).
2. In each course repo (committed clean first): `python scripts/reformat_header_convention.py slides --apply`, then hand-fix the ~15 clang-format outliers, then rebuild and diff outputs (expect C++ identical; C# EN title slides corrected).
3. Only then proceed with the comment-token work (Problem A / Phase 1) — header-line-less `//` decks still need the `#`→token parser generalization to parse at all.

### 10.8. Net effect on the P-0 risk

§2.5 / risk #0 is **downgraded from "top blocker / open design question" to "decided + validated"**: after this reformat there is exactly **one** header convention across all languages, so the parser needs no dual-shape logic and the split/sync/voiceover surface inherits the Python assumptions unchanged (modulo the comment token). Residual risk is confined to the 15 outlier decks (manual) and a one-time EN-title review for the C# corpus — both bounded and surfaced, neither destructive.