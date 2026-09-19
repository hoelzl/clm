---
status: active
owner: maintainers
updated: 2026-09-20
review-by: 2027-03-20
---

# State: agent-toolkit candidates (which clm features stop being stand-alone)

The owner's question (S2, 2026-09-19): sync (`clm slides sync`) and cohort
release (`clm release`) were deliberately reshaped from "clm does the task"
into toolkits an agent drives; which other features would profit from the
same revision, and which only need affordances? `clm build` and `clm git`
stay CLI-first by owner preference (they may still gain agent-useful flags).

Task state lives in the tracker, not here: umbrella **#970**, children
#959–#969 plus #981. The investigation itself is
`docs/claude/agent-toolkit-candidates-investigation.md` (PR #971, merged).

S5 (2026-09-19/20, opencode session): resumed the thread, landed **#961**
(PR #980, merged), settled the #962 placement question, ran a
command-surface orthogonality review whose outcomes are recorded on the
tracker (#963 rename note + owner ratification, #970 shaping rule, #981
filing) and in `clm info commands` (narration map, PR #982).

## Settled

- **The contract** (`clm info agent-tasks`, kit `clm.slides.agent_task` +
  `clm.cli._default_verb_group`, #959): envelope identity, freshness-token
  echo, validator registry (`harvest-bullets`, `sync-decisions`,
  `harvest-align`, `harvest-compare`, and since #961 `translate-deck`),
  load-bearing exit codes, `<x>-agents` topics, autopilot quarantine.
- **Three tiers + order** (owner-confirmed): kit → #960 → #961/#962/#963 →
  Tier 2 (#965–#967) → Tier 3 (#968–#969). **Not converting**: validate,
  course gate/orphans/renumber/decks, cache explain, cassette,
  kernel-triage, query, export outline, run, and the infrastructure groups.
- **#960 landed** (PRs #973–#978): history verbs removed without shims,
  `export-at-rev`, MCP conversion, companion-aware readers.
- **#961 landed** (PR #980): `clm slides translate` is a default-verb group
  — `report` (bare, read-only; twin absent → counts + pointer, exit 1),
  `task` (whole-deck cold-start framing; the engine's own classification
  via `plan_cells`, the shared prose/code/title prompts, glossary,
  fingerprints), `accept` (shape+freshness+coverage, then the public
  `bootstrap_deck()` — byte-identical post-conditions to autopilot, pinned
  by a differential test), `autopilot` (the in-process OpenRouter path).
  Deferred: an MCP mirror of `task` (same posture as the deferred
  `align report` mirror).
- **#962 placement settled (S5)**: `slides polish` becomes **its own verb
  pair** (`report`/`task`/`accept` + `autopilot` quarantine), NOT
  `harvest task --kind polish` — discovery-first rationale (name match,
  prefix pruning, `--kind` already carries two input shapes, slides-family
  info routing, pattern reuse). Recorded on #962; the owner engaged with
  the recommendation and did not countermand — confirm once at
  implementation start.
- **Surface-shaping rule (S5, recorded on #970)**: conversions keep the
  feature's name and home, gain the standard verb set, and absorb or rename
  their report-ish siblings; superseded commands retire without shims; no
  big-bang regrouping of `slides`. The sync `apply` vs harvest/translate
  `accept` wording stays as the one accepted dialect bend.
- **`coverage-report` → `language-coverage` (S5, owner-ratified on #963)**:
  the rename lands in #963's own PR together with the `coverage` verbs —
  no alias; four alternatives documented as rejected on the issue.

## Open (owner decisions)

- #965: wait for #907 drift semantics or land the `--json` half first
  (unchanged).

## Deferred / revisit conditions

- MCP mirrors: `align report` (from S3) and `translate task` (from S5) —
  add only if callers need them over MCP.
- Discarded `manual_review` match candidates and partial alignment patches
  (from S3) — possible follow-ups, not planned.
- #981 (`suggest-sync` + MCP `slides_suggest_sync`): retire if the
  pre-split bilingual format is dead, else unhide + document —
  investigation steps on the issue; outside the tier order.

## Known weak points

- `tests/test_architecture_contracts.py` (cross-module private-import
  guard) is NOT in the pre-push smoke tier — run it explicitly when
  touching cross-module imports (documented in
  `docs/developer-guide/testing.md`).
- Full-suite xdist runs can flake `TestMockWorkerBasics.
  test_mock_worker_stops_and_marks_dead` under contention (passes
  standalone); cap workers (`PYTEST_XDIST_AUTO_NUM_WORKERS=4`) — already
  the ship-a-pr guidance.

## Next conversational boundary

Start **#962** (`slides polish` → `report`/`task`/`accept`/`autopilot`,
per the #961 template and the placement decision on the issue; level
prompts already live in `src/clm/notebooks/polish_levels/*.md`). Then
**#963** with the `language-coverage` rename baked into the same PR.
#981 opportunistically. Do not re-litigate placement, the rename, or the
shaping rule.
