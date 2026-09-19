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

S3 (2026-09-19, opencode session, no cleaned transcript — the cleaner targets
Claude/Hermes formats): the owner confirmed the tier ordering (kit first) and
#959 plus #960 slices 1–4 **landed** (PRs #973–#976, all merged).

## Settled

- **The contract is already defined twice** (`clm info sync-agents`,
  `clm info harvest-agents`) — and since #959 there is one canonical
  description (`clm info agent-tasks`) plus one shared kit:
  `clm.slides.agent_task` (envelope builder, `freshness_mismatch`, the
  `VALIDATORS` registry, `AnswerRejected`, `EXIT_*` constants) and
  `clm.cli._default_verb_group`. Validators registered so far:
  `harvest-bullets` (curate/translate/port), `sync-decisions`,
  `harvest-align`, `harvest-compare`. New conversions reuse this kit; no
  third dialect.
- **Premise correction (S2).** The owner recalled video transcription as
  "mostly stand-alone". It was already half-converted (#546); what remained
  was the revision-history family and the heuristic alignment decisions —
  that was #960, now mostly landed (below).
- **Three tiers**, as filed: Tier 1 = features that still invoke a model
  in-process (harvest history #960, `slides translate` #961, `slides polish`
  #962, `coverage` + `assign-ids --llm-suggest` #963, `export summary` #964);
  Tier 2 = deterministic features locked behind Rich/prose output
  (`recordings` #965 with #907, `calendar` #966, `release status`/`sync
  --dry-run --json` #967); Tier 3 = CLI-first affordances (`build --report
  FILE` #968, `git status --json` #969). **Owner-confirmed order**: kit →
  #960 → #961/#962/#963 → Tier 2 → Tier 3.
- **Explicitly not converting**: `validate`, `course gate/orphans/renumber/
  decks`, `cache explain`, `cassette`, `kernel-triage`, `query`, `export
  outline`, `run` (all JSON-capable already) and the infrastructure groups
  (`workers`, `jobs`, `db`, `docker`, `provision`, `monitor`, `jupyterlite`,
  `zip`, `serve` — the Studio `/api/studio/*` API is typed and token-gated
  and usable as-is).

## #960 decisions (S3, owner-approved)

- **Reviewable alignment landed** (PR #974): the aligner keeps per-segment
  `SegmentAssignment` records (overlap fractions, runner-up, reason), the
  matcher keeps runner-up + `overridden_by_sequential` (merged timeline
  entries keep the *weakest* evidence), `harvest align report` frames
  uncertain decisions, `align accept` applies reassignments and writes a
  **full alignment file** fed back via `--alignment` (the agreed v1 — a
  partial-patch layer with cache-key interplay was deliberately deferred).
- **Port/compare framing landed** (PR #975): `harvest task --kind port`
  frames on the target's v3 bundle and its answers land through the ordinary
  `harvest accept` (id-keyed, companion-aware — replacing the old index-keyed
  `update_narrative` write). **Deviation from the issue text, owner-visible
  in the PR**: the issue suggested routing *compare* answers through
  `harvest accept`, but compare is read-only auditing with no deck write —
  `harvest compare-accept` follows the `align accept` sidecar precedent and
  writes the canonical report JSON (freshness = file-content fingerprints,
  full coverage of framed pairs required).
- **MCP question answered (S2 open item)**: convert-over-drop, implemented
  as "drop the invoke path, extend the mirror" (PR #976) — the model-invoking
  `harvest_compare` MCP tool is **removed**; `harvest_task(kind="port"|
  "compare", source=…)` mirrors the framing. Breaking for MCP clients;
  `clm info migration` covers it.
- **Tool trap (durable)**: `tests/test_architecture_contracts.py`
  (cross-module private-import guard) is NOT in the pre-push smoke tier —
  two pushes passed the gate and failed CI's unit tier. Documented in
  `docs/developer-guide/testing.md`; run it explicitly when touching
  cross-module imports.
- `manual_review` match pairs are NOT framed: the slide matcher discards
  the candidates when it demotes a pair, so there is nothing to frame. The
  task error says to fix duplicate slide ids/titles. Surfacing the discarded
  candidates (align-style) is possible follow-up, not planned.

## #960 remaining (next session's work)

- **Slice 5 — retire the embedded-LLM verbs behind `autopilot`**
  (the acceptance criterion "no `clm harvest` verb other than `autopilot`
  imports the LLM client"): `autopilot`, `sync-at-rev`, `backfill`, and the
  CLI `port`/`compare` verbs still call `merge.py`/`port.py`/`compare.py`.
  `backfill` should become a documented loop in `harvest-agents`
  (`identify-rev --json` → sync-at-rev-equivalent → `task --kind port` →
  `accept`) instead of a three-stage embedded pipeline; whether the CLI verbs
  `port`/`compare`/`backfill`/`sync-at-rev` stay as thin autopilot-marked
  shims or are removed outright is the design question to settle first.
  Note `sync-at-rev` produces an *artifact* (the old-revision deck), not
  judgment — it may legitimately stay, minus its embedded-model merge.
- **Slice 6**: the backfill loop documentation (folds into slice 5's docs).
- MCP mirror for `align report` was deferred (contract says every toolkit
  has a read-only mirror; harvest has one, align is a sub-verb — decide in
  slice 5/6 whether to add `harvest_align_report`).

## Open (unchanged, still owner decisions)

- #962: `slides polish` as its own verb pair vs `harvest task --kind polish`.
- #965: wait for #907 drift semantics or land the `--json` half first.

## Next conversational boundary

Slice 5's design question (shim vs removal for the legacy verbs). The
kickoff prompt the owner holds names it.
