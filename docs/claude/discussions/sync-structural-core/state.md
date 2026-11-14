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
  core as `clm.slides.doc_core`), #1243 and #1244 (phase B), #1246 (phase C,
  `CLM_SYNC_CORE=sequence`, opt-in), save #1247.
- S23 fifth part (2026-11-14): **clm 1.40.0** (PR #1248, released by the
  workflow; the owner's global `clm` is on it) and the **nightly
  `sync-probe` job** (PR #1249): the sequence core with
  `--require-converged`, the positional engine gated against the latest
  release tag. Measured on a hosted runner: about 6 minutes per engine (all
  67,871 cases converged in 315 s; positional vs v1.40.0: 0 better, 0 worse).
- Course repos, same day (owner-reviewed, no auto-merge): the hand repair of
  every deck the checks flagged (PythonCourses #424, CppCourses #234), the
  pending work after it (PythonCourses #425,
  CppCourses #235), the `ws_` decks deleted or
  moved into opt-in topics with a `stack_sk` starter kit (CppCourses #236),
  the `functions_very_short` solution cell made per-language
  (PythonCourses #426), stale planning docs archived (CppCourses #237), the
  greeting's spelling (PythonCourses #427, CppCourses #238, CSharpCourses #12)
  and deck titles restored to "Herzlich Willkommen" (PythonCourses #428,
  CppCourses #239). All merged.

## Settled in the fifth part

- **The four companion-order decks** (module_410) were fixed by hand: one DE
  narration cell was out of place in each; the DE companion now follows EN.
  No core change. (The owner asked "Can we fix the decks by hand?")
- **No clm issue for `ws_` files** (agent recommendation, owner followed it by
  having the decks deleted or moved into topics).
- **The probe runs nightly, not on PRs** (owner: "A nightly job sounds right,
  it is much too slow for CI"). That rested on the agent's estimate of 35 to
  70 minutes; the measured runtime is about 6 minutes per engine, so a
  path-filtered PR job is feasible. The agent offered it; not answered.
- **Greeting spelling:** running text says "herzlich willkommen" / "a very
  warm welcome" (owner: "Please fix the spelling"); deck titles and headings
  keep "Herzlich Willkommen", because the title names the generated notebook
  and a case-only rename trips case-insensitive checkouts. The owner's reason
  ("we generally use title case in German headers") did not hold up: the
  German deck titles follow ordinary German capitalisation.

## Open

- **The dogfood period** (O4): the owner's course work with
  `CLM_SYNC_CORE=sequence`, two weeks or 50 sync passes, whichever comes
  later. Unblocked: 1.40.0 is installed, and every deck (707 PythonCourses,
  364 CppCourses) reports clean on both engines, which also meets phase D's
  precondition (D4).
- **A path-filtered PR probe job:** offered, not answered.
- **Phase D** (the default flips, schema 6 wired, info topics and migration)
  and **phase E** (deletion) follow the dogfood period.
- **D5** (Lean): no decision needed before the core is in production.

The next conversational boundary is the owner reporting from the dogfood
period.

Also open, unrelated to sync: `scripts/clean_transcript.py` cleans
`api-request-blob` rows as dialogue (#1219), and a queued subagent
hand-back (a `queue-operation` record) as an owner message. Saves of this
session filter a temporary copy first (the register says how).

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

- S23 fifth part: the agent claimed CppCourses' `generate_xml_specs.py` and
  `analyze_course.py` still existed; both were removed in June. It first left
  "Herzlich Willkommen" alone as house style, then lowercased it everywhere,
  titles included, until a side agent flagged the notebook rename. Its probe
  runtime estimate (35 to 70 minutes) was about ten times too high. In a
  status reply it misstated O4 as "whichever comes first"; the decision is
  "whichever comes later".

## Not in the transcript

- The solo work between owner messages is reduced to progress notes and tool
  stubs; its reasoning is in the design note, the investigation's §2.6/§2.7,
  and the PR descriptions.
- The session was compacted several times; the summaries are harness text and
  absent from the transcript.
