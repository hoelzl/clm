---
status: active
owner: maintainers
updated: 2026-09-25
review-by: 2027-03-25
---

# State: recordings — from `drift` to a re-recording backlog (#907)

The design is `docs/claude/design/recordings-rerecord-backlog.md` (decided
2026-09-25, S8); the implementation issues are #1004 (ledger), #965 (the
`report`/`ack` half, now unblocked), #1005 (seed + retire the PythonCourses
tracker #360), #1006 (legacy inventory), #1007 (rename migration). This
file carries the argument and where the agent's priors were corrected. S8
was a one-owner-turn autonomous design session: the owner's brief is the
single Owner block of the transcript, and the reasoning lives in the agent
blocks and the design note.

## Settled (S8)

- **Source-anchored, committed, per topic.** The verdict comes from the
  deck's source at `HEAD` versus per-member content fingerprints stored at
  record time in `<topic>/.clm/recordings-ledger.json`; the build-output
  digest is an optional secondary flag. The measurement that decided it
  (design §3): on the 197 parts of `machine-learning-azav-de.json`, `drift`
  is wrong-in-the-"changed"-direction for 22 of 84 answerable parts and
  spec-bound `unknown` for 30 more, while the stamped `git_commit` answers
  117 of 123 parts from the repo alone.
- **The (a)–(d) order**: (c) first; (b) in its cheap five-class form
  (`structural` / `visible` / `narration` / `notes` / `none` — the tracker's
  own vocabulary); (d) as member-granular `ack`; (a) split into seeding from
  the local state files (prerequisite, no video) and the 402-video inventory
  (follow-up, `identify-rev` as the per-deck upgrade path).
- **The minimum that retires #360**: ledger + `report` + `ack` +
  `seed-ledger` run once on the two ML-AZAV state files; then the
  course-repo `AGENTS.md` rule becomes "run `report`, `ack` what you keep".
- **Non-goal reaffirmed**: no automatic re-record judgment. The
  reasoning_effort one-liner in ~20 decks is `structural` by any rule and
  cosmetic to the author; `ack` is the mechanism, not a threshold.
- **`drift` retires into `report`** without a shim (the agent-toolkit
  shaping rule; `drift` is one release old and agent-facing).

## Corrections of the agent's priors (worth not rediscovering)

- The issue framed backfill (a) as "turn `identify-rev` output into
  stamps". The dashboard-era state files already carry `git_commit` on
  123/197 parts and `recorded_at` on all of them; `git rev-list -1
  --before=<recorded_at>` pins the rest. `identify-rev` is only needed for
  the video-only legacy inventory, and only where an mtime anchor is
  doubtful.
- The issue framed (c) as new machinery ("commit + slide-file fingerprints
  at recording time"). The commit is already stamped; what is missing is
  the **deck path** (the lecture key is display names, and a topic id
  resolves to two directories for 6 parts because of the cohort-archive
  module) and a **committed home** — the state file is machine-local.
- `clm info recordings` does not exist (the brief asked for it). The
  sources are `docs/user-guide/recordings.md` and the `clm recordings`
  section of `clm info commands`; the `recordings-agents` topic arrives
  with #965.
- The "~400 legacy OBS videos" and the dashboard-era parts are **two
  populations**: the inventory (`planning/video_to_slide_mapping.json`,
  402 rows, 9 courses, already carrying an mtime-based `freshness`) versus
  the state files (197 + 9 ML-AZAV parts since 2026-04-18, 37 + 22 + 12 in
  the other courses). The tracker's subject is the second one.

## Open (owner, confirm once before #1004 starts)

- Per-topic `.clm/recordings-ledger.json` (sync-ledger precedent; needs one
  gitignore exception per course repo) versus a course-level file. The
  design chose per-topic for merge locality and rename migration.
- `ack` is per **deck** entry (all parts), not per part. A part-level ack
  was judged a finer knob than the author's workflow uses.
- `drift` retiring without a shim (versus an alias for one release).

## Deferred / revisit conditions

- Root cause of the 22 false `changed` verdicts — only if the secondary
  `built_output_changed` flag proves noisy; otherwise drop the flag.
- Finer prose-similarity ranking inside `visible` — only if the
  `changed/total` count is not enough for the author's cosmetic calls.
- Legacy inventory import and `identify-rev` re-anchoring (#1006).

## Known weak points

- The measurement is one course's state file and one manifest build;
  "untouched" means no commit touched the **topic directory**, which
  misses include/template changes that legitimately alter what students
  see. The design accepts that as the secondary flag's job.
- Severity keys members by `slide_id`; a deck without ids falls back to
  positional identity, where the sync engine's cold-classification rules
  apply — the design note does not spell this out.
- 43 of 197 parts were recorded on a dirty tree; their stored fingerprints
  will be exact going forward, but seeded entries for those parts carry
  `commit-dirty` confidence and can only be as good as the commit.
- The S8 transcript is thin (one owner block); the design note is the
  primary record.

## Next conversational boundary

Implementation in order #1004 → #965 → #1005, each a resolve-issue pass
with test-first and an adversarial review above ~150 non-test lines. The
three confirm-once points above are the only owner input needed; if the
owner is silent, proceed with the design as written.
