---
status: active
owner: maintainers
updated: 2026-11-14
review-by: 2027-05-14
---

# State: sync structural core (can pairing and order be fixed at the root?)

The finding, the measurements and the path up to the go are in
`docs/claude/sync-structural-core-investigation.md` (§9). The switch-over is
designed, and its progress recorded, in
`docs/claude/design/sync-sequence-core.md` (§9 has each phase's results).
This file keeps only the conversational position.

## The owner's question (S20, 2026-11-10)

- The owner asked for a look at the fundamentals rather than more special
  cases: "My concern is that recent fixes have increased the number of special
  cases handled, and that one of the impressions I got from looking at the
  patches was, that various code paths had diverged in the way they handle
  certain situations." And whether the engine's operation can be formalized
  and properties proved "(say in lean or another framework), if that is
  feasible at all".
- This thread does not re-open ids for shared cells (#1130 step 3, withdrawn):
  the core needs none.

## Settled

- **D1, pursue it, in a revised order** (owner, S23: "D1: I agree with your
  revised ordering"); **D2, keep the width of the order question**; **D3, ask**
  when each half edited a different one of several identical cells.
- **The shadow before the ledger** (owner, S23 third part, the question
  dialog): the shadow needed no ledger change.
- **The go for the switch-over** (owner, S23 fourth part, 2026-11-13: "Let's
  go", on the shadow's results). **D4** settled with it: no migration step;
  each deck's sequence is written on its next record or clean pass.
- **The design note** (owner: "Great plan. Please continue with the design
  note."), accepted with **O1 to O5 as recommended** (owner: "01: By place
  seems fine / 02: Let's go with the whole deck / 03: Ask it / 04: Two
  weeks/50 sync passes sounds good / 05: Agree with your recommendation").
  So: index handles `seq:<n>` / `new:<lang>:<file>:<n>`; an open question
  holds the whole deck; `identify` is asked; dogfood two weeks or 50 sync
  passes, whichever comes later; positional ledger entries kept through
  phases C and D.
- **Whitespace gets no rule of its own** (owner, phase B: "I would not greatly
  complicate the algorithm for changes that are pure whitespace"). A twin
  rewritten by `translate_edit` takes the edited cell's blank lines only when
  the edit changed them.
- **The owner rule is fitted on history** (phase B, agent, measured with the
  shadow): an owner change propagates in place, except an `fp:` `vo_anchor`
  the halves recorded differently. Two alternatives were measured and dropped
  (design note §5). Do not change it without re-running the shadow.

## Shipped

- S20/S23: PR #1217 (investigation), #1224 (laws ratchet), #1225 (prototype
  pass), #1230 and #1232 (shadow), plus the saves #1218, #1220, #1221, #1226,
  #1227.
- S23 fourth part (2026-11-13/14): PR #1234 (step 4, the ledger's cell
  sequence), #1237 (the design note), #1239 (O1 to O5), #1242 (phase A, the
  core as `clm.slides.doc_core`), #1243 and #1244 (phase B, renderer and text
  layer, byte-exact on real history). PR #1246 (phase C code,
  `CLM_SYNC_CORE=sequence`) was open with auto-merge armed and CI running when
  this was saved; check `gh pr view 1246 --json state`.

## Where phase C stands (after the owner's last message, solo work)

- `report` / `apply` / the MCP report tool run the sequence core when
  `CLM_SYNC_CORE=sequence` is set. Results against the positional engine:
  laws (CI scope) all hold against 2,658 violations; sweep 2,112/2,112 against
  2,096; probe 67,871/67,871 against 50,031 (1,208 silent failures); no case
  worse. Numbers and the repo check are in the design note's §9.
- Found on the way, all fixed in #1246 and written up in the design note
  (§7 and §8.2 "as built"):
  - no course deck has a recorded sequence yet, so a deck without one takes
    its current halves, else its `HEAD` commit, as the base;
  - "in sync" said nothing about translations: trust now comes from the
    positional ledger's member body fingerprints (the first version silently
    blessed a pending translation);
  - questions after `identify` were invisible to `report`: `apply --dry-run
    --decisions` lists them (400 probe cases had stalled);
  - a translated README never had a sequence (differing preambles counted
    as out of sync).
- The agent's closing report to the owner described this and asked nothing;
  the owner's next message was this save.

## Open

- **The dogfood period** (O4): the owner's course work with
  `CLM_SYNC_CORE=sequence`, two weeks or 50 sync passes. It has not started.
  Before it, 15 course decks that are out of sync now and at `HEAD` need
  finishing with the positional engine (D4).
- **Four PythonCourses decks whose companions list narration in different
  orders** are refused by the sequence core (clean on the positional engine).
  The agent left the choice to the owner: reorder one companion by hand once,
  or change the core's order rule for companions (a core change, through the
  four checks). Not answered.
- **Phase D** (the default flips, schema 6 wired, info topics and migration)
  and **phase E** (deletion) follow the dogfood period.
- **D5** (Lean): no decision needed before the core is in production.

The next conversational boundary is the owner starting (or reporting from)
the dogfood period, or answering the companion-order choice.

Also open, unrelated to sync: `scripts/clean_transcript.py` cleans
`api-request-blob` rows as dialogue (#1219). Saves of this Claude Code session
filter a temporary copy first (the register says how).

## Known weak points

- The probe and the laws answer every question as a perfect reader would;
  real `identify` answers come from agents reading `new:` handles.
- Two-sided real edits are rare in history and untested there; the dogfood
  period is the field test.
- A deck without a recorded sequence trusts the positional ledger's body
  fingerprints; header-only changes since that record are not seen as text
  edits.
- The text layer's rules were fitted on two repositories' history.

## Agent corrections recorded in the transcripts, or made on the way

- S20: a plain diff3 first; two prototype rules found wrong by the probe and
  one by the exhaustive check; a double-counting conservation check; size
  claims corrected before merge; no proof attempted.
- S23: §4.6's "a member with an open question keeps its state" was refuted by
  the check across passes. The recommendation for D1 rested on a probe number
  that described the probe, not small decks.
- S23 third part: four apparent engine failures were the shadow's own
  harness; three over-broad claims in §2.6 were narrowed before the commit.
- S23 fourth part, phase B: several owner and separator rules were tried and
  measured worse on the shadow (vo_anchor treated as shared, always taking the
  leader's separator, "never propagate" with drop-and-append `for_slide`,
  which gave 80 differences); the agent reverted to the best-measured rule.
- S23 fourth part, phase C: the design's §8.2 rule ("the current halves are
  the base when they are in sync") would have blessed pending translations;
  the repo check showed it, and trust was added. The first trust check
  compared whole-cell fingerprints and flagged decks the positional engine
  calls clean; bodies are what it trusts.

## Not in the transcript

- The solo work between owner messages is reduced to progress notes and tool
  stubs; its reasoning is in the design note, the investigation's §2.6/§2.7,
  and the PR descriptions.
- The session was compacted several times; the summaries are harness text and
  absent from the transcript.
