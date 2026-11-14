---
status: active
owner: maintainers
updated: 2026-11-14
review-by: 2027-05-14
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
| `typing-replay` | Replaying code-along → completed cell edits as simulated typing in recording notebooks (C++ first): editor transactions instead of key events, a line-step plan checked by replay, step and hacker modes behind a global switch, an override-only sidecar keyed by slide id + fingerprint; spike built and verified in the browser the same session | Active — spike verified (no live kernel); step 1 is #1023 (variant shape open); packaging + Docker dogfood next | `docs/claude/design/typing-replay.md`; spike repo `jupyterlab-clm-typing` (local, `94d81f7`); issue #1023 | S11 |
| `typing-replay` (S11 save extension) | After the first save: the owner settled the step-1 shape (new opt-in `recording-code-along` kind, notebook only, no notes/voiceover/alt; it is the recorded notebook), made the extension repo public, and had it packaged, live-tested on two CppCourses decks in the cam-cpp container (xcpp20), #1023 implemented with an adversarial review, and the cam-notebook images rebuilt with the extension pinned | Active — steps 1–2 shipped; Docker Hub push of 0.5.4 awaits the owner; real recordings next | PR #1025 (decisions), PR #1027 (#1023, merged), issue #1026 (follow-up); hoelzl/jupyterlab-clm-typing (public); PythonCourses `4e09d7d` (image pin) | S11 |
| `typing-replay` (S11 second extension) | Extension 0.1.1 (Ctrl/Alt+Enter also disarm), an on-screen cheat-sheet artifact, and the 0.1.1 image update: the agent found that a base-stage pin bump re-uploads GBs, drafted a last-layer update (~160 kB), then handed the Dockerfile commit to the 0.5.6 release agent (0.5.5 never pushed) | Active — 0.5.6 must ship 0.1.1; real recordings next | hoelzl/jupyterlab-clm-typing `3cac2b0`; cheat sheet artifact `9gUrMUj3EMgj3uPVqwFHUG`; uncommitted PythonCourses Dockerfile edit (handed off) | S11 |
| `typing-replay` (S12) | VS Code player: a separate extension in the same repo (`vscode/`) sharing the planner and player code, not part of jupyter-slide-nav. The owner's half-armed bug (command mode after Shift+Enter) gave 0.1.1, where Alt+N always enters edit mode. The command-mode-key consequences were weighed and accepted as 0.1.2. Then CI and a GitHub-release workflow, and the clm 1.31.0 release in between | Active — player merged and released (`vscode-v0.1.2`); the owner sets up a recording profile; real recordings next | hoelzl/jupyterlab-clm-typing PRs #1, #2, #3 and release `vscode-v0.1.2`; clm PR #1030 (1.31.0), PR #1032 (design doc §6 item 5) | S12 |
| `cam-portal-inception` | Restructuring for multi-role CAM course operations (back-office, trainers, course creators): owner interview on domain, roles, integrations and constraints; one extended clm vs. two systems vs. role tools | Decided — two systems split by data/trust boundary: clm emits CI-built release bundles, new private cam-portal runs operations; inception doc written, owner review + P0 (bundle) design next | hoelzl/cam-portal `docs/inception.md` (private) | S13 |
| `cam-portal-inception` (moved) | The thread continues in the private repository `hoelzl/cam-portal`, `docs/discussions/inception/`. S13 is that thread's `INC-1`, saved there again with its second half (agent-workflow setup). This thread is frozen | Moved — resume and save in cam-portal; clm-side P0 work starts a new thread here | hoelzl/cam-portal `docs/discussions/inception/` (private) | — (S13 only) |
| `sync-pool-pairing` | #1130: moved + edited un-id'd shared cells livelock or silently misplace under B2. The adversarial review of #1129 led to its revert and the root-cause framing (row splitting under the pre-write verify). The owner chose slot-atomic + split-answer (options 2 + 3, not 1) and asked for safety by design (→ content conservation). The order oracle showed the real defect is pairing (71 silent misplacements), so the plan was re-ordered to ledger spans, conservation, ids, placement. #1144 shipped with a documented residual after zero-worse proved unreachable (owner: "which half moved" without ids is whack-a-mole). Step 3 (ids) was withdrawn after a history scan found 0 field instances | Decided — #1130, #1142, #1137, #1150 closed; residuals accepted with a re-open trigger; silent bugs #1179-#1182 next | PRs #1131, #1132, #1133, #1134, #1141, #1144, #1146; #1148; design doc §13 (#1130 and #1142 rows); `docs/developer-guide/testing.md` (sweeps); `scripts/scan_sync_history.py` | S19 (opening, saved later), S14, S15 |
| `sync-prewrite-verify` | #1051: per-item apply left structurally broken halves on disk. The agent laid out A (per-item, verify after writing), B1/B2 (verify the projected files, deck-wide or per slide group) and C (hold back dependent mechanical rows); the owner chose B2 + C with agents as the primary users. During implementation the guarantee was narrowed to violations the apply introduces | Decided — #1051 closed | PR #1074; sync design note §13 (#1051 row); the S16 triage's PRs #1059-#1072 | S16 |
| `docker-workers-ops` | Docker worker images and the Docker host: the 1.34.2 rebuild and the owner's correction that the httpx2 replay fix had already shipped; slow-to-activate workers marked dead (#1078) and the container-path executed-notebook cache key (#1079), released as 1.34.3; moving Docker's disk to G: rather than USB/QLC P:; `force-unsafe-io` measured useless | Decided — work shipped; 0.6.0 backup containers/images and the Ubuntu WSL disk left open | 1.34.3; PRs #1080, #1085, #1086, #1087, #1089; `docker/BUILDING.md` | S17, S18 |
| `sync-structural-core` | Whether the sync engine can be improved at the level of the algorithm instead of by more special cases, and whether its operation can be formalized. A one-session investigation: the root cause traced to positional identity for un-id'd cells (no total order in the ledger, half-to-half pairing before the ledger is consulted, rows executed one at a time); a prototype replacement core scored on the sync probe (67,871 of 67,871 against master's 50,031; with one half untouched 27,502 of 27,502 against 22,513); its laws checked on every small input; Lean assessed as feasible and not attempted | Proposed — investigation landed; the owner has decided nothing beyond landing it (the doc's §9, D1–D5, is open) | PR #1217 (merged); `docs/claude/sync-structural-core-investigation.md`; `scripts/sync_model.py`, `sync_model_probe.py`, `sync_model_laws.py`; `docs/claude/analysis/sync-structural-core/issue-classification.md`; the doc's §4.8 with this save | S20 |
| `sync-structural-core` (S20 save extension) | After the first save: the owner gave explicit permission to file the transcript-cleaner defect the save had found, and asked for the save again at the end. Nothing new on the proposal itself | Proposed — unchanged: D1–D5 open; the cleaner issue open | PR #1218 (first save, merged); issue #1219; PR #1220 (the first save's two now-false lines, merged) | S20 |
| `studio-preview-review` | The adversarial review of PR #704 (Studio tier-2 preview) with the owner's wider brief: find the design mistakes behind two bad review rounds and fix root causes "rather than putting patches on patches". Three causes named (no defense in depth, a hand-rolled layer over nh3, wired claims without an executing test); the owner had #704 merged and the pattern made a standing rule, and chose the bundled-logo-only option for #706. The same session then root-caused the failing nightly (#694) | Decided — all shipped 2026-07-26; a record, saved retroactively on 2026-11-10 | PRs #704, #707 (the claimed-wired rule), #708 (#705, CSP), #709 (#706, logo asset), #710 (#694); `docs/developer-guide/testing.md`; `docs/claude/design/mobile-deck-studio.md` | S21 |
| `cache-performance-regression` | #711: the owner's impression that cached builds of a large course were slower on clm 1.23 than on 1.21. Measured on the real course without deleting cache or output; the "content changed" storms traced to CRLF against LF templates in two installs of the same clm, and the slow hit path profiled with py-spy. The owner had the results written down, a PR opened and the first three optimizations started | Decided — shipped 2026-07-27, #711 closed 2026-09-18; a record, saved retroactively on 2026-11-10 | issue #711; PR #712 (`docs/claude/cache-performance-investigation.md`); PR #713 (the three fixes) | S22 |
| `sync-structural-core` (S23) | The owner's answers to D1, D2 and D3 after a resume at conversation depth (pursue the replacement core in a revised order, keep the width of the order question, ask on byte-identical copies), and the two steps the agent then carried out alone: the oracle-free laws as a ratchet against the engine, and the prototype's pass with open questions, tags and slide-hood. A check across passes refuted the first design of that pass | Decided (D1 to D3) — steps 1 and 2 shipped; the owner has not read their findings yet; step 3 (the ledger) waits for the owner's word | PR #1224 (merged), PR #1225 (merged); the doc's §2.5, §4.6, §4.8 items 6 to 10, §4.9 and §9; `tests/slides/test_sync_laws.py`, `scripts/sync_laws.py`, `scripts/sync_model_passes.py` | S23 |
| `sync-structural-core` (S23 save extension) | After the first save: the agent's closing report to the owner on what steps 1 and 2 found, with step 3 and the size of a "part" named as the two open items, and a day later the owner's second save request. Nothing new on the proposal itself | Decided (D1 to D3) — unchanged; the findings are reported and not answered yet; step 3 (the ledger) and D4 wait for the owner's word | PR #1226 (first save, merged) | S23 |
| `sync-structural-core` (S23, third part) | Two days later the owner asked to start step 3 (the ledger change) "unless there are any issues we need to resolve first". The agent raised one: the shadow run does not need the ledger change, and recording first would put a field nothing reads into every committed course ledger. The owner chose the shadow first. The agent then built the shadow on real history: every change between in-sync committed states, replayed as an edit of one half, with the engine and the prototype judged against what the author committed next. The prototype matched in every replay after three of its gaps were closed (forks, kinds, cells moving between a deck and its companion); the engine matched in 3,577 of 3,849 and 2,279 of 2,734 | Decided (the order); step 3 shipped; the go/no-go and step 4 (the ledger) wait for the owner | PR #1230 (merged), PR #1232 (merged); the doc's §2.6, §4.9 and §9; `scripts/sync_shadow.py` | S23 |
| `sync-structural-core` (S23, fourth part) | The owner's go for the switch-over on the shadow's results ("Let's go"), then step 4 (the ledger's cell sequence), the switch-over's design note and the owner's answers to its decisions O1 to O5 (all as recommended), and phases A to C done by the agent: the core as a library, the text layer and renderer byte-exact on real history (the owner: no special rules for pure whitespace), and `report` / `apply` behind `CLM_SYNC_CORE=sequence`. Phase C's checks on the course repositories found that no deck has a recorded sequence yet and that the design's fallback would have blessed pending translations; both fixed before the PR | Decided (go, D4, O1 to O5) — phases A and B shipped; phase C's code in PR #1246; the dogfood period and the companion-order choice wait for the owner | PRs #1234, #1237, #1239, #1242, #1243, #1244, #1246; `docs/claude/design/sync-sequence-core.md` (§5 to §9, "as built"); the investigation's §2.7 | S23 |

### Dropped threads / corrections worth keeping

- The owner's first framing "trainers get only the outputs, without the
  speaker notes" was imprecise: trainers get speaker `notes` but no
  `voiceover` — exactly clm's existing `trainer` kind, so no new tier is
  needed. (S13)
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
- The S9 save recorded #1005 (PR #1017) as shipped; the PR was open with
  red unit jobs (`_commits_since` ordered by one-second `%ct`). An armed
  auto-merge is not a merge — a save must check `gh pr view` state, not the
  session's intent. Found and fixed on resume. (S10)
- The agent first proposed emitting typing *plans* at build time (with an
  agent-authored sidecar per tricky cell). Building the spike showed that a
  plan recomputed at load time from `(start, target)` and checked by replay
  covers the common cases, so clm only needs to emit the two texts, and the
  sidecar shrinks to optional overrides. The owner's proposal to reuse
  slide-id sync for the sidecar holds for that smaller sidecar. (S11)
- The spike's hacker mode first disarmed itself when the script ran out;
  over-typing then leaked keys into the cell. Fixed by staying armed and
  swallowing keys until Shift+Enter/Esc. This is a recording-specific
  invariant, recorded in the design doc. (S11)
- The S11 conversation (owner and agent alike) said "the recording
  notebooks" as if they were code-along. `RecordingOutput` is completed +
  notes + voiceover, and the public `code-along` kind must not carry
  solutions, so step 1 became a new private recording code-along variant.
  This was found while filing #1023, after the last owner turn. (S11)
- The agent looked for the cam-notebook image's source in CppCourses `etc/`. Neither Dockerfile there (`cam-cpp-notebook`, `cam-cpp-container`) builds the running `mhoelzl/cam-notebook:*-cpp`. The owner pointed to PythonCourses `docker/cam-notebooks` (`Dockerfile.split` targets + monolith `Dockerfile`), which was confirmed by the base image, extension set and CMD. (S11 save extension)
- The owner hoped a 0.1.1 bump would be a small push because "most of the layers should remain unchanged". That was only true after a restructure: the pin sat in the shared `base` stage, so every child layer would have been rebuilt with new digests. Measured fix: install updates as the last layer (20/21 layers kept, ~160 kB new). (S11 second extension)
- The VS Code player assumed `activeTextEditor` tells whether a cell is in edit mode, and so skipped entering it. In command mode the active editor still points at the cell's editor, which the owner found as a "half-hacker mode" bug. The obvious fix (always run `notebook.cell.edit`) was probed and also wrong: it toggles out of edit mode on a cell that is already editing. `showTextDocument(cell.document, {selection})` is the working fix. (S12)

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

- The agent's first prototype of a replacement sync core was a plain diff3
  over cell content. It cannot carry one half's edit along with the other
  half's move of the same cell, which is the probe's main family. Identity has
  to be settled first (each half aligned to the recorded sequence), then order
  and content merged separately. Do not start from diff3 again; the
  investigation doc's §4.8 lists this and four more dropped approaches. (S20)
- An oracle-free exhaustive check found in minutes what the 67,871-case probe
  had not: the prototype silently chose which of several byte-identical cells
  a half had removed. Laws over every small input are the stronger tool for
  this engine, and the one-sided law fails on master today. (S20)
- Not attempted, on purpose: pairing by body similarity (the ledger holds
  fingerprints only, and a tuned threshold would be one more special case) and
  a Lean proof (no proof assistant on the machine). (S20)
- The raw session file holds `api-request-blob` rows that repeat user and
  assistant text, and `clean_transcript.py` cleans them as dialogue: every
  block appeared twice, and two skill bodies came back as owner text through
  their unflagged blob copies. The subagent hand-back arrives as a
  `queue-operation` enqueue row and was filed under the owner. All were dropped
  from a temporary source copy (90 blob rows, the hand-back rows, the skill
  bodies; a line-by-line comparison showed no dialogue lost). No issue is
  filed for the cleaner yet. (S20)
- Follow-up to the line above: the cleaner defect is filed as #1219, after the
  owner gave explicit permission. (S20, after the save)

- The agent's measurement of the cache regression caused the thing it
  measured: it built with the course venv's clm while the owner used the
  global editable install, the template fingerprint flipped on CRLF against
  LF, 51% of the cache missed, 3,660 output files were rewritten and the build
  failed. Its first suspect (hanging notebooks that never let the cache warm)
  was wrong. Measure with the install the owner builds with. (S22)
- The agent first timed the wrong spec (`machine-learning-azav.xml` instead of
  `machine-learning-azav-2026-04.xml`); the owner corrected it. (S22)
- A flake that would not reproduce locally in eleven full-suite runs was found
  by instrumenting the real nightly workflow on a throwaway branch with a
  per-test snapshot of the logger state: reproduce the state corruption, not
  the flake. (S21)
- A staged Hermes session holds each pre-compaction row two or three times
  (compaction re-inserts copies stamped with the compaction time, tool-call ids
  unchanged), and delegation results, background-process notices and loaded
  skill bodies arrive as user rows. `clean_transcript.py` cleans all of them as
  dialogue: the first clean of S21 had 28 owner blocks where 9 were the
  owner's. Both sessions were cleaned from a filtered temporary copy (copies
  matched on role, content and tool-call ids; harness rows by their opening
  marker). The cleaner also printed each session's start, in local time, as
  its content boundary; both were replaced by hand. (S21, S22)

- The investigation doc's §4.6 said that a member with an open question keeps
  its state while the rest of a pass goes on. Checked across passes on every
  small input, that holds only for questions about id'd cells (`translate_new`,
  `remove_localized_side`). Whether a question about un-id'd cells or about
  order is asked depends on the cells around it, so any other open question has
  to hold the whole part. Do not design a finer partial apply again without
  `scripts/sync_model_passes.py`; the doc's §4.8, items 6 to 10, lists what
  broke. (S23)
- The agent recommended D1 on the probe's "18% of one-sided cases fail". The
  law check on small decks measured 92% (15,400 of 16,744), of which 9,200 are
  the engine's designed answerless `verify_cold` for a one-sided un-id'd
  insertion. The probe counts an answered question as convergence and has few
  insertions; its rate describes the probe, not the engine. (S23)
- A recorded cell that gains a `slide_id` on one half looked like a detail of
  the cell's output. Folding the new id into the member's identity at once
  dropped the cell from the identical-copies rule, and the exhaustive check
  with tags found merges that applied both halves' edits to two identical
  cells without asking (the D3 case). The recorded identity stays as recorded
  until the pass is over. (S23)
- Four failures the shadow on real history first showed as the engine's were the harness's own: a new companion is written into `voiceover/`; the title macro's key is `id:title` without a `slide_id`; a merge commit lists its files only with `diff-tree -m --first-parent`; and `tests/slides/sync_probe.py`'s apply helper passes `#` to the verify gate, which fails every C++ deck. Trace a surprising engine result before counting it. (S23, third part)
- The prototype was written against the probe's vocabulary: its identity question looped over the kinds `code` and `md`, and it had no notion of a fork or of a cell's file. Only real decks showed it; the probe and the laws could not. (S23, third part)

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
| S9 (save extension) | 2026-09-26 | `recordings-rerecord-backlog/transcripts/2026-09-25-s9.md` | 6.96 MB → 38 kchar (184:1) | 1 / 42 (278 tool stubs, 3 harness-authored turns dropped) | same Claude Code session `fa157537-e0aa-465d-adaf-ed286cdc900b.jsonl` | content through 2026-09-26 03:18 — extends the row above with the first save itself (PR #1018, merged) and the closing recap; supersedes the earlier boundary |
| S11 | 2026-10-02 | `typing-replay/transcripts/2026-10-02-s11.md` | 5.25 MB → 23 kchar (229:1) | 3 / 25 (95 tool stubs, 1 harness-authored turn dropped) | Claude Code session `6206a73a-1f92-4816-a421-5dc6a9b8e9f1.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout; save written in worktree `save-typing-replay`) | content through 2026-10-02 05:53 (the brainstorm, the owner's four answers, the spike build and browser verification, the save request); the design doc, this save and the step-1 issue are covered by the state file |
| S11 (save extension) | 2026-10-02 | `typing-replay/transcripts/2026-10-02-s11.md` | 9.08 MB → 57 kchar (159:1) | 6 / 103 (310 tool stubs, 3 harness-authored turns dropped; one documented container password redacted by hand) | same Claude Code session `6206a73a-1f92-4816-a421-5dc6a9b8e9f1.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`; #1023 in worktree `issue-1023-recording-code-along`, saves in `save-typing-replay`) | content through 2026-10-02 11:35 (step-1 decisions, the repo made public, packaging + live-kernel test, the #1023 implementation and review, the image rebuild); supersedes the earlier boundary; this save itself is covered by the state file |
| S11 (second save extension) | 2026-10-02 | `typing-replay/transcripts/2026-10-02-s11.md` | 9.85 MB → 72 kchar (136:1) | 12 / 127 (365 tool stubs, 4 harness-authored turns dropped; re-cleaned with `--allow-narrowing` after review: the only "lost" line was the earlier hand redaction of the container password, re-applied) | same Claude Code session `6206a73a-1f92-4816-a421-5dc6a9b8e9f1.jsonl` | content through 2026-10-02 13:42 (0.1.1, cheat sheet, the image-layer finding and the 0.5.6 hand-off); supersedes the earlier boundary; this save is covered by the state file |
| S12 | 2026-10-02/03 | `typing-replay/transcripts/2026-10-02-s12.md` | 1.83 MB → 29 kchar (63:1) | 12 / 36 (95 tool stubs, 3 harness-authored turns dropped; 3 owner messages appear twice, as queued and as delivered input, left as cleaned) | Claude Code session `86dd51d6-d947-4015-bac9-7c8f00697adc.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout; extension work in the separate jupyterlab-clm-typing checkout; save written in worktree `save-s12-typing-vscode`) | content through 2026-10-03 01:39 (feasibility, the spike, the edit-mode bug, the clm 1.31.0 release, the command-mode-key trade-off, CI and releases, the save request); the save itself and the merge of PR #1032 are covered by the state file |
| S13 | 2026-10-09 | `cam-portal-inception/transcripts/2026-10-09-s13.md` | 0.90 MB → 10 kchar (87:1) | 2 / 5 (25 tool stubs, 1 harness-authored turn dropped; the interview answers live in the AskUserQuestion stubs; two agent messages — the exploration summary and the trust-boundary argument — are absent from the raw source, see the thread's state file) | Claude Code session `bd0a79ed-f9fe-484d-a9e5-dc49bc72e463.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout, plan mode; inception doc in the separate cam-portal repo; save written in worktree `save-cam-portal-inception`) | content through 2026-10-09 14:02 (the interview, the architecture decision, the cam-portal repo creation, the trainer-notes correction); the save itself is covered by the state file |
| S14 | 2026-11-03/04 | `sync-pool-pairing/transcripts/2026-11-04-s14.md` | 12.69 MB → 135 kchar (93:1, after removing 3 compaction summaries, 4 subagent hand-backs, 2 skill bodies and a tool-load notice from a temporary source copy) | 36 / 188 (672 tool stubs; owner messages queued mid-turn appear twice, left as cleaned; two "note offered by a side agent" blocks are another session's output relayed as input, not the owner's words) | Claude Code session `94d81808-463b-4f22-8910-367dc9adf11e.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout; work in worktrees `sync-sweep` (removed) and others; save written in worktree `save-s14`) | content through 2026-11-04 19:06 (the review, revert, sweep, steps 1–4, the residual decision, the step 3 scan and withdrawal, the branch and worktree clean-up, the save request); this save itself is covered by the state file |
| S15 | 2026-11-05/08 | `sync-pool-pairing/transcripts/2026-11-06-s15.md` | 18.39 MB → 228 kchar (extended snapshot; after removing delivered blocks, skill bodies and 3 compaction summaries filed under the owner, post-clean; delivered subagent reports are absent, their content lives in the PRs and issues) | 30 / 64 (tool stubs kept; owner messages queued mid-turn can appear twice, left as cleaned) | Claude Code session `b021abd2-1e6d-4d4c-84f5-2eaf06e11699.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, resumed S14; work in worktrees, saves written in worktrees `save-s15`, `save-s15b` and `save-s15c`, the last reused for the S16-S19 saves) | content through 2026-11-08 05:50: #1142, #1150, the 1.36.0 and 1.37.0 releases, #1163, the open-issue triage, #1137, #1093/#908 and #1143 decisions, the #787 check, the recovered-branch question, and #799 to its close (C3 + C5, the adversarial review of #1201, C11 + C6, C9, C2, C10, the digest-check timing and the no-flag decision), the recovered-branch deletion, the old-session triage (S16-S19) and #1213; the save and later work are covered by the state file. Scanned against the 17 live secret-named env values: 0 hits |
| S16 | 2026-10-09 | `sync-prewrite-verify/transcripts/2026-10-09-s16.md` | 1.47 MB → 37 kchar (40:1, after removing delivered blocks, skill bodies and harness notices filed under the owner) | 4 / 15 (45 tool stubs) | Claude Code session `0d0c3183-80df-4622-bdd1-6cd35582ba51.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, main checkout; cleaned retroactively on 2026-11-08 in worktree `save-s15c`) | content through 2026-10-09 20:18 UTC (the cleaner's file-mtime boundary (2026-10-29 22:42) was replaced by hand with the last dialogue record): the triage, the #1051 design discussion and decision, PR #1074, worktree cleanup. Secrets scan: 0 hits against 17 live values |
| S17 | 2026-10-11 | `docker-workers-ops/transcripts/2026-10-11-s17.md` | 1.84 MB → 28 kchar (65:1) | 6 / 6 (111 tool stubs; owner messages queued mid-turn appear twice, left as cleaned) | Claude Code session `d0088deb-6f22-4d3d-aff2-bc20b2fc546b.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, main checkout; cleaned retroactively on 2026-11-08 in worktree `save-s15c`) | content through 2026-10-11 22:00 UTC (the cleaner's file-mtime boundary (2026-10-29 22:42) was replaced by hand with the last dialogue record): the 1.34.2 image rebuild, the openai/httpx2 correction, the push, the CppCourses Docker build. Secrets scan: 0 hits |
| S18 | 2026-10-12/13 | `docker-workers-ops/transcripts/2026-10-12-s18.md` | 5.91 MB → 99 kchar (60:1) | 21 / 21 (361 tool stubs; owner messages queued mid-turn appear twice, left as cleaned) | Claude Code session `0ace84b6-a03c-4f15-a188-b253a78bbece.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, main checkout; cleaned retroactively on 2026-11-08 in worktree `save-s15c`) | content through 2026-10-13 01:11 UTC (the cleaner's file-mtime boundary (2026-10-29 22:42) was replaced by hand with the last dialogue record): #1078/#1079 and 1.34.3, the disk move to G:, `force-unsafe-io`, cam container recreation, the cache-dir setting, #1088. Secrets scan: 0 hits |
| S19 | 2026-11-01/02 | `sync-pool-pairing/transcripts/2026-11-02-s19.md` | 3.08 MB → 32 kchar (97:1, after removing delivered blocks, skill bodies and harness notices filed under the owner) | 3 / 16 (118 tool stubs) | Claude Code session `e79f0a58-3fe1-4931-a4c9-59f1a38ea215.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, main checkout; cleaned retroactively on 2026-11-08 in worktree `save-s15c`) | content through 2026-11-02 00:45 UTC: the 39-issue triage and the start of the #1124 fix that became #1129. Precedes S14; numbered in save order. Secrets scan: 0 hits |
| S20 | 2026-11-10 | `sync-structural-core/transcripts/2026-11-10-s20.md` | 5.12 MB → 19 kchar (after removing the `api-request-blob` rows, the subagent hand-back rows, two skill bodies and the task notifications from a temporary source copy) | 2 / 10 (136 tool stubs; the agent's short progress messages between the opening and the report are absent from the raw source) | Claude Code session `4c2074d5-e2ad-424b-ac0f-4831cdc6a870.jsonl` (slug `C--Users-tc-Programming-Python-Projects-clm`, started in the main checkout; the landing and this save written in worktree `sync-structural-core`) | content through 2026-11-10 07:18 UTC: the investigation request, the report, the landing of PR #1217, the save request and the first steps of the save; the rest of the save is covered by the state file. Scanned against the 15 live secret-named env values: 0 hits |
| S20 (save extension) | 2026-11-10 | `sync-structural-core/transcripts/2026-11-10-s20.md` | 5.57 MB → 24 kchar (same staging: 114 `api-request-blob` rows, the hand-back rows, three skill bodies and the task notifications removed from a temporary source copy; the cleaner accepted the overwrite, so no earlier dialogue line was lost) | 3 / 13 (167 tool stubs; the output of a `/context` command is harness text and absent) | same Claude Code session `4c2074d5-e2ad-424b-ac0f-4831cdc6a870.jsonl` | content through 2026-11-10 08:13 UTC — extends the row above with the first save (PR #1218), the owner's permission to file the cleaner issue, #1219 and PR #1220, and the second save request; supersedes the earlier boundary. Scanned against the 15 live secret-named env values: 0 hits |
| S21 | 2026-07-26 | `studio-preview-review/transcripts/2026-07-26-s21.md` | 2.47 MB → 72 kchar (staged full lineage, 1,049 rows of which 778 archived; cleaned from a filtered temporary copy without 182 compaction copies, 10 delegation results, 6 background-process notices, and with the `next-issue` skill body reduced to one line; the cleaner dropped 3 compaction summaries) | 9 / 104 (336 tool stubs) | Hermes session `20260726_191709_03773c` ("Ongoing CLM fixes"), the maintainer's Hermes profile, staged with `scripts/stage_hermes_session.py`; saved retroactively on 2026-11-10 in worktree `sync-structural-core` | content through 2026-07-26 22:56 UTC (the last dialogue record; the cleaner's boundary was replaced by hand): the #704 review, the three follow-ups, the #706 option, the #694 flake hunt, the break. Scanned against the 15 live secret-named env values: 0 hits; read in full |
| S22 | 2026-07-27 | `cache-performance-regression/transcripts/2026-07-27-s22.md` | 1.88 MB → 46 kchar (staged full lineage, 976 rows of which 805 archived; filtered copy without 213 compaction copies, 4 background-process notices and 2 preserved-task-list rows; the cleaner dropped 3 compaction summaries) | 9 / 68 (282 tool stubs; 2 owner blocks delivered out-of-band; two owner steers the agent replies to are absent from the source) | Hermes session `20260727_003818_23147e` ("Investigating CLM build performance regression"), same profile and staging; started in the main checkout, later worktree `issue-711-cache-perf` | content through 2026-07-27 02:20 UTC (the last dialogue record; boundary replaced by hand): #711, the measurements, the pause, the instrumented owner build, the root cause, PR #712 and PR #713. Scanned against the 15 live secret-named env values: 0 hits; read in full. Precedes S1; numbered in save order, like S21 |
| S23 | 2026-11-10 | `sync-structural-core/transcripts/2026-11-10-s23.md` | 5.30 MB → 26 kchar (after removing 114 `api-request-blob` rows, 25 task notifications and the skill body from a temporary source copy) | 1 / 26 (250 tool stubs; the owner's choice of thread and depth is in the AskUserQuestion stub; one owner message, after which the session is solo work) | Claude Code session `0cc4adea-2dd6-4968-b25d-bf5719caa9c4.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, started in the main checkout with `/resume-discussion` on S20; the work and this save in worktree `sync-structural-core`) | content through 2026-11-10 13:51 UTC: the resume report, the agent's read of D1 to D5, the owner's three answers, steps 1 and 2 with their two PRs, and the first steps of this save; the rest of the save is covered by the state file. Scanned against the live secret-named env values: 0 hits; read in full |
| S23 (save extension) | 2026-11-10/11 | `sync-structural-core/transcripts/2026-11-10-s23.md` | 6.21 MB → 30 kchar (same staging: 129 `api-request-blob` rows, 26 task notifications and two skill bodies removed from a temporary source copy; the cleaner accepted the overwrite, so no earlier dialogue line was lost) | 1 / 29 (265 tool stubs; 3 harness-authored turns dropped by the cleaner; the summary of one context compaction is absent, as it should be) | same Claude Code session `0cc4adea-2dd6-4968-b25d-bf5719caa9c4.jsonl` | content through 2026-11-11 08:44 UTC — extends the row above with the first save (PR #1226), the closing report to the owner and the second save request; supersedes the earlier boundary. Scanned against the live secret-named env values: 0 hits; read in full |
| S23 (third part) | 2026-11-10/13 | `sync-structural-core/transcripts/2026-11-10-s23.md` | 9.71 MB → 49 kchar (same staging: 198 `api-request-blob` rows, 48 task notifications and two skill bodies removed from a temporary source copy; the cleaner accepted the overwrite, so no earlier dialogue line was lost) | 2 / 63 (457 tool stubs; 4 harness-authored turns dropped by the cleaner; the owner's choice of order is in an AskUserQuestion stub) | the same session `0cc4adea-2dd6-4968-b25d-bf5719caa9c4.jsonl`, continued from the profile directory `~/.claude-cam` (slug `C--Users-tc-Programming-Python-Projects-clm`), whose copy holds the whole session; the third part ran on Claude Opus 5.5 | content through 2026-11-13 02:38 UTC — extends the rows above with the owner's request to start step 3 (00:13 UTC), the order question, the shadow and the two PRs; supersedes the earlier boundary. Scanned against the live secret-named env values: 0 hits; read in full |
| S23 (fourth part) | 2026-11-10/14 | `sync-structural-core/transcripts/2026-11-10-s23.md` | 19.72 MB → 133 kchar (staged: 1,375 rows removed from a temporary source copy — the `api-request*` rows, task notifications and skill bodies; the cleaner accepted the overwrite, so no earlier dialogue line was lost) | 8 / 227 (1,070 tool stubs; 7 harness-authored turns dropped by the cleaner; the summaries of several context compactions are absent, as they should be) | the same session `0cc4adea-2dd6-4968-b25d-bf5719caa9c4.jsonl` in the profile directory `~/.claude-cam` (slug `C--Users-tc-Programming-Python-Projects-clm`); run on Claude Opus 5.5 | content through 2026-11-14 03:53 UTC — extends the rows above with the go, step 4, the design note and O1 to O5, phases A, B and C, and the start of this save; supersedes the earlier boundary. Checked for the owner's e-mail address and home paths: none |

### Deliberately skipped

| Date | Session | Reason |
|------|---------|--------|
| 2026-07-26 … 2026-09-13 | 11 Claude sessions and 2 Hermes sessions listed by `python scripts/audit_transcripts.py` at the time of S1 | Predate the discussion stack; not triaged yet, not skipped for good — the #928 design sessions (2026-09-12/13, slug `…-issue-928-cpp-ide-export`) and the two long Hermes reviews of July 2026 (#704 adversarial review, performance-regression investigation) are the candidates; the raw sources still exist on the maintainer's machine |
| 2026-09-14, 2026-09-18 | `64d938aa…`, `8891edb1…` (root slug) | Empty sessions (0 owner turns); nothing to save |
| 2026-09-18 | `8c80e928…` (14 owner turns, open-issue prioritisation → the 1.29.0 release) and `4fddb4b5…` (3 owner turns, #914 / #881+#886 / #868–#870 implementation) | Not triaged at S2; the first is a candidate (prioritisation argument), the second is task work already recorded in PRs #954/#956/#957 and the release notes |
| 2026-09-06 … 2026-09-13 | `83a877dc…` (#917 investigation, 3 owner turns), `0260bc6b…` (#793, 1 turn), `8ad64f38…` / `c0890feb…` / `4fdc59b9…` (#928 phases 1–3 in the `issue-928-cpp-ide-export` worktree slug, 8/2/4 turns) | Task sessions recorded in their PRs and the #928 handover; the #928 design argument is the S1 candidate already noted above — not re-triaged at S8 |
| 2026-09-25 | `ca62afc6…` (0 owner turns), `942bdc7f…` (4 owner turns, issue triage that led to the autonomous backlog run), `c787e1f0…` and `63c0dc34…` (3 owner turns each, skill-driven implementation of #968/#969/#981/#993/#991) | The empty one has nothing to save; the two implementation sessions are task work recorded in PRs #998–#1001 and #1003; the triage session is a candidate (the prioritisation argument for the backlog order), not triaged at S8 |
| 2026-09-25 (at S9) | the same unsaved Claude sessions the S8 audit listed, plus the two July Hermes reviews | Nothing new to triage at S9; the S8 verdicts stand (the 2026-09-18 prioritisation session `8c80e928…` and the 2026-09-25 triage session `942bdc7f…` remain candidates) |
| 2026-09-26 (at S11) | `dcf272be…` (5 owner turns; the S10 resume that found PR #1017 unmerged and fixed `_commits_since` ordering) | Number S10 is used by the corrections above and by `recordings-rerecord-backlog/state.md`, but the session has no transcript row: its substance landed in the corrected state file and the #1017 fix. Candidate for a later save if the resume argument is needed |
| 2026-09-28 | `22c90a9b…` (0 owner turns) | Empty session; nothing to save |
| 2026-10-03 (at S12) | the unsaved sessions the audit lists besides S12 (`63c0dc34…`, `dcf272be…`, `22c90a9b…` and the two July Hermes reviews) | Nothing new to triage; earlier verdicts stand |
| 2026-10-09 (at S13) | `53821502…` (2026-10-03, 8 owner turns, issues #1031/#1034) and `0d0c3183…` (2026-10-09, 11 owner turns, issue triage) plus the earlier unsaved ones | Not triaged at S13 (other sessions' work; the triage session is a candidate); earlier verdicts stand |
| 2026-10-09 … 2026-11-02 (at S14) | `0d0c3183…` (13 owner turns, issue triage), `81ca7aad…` / `47bc20f8…` (2 each), `d0088deb…` (6), `0ace84b6…` (24, C++ build errors), `e79f0a58…` (16, issue triage) plus the two July Hermes reviews | Not triaged at S14 (other sessions' work); the two triage sessions and `0ace84b6…` are candidates; earlier verdicts stand |
| 2026-10-09 … 2026-10-11 (at S16-S19) | `0d92656a-6fad-4d83-a754-3e5bc92a8d53` (empty, 0 owner turns), `47bc20f8-acd3-48f9-9145-8d7e15e56c63` (2 owner turns: `uv tool install` blocked by a locked `Scripts` dir, fixed by closing VS Code), `81ca7aad-d1dc-4e0a-80de-96546ba2c1ad` (2 owner turns as counted by the audit, one message: a release request) | Nothing to resume; the release is in the changelog. The other four sessions that audit listed were saved as S16-S19 |
| 2026-11-10 (at S20) | the two July Hermes reviews (`20260726_191709_03773c`, 18 owner turns; `20260727_003818_23147e`, 22 owner turns), the only other sessions the audit lists | Not triaged at S20 (other sessions' work); the earlier verdict stands: candidates, not skipped for good |
| 2026-11-10 (at S21, S22) | the same two July Hermes reviews | No longer skipped: saved as S21 and S22 at the owner's request, after the row above had made the audit stop listing them. The candidate notes in the rows above are discharged for these two |
| 2026-11-10 (at S23) | none | The audit lists only the session being saved; nothing to skip |
| 2026-11-11 (at the S23 save extension) | none | The audit lists no unsaved session; nothing to skip |
| 2026-11-13 (at S23's third part) | none | The audit, pointed at `~/.claude-cam/projects` with `--claude-projects`, lists no unsaved session; nothing to skip |
| 2026-11-14 (at S23's fourth part) | none | The audit lists no unsaved session; nothing to skip |
