---
status: active
owner: maintainers
updated: 2026-11-10
review-by: 2027-05-10
---

# State: sync structural core (can pairing and order be fixed at the root?)

The finding, the proposed core, the measurements, the laws and the limits live
in `docs/claude/sync-structural-core-investigation.md`. The prototype and its
checks are `scripts/sync_model.py`, `scripts/sync_model_probe.py` and
`scripts/sync_model_laws.py`. This file keeps only the conversational position.

## The owner's question (S20, 2026-11-10)

- The owner asked for a look at the fundamentals rather than more special
  cases: "My concern is that recent fixes have increased the number of special
  cases handled, and that one of the impressions I got from looking at the
  patches was, that various code paths had diverged in the way they handle
  certain situations." And, as a second question, whether the engine's
  operation can be formalized and properties proved "(say in lean or another
  framework), if that is feasible at all".
- This follows the same owner's remark in `sync-pool-pairing` (S14) that
  telling which half moved without ids is "whack-a-mole with more and more
  complex rules". That thread's accepted residuals and its re-open trigger
  still stand. This thread does not re-open ids for shared cells (#1130 step 3,
  withdrawn): the proposal needs none.

## Settled

- **Land the investigation** (owner, after reading the report): PR #1217,
  merged 2026-11-10. Nothing else is decided.
- **No engine change, no design-note amendment, no changelog fragment, no
  issue for the proposal** (agent's call, stated in the landing report): the
  proposal is undecided and nothing user-visible changed.
- **Save the discussion, and file the cleaner issue** (owner). The save was
  asked for twice, after the landing and again at the end. The issue was filed
  only once the owner wrote "with my explicit permission".

## Shipped

- PR #1217 (merged 2026-11-10): the investigation doc, the three scripts, and
  the issue classification under `docs/claude/analysis/sync-structural-core/`.
- PR #1218 (merged): the first S20 save, and the doc's §4.8 (what was tried
  and dropped while building the prototype), added by the knowledge audit.
- Issue #1219 (open): the transcript-cleaner defect.
- PR #1220 (merged): the two lines of the first save that #1219 made false.
- The second save extends the transcript to the end of the session and brings
  this file and the register up to date. Its knowledge audit found nothing new
  for the repository.

## Open: the five decisions (the doc's §9)

- **D1.** Pursue the replacement core, or keep fixing the current structure.
- **D2.** How wide an order question is: whenever both halves reordered within
  one slide (today's doctrine, and what the probe's oracle expects), or only
  when the two reorders are incompatible.
- **D3.** Two byte-identical recorded cells where each half changed a different
  one: merge both changes (the prototype) or ask (the conservation gate's
  stance).
- **D4.** The ledger migration to a recorded cell sequence.
- **D5.** Whether to prove the laws in Lean once the core is in production.

The next conversational boundary is the owner's answer to D1. If it is yes, the
doc's suggested first step needs no further decision: the laws as tests against
the current engine, where the one-sided law fails today.

Also open, unrelated to sync: `scripts/clean_transcript.py` cleans
`api-request-blob` rows as dialogue and files a subagent hand-back under the
owner (#1219). Until it is fixed, a save of a Claude Code session needs the
staging step the register describes under S20. The worktree
`sync-structural-core` is left in place with all four branches merged.

## Known weak points of the proposal

- The prototype works on an abstract view of a deck (id or none, kind,
  content). Tags, `lang`, companions, id stamping, fork and unify are not
  modelled, and the probe does not exercise them either.
- Partial progress (some questions unanswered) is designed in the doc's §4.6
  and not prototyped. The claims that it removes "the engine reads its own
  write" and "recording banks more than was decided" rest on that design.
- The identity question was answered by a perfect reader in the measurement.
- The probe was built around the current engine's failures. The exhaustive law
  check already found two prototype defects the probe could not.
- Under swapped halves 128,848 of 500,395 small merges differ in order. The
  German-first tie-break explains such differences; they were not classified.
- The issue classification is a subagent's reading of issue and PR text, not
  re-verified row by row.

## Not in the transcript

- The agent's short progress messages between the opening and the report are
  absent from the raw session source. What they carried (how the prototype
  changed after each measurement) is the doc's §4.8 and the corrections below.
- The subagent's report was removed from the transcript as harness-delivered
  text. Its substance is the committed issue classification.
- The output of a `/context` command the owner ran before asking for the
  issue: harness output, dropped by the cleaner.
- The second save itself, after its first step: this file covers it.

## Agent corrections recorded in the transcript, or made on the way

- The first prototype was a plain diff3 over content. It cannot carry an edit
  along with a move, which is the probe's main family (doc §4.8, item 1).
- Two further prototype rules were wrong and found by the probe (stale
  pairwise relations for new cells; a silent "removed" reading), and one by
  the exhaustive law check (a guess among byte-identical cells). All are in the
  doc's §4.8 and §5.
- The first conservation check counted nearly every merge as a violation: the
  script labelled the result's labels a second time. Caught before reporting.
  The 25,132 an interim message mentions is the next, real finding (the
  identical-cells removal case), which the landing report explains.
- The first report said "about 400 lines" (the prototype is about 450) and the
  first doc draft said "about half" of the issues without an un-id'd cell fall
  under row-by-row execution (it is about a third). Both corrected before the
  merge.
- A proof was not attempted: no proof assistant is installed on the machine.
  The doc's §6 is an assessment, not a result.
- The first save recorded "no issue exists" for the cleaner defect. Once the
  issue was filed, that was false in two places, and it took PR #1220 to
  correct them. A save that records a blocked action goes stale the moment
  the action happens; check for it before closing the session.
- The issue draft said the second copy of the subagent's report came through
  a second queue row. It came through the report's `api-request-blob` copy.
  Corrected before filing.
