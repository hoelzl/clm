---
status: active
owner: maintainers
updated: 2026-11-13
review-by: 2027-05-13
---

# State: sync structural core (can pairing and order be fixed at the root?)

The finding, the proposed core, the measurements, the laws, the path and the
decisions live in `docs/claude/sync-structural-core-investigation.md` (§9 for
the path). The prototype and its checks are `scripts/sync_model.py`,
`sync_model_probe.py`, `sync_model_laws.py` and `sync_model_passes.py`; the
laws on the engine are `tests/slides/sync_laws.py` and `scripts/sync_laws.py`;
the shadow on real history is `scripts/sync_shadow.py`. This file keeps only
the conversational position.

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

- **Land the investigation** (owner, S20): PR #1217.
- **D1, pursue it, in a revised order** (owner, S23, 2026-11-10: "D1: I agree
  with your revised ordering"). The laws as tests against the engine first;
  then extend the prototype before any ledger or engine change; a go/no-go
  from the owner before the engine is switched over.
- **The shadow before the ledger** (owner, S23, 2026-11-13). The owner wrote
  "Let's start with step 3, unless there are any issues we need to resolve
  first." The agent raised one issue: the shadow run does not need the ledger
  change, and recording first would put a field nothing reads into every
  committed course ledger (PythonCourses 575, CppCourses 340). That field
  would have to be removed again after a no-go, and its format would be fixed
  before the shadow showed what the core needs. The owner chose "Shadow first
  (Recommended)" in the question dialog. The doc's §9 now has the shadow as
  step 3 and the ledger as step 4.
- **D2, keep today's width of the order question** (owner, S23: "D2: Keep
  width"). The agent's argument: the switch-over then changes the mechanism
  and not the policy, and the probe's oracle stays valid.
- **D3, ask** when each half edited a different one of several byte-identical
  recorded cells (owner, S23: "D3: Ask"). The agent's argument: which copy a
  half changed cannot be read from the files and the readings give different
  results, and the doc already asked in the neighbouring removal case.
- **No design-note amendment, no changelog fragment, no issue for the
  proposal** (agent's call, all sessions): no engine code has changed.

## Shipped

- PR #1217 (S20): the investigation doc, the prototype, the issue
  classification. PRs #1218, #1220, #1221: the S20 saves. Issue #1219 (open):
  the transcript-cleaner defect.
- PR #1224 (S23, 2026-11-10): step 1, the oracle-free laws as a ratchet against
  the engine, and D1 to D3 recorded.
- PR #1225 (S23, 2026-11-10): step 2, the prototype's pass with open
  questions, tags, slide-hood and an id gained on one half.
- PRs #1226, #1227: the first two saves of S23.
- PR #1230 (S23, merged 2026-11-13): step 3, `scripts/sync_shadow.py`, the
  prototype beside the engine on real history. It also adds fork and unify to
  the prototype, takes the kinds from the cells, gives the doc its §2.6 and
  the reordered §9, and records the count D4 waited for.
- PR #1232 (S23, merged 2026-11-13): a cell's file (deck or voiceover
  companion) as a merged aspect of it, the one gap the shadow had left open.

## What step 3 found (in the closing message of S23's third part; not answered yet)

- **On real history the prototype ends as the author did in every replay.**
  Each change between two in-sync committed states was replayed as an edit of
  one half: 3,849 replays in PythonCourses and 2,734 in CppCourses. The engine
  ends as the author did in 3,577 and 2,279. It stops in 257 and 453 (it asks
  a structural question, stalls on `verify_cold`, or livelocks). It ends
  "clean" with companion narration out of order in 15 (#1118).
- **The shadow found three gaps in the prototype, all closed.** A fork on one
  half (a shared cell turned per-language) was copied without a word, 18
  replays; it is now a question, and so is the reverse. The identity question
  and the inferences only knew the probe's kinds. Narration moved between a
  deck and its companion read as removed plus new (42 replays, 193 cells).
  After each change the probe, both law checks and the check across passes
  gave the same numbers as before.
- **D4's count:** 45 of 1,074 course decks have pending items (PythonCourses 21
  of 708, CppCourses 24 of 366).

## Open

- **The go/no-go before the switch-over** (step 5), on the results of steps 2
  and 3. This is the owner's.
- **Step 4, recording the cell sequence in the ledger,** in the form the
  shadow showed: one sequence over both files, each cell's id or none, kind,
  file, tags and content fingerprint. It is the first change to files that
  course repositories commit. The agent's reading of the option the owner
  chose ("Step 3 then records exactly what the shadow used, after your go and
  before the switch-over") is that it waits for the go. **D4** comes with it;
  the agent proposes writing the sequence on each deck's first clean pass and
  finishing the pending decks first.
- **How small a "part" can be** (the unit an open question holds). Not
  modelled; unchanged since S23's first part.
- **D5** (Lean), no decision needed before the core is in production.

The next conversational boundary is the owner's reading of the shadow's
results, and the go/no-go.

Also open, unrelated to sync: `scripts/clean_transcript.py` cleans
`api-request-blob` rows as dialogue (#1219). Until it is fixed, a save of a
Claude Code session needs the staging step the register describes under S20.

## Known weak points

- The prototype has no text layer: it decides which cell goes where, not what
  a translation says, and it renders cells, not files. A localized cell's
  header attributes (such as `for_slide`) are part of its text there.
- The shadow replays one-sided edits only. Edits an author made on both halves
  before syncing stay with the probe and the laws. The shadow does not compare
  the preamble, the order of a cell's tags, or the text of localized cells.
- Fork and unify hold the whole part: the check across passes does not generate
  them, so it cannot show that a narrower hold is safe.
- The identity question was answered by a perfect reader on the probe, and by
  fixed rules in the check across passes. The shadow's judge refuses it, and
  the prototype never asked it on real history.
- An identity answer is not recorded on its own; it lasts only when the pass
  that got it completes.
- The laws across passes compare order only for recorded cells (63,146 of
  1,016,127 cases end with added cells in another order).
- The issue classification is a subagent's reading of issue and PR text.

## Not in the transcript

- S20: the agent's short progress messages between the opening and the
  report, the subagent's report, and a `/context` output.
- S23: the work after each owner message is solo. The transcript has its
  progress notes; the reasoning is in the doc's §2.6, §4.6, §4.8 (items 6 to
  10), §4.9 and §9, and in the PR descriptions.
- S23: the context was compacted once, the session was renamed "Sync engine
  improvements", and before the third part the owner ran `/context`. None of
  these leaves dialogue. The third part ran on another model (Claude Opus 5.5)
  in the same session.

## Agent corrections recorded in the transcripts, or made on the way

- S20: a plain diff3 first; two prototype rules found wrong by the probe and
  one by the exhaustive check; a conservation check that counted its own
  labels twice; "about 400 lines" and "about half" corrected before the merge;
  no proof attempted; a save that recorded "no issue exists" went stale when
  the issue was filed.
- S23: the doc's §4.6 said a member with an open question keeps its state and
  the rest goes on. The check across passes refuted that for every question
  about un-id'd cells and about order (the doc's §4.8, items 6 to 10).
- S23: the agent recommended D1 on the probe's "18% of one-sided cases fail".
  Step 1 measured 92% on small decks, most of it the engine's designed manual
  exit. The recommendation stands, but the number described the probe.
- S23, third part: four failures that first looked like the engine's were the
  shadow's own (companions written to `voiceover/`, the title macro's key,
  merge commits listing no files, the probe's apply helper passing `#` to the
  verify gate of a C++ deck). The agent also first called two CppCourses
  replays worse than the rest ("the engine changed the half the author
  edited"); tracing showed the engine completing a pending id stamp, which is
  benign. The doc's §2.6 has the corrected readings.
- S23, third part: three claims in the first draft of §2.6 went beyond what was
  checked (that all part moves were `voiceover extract` work, why out-of-sync
  states were refused, that every harness defect had looked like an engine
  failure). They were narrowed before the commit.
- S23: a formatter run over all of `scripts/` reformatted two unrelated files;
  reverted before the commit. A squash by soft reset would have reverted
  another PR that had landed meanwhile; the branch was rebased first. A branch
  for the part change started from a stale `origin/master` and was reset onto
  the fetched one before any commit.
