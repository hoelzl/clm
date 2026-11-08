---
status: active
owner: maintainers
updated: 2026-11-08
review-by: 2027-05-08
---

# State: sync pre-write verify (#1051, per-item apply vs all-or-nothing)

Saved retroactively on 2026-11-08 from the 2026-10-09 session (S16). The
mechanism and its later amendments live in the sync design note
(`docs/claude/design/sync-total-identity-document-model.md`, §13 rows for
#1051 and later for #1130/#1141, which built on this gate). This file keeps
only the conversational position.

## Settled (S16, 2026-10-09)

- **Apply verifies the projected files before writing (B2), and holds back
  dependent mechanical rows (C)** (owner, after the agent laid out A, B1, B2
  and C). The owner framed the constraint: "In practice, I never reconcile
  decks by hand ... the overriding constraint is the ease of use and
  reliability of synchronization when agents use the tool." The agent's
  argument: under A (per-item apply, verify after writing) the files left
  "open for correction" can be structurally broken on disk, and a
  written-but-unrecorded state is read back next pass as an author edit
  (#885). B2 is #992's per-slide-group scoping applied to the write; C is the
  "defer, re-derive next pass" pattern of #824/#885.
- **The guarantee was narrowed during implementation, and the owner did not
  object:** only violations the apply *introduces* withhold a write, not ones
  the deck already had. Blocking on pre-existing violations would deadlock
  #992's own case (removing a narrated slide orphans its voiceover until the
  removal is written) and stall unrelated items on any deck already failing
  verify. Recorded in the design note; shipped as PR #1074.

## Shipped

- The S16 triage: 11 issues fixed and merged (#1021, #1026, #1039, #1043,
  #1045, #1053, #1054, #1055, #1057, #1058, #1052), plus #1051 (PR #1074,
  after the decision above). Follow-ups filed: #1062, #1067, #1071.

## Open / revisit conditions

- None in this thread. Later work on the same gate (#1124 → #1130, B2's
  livelock on rows that are only valid jointly) continues in
  `sync-pool-pairing`.

## Agent corrections recorded in the transcript

- S16: during worktree cleanup the agent listed a leftover
  `save-cam-portal-inception` worktree folder, saw a `state.md` inside, and ran
  `rm -rf` on it anyway without reading it. The committed version was on
  `origin/master` (`6c25ceae`); whether a newer uncommitted version was lost was
  not established.
