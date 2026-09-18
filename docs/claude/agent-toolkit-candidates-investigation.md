# Agent-Toolkit Candidates — Which clm Features Should Stop Being Stand-Alone

**Status**: Investigation — complete; tracked by the issues in §6
**Date**: 2026-09-19
**Author**: Claude (Fable 5.1), at the maintainer's request
**Question**: Several clm features were deliberately redesigned from
"clm does the task itself" into *toolkits an agent drives* — the bilingual
sync engine (`clm slides sync`, #366/#440) and the per-cohort release system
(`clm release`). Which other features would profit from the same revision, or
from smaller affordances that make them easier to drive from an agent?

---

## 1. What "agent toolkit" means in clm today

The sync and harvest designs (`docs/claude/design/sync-agent-toolkit-redesign.md`,
`docs/proposals/video-narration-harvest.md`) share one contract. Every
conversion below should reuse it rather than invent a third dialect:

| Principle | Concretely |
|---|---|
| **Read by default** | The bare command is `report`; every write is an explicit verb (`apply` / `accept`). |
| **Emit, don't invoke** | Judgement is framed as a JSON *task*: `instructions`, `inputs`, `answer_schema`, and freshness tokens the agent echoes back. The engine validates **shape and freshness only**, never quality. It never calls a model. |
| **Ledger as trust store** | Stable member handles (`id:<slide_id>` / `pos:…`), a committed per-topic ledger, provenance on every recorded row. |
| **Load-bearing exit codes** | `0` clean / `1` work pending / `2` error, so a CI sweep can branch without parsing. |
| **Guide + mirror** | An `<x>-agents` info topic (`clm info sync-agents`, `clm info harvest-agents`) and a **read-only** MCP mirror; writes stay CLI-only. |
| **`autopilot` quarantine** | Embedded models survive only behind one explicit `autopilot` verb for the agent-less human, key-gated, never in CI. |

## 2. Correction of the premise: transcription is already half-converted

The maintainer's recollection was that video transcription is "mostly a
stand-alone feature". That is out of date: the video narration harvest (EPIC
#546, Phase 4 cutover) already re-cut it. `clm harvest report → task →
accept → verify` is model-free, keyed by v3 member handles, freshness-token
guarded, documented in `clm info harvest-agents`, and mirrored read-only over
MCP (`harvest_report`, `harvest_task`). `clm voiceover` is now only the
companion-file text layer (`extract` / `inline` / `inline-notes`).

**But only the curate/translate half was converted.** The revision-history
half of the same domain still runs embedded LLM prompts with no `task` /
`accept` equivalent — see §3.1.

## 3. Tier 1 — features that still invoke a model inside clm

These are the direct analogues of the pre-pivot sync engine: a deterministic
core with an OpenRouter/Ollama client bolted onto the main path.

### 3.1 Voiceover revision history (`harvest port` / `compare` / `compare-from-inventory` / `backfill` / `sync-at-rev` / `autopilot`)

- All run embedded prompts: `src/clm/voiceover/merge.py` (`polish_and_merge`,
  `propagate_*`), `port.py` (`polish_and_port`), `compare.py`
  (`judge_slide_pair`), `autopilot.py`. Prompts under
  `src/clm/voiceover/prompts/{merge,port,compare,propagate_*}_*.md`.
- `harvest_compare` even invokes a model **through MCP** (`tools.py`
  `run_compare_async`) — the one MCP tool that does.
- There is no framed task for "carry old-revision bullets onto HEAD" or
  "label bullet relations across revisions". `backfill` is a composition of
  three LLM stages behind `--dry-run`.
- **Heuristic judgement with no agent review path**: OCR→slide matching
  (`matcher.py`), transcript→slide alignment incl. `[Revisited]` grouping
  (`aligner.py`), cross-revision slide matching (`slide_matcher.py`), revision
  scoring (`rev_scorer.py`). An agent cannot see low-confidence assignments
  or override one except via the blunt `--transcript` / `--alignment`
  injection files (`overrides.py`).

Proposal: `harvest task --kind port|compare` + `accept`, an `align report`
that frames uncertain segment assignments as items, and retirement of
`merge.py`/`port.py`/`compare.py` from the main path (kept only behind
`autopilot`). This retires the largest embedded-LLM code in the repo and makes
the transcription domain uniformly agent-first.

### 3.2 `clm slides translate` (bootstrap)

- Hard-stops without an API key; translates the whole deck itself through
  OpenRouter (`src/clm/slides/translate_bootstrap.py`, `sync_translate.py`).
- `--dry-run` only counts cells (`_emit_dry_run` in
  `src/clm/cli/commands/slides/translate.py`) — it does not hand the agent the
  work.
- The prompt seam is *already shared* with `clm slides sync task`
  (`sync_translate.py` ~:201/:266), so the pieces exist.

Proposal: `translate task DECK` emits every `lang`-tagged cell with glossary
and code-prompt rules; `translate accept DECK --answer` writes the twin,
mints EN-authority ids on both halves, records the ledger. Bootstrap stays
as `autopilot`.

### 3.3 `clm slides polish`

- One chat completion per run (`src/clm/notebooks/polish.py` `polish_text`);
  prose in, prose out; no `--json`; `--dry-run` prints to the terminal.
- Level prompts are already markdown files
  (`src/clm/notebooks/polish_levels/*.md`); `verbatim` is a no-LLM
  passthrough.

Proposal: the cheapest conversion. Either `polish task/accept`, or fold it in
as `harvest task --kind polish` since the level prompts live beside the
harvest prompts.

### 3.4 `clm slides coverage` and `clm slides assign-ids --llm-suggest`

- Both call a local Ollama model for one verdict/title
  (`src/clm/slides/coverage.py`, `assign_ids.py` `TitleSuggester`).
- Coverage is a pure judgement with a cache; `assign-ids --json` already emits
  the refusal report, so only the **accept** side is missing.

Proposal: `coverage report` (bullet/voiceover pairs as items) + `coverage
accept` (bank verdicts into the cache / findings); `assign-ids accept
--answers` taking agent-proposed titles for refused cells.

### 3.5 `clm export summary` / `export context --level summary`

- LLM prose through `infrastructure/llm/prompts.py`, cached in
  `clm_summaries.db`. An `agent` audience already exists in the prompt table
  but the CLI only exposes `client|trainer`.
- Lower priority: `export context --level full` already hands an agent the raw
  material, so it can summarise itself.

Proposal: let the cache accept agent-written summaries (`export summary
accept`) so `context` reuses them; expose `--audience agent`.

**Side effect of Tier 1**: the open TODO "one uniform per-purpose LLM model
configuration" (`docs/claude/TODO.md` §LLM) mostly dissolves — only the
`autopilot` verbs would still choose a model.

## 4. Tier 2 — deterministic features locked behind human output

No model anywhere. The fix is structured output plus a `report` verb, not a
redesign.

### 4.1 `clm recordings`

- `status`, `jobs list|…`, `backends`, `check` are Rich-only
  (`src/clm/cli/commands/recordings.py`); only `drift` has `--json`.
- Good agent inputs already exist: the per-course state file
  (`<recordings-dir>/<course_id>.json`, `state.py`) and the jobs ledger
  (`.clm/jobs.json`). The "which deck to record next" decision is a heuristic
  the agent-harness draft names as judgement territory.
- The recordings dashboard (`clm recordings serve`) has two JSON routes and no
  auth — not an agent surface.
- Overlaps #907 (drift → re-recording backlog); this is the *surface* half of
  that design.

Proposal: `--json` on `status`/`jobs`/`backends`; a `recordings report`
combining drift, missing parts, and next candidates as items; an
acknowledgement ledger (#907 gap 4) written by an `accept`-style verb.

### 4.2 `clm calendar check` / `status` / `push`

- Plain-text diagnostics with exit codes (`src/clm/cli/commands/calendar.py`);
  no `--json` anywhere. The TOML is hand-edited and clm never writes it, so
  the agent *is* the editor and needs structured findings (file/line/rule).

Proposal: `check --json`, `status --json`, `push --dry-run --json` (the plan).

### 4.3 `clm release`

- Already ledger-driven (the reason it counts as agent-shaped). But only
  `channels` has `--json`; `status` and `sync --dry-run` render tables.

Proposal: `status --json`, `sync --dry-run --json` (promotion plan incl.
`skip-failed`, evergreen and refreeze rows) so an agent can decide what to
release and predict what a sync will do.

## 5. Tier 3 — stays CLI-first, gains affordances

- **`clm build`**: `--output-mode json` already emits a rich envelope
  (`errors[].actionable_guidance`, `rebuild_reasons`, `flaky_files`,
  `output_conflicts`, `stages`, …; `src/clm/build/output_formatter.py`), but
  stdout-only. `kernel-triage` already scrapes it (`_extract_build_json`),
  which shows the demand. Proposal: `--report FILE` persisting the same
  envelope regardless of output mode.
- **`clm git`**: `status --json` (incl. `--all`) answering "is any target or
  cohort repo behind / dirty" in one call.

**Not worth converting** (already JSON-capable or pure infrastructure):
`validate`, `course gate/orphans/renumber/decks`, `cache explain`, `cassette`,
`kernel-triage`, `query affected-specs`, `export outline`, `run`; and
`workers`, `jobs`, `db`, `docker`, `provision`, `monitor`, `jupyterlite`,
`zip`, `serve` (the Studio `/api/studio/*` API is typed and token-gated —
usable as-is).

## 6. Cross-cutting: extract the shared task/accept kit first

Harvest and sync each implemented the contract separately (answer envelope,
freshness tokens, member handles, validator registry `harvest-bullets`, exit
codes). Before starting Tier 1, extract that into one shared module so
polish / translate / coverage do not produce a third dialect. Each conversion
also needs its own `clm info <x>-agents` topic and a row in the MCP
read-only mirror.

### Tracking issues

- Umbrella: #970
- Cross-cutting kit: #959
- Tier 1: #960 (harvest history), #961
  (translate), #962 (polish), #963
  (coverage + assign-ids), #964 (export summary)
- Tier 2: #965 (recordings), #966 (calendar),
  #967 (release)
- Tier 3: #968 (build `--report`), #969 (git status
  `--json`)

## 7. Method

CLI surface from `src/clm/cli/info_topics/commands.md` and the command
modules; embedded-model call sites by grepping `src/clm` for the LLM client
imports; `--json` capability per command module; MCP registrations in
`src/clm/mcp/server.py`; the two existing toolkit designs read in full; two
delegated read-only surveys (voiceover/harvest; recordings, polish, summary,
calendar, serve) whose file-level findings are folded into §3–§5.
