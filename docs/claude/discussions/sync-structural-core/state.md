---
status: active
owner: maintainers
updated: 2026-11-11
review-by: 2027-05-11
---

# State: sync structural core (can pairing and order be fixed at the root?)

The finding, the proposed core, the measurements, the laws, the path and the
decisions live in `docs/claude/sync-structural-core-investigation.md` (§9 for
the path). The prototype and its checks are `scripts/sync_model.py`,
`sync_model_probe.py`, `sync_model_laws.py` and `sync_model_passes.py`; the
laws on the engine are `tests/slides/sync_laws.py` and `scripts/sync_laws.py`.
This file keeps only the conversational position.

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
  with your revised ordering"). The order the agent had proposed on resuming:
  the laws as tests against the engine first; then extend the prototype to
  partial progress and tags before any ledger or engine change; a go/no-go
  from the owner before the engine is switched over. The doc's §9 has it as
  five steps.
- **D2, keep today's width of the order question** (owner, S23: "D2: Keep
  width"). The agent's argument: the switch-over then changes the mechanism
  and not the policy, and the probe's oracle stays valid.
- **D3, ask** when each half edited a different one of several byte-identical
  recorded cells (owner, S23: "D3: Ask"). The agent's argument: which copy a
  half changed cannot be read from the files and the readings give different
  results, and the doc already asked in the neighbouring removal case.
- **No design-note amendment, no changelog fragment, no issue for the
  proposal** (agent's call, both sessions): no engine code has changed.

## Shipped

- PR #1217 (S20): the investigation doc, the prototype, the issue
  classification. PRs #1218, #1220, #1221: the S20 saves. Issue #1219 (open):
  the transcript-cleaner defect.
- PR #1224 (S23, merged 2026-11-10): step 1. The oracle-free laws as a ratchet
  against the engine (`tests/slides/test_sync_laws.py`, slow tier), the D1 to
  D3 decisions in the doc, D3 in the prototype (`conflict_copies`).
- PR #1225 (S23, merged 2026-11-10): step 2. The prototype's pass with open
  questions, tags, slide-hood and an id gained on one half, with a check
  across passes (`scripts/sync_model_passes.py`).
- PR #1226 (S23, merged 2026-11-10): the first save of S23.

## What the two steps found (reported to the owner, not answered yet)

The agent's closing message of S23 (2026-11-10, in the transcript) reported
these, with the two open items below as "Open, for you". The owner's next
input, a day later, was a session rename and the request for this save; the
owner has said nothing on the findings or on step 3.

- **The engine violates the one-sided law far more often than the probe
  suggested.** On every small deck, with every question refused, the law holds
  for 1,344 of 16,744 cases. 9,200 are the answerless `verify_cold` of a
  one-sided un-id'd insertion, which is the engine's design (the author gives
  the cell a `slide_id`), 5,816 are placement questions, 384 are livelocks. No
  violation is silent. The probe's 18% counted an answered question as
  convergence and has few insertions.
- **A pass with open questions makes progress only around questions about
  id'd cells.** The first design (hold back what each question names) broke a
  law on 60,036 of 306,451 small cases and four repairs stopped at 6,714.
  The rule that holds: a question about an id'd cell (`translate_new`,
  `remove_localized_side`) holds that cell; any other open question holds the
  whole part. 29% of the passes that left a question open still made progress.
- **The three slide-hood issues' shapes go through the prototype** (#1092,
  #1187, #1216) as edits of one half, without a question.

## Open

- **The go/no-go before the switch-over** (step 5), on the results of steps 2
  and 4.
- **Step 3, recording the cell sequence in the ledger.** It is additive, but
  it is the first change to a file that course repositories commit, so the
  agent did not start it. It wants the owner's word, and with it **D4** (the
  migration: re-record from a clean report, and a deck with pending items),
  which needs a count of decks with pending items in the course repositories.
- **How small a "part" can be.** In the prototype the unit an open question
  holds is everything given to one merge. Whether a deck can be cut into
  slide groups that do not affect each other is not modelled.
- **D5** (Lean), no decision needed before the core is in production.

The next conversational boundary is the owner's answer to the closing report:
the word on step 3 (with D4), and whether the size of a "part" should be
looked at first. A resuming agent need not present the findings again in
full; they are in the transcript's last agent block and in the doc.

Also open, unrelated to sync: `scripts/clean_transcript.py` cleans
`api-request-blob` rows as dialogue (#1219). Until it is fixed, a save of a
Claude Code session needs the staging step the register describes under S20.

## Known weak points

- The prototype is abstract: no text layer, no `lang`, no companions, no fork
  or unify. Tags and id stamping are modelled as far as the doc's §4.9 goes
  (not: both halves stamping different ids, a cell stamped and edited at once).
- The identity question was answered by a perfect reader on the probe, and by
  fixed rules in the check across passes.
- An identity answer is not recorded on its own; it lasts only when the pass
  that got it completes. Recording one would need what a `slide_id` is.
- The probe and the law cases are generated shapes. The S14 history scan found
  0 field instances of a shape the probe produced freely, so the shadow run on
  real history (step 4) is what measures the benefit to an author.
- The laws across passes compare order only for recorded cells; cells the two
  halves added may end in another order (63,146 of 1,016,127 cases).
- The issue classification is a subagent's reading of issue and PR text.

## Not in the transcript

- S20: the agent's short progress messages between the opening and the
  report, the subagent's report, and a `/context` output.
- S23: the work after the owner's one message is solo. The transcript has its
  progress notes; the reasoning is in the doc's §4.6, §4.8 (items 6 to 10) and
  §4.9, and in the two PR descriptions.
- S23, after the closing report: the session's context was compacted once (the
  harness's summary is not dialogue and is absent), and the owner renamed the
  session "Sync engine improvements" before asking for the second save. The
  rename is a local command and leaves no line in the transcript.

## Agent corrections recorded in the transcripts, or made on the way

- S20: a plain diff3 first; two prototype rules found wrong by the probe and
  one by the exhaustive check; a conservation check that counted its own
  labels twice; "about 400 lines" and "about half" corrected before the merge;
  no proof attempted; a save that recorded "no issue exists" went stale when
  the issue was filed.
- S23: the doc's §4.6 said a member with an open question keeps its state and
  the rest goes on. That was the agent's design, and the check across passes
  refuted it for every question about un-id'd cells and about order. One
  kind of difference was accepted as an exception (the order of cells the
  two halves added, as in the single-pass law). Three others that looked
  like tie-break effects were real dependences on a neighbouring cell and
  were fixed (the doc's §4.8, items 8 to 10).
- S23: the agent recommended D1 on the probe's "18% of one-sided cases fail".
  Step 1 measured 92% on small decks, most of it the engine's designed manual
  exit. The recommendation stands, but the number it rested on described the
  probe, not the engine.
- S23: a formatter run over all of `scripts/` reformatted two unrelated files;
  reverted before the commit. A squash by soft reset would have reverted
  another PR that had landed meanwhile; the branch was rebased first.
