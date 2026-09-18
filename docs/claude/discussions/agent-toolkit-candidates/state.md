---
status: active
owner: maintainers
updated: 2026-09-19
review-by: 2027-03-19
---

# State: agent-toolkit candidates (which clm features stop being stand-alone)

The owner's question (S2, 2026-09-19): sync (`clm slides sync`) and cohort
release (`clm release`) were deliberately reshaped from "clm does the task"
into toolkits an agent drives; which other features would profit from the
same revision, and which only need affordances? `clm build` and `clm git`
stay CLI-first by owner preference (they may still gain agent-useful flags).

Task state lives in the tracker, not here: umbrella **#970**, children
#959–#969. The investigation itself is
`docs/claude/agent-toolkit-candidates-investigation.md` (PR #971, merged).

## Settled

- **The contract is already defined twice** (`clm info sync-agents`,
  `clm info harvest-agents`): read by default, emit-don't-invoke (framed JSON
  task with instructions / inputs / answer schema / freshness tokens; engine
  validates shape and freshness only), ledger as trust store, `0/1/2` exit
  codes, an `<x>-agents` info topic, read-only MCP mirror, embedded model only
  behind `autopilot`. New conversions reuse it; #959 extracts it into one
  shared kit **first** so polish/translate/coverage do not add a third dialect.
- **Premise correction.** The owner recalled video transcription as "mostly
  stand-alone". It is already half-converted: `clm harvest report → task →
  accept → verify` (EPIC #546) is model-free and agent-first; `clm voiceover`
  is only the companion text layer. What is *not* converted is the
  revision-history family (`port`, `compare`, `backfill`, `sync-at-rev`,
  `autopilot`) and the heuristic alignment decisions (OCR→slide, transcript→
  slide, revision scoring) that an agent cannot review or override except via
  the `--transcript`/`--alignment` injection files. That is #960.
- **Three tiers**, as filed: Tier 1 = features that still invoke a model
  in-process (harvest history #960, `slides translate` #961, `slides polish`
  #962, `coverage` + `assign-ids --llm-suggest` #963, `export summary` #964);
  Tier 2 = deterministic features locked behind Rich/prose output
  (`recordings` #965 with #907, `calendar` #966, `release status`/`sync
  --dry-run --json` #967); Tier 3 = CLI-first affordances (`build --report
  FILE` #968, `git status --json` #969).
- **Explicitly not converting**: `validate`, `course gate/orphans/renumber/
  decks`, `cache explain`, `cassette`, `kernel-triage`, `query`, `export
  outline`, `run` (all JSON-capable already) and the infrastructure groups
  (`workers`, `jobs`, `db`, `docker`, `provision`, `monitor`, `jupyterlite`,
  `zip`, `serve` — the Studio `/api/studio/*` API is typed and token-gated
  and usable as-is).
- Side effect noted in `docs/claude/TODO.md`: the "uniform per-purpose LLM
  model configuration" item (#167) mostly dissolves once Tier 1 lands.

## Grounding facts (from the survey)

- Embedded-LLM call sites on a main path, by module: `voiceover/{merge,port,
  compare,autopilot}.py`, `slides/{sync_translate,translate_bootstrap}.py`,
  `notebooks/polish.py`, `slides/{coverage,assign_ids}.py` (Ollama),
  `cli/commands/export/{summary,context}.py`. `harvest_compare` is the one
  MCP tool that invokes a model.
- `--json` is absent from: `build` (has `--output-mode json`, stdout only),
  `calendar`, `git`, `export summary/context`, `slides polish`, and all of
  `recordings` except `drift`; `release` has it only on `channels`.
- The translation prompt seam is already shared between `sync task` and the
  bootstrap (`sync_translate.py`), so #961 is plumbing.

## Open

- Owner has not yet ordered the tiers beyond "kit first"; the umbrella lists
  the recommended order (kit → #960 → #961/#962/#963 → Tier 2 → Tier 3).
- Whether `slides polish` becomes its own verb pair or `harvest task --kind
  polish` (#962 leaves both open).
- Whether `harvest_compare` over MCP becomes model-free or is dropped (#960).
- Whether the recordings surface (#965) waits for #907's drift semantics or
  lands its `--json` half first.

## Next conversational boundary

None pending. The next contact with this topic is picking up #959 (the shared
kit); the discussion resumes only if the owner wants to re-cut the tiers.
