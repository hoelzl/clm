---
status: active
owner: maintainers
updated: 2026-09-26
review-by: 2027-03-26
---

# State: recordings — from `drift` to a re-recording backlog (#907)

The design is `docs/claude/design/recordings-rerecord-backlog.md` (decided
2026-09-25, S8). S9 (2026-09-25/26, one owner turn, autonomous) implemented
the minimum that retires the tracker: #1004 (ledger, PR #1011), #965
(`report`/`ack`, PR #1014), #1005 (`seed-ledger`, PR #1017), plus the
deck-stem half of #1007 (PR #1016) and, from the sibling threads, #987 step 1
(PR #1012 + PythonCourses `50c30395`), #1008 (PR #1015) and #1009 (PR #1013).
hoelzl/PythonCourses#360 is closed; the seeded ledgers and the new AGENTS.md
rule are in PythonCourses `940f3bec`. This file carries the argument and
where the design bent under real data; the version-accurate mechanics are
`clm info recordings` and `clm info recordings-agents`.

## Settled (S8, unchanged)

- Source-anchored, committed, per topic (`<topic>/.clm/recordings-ledger.json`);
  severity in the tracker's five-word vocabulary from the member-fingerprint
  diff; member-granular `ack`; `drift` retired without a shim; no automatic
  re-record judgment; the build-output digest only as the secondary
  `built_output_changed` flag.

## Where the design bent (S9 — all owner-visible on the issues and PRs)

- **Part identity is `(course_id, part, lang)`**, not `(part, lang)`: two
  cohorts recording the same deck keep separate entries (review finding on
  #1004). One consequence seen in seeding: the same recording listed under
  two section names in one state file collapses to one entry (the later
  run wins) — six parts in `machine-learning-azav-de`.
- **Evidence is taken when the recording stops**, not at arm time (a
  retake inside the retake window never re-arms). Entries carry
  `evidence: recorded | anchor`; a seeded entry is never reported as exact
  (`recomputed` for a clean commit, `approximate` otherwise) and is never
  written over a record-time entry.
- **Member order is stored explicitly** (`order`): canonical sorted JSON
  loses document order, which the "member order changed" rule needs. The
  first report over the seeded repo read *every* deck as structural until
  the rule used the stored list — the single most misleading artefact of
  the session.
- **Keys are handles, fingerprints are the evidence.** Slide ids were
  stamped on most ML-AZAV decks *after* the 2026-04 recordings, so no key
  survived although the bytes did: 40+ decks read "every member changed".
  `report` now matches a member by fingerprint when its key no longer
  lines up, pairs an edited-and-re-keyed member once, and counts only the
  inserted cell when positional handles renumber.
- **Single-file bilingual decks** exist in the ML-AZAV course; they and
  split pairs that refuse normalization at the anchor (duplicate ids at the
  time) use a cell-based member model (`id:` / `cell:<group>/<kind>/<n>`,
  repeated ids kept as `id:x#2`), same fingerprint function.
- **Time anchors look forward.** A course authored just in time is
  recorded from a working tree whose edits land in the *next* commits, and
  the week's section is often still disabled in the last commit before the
  recording; `seed-ledger` tries the last commit before `recorded_at`, then
  the first five after it, parses specs with disabled sections kept, and
  resolves a renamed section by unique deck name or a renumbered deck by
  unique title.
- **`commits_since_anchor` follows renames** per bundle path (1 vs 10
  commits on a renumbered topic). Pathspecs are topic-relative so
  `voiceover/` companions count.
- **An `unanchored` anchor kind** exists for a non-git checkout (the design
  listed five kinds).
- **An ack covers the languages it was written for**; a language recorded
  later reads `unacknowledged`, not `drifted-since-ack`.
- **`report` takes a path, not a course id**; the `drift` fallback to the
  `recordings.courses` config entry is gone (documented in the migration
  topic).

## The run (S9)

| state file | parts | seeded | unresolved | ledgers |
|---|---|---|---|---|
| machine-learning-azav-de | 197 | 175 | 22 | 68 |
| machine-learning-azav-2026-08-de | 9 | 9 | 0 | 5 |

Anchors: 80 clean commits, 43 dirty, 74 chosen by `recorded_at`. The 22
unresolved parts are deck-stem renames since the recording (for example
`slides_010v_api_key_setup` → `slides_010_api_key_setup`) and two Git
Quickstart decks absent at their anchor — listed, not guessed. First
`clm recordings report .` over the tree: 115 recorded decks, 49 structural,
33 visible, 33 none, with the tracker's own entries at the top
(`intro_ai_coding`, `copilot_tools/mcp_servers`, `copilot_customization`,
`agent_mode`, `pe_02a`, `setup_copilot`). One tracker belief was wrong: the
tracker said `topic_0600` workshop "has no recording"; the state file has a
part for it.

## Open (owner)

- None blocking. Whether the author wants the 22 stem-renamed parts
  re-pointed by hand (`clm recordings ack` after re-seeding under the new
  stem is not possible: the entry never existed) or re-recorded.

## Deferred / revisit conditions

- #1006: `import-inventory` for the 402-video legacy inventory and
  `identify-rev` re-anchoring — untouched in S9.
- #1007 topic-rename half — waits for #1002 / `clm course mv`.
- An MCP mirror of `report` (the agent-tasks "guide + mirror" rule) — not
  requested; the CLI `--json` is the agent surface.
- Root cause of the 22 false `drift` verdicts — moot; `drift` is gone and
  the secondary flag stays optional.
- Fuzzy stem mapping for renamed decks in `seed-ledger` — deliberately not
  done (guessing); revisit only if the author asks for the 22 parts.

## Known weak points

- Seeded fingerprints are only as good as the anchor commit; 103 of 178
  seeded parts are `approximate` (dirty or time anchors). The report says
  so per part.
- `seed-ledger` materialises one full worktree per distinct anchor commit
  (~80 for the ML-AZAV file, ~20 minutes); acceptable for a one-off.
- The title-only resolution fallback could in principle mis-resolve two
  decks with identical titles in different sections; it applies only when
  the section+name and name-across-sections lookups both fail and the title
  is unique in the course.
- The cell scheme classifies id-less removed members from the handle alone
  (`code` → structural, else visible) — conservatively one class high for
  id-less notes or narration cells.

## Next conversational boundary

The owner's first real `report` / `ack` pass over PythonCourses. (S9's own
save landed as PR #1018; PR #1017 for #1005 was still waiting on CI when
the session closed.) Then #1006
if the legacy inventory still matters, and the topic-rename half of #1007
when #1002 lands.
