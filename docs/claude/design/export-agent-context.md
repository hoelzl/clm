# Agent-audience course context (`clm export context` + MCP `course_context`)

## Problem

An LLM that is authoring or revising course material needs to know **what the
course has already taught up to a given point**. An assistant writing section 11
should be able to see sections 1–10 — enough to reference prior workshops and
avoid re-teaching concepts the participants already know.

The two existing exports don't cover this:

- `export outline` — deterministic titles only; usually too little for an author
  (no idea what a topic actually introduced).
- `export summary` — LLM prose for **humans** (`client` marketing copy /
  `trainer` methodology notes), and **whole-course** only (no "up to here"
  scope).

So the gap is: a **scoped**, **agent-tuned**, **depth-selectable** view.

## Design

A new command `clm export context SPEC_FILE` (module
`cli/commands/export/context.py`) and a mirrored MCP tool `course_context`.

### Three axes

1. **Audience — `agent`** (new, reusable). Added to the shared
   `infrastructure/llm/prompts.py` (`AGENT_SYSTEM_PROMPT_EN/DE` + templates +
   length instructions + `_PROMPTS`/`_LENGTH_INSTRUCTIONS` entries) and to
   `extract_notebook_content` (agent sees code, like `trainer`, via the
   `_CODE_AUDIENCES` set). The prompt asks for dense, factual notes on the
   concepts/terms/APIs introduced and any workshop — explicitly *for an LLM that
   will author later material*, no marketing. The summary cache
   (`clm_summaries.db`) is already keyed by audience, so `agent` partitions
   automatically — **no schema change**.

   *Workshop hint.* The `has_workshop` signal feeding the prompt comes from
   `notebook_contains_workshop()` (in `export/summary.py`), which detects
   workshops on the jupytext percent-format `.py` source decks via the canonical
   `workshop`/`end-workshop` tag + `workshop-…` slide_id convention
   (`clm.slides.workshop_scope.find_workshop_ranges`). The previous `.ipynb`-only
   JSON parse was dead for real courses (decks are `.py`; `.ipynb` is build
   output) — it is kept only as a fallback. This fix also restores the
   `[Workshop]` marker in `export summary --audience trainer`.

2. **Scope** (the novel part). Two mutually-exclusive selector families over the
   ordered (section, topic) walk:
   - Section: `--through SECTION` (+ optional `--from SECTION`); `SECTION` is a
     1-based number **or** a section id.
   - Topic anchor: `--before TOPIC_ID` (exclusive) / `--upto TOPIC_ID`
     (inclusive) — keeps earlier sections whole and truncates the anchor's
     section.
   Section **numbers are preserved** under scoping (`--from 5 --through 10` →
   sections numbered 5..10), because an author counts them by position.
   Resolution lives in pure, unit-testable functions (`apply_scope`,
   `_resolve_section_position`, `_locate_topic`) operating on `_SectionUnit`
   lists, raising `ScopeError` for anything unresolvable.

3. **Depth — `--level`**:
   - `titles` — deterministic structure (section → topic → slide titles). No LLM.
   - `summary` — per-topic `agent` LLM summary, cached. The value-add for large
     courses. Reuses `summarize_notebook` + `SummaryCache` +
     `SummarizeProgress`.
   - `full` — raw extracted markdown **+ code** per topic. Deterministic,
     complete, large. No LLM.

### Why a new command, not `--audience=agent` on `summary`

`summary`'s option surface (`--granularity notebook|section`, client/trainer
output shaping) doesn't carry scope, and overloading it would still leave the
"up to here" requirement unmet. `context` owns the scope + level vocabulary and
leaves `summary` untouched, while **reusing** its content-extraction, LLM-call,
cache and progress machinery (imported from `export/summary.py`). Rendering is a
simple linear course → section → topic walk (not the subsection/weekday outline
renderer): an author wants the topic sequence, not weekday grouping.

### Units pipeline

`build_section_units()` produces ordered, 1..N-numbered `_SectionUnit`s
(enabled sections from the built `Course`; disabled whole sections read from
disk via `disabled_topic_files`, tagged `disabled=True`). `apply_scope()` trims
them. `load_scoped_units()` ties loading + scoping together and is shared by the
CLI and the MCP handler. Renderers (`render_titles_markdown`,
`render_full_markdown`, `render_summary_markdown`, `render_json`) consume the
scoped units; `_summaries_by_hash()` computes cache-or-LLM agent summaries keyed
by content hash (identical decks summarized once).

`--include-disabled` is honored (bare/`=marked` only — disabled sections tagged
and appended). `=merge` is **rejected** for `context`: a linear "what came
before" reference shouldn't silently interleave disabled content.

### MCP `course_context`

`handle_course_context()` in `mcp/tools.py`, registered in `mcp/server.py`,
mirrors `course_outline`: async, loads the `Course` via the mtime cache, returns
JSON (or `{"error": …}` for an unresolvable selector / bad level). It accepts
`level`, the four scope selectors, `include_disabled`, `model`, `no_cache`.

**Default `level=titles`** (vs. the CLI's `summary` default): an interactive MCP
tool call must not silently trigger a paid LLM request. The agent opts into
`summary`/`full` explicitly; cache makes repeats instant.

## JSON shape

```jsonc
{
  "course_name": "...", "language": "en", "level": "summary",
  "scope": { "through": "10" },              // only the active selectors
  "sections": [
    { "number": 1, "name": "...", "disabled": false, "id": "...?",
      "topics": [
        { "topic_id": "...",
          "slides": [
            { "file": "slides_x.py", "title": "...",
              "summary": "...",   // level=summary
              "content": "..." }  // level=full
          ] } ] } ]
}
```

## Tests

- `tests/cli/test_export_context.py` — scope resolution (number/id, window,
  before/upto, error cases, number preservation), titles/full deterministic
  levels, summary level with a mocked `summarize_notebook` (asserts the `agent`
  audience), agent prompt selection (EN/DE), agent-vs-client code inclusion.
- `tests/mcp/test_tools.py::TestHandleCourseContext` — titles default, section &
  topic scoping, full content, error JSON for incompatible selectors / unknown
  level / unknown topic, summary level mocked. `test_server.py` expects the new
  tool name.

## Future

- A `concepts`/`apis` structured extraction level (machine-readable lists rather
  than prose) if authors want to query "has X been introduced?" directly.
- Cross-course context (pull prior modules from a multi-course program).
