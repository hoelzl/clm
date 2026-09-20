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

S6 (2026-09-20, opencode session): resumed the thread at outcomes depth,
confirmed the #962 placement once at implementation start (as S5 required),
landed **#962** (PR #984, merged) and **#963** (PR #985, merged; both
issues closed). **Tier 1 is complete: #959–#963 all landed.**

## Settled

- **The contract** (`clm info agent-tasks`, kit `clm.slides.agent_task` +
  `clm.cli._default_verb_group`, #959): envelope identity, freshness-token
  echo, validator registry (`harvest-bullets`, `sync-decisions`,
  `harvest-align`, `harvest-compare`, `translate-deck`, and since #962
  `polish-notes`, since #963 `coverage-verdicts` + `assign-ids-titles`),
  load-bearing exit codes, `<x>-agents` topics, autopilot quarantine.
- **Three tiers + order** (owner-confirmed): kit → #960 → #961/#962/#963 →
  Tier 2 (#965–#967) → Tier 3 (#968–#969). **Not converting**: validate,
  course gate/orphans/renumber/decks, cache explain, cassette,
  kernel-triage, query, export outline, run, and the infrastructure groups.
- **#960 landed** (PRs #973–#978): history verbs removed without shims,
  `export-at-rev`, MCP conversion, companion-aware readers.
- **#961 landed** (PR #980): `clm slides translate` is a default-verb group
  — `report`/`task`/`accept`/`autopilot` per the established template.
- **#962 landed** (PR #984): `clm slides polish` is a default-verb group —
  `report` (bare, read-only; polishable-notes counts + pointer, exit 1),
  `task` (per-slide notes rows with `id:`/`pos:` handles, both language
  sides as context, the level prompt as `instructions`, source/twin
  fingerprints), `accept` (shape + freshness + coverage → the ordinary
  `update_narrative` splice, atomically, byte-identical to autopilot,
  pinned by a differential test; sync ledger deliberately untouched so the
  next sync report frames the twin's update), `autopilot` (in-process LLM,
  key-gated; `verbatim` stays key-free). Placement (own verb pair, NOT
  `harvest task --kind polish`) was confirmed once at implementation start
  per the S5 note — do not re-litigate.
- **#963 landed** (PR #985): `clm slides coverage` is a default-verb group
  — `report` (framed judgment: pending pairs with bullets + voiceover +
  content-hash freshness tokens, cached verdicts with gaps surfaced as
  findings, duplicate-content pairs framed once as `duplicate`, `--dump`
  stays, directories frame every deck), `accept` (validator
  `coverage-verdicts`; banks into the same `CoverageCache` rows the Ollama
  judge wrote — the cache is the trust store), `autopilot` (the in-process
  judge). `clm slides assign-ids` became a hidden group: bare/`run` keeps
  minting, new `accept` (validator `assign-ids-titles`) answers the
  unchanged `--report-refusals --context --json` worklist with
  `{file, line, title, body}` rows (body echo = freshness; engine
  slugifier; ordered-sequence pair-consistency guard; atomic stamps);
  `--llm-suggest` removed without a shim. `coverage-report` renamed to
  **`language-coverage`** (no alias), as ratified on #963. Prompts, prompt
  versions, and `CoverageVerdict` moved to the model-free
  `infrastructure/llm/prompts.py` seam (re-exported); report/accept never
  import the Ollama client (AST-pinned, #963 acceptance).
- **Surface-shaping rule (S5, recorded on #970)**: conversions keep the
  feature's name and home, gain the standard verb set, and absorb or rename
  their report-ish siblings; superseded commands retire without shims; no
  big-bang regrouping of `slides`. The sync `apply` vs harvest/translate
  `accept` wording stays as the one accepted dialect bend.

## Open (owner decisions)

- #965: wait for #907 drift semantics or land the `--json` half first
  (unchanged).

## Deferred / revisit conditions

- MCP mirrors: `align report` (from S3), `translate task` (from S5), and
  now implicitly `polish task` / `coverage report` / `assign-ids accept`
  (from S6) — add only if callers need them over MCP.
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
  test_mock_worker_processes_job` / `test_mock_worker_stops_and_marks_dead`
  under contention (passes standalone); cap workers
  (`PYTEST_XDIST_AUTO_NUM_WORKERS=4`) — already the ship-a-pr guidance.
  Recurred in both S6 implementation sessions; still unrelated to any
  slides/CLI diff.
- `assign-ids accept`'s framing (`--report-refusals --context --json`)
  carries no fingerprints; freshness is the body echo + id-less
  precondition (recorded in `sync-agents` info topic). Adequate for the
  current loop; revisit only if line-drift false accepts ever surface.

## Next conversational boundary

**Tier 1 is done.** Start Tier 2 when the owner picks it up: **#965**
(exists-first decisions for slide deletions/moves — open question above),
**#966**, **#967** (check their issue texts for current shape), or the
deferred **#981** opportunistically. Do not re-litigate the shaping rule,
the #962 placement, the #963 rename, or the Tier-1 designs. The
implementation rhythm that worked for #962/#963: resume at outcomes depth,
confirm any recorded "confirm once" notes, resolve-issue workflow
(test-first), adversarial review above ~150 non-test lines, ship-a-pr with
CI-gated auto-merge.
