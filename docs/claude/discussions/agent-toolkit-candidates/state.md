---
status: active
owner: maintainers
updated: 2026-09-24
review-by: 2027-03-24
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

S7 (2026-09-20→24, opencode session): first full conversation-depth
resume. The owner picked **#965** from the Tier-2 set and scoped it:
**land the `--json` half first** — the `--json` flags are independent of
drift semantics, while `report`/accept need #907's design. Landed as
**PR #988** (merged 2026-09-21); issue #965 stays open for the #907-gated
half (split-landing comment on the issue).

## Settled

- **The contract** (`clm info agent-tasks`, kit `clm.slides.agent_task` +
  `clm.cli._default_verb_group`, #959): envelope identity, freshness-token
  echo, validator registry (`harvest-bullets`, `sync-decisions`,
  `harvest-align`, `harvest-compare`, `translate-deck`, `polish-notes`,
  `coverage-verdicts` + `assign-ids-titles`), load-bearing exit codes,
  `<x>-agents` topics, autopilot quarantine.
- **Three tiers + order** (owner-confirmed): kit → #960 → #961/#962/#963 →
  Tier 2 (#965–#967) → Tier 3 (#968–#969). **Not converting**: validate,
  course gate/orphans/renumber/decks, cache explain, cassette,
  kernel-triage, query, export outline, run, and the infrastructure groups.
- **#960–#963 landed** (PRs #973–#978, #980, #984, #985): harvest history
  verbs removed without shims; translate, polish, coverage, assign-ids are
  default-verb groups; `coverage-report` renamed `language-coverage`; the
  coverage prompts/verdict types live at the model-free
  `infrastructure/llm/prompts.py` seam.
- **#965 split-landing decision (S7, owner via question tool)**: the
  `--json` surface half (PR #988) is independent of #907 and landed first;
  the `recordings report` verb, the acknowledgement verb, and the
  `recordings-agents` info topic wait for #907's drift-semantics design.
  Do not re-litigate the split.
- **#965 surface contract (PR #988)**: `--json` on `status`,
  `jobs list/cancel/fail/poll/wait/prune`, `backends`, `check`.
  Full-fidelity job rows (full ids, untruncated messages, full paths);
  `jobs poll --json` is JSON Lines (one compact doc per tick, `--watch`
  honored in both modes); `jobs wait --json` emits one `{outcome, job}`
  document; exit codes stay load-bearing in JSON mode (0/1/2, including
  an already-failed wait target); failure diagnostics go to stderr (the
  exit code is the machine signal); `jobs prune --json` requires `--yes`
  (the interactive prompt's input echo cannot be kept off stdout).
  Documented in `clm info commands`.
- **Surface-shaping rule (S5, recorded on #970)**: conversions keep the
  feature's name and home, gain the standard verb set, and absorb or rename
  their report-ish siblings; superseded commands retire without shims; no
  big-bang regrouping of `slides`. The sync `apply` vs harvest/translate
  `accept` wording stays as the one accepted dialect bend.

## Open (owner decisions)

- None newly open. #965's remainder and #907 are design-gated, not
  owner-gated: the next owner call is only *when* to take the #907 design
  work (vs #966/#967/#981).

## Deferred / revisit conditions

- MCP mirrors: `align report` (S3), `translate task` (S5), `polish task` /
  `coverage report` / `assign-ids accept` (S6) — add only if callers need
  them over MCP.
- Discarded `manual_review` match candidates and partial alignment patches
  (S3) — possible follow-ups, not planned.
- #981 (`suggest-sync` + MCP `slides_suggest_sync`): retire if the
  pre-split bilingual format is dead, else unhide + document —
  investigation steps on the issue; outside the tier order.

## Known weak points

- `tests/test_architecture_contracts.py` (cross-module private-import
  guard) is NOT in the pre-push smoke tier — run it explicitly when
  touching cross-module imports (documented in
  `docs/developer-guide/testing.md`).
- Full-suite xdist runs flake a whole *family* under contention, not just
  `TestMockWorkerBasics` (S7 also saw sqlite-backend bookkeeping races,
  an operation-timing assert, drawio/notebook worker integration tests,
  and one `INTERNALERROR> KeyError: WorkerController` — all pass
  standalone, none in touched areas). Standing guidance: cap workers
  (`PYTEST_XDIST_AUTO_NUM_WORKERS=4`), verify suspects standalone before
  blaming a diff.
- `assign-ids accept`'s framing (`--report-refusals --context --json`)
  carries no fingerprints; freshness is the body echo + id-less
  precondition (recorded in `sync-agents` info topic). Adequate for the
  current loop; revisit only if line-drift false accepts ever surface.

## Next conversational boundary

**#965's surface half is done and merged (PR #988).** Next: **#966**,
**#967** (check their issue texts for current shape), the **#907 design
work** (unlocks #965's report/accept verbs), or deferred **#981**
opportunistically. Do not re-litigate the shaping rule, the #962
placement, the #963 rename, the Tier-1 designs, the #965 split-landing
decision, or the JSON surface contracts. The implementation rhythm that
worked for #962/#963/#965: resume at outcomes depth, confirm any recorded
"confirm once" notes, resolve-issue workflow (test-first), adversarial
review above ~150 non-test lines, ship-a-pr with CI-gated auto-merge.
