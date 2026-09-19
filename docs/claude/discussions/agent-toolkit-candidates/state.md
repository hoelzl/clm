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

S4 (2026-09-19, OpenCode session; dialogue in
`transcripts/2026-09-19-s4.md`): the owner chose
outright removal over top-level compatibility shims and corrected the
artifact verb's name to **`export-at-rev`**: without a merge it no longer
syncs anything. Slice 5 and its slice-6 documentation **landed in PR #978**
(merged 2026-09-19); #960 is complete. The S4 save extends the earlier
state-only snapshot with exported, reviewed dialogue.

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
  that was #960, now landed (below).
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

## #960 retirement decisions and findings (S4)

- **Remove the top-level verbs, no shims:** `port`, `compare`, `backfill`,
  `compare-from-inventory`, and `sync-at-rev`. Legacy model execution is
  explicitly nested under `autopilot`; its original bare one-shot spelling
  still dispatches to `autopilot run`.
- **Rename rather than silently weaken `sync-at-rev`.** The owner asked:
  "should we not rename it, e.g., to `export-at-rev`, since it doesn't seem
  to sync anymore?" The new command exports git blobs into a new directory,
  preserving the historical deck/twin/companion names and layout. It takes
  no videos or model options. This is artifact production, not judgment.
- **Backfill includes curation:** export carries only narration already in
  git. The documented loop is identify → export → report/curate/accept on
  the old deck → task-port/accept on the current deck → verify. Tested by
  `tests/cli/test_harvest_history_retirement.py::test_export_to_port_accept_loop_with_companions`
  with embedded imports blocked. See `clm info harvest-agents` for the
  actual procedure; migration and changelog fragments capture the break.
- **Companion prerequisite found:** slice 2's target write was companion-aware,
  but its revision readers ignored companions. Port/compare CLI and MCP now
  read them; compare freshness includes their bytes. Regression coverage:
  `test_compare_companion_freshness_and_model_free_accept` in that same file
  and `tests/mcp/test_harvest_tools.py::TestHandleHarvestTaskPort::test_reads_companion_narration`.
- **Correction to S3's MCP assessment:** `harvest_compare` was not the last
  embedded-model entry point. `harvest_backfill_dry` spawned the old pipeline
  with `--dry-run`, which still ran both model stages before suppressing the
  final patch write. That tool is now removed too; the exact MCP tool set is
  pinned in `tests/mcp/test_server.py::TestCreateServer`.
- **Knowledge audit:** durable behavior is in the regression tests, user guide,
  `commands` / `harvest-agents` / `migration` info topics, and #960 changelog
  fragments. The naming rationale and corrected MCP premise live here.
- **Save-source correction:** OpenCode can export this session with
  `opencode export ses_f46390f38ffe5pplYxGjjeYyH4`. The repository cleaner
  has no native OpenCode adapter, but public text parts can be extracted
  and reviewed without committing reasoning or tool payloads. Do not use
  `--sanitize` for the dialogue extraction: in the installed version it
  replaces even ordinary owner text with redaction placeholders. The S4
  transcript documents its manual extraction and exact boundary; adding a
  native cleaner adapter remains tooling follow-up, not part of #960.
- **Still deferred:** an MCP mirror specifically for `align report` (harvest
  already has a read-only toolkit mirror); add it if callers need alignment
  review over MCP. It was not added as part of retirement. Discarded
  `manual_review` match candidates and partial alignment patches remain
  deferred as above.

## Open (unchanged, still owner decisions)

- #962: `slides polish` as its own verb pair vs `harvest task --kind polish`.
- #965: wait for #907 drift semantics or land the `--json` half first.

## Next conversational boundary

Continue the owner-approved Tier-1 order with #961 (slides translate).
Before #962, settle its verb-pair vs harvest-task placement question.
Do not reopen shim-vs-removal or the
`export-at-rev` naming decision.
