---
status: active
owner: maintainers
updated: 2026-09-26
review-by: 2027-03-26
---
# Register: discussions index

One row per thread; sessions numbered S1… across the tree. Gaps mean a
lost or deliberately skipped conversation (recorded below). The format and
the reasons behind it are in `README.md`; the save procedure is
`.agents/skills/save-discussion/SKILL.md`.

## Threads

| Thread | Subject | Disposition | Landed | Sessions |
|--------|---------|-------------|--------|----------|
| `cpp-ide-export` | Student-facing C++ IDE export (#928): Phase 4 verification and rollout, the owner's review of the generated output (kernel drops bare-expression output → `SHOW(expr);` everywhere; `slide_` prefix; `section_name` attribute; plain title; HTML→Markdown comments; no promotion heuristic), the lambda-crash landmine, differential check with values | Active — all follow-ups merged; owner's student-view review of four decks still open | clm #937, #938, #939, #940; CppCourses #129, #130; `docs/claude/handovers/cpp-ide-export-handover.md` | S1 |
| `agent-discussion-continuity` | Porting the save/resume-discussion stack (skills, `docs/claude/discussions/`, transcript tooling) from CppCourses/Cenotaph to this repository | Active — stack landed and in use from this save; first saves of older sessions pending | clm #941 | S1 (last owner turn of the `cpp-ide-export` transcript) |
| `agent-toolkit-candidates` | Which clm features should stop being stand-alone and become agent toolkits (the sync / release / harvest shape): survey of embedded-LLM paths, human-only output, and CLI-first affordances; three tiers filed as issues | Active — Tier 1 complete (#959–#963); S7 landed #965's `--json` surface half (PR #988, merged; issue stays open for the #907-gated `report`/accept verbs per the owner's split-landing decision); #966/#967, the #907 design, or deferred #981 next | umbrella clm #970, children #959–#969 plus #981, `docs/claude/agent-toolkit-candidates-investigation.md` (PR #971); kit PR #973; #960 PRs #974, #975, #976, #978; #961 PR #980; narration map PR #982; #962 PR #984; #963 PR #985; #965 surface half PR #988 | S2, S3, S4, S5, S6, S7 |
| `recordings-rerecord-backlog` | #907: what retires the hand-maintained re-recording tracker (PythonCourses#360) — source-anchored provenance in a committed per-topic ledger, five severity classes from the member diff, member-granular `ack`, seeding from the local state files without video analysis; the build-output digest demoted after measuring 22/84 false "changed" verdicts | Decided — design note written, implementation issues filed, #907 open as umbrella | `docs/claude/design/recordings-rerecord-backlog.md`; issues #1004, #1005, #1006, #1007; #965 unblocked | S8 |
| `recordings-rerecord-backlog` (S9) | Implementation of the S8 decision, autonomous: #1004 ledger (PR #1011), #965 report/ack (PR #1014), #1005 seed-ledger (PR #1017), #1007 deck-stem half (PR #1016); hoelzl/PythonCourses#360 closed, ledgers seeded in PythonCourses `940f3bec`. The design bent under real data in eight places (stored member order, fingerprint matching after id stamping, cell scheme for single-file decks, forward-looking time anchors, …) — all in `state.md`. Sibling threads in the same session: #987 step 1 (PR #1012 + PythonCourses `50c30395`), #1008 (PR #1015), #1009 (PR #1013). | S9 |
| `shared-diagram-renders` | #987: sharing generated diagram renders — include the diagram *source* per consuming topic (the code path exists, untested), validator carve-out + `as` prefix check, `image_ref_missing` / `image_name_conflict` as the safety net; topic-level declaration deferred with a revisit condition | Decided — design note written; #987 = step 1, #1008 = step 1b | `docs/claude/design/shared-diagram-renders.md`; issue #1008 | S8 (transcript under `recordings-rerecord-backlog`) |
| `schedule-section-week` | #916: sections are weeks by *convention* for `export schedule`; the calendar already places subsections as a flat sequence; non-week sections are name-only subsections — written into `clm info calendar` / `clm info spec-files`; `release section` alias accepted, the other shapes rejected as duplicates of existing mechanisms | Decided — docs landed with the S8 save; #916 closes on merge | info topics `calendar.md`, `spec-files.md`; issue #1009 | S8 (transcript under `recordings-rerecord-backlog`) |

### Dropped threads / corrections worth keeping

- #907's framing of backfill as "turn `identify-rev` output into stamps"
  was a prior worth dropping: the dashboard-era state files already stamp
  `git_commit` on 123/197 parts and `recorded_at` on all, so seeding needs
  no video; `identify-rev` is the upgrade path for the separate 402-video
  inventory only. Likewise the issue's (c) was mostly present already — the
  missing pieces are the deck path and a committed home, not the commit.
  `clm info recordings` does not exist (the brief asked for it). (S8)
- #987's "including another topic's diagram source is unclear or
  unsupported" is wrong in the useful direction: the virtual splice
  classifies by suffix and renders into the consumer's `img-generated/`;
  it is untested, not unsupported. The trap is `as` without the
  `drawio/`/`pu/` prefix (the render target is derived from the virtual
  path's grandparent). (S8)
- #916's "a sixth teaching day has nowhere to go" assumed `weekday=` is
  required; name-only subsections are teaching days already, and the
  calendar never looked at section length. The date-anchored
  `export schedule` shape was dropped as a duplicate of
  `calendar generate -f md|csv`. (S8)

- The initial S4 snapshot said no supported transcript source was available.
  OpenCode's export does expose the public dialogue; a manually reviewed
  extraction now preserves it in `agent-toolkit-candidates/transcripts/2026-09-19-s4.md`.
  The cleaner still lacks a native OpenCode adapter. (S4 save extension)

- `opencode export` without a session ID hangs (interactive). The reliable
  OpenCode source is the storage SQLite (`~/.local/share/opencode/opencode.db`,
  read-only; `message.data.role` + `part.data.type == "text"`); S5's
  transcript was extracted from it directly. (S5)

- Refined at S6: in the current storage schema the `message` table carries
  metadata only — the dialogue text lives in the separate `part` table
  (join on `message_id`; the role comes from the parent message row).
  An extraction reading `message.data` for parts finds nothing. The
  S6 transcript was built from `part` joined against `message`. (S6)

- The owner chose **removal without shims** and corrected the proposed
  retained `sync-at-rev` name to `export-at-rev`: exporting a historical
  artifact is legitimate, but an export without merge is no longer a sync.
  (S4)
- S3's "MCP conversion complete" premise missed `harvest_backfill_dry`:
  it spawned a dry-run CLI pipeline that still invoked models. S4 removes
  it rather than redirecting it to autopilot. Exported companion narration
  also exposed a slice-2 reader gap; the revision-task readers now include
  companions. Canonical procedure and migration are in the info topics. (S4)

- Issue #960's text said compare answers would be "accepted through the
  existing `harvest accept` write path"; the implementation found compare is
  read-only auditing with no deck write to route, so `harvest
  compare-accept` writes the canonical report JSON instead (the
  `align accept` sidecar precedent). Do not re-litigate; the owner saw the
  deviation in PR #975. (S3)
- The S2-open question "MCP `harvest_compare`: model-free or dropped" was
  answered as both — the model-invoking tool was dropped and the task mirror
  extended (`harvest_task` kind=port/compare). (S3)
- The cross-module private-import guard
  (`tests/test_architecture_contracts.py`) is not in the pre-push smoke tier;
  two #960 pushes passed the gate and failed CI. Documented in
  `docs/developer-guide/testing.md` — run it explicitly when touching
  cross-module imports. (S3)

- The owner recalled video transcription as "mostly a stand-alone feature";
  the survey showed `clm harvest` is already agent-first (#546) and only its
  revision-history family (`port`/`compare`/`backfill`/`sync-at-rev`) and the
  heuristic alignment decisions are not. Do not re-propose converting the
  curate/translate loop. (S2)
- Two subagent hand-back reports arrived as user-role rows and were cleaned
  as Owner turns on the first pass; they were removed from a temporary
  source copy (both the `user` row and its `queue-operation` twin) before the
  transcript was re-cleaned. Their substance lives in the investigation doc,
  not in the dialogue. (S2)

- The agent proposed a deterministic "promote the whole run of consecutive
  variable cells" emitter rule as a safety net for split variable groups;
  the owner rejected any such tool heuristic in favour of an explicit
  authoring rule (tag the run `global`). Do not re-propose it. (S1)
- The agent's first display helper used a lambda; xeus-cpp 0.8 crashes on
  the second lambda wrapper after new globals are defined. Any future
  helper must stay lambda-free (overloaded comma operator now). (S1)
- The owner recalled that the Python templates already drop the title slide
  from code exports; they only gate the logo — Python code exports never
  show it because they drop markdown cells entirely. (S1)
- The agent first claimed the kernel-image display gap made `#930` urgent
  as a notebook-UX problem; the owner's interactive check turned it into a
  deck convention (always `;`, `SHOW`), which dissolved #930's premise
  rather than escalating it. (S1)

## Sessions

| S | Date | Transcript | Size | Owner/agent blocks | Source | Boundary |
|---|------|-----------|------|--------------------|--------|----------|
| S1 | 2026-09-13 | `cpp-ide-export/transcripts/2026-09-13-s1.md` | 5.91 MB → 48 kchar (122:1) | 4 / 26 | Claude Code session `a57d56f4-065d-49fc-852b-06051debb22c.jsonl` (ran in slug `C--Users-tc-Programming-Python-Projects-clm--claude-worktrees-issue-928-cpp-ide-export`, moved to slug `c--Users-tc-Programming-Python-Projects-clm` when the working directory changed) | content through 2026-09-13 23:01 (Phase 4, owner review + follow-ups incl. the SHOW rollout and gate, the discussion-stack port); this save itself is covered by the two state files |
| S2 | 2026-09-19 | `agent-toolkit-candidates/transcripts/2026-09-19-s2.md` | 1.15 MB → 14 kchar (96:1, after removing two subagent hand-back rows from a temporary source copy) | 2 / 13 | Claude Code session `f666c194-70c2-4d3d-ab31-103a52e938ea.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout) | content through 2026-09-19 01:39 (the survey, the assessment, the doc, issues #959–#970, PR #971); this save itself is covered by the state file |
| S3 | 2026-09-19 | *(none — opencode session; the transcript cleaner targets Claude/Hermes formats)* | — | — | opencode session in the main checkout (resumed S2 via the `resume-discussion` skill) | content through 2026-09-19 (owner confirmed tier ordering; #959 + #960 slices 1–4 landed as PRs #973–#976; the slice-5 shim-vs-removal question is the next boundary); this save is covered by the state file |
| S4 | 2026-09-19 | *(none — opencode session; no supported cleaner source available)* | — | — | opencode session in the main checkout, branch `claude/issue-960-retire-history-verbs` | owner approves outright removal, proposes export-at-rev, then asks to continue; implementation and knowledge findings captured in state.md and #960's linked PR; state-only continuity update, not a complete transcript save |
| S4 (save extension) | 2026-09-19 | `agent-toolkit-candidates/transcripts/2026-09-19-s4.md` | Public-text extraction; tool/reasoning payloads omitted | 4 / 10 | OpenCode session `ses_f46390f38ffe5pplYxGjjeYyH4`, `opencode export`, manually reviewed | through the owner's save request and the initial acknowledgement; supersedes the state-only limitation above. PR #978 subsequently verified merged; #961 next |
| S5 | 2026-09-20 | `agent-toolkit-candidates/transcripts/2026-09-20-s5.md` | Public-text extraction from the storage DB; tool/reasoning payloads omitted (~20 kchar) | 8 / 55 | OpenCode session `ses_f44adfd80ffe59gdU9Pk47pW0o`, main checkout (resumed S4 via the `resume-discussion` skill), manually reviewed | content through the owner's save request and the knowledge-audit acknowledgement (the resume + #961 landing as PR #980, the polish-placement argument, the surface-orthogonality review, the three follow-ups: #963 note + ratification, #981, narration-map PR #982); the save itself is covered by the state file |
| S6 | 2026-09-20 | `agent-toolkit-candidates/transcripts/2026-09-20-s6.md` | Public-text extraction from the storage `part` table; tool/reasoning payloads omitted (~15 kchar) | 3 / 84 | OpenCode session `ses_f4404fcb1ffek3baid0Qv0DK78`, main checkout (resumed S5 at outcomes depth via the `resume-discussion` skill; the two question-tool answers are recorded as a marked parenthetical block, not verbatim dialogue), manually reviewed | content through the owner's save request and the knowledge-audit acknowledgement (#962 landed as PR #984 with the confirm-once placement check and a three-finding adversarial review; #963 landed as PR #985 — coverage/assign-ids toolkits, `language-coverage` rename, a seven-finding review; both merged, both issues closed); the save itself is covered by the state file |
| S7 | 2026-09-24 | `agent-toolkit-candidates/transcripts/2026-09-24-s7.md` | Public-text extraction from the storage `part` table; tool/reasoning payloads omitted (~12 kchar); session span 2026-09-20 → 2026-09-24 | 4 / 57 | OpenCode session `ses_f3ff89f24ffemxPDVwF10rs0MP`, main checkout (resumed S6 at conversation depth via the `resume-discussion` skill — the first full-depth opencode resume; the two question-tool answers are recorded as marked parenthetical blocks, not verbatim dialogue), manually reviewed | content through the owner's save request and the knowledge-audit acknowledgement (#965 picked by the owner and scoped to the `--json` half; PR #988 shipped with a five-finding adversarial review — all fixed — and merged 2026-09-21; issue #965 stays open with a split-landing comment); the save itself is covered by the state file |
| S8 | 2026-09-25 | `recordings-rerecord-backlog/transcripts/2026-09-25-s8.md` | 2.13 MB → 13 kchar (167:1) | 1 / 6 (one-owner-turn autonomous design session; 116 tool stubs) | Claude Code session `294de972-4d44-4c5b-b9e7-73168a6a6815.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout; docs written in worktree `design-907-987-916`) | content through the design-note, issue and comment work for #907 / #987 / #916 (three threads from one session); the save itself is covered by the three state files |
| S9 | 2026-09-25/26 | `recordings-rerecord-backlog/transcripts/2026-09-25-s9.md` | 6.68 MB → 35 kchar (192:1) | 1 / 39 (one-owner-turn autonomous implementation session; 268 tool stubs, 2 harness-authored turns dropped) | Claude Code session `fa157537-e0aa-465d-adaf-ed286cdc900b.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, main checkout; worktrees `issue-987-diagram-includes`, `issue-1008-image-checks`, `issue-1009-release-section`, `issue-1007-ledger-rename` for the sibling issues) | content through 2026-09-26 02:28 (the seven PRs, four adversarial reviews, the PythonCourses migration and seeding, the #360 closure); this save itself is covered by the state file |

### Deliberately skipped

| Date | Session | Reason |
|------|---------|--------|
| 2026-07-26 … 2026-09-13 | 11 Claude sessions and 2 Hermes sessions listed by `python scripts/audit_transcripts.py` at the time of S1 | Predate the discussion stack; not triaged yet, not skipped for good — the #928 design sessions (2026-09-12/13, slug `…-issue-928-cpp-ide-export`) and the two long Hermes reviews of July 2026 (#704 adversarial review, performance-regression investigation) are the candidates; the raw sources still exist on the maintainer's machine |
| 2026-09-14, 2026-09-18 | `64d938aa…`, `8891edb1…` (root slug) | Empty sessions (0 owner turns); nothing to save |
| 2026-09-18 | `8c80e928…` (14 owner turns, open-issue prioritisation → the 1.29.0 release) and `4fddb4b5…` (3 owner turns, #914 / #881+#886 / #868–#870 implementation) | Not triaged at S2; the first is a candidate (prioritisation argument), the second is task work already recorded in PRs #954/#956/#957 and the release notes |
| 2026-09-06 … 2026-09-13 | `83a877dc…` (#917 investigation, 3 owner turns), `0260bc6b…` (#793, 1 turn), `8ad64f38…` / `c0890feb…` / `4fdc59b9…` (#928 phases 1–3 in the `issue-928-cpp-ide-export` worktree slug, 8/2/4 turns) | Task sessions recorded in their PRs and the #928 handover; the #928 design argument is the S1 candidate already noted above — not re-triaged at S8 |
| 2026-09-25 | `ca62afc6…` (0 owner turns), `942bdc7f…` (4 owner turns, issue triage that led to the autonomous backlog run), `c787e1f0…` and `63c0dc34…` (3 owner turns each, skill-driven implementation of #968/#969/#981/#993/#991) | The empty one has nothing to save; the two implementation sessions are task work recorded in PRs #998–#1001 and #1003; the triage session is a candidate (the prioritisation argument for the backlog order), not triaged at S8 |
| 2026-09-25 (at S9) | the same unsaved Claude sessions the S8 audit listed, plus the two July Hermes reviews | Nothing new to triage at S9; the S8 verdicts stand (the 2026-09-18 prioritisation session `8c80e928…` and the 2026-09-25 triage session `942bdc7f…` remain candidates) |
